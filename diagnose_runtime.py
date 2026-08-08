"""Read-only runtime and data-source diagnostic for macro-dashboard.

Run from the project directory:
    python diagnose_runtime.py

The script deliberately avoids printing secrets and does not refresh data or
call the AI API. Its output is intended to be pasted back for diagnosis.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "macro_data.db")))
ENV_PATH = ROOT / ".env"

CRITICAL_SERIES = [
    ("fred", "FEDFUNDS"),
    ("fred", "DGS2"),
    ("fred", "DGS10"),
    ("fred", "DFII10"),
    ("fred", "T10Y3M"),
    ("fred", "CPIAUCSL"),
    ("fred", "UNRATE"),
    ("fred", "INDPRO"),
    ("fred", "BAMLH0A0HYM2"),
    ("fred", "NFCI"),
    ("fred", "VIXCLS"),
    ("fred", "SP500"),
    ("fred", "NASDAQCOM"),
    ("fred", "CBBTCUSD"),
    ("fred", "WALCL"),
    ("crypto_liquidity", "STABLE_TOTAL_MCAP"),
    ("crypto_liquidity", "STABLE_MAJOR_MCAP"),
    ("crypto_liquidity", "ETHBTC"),
    ("crypto_market", "BTC_FUNDING_RATE"),
    ("crypto_market", "BTC_OPEN_INTEREST"),
    ("crypto_flows", "BTC_ETF_NETFLOW"),
    ("crypto_flows", "BTC_EXCHANGE_NETFLOW"),
    ("akshare", "CN_PMI"),
    ("binance_spot", "BTC-USD"),
    ("binance_spot", "ETH-USD"),
]

MARKET_CANDIDATES = {
    "DXY": [("yfinance", "DX-Y.NYB"), ("fred", "DEXUSEU")],
    "MSTR": [("yfinance", "MSTR"), ("alpha_vantage", "MSTR")],
    "SP500": [("fred", "SP500"), ("yfinance", "^GSPC")],
    "NASDAQ": [("fred", "NASDAQCOM"), ("yfinance", "^IXIC")],
    "BTC": [("fred", "CBBTCUSD"), ("binance_spot", "BTC-USD")],
    "Gold": [("yfinance", "GC=F")],
}


def _env_file_values():
    values = {}
    if not ENV_PATH.exists():
        return values
    for raw in ENV_PATH.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _effective_env(key, file_values):
    return os.getenv(key) or file_values.get(key, "")


def _mask_url(value):
    if not value:
        return ""
    return re.sub(
        r"([?&][^=&#\s]*(?:token|api[_-]?key|secret|password|signature|sig|hook)[^=&#\s]*=)[^&#\s]+",
        r"\1<redacted>",
        str(value),
        flags=re.I,
    )


def _safe_text(value):
    """Keep diagnostic text useful while masking common credential formats."""
    text = str(value or "")
    text = re.sub(r"(Bearer\s+)[^\s]+", r"\1<redacted>", text, flags=re.I)
    return _mask_url(text)


def _safe_row(row, error_index):
    if row is None:
        return None
    values = list(row)
    if 0 <= error_index < len(values):
        values[error_index] = _safe_text(values[error_index])
    return tuple(values)


def _version(package):
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "NOT_INSTALLED"


def _print_section(title):
    print(f"\n=== {title} ===")


def _db_connection():
    return sqlite3.connect(str(DB_PATH))


def _db_diagnostics():
    if not DB_PATH.exists():
        print(f"database: MISSING ({DB_PATH})")
        return

    print(f"database: {DB_PATH}")
    print(f"database_size_mb: {DB_PATH.stat().st_size / 1024 / 1024:.2f}")
    with _db_connection() as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        print("tables:", ",".join(tables))
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        print("integrity_check:", integrity)

        print("row_counts:")
        for table in (
            "time_series", "series_meta", "fetch_log", "data_quality_issues",
            "news_articles", "ai_analyses", "daily_reports",
            "composite_signal_snapshots", "composite_signal_reviews",
        ):
            if table in tables:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  {table}: {count}")

        malformed = conn.execute(
            """SELECT source, series_id, date, value
               FROM time_series
               WHERE date NOT GLOB '????-??-??' OR value IS NULL
               ORDER BY source, series_id, date
               LIMIT 20"""
        ).fetchall()
        print("malformed_or_null_time_series_rows:", len(malformed))
        for row in malformed:
            print("  ", tuple(row))

        print("source_latest_valid_dates:")
        rows = conn.execute(
            """SELECT source,
                      MAX(CASE WHEN date GLOB '????-??-??' THEN date END),
                      COUNT(DISTINCT CASE
                          WHEN date GLOB '????-??-??' AND value IS NOT NULL
                          THEN series_id END),
                      MAX(fetched_at)
               FROM time_series
               GROUP BY source
               ORDER BY source"""
        ).fetchall()
        for row in rows:
            print("  ", _safe_row(row, 4))

        print("latest_fetch_status_by_source_series:")
        rows = conn.execute(
            """SELECT fl.source, fl.series_id, fl.status,
                      fl.records_fetched, fl.error_message, fl.created_at
               FROM fetch_log fl
               WHERE fl.id = (
                   SELECT fl2.id FROM fetch_log fl2
                   WHERE fl2.source = fl.source AND fl2.series_id = fl.series_id
                   ORDER BY fl2.created_at DESC
                   LIMIT 1
               )
               ORDER BY fl.source, fl.series_id"""
        ).fetchall()
        for row in rows:
            print("  ", _safe_row(row, 4))

        print("critical_series:")
        for source, series_id in CRITICAL_SERIES:
            row = conn.execute(
                """SELECT MAX(CASE WHEN date GLOB '????-??-??' THEN date END),
                          COUNT(*), MAX(fetched_at)
                   FROM time_series
                   WHERE source = ? AND series_id = ?
                     AND date GLOB '????-??-??' AND value IS NOT NULL""",
                (source, series_id),
            ).fetchone()
            log = conn.execute(
                """SELECT status, records_fetched, error_message, created_at
                   FROM fetch_log
                   WHERE source = ? AND series_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (source, series_id),
            ).fetchone()
            print(
                f"  {source}/{series_id}: data={tuple(row)} "
                f"fetch={_safe_row(log, 2)}"
            )


def _runtime_diagnostics(file_values):
    print("python:", sys.version.replace("\n", " "))
    print("platform:", platform.platform())
    for package in ("pandas", "streamlit", "openai", "requests", "yfinance", "akshare"):
        print(f"package_{package}: {_version(package)}")
    print("env_file_present:", ENV_PATH.exists())
    for key in (
        "OPENAI_BASE_URL", "OPENAI_MODEL", "AI_THINKING_MODE",
        "AI_ANALYZE_MAX_TOKENS", "AI_DAILY_MAX_TOKENS",
        "AI_MARKET_BRIEF_MAX_TOKENS", "AI_MARKET_BRIEF_CACHE_SECONDS",
        "NOTIFY_CHANNELS", "NOTIFY_ON_RUNTIME_ERROR",
    ):
        value = _effective_env(key, file_values)
        print(f"{key}: {value if value else '<unset>'}")
    for key in (
        "FRED_API_KEY", "OPENAI_API_KEY", "ALPHA_VANTAGE_KEY",
        "LARK_WEBHOOK_URL", "LARK_WEBHOOK_SECRET",
        "BTC_ETF_FLOWS_URL", "BTC_EXCHANGE_NETFLOW_URL",
    ):
        value = _effective_env(key, file_values)
        if key.endswith("URL") and value:
            value = _mask_url(value)
        print(f"{key}_configured: {bool(value)}")

    status_path = ROOT / "runtime" / "task_status.json"
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            print("task_status:", _safe_text(json.dumps(status, ensure_ascii=False, sort_keys=True)))
        except Exception as exc:
            print("task_status_error:", repr(exc))
    else:
        print("task_status: MISSING")


def _network_check(label, url):
    request = urllib.request.Request(url, headers={"User-Agent": "macro-dashboard-diagnostic/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            print(f"{label}: HTTP {response.status} {_mask_url(response.geturl())}")
    except urllib.error.HTTPError as exc:
        print(f"{label}: HTTP {exc.code} {exc.reason}")
    except Exception as exc:
        print(f"{label}: ERROR {type(exc).__name__}: {_safe_text(exc)}")


def _network_diagnostics(file_values):
    print("network checks are read-only and use small/health endpoints")
    _network_check("deepseek_base", _effective_env("OPENAI_BASE_URL", file_values) or "https://api.deepseek.com")
    _network_check("binance_public", "https://api.binance.com/api/v3/ping")
    _network_check("binance_futures", "https://fapi.binance.com/fapi/v1/ping")
    _network_check("yahoo_chart", "https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=5d&interval=1d")
    _network_check("defillama_stablecoins", "https://stablecoins.llama.fi/stablecoins?includePrices=false")


def _market_candidates():
    if not DB_PATH.exists():
        return
    print("market_candidate_coverage:")
    with _db_connection() as conn:
        for logical_id, candidates in MARKET_CANDIDATES.items():
            parts = []
            for source, series_id in candidates:
                row = conn.execute(
                    """SELECT MAX(CASE WHEN date GLOB '????-??-??' THEN date END), COUNT(*)
                       FROM time_series WHERE source=? AND series_id=?
                         AND date GLOB '????-??-??' AND value IS NOT NULL""",
                    (source, series_id),
                ).fetchone()
                parts.append(f"{source}/{series_id}=latest:{row[0] or '-'},rows:{row[1]}")
            print(f"  {logical_id}: " + " | ".join(parts))


def main():
    file_values = _env_file_values()
    print("macro-dashboard runtime diagnostic")
    print("generated_at_utc:", datetime.now(timezone.utc).isoformat())
    print("project_root:", ROOT)
    _print_section("RUNTIME AND CONFIG")
    _runtime_diagnostics(file_values)
    _print_section("DATABASE AND DATA QUALITY")
    _db_diagnostics()
    _print_section("MARKET PROVIDER COVERAGE")
    _market_candidates()
    _print_section("NETWORK REACHABILITY")
    _network_diagnostics(file_values)
    print("\nEND DIAGNOSTIC")


if __name__ == "__main__":
    main()
