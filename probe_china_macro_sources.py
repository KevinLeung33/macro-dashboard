"""Read-only probe for the China macro endpoints used by the dashboard.

Run this on the server before changing a production source mapping:

    source .venv/bin/activate
    python probe_china_macro_sources.py

It does not write SQLite, change configuration, or make AI calls.  It checks
the real AKShare response schema, keeps only finite numeric observations within
the expected range, and reports the newest usable observation for each series.
"""
from __future__ import annotations

import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

try:
    from dotenv import load_dotenv
except ImportError:  # The primary probe works without optional .env loading.
    load_dotenv = None


def _pick_column(columns, keywords, fallback=0):
    """Find a response column by its semantic name, with a safe fallback."""
    normalized = [(str(column).strip().lower(), column) for column in columns]
    for keyword in keywords:
        needle = str(keyword).lower()
        for name, original in normalized:
            if needle in name:
                return original
    if not columns:
        return None
    return columns[min(max(fallback, 0), len(columns) - 1)]


def _normalize_date(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y-%m-%d")

    text = str(value).strip()
    chinese_month = re.match(r"^(\d{4})\s*年\s*(\d{1,2})\s*月", text)
    if chinese_month:
        return f"{chinese_month.group(1)}-{chinese_month.group(2).zfill(2)}-15"
    if len(text) == 6 and text.isdigit():
        return f"{text[:4]}-{text[4:]}-01"
    if len(text) == 7 and text[4:5] == "-":
        return f"{text}-15"

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y-%m-%d")


def _finite_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _usable_pairs(frame, date_keywords, value_keywords, valid_range):
    columns = list(frame.columns)
    date_col = _pick_column(columns, date_keywords, fallback=0)
    value_col = _pick_column(columns, value_keywords, fallback=1)
    if date_col is None or value_col is None:
        return [], date_col, value_col

    pairs = []
    for _, row in frame.iterrows():
        observation_date = _normalize_date(row[date_col])
        value = _finite_float(row[value_col])
        if observation_date is None or value is None:
            continue
        if valid_range is not None and not (valid_range[0] <= value <= valid_range[1]):
            continue
        pairs.append((observation_date, value))
    return sorted(set(pairs)), date_col, value_col


def _probe(
    ak,
    label,
    function_name,
    *,
    date_keywords,
    value_keywords,
    valid_range,
    max_age_days,
    kwargs=None,
    attempts=1,
):
    """Probe one endpoint and report whether it is usable for this logical series."""
    func = getattr(ak, function_name, None)
    if func is None:
        print(f"FAIL  | {label:<32} | {function_name} not found in installed AKShare")
        return False

    kwargs = kwargs or {}
    last_error = None
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            frame = func(**kwargs)
            elapsed = time.monotonic() - started
            if frame is None or frame.empty:
                raise ValueError("empty response")
            pairs, date_col, value_col = _usable_pairs(
                frame, date_keywords, value_keywords, valid_range
            )
            if not pairs:
                raise ValueError(
                    "no finite in-range rows "
                    f"(date_col={date_col!r}, value_col={value_col!r}, cols={list(frame.columns)!r})"
                )
            latest_date, latest_value = pairs[-1]
            age_days = (date.today() - datetime.strptime(latest_date, "%Y-%m-%d").date()).days
            freshness = "PASS" if age_days <= max_age_days else "STALE"
            print(
                f"{freshness:<5} | {label:<32} | rows={len(pairs)}, "
                f"latest={latest_date} value={latest_value:g}, age={age_days}d, "
                f"columns=({date_col}, {value_col}), {elapsed:.1f}s"
            )
            return freshness == "PASS"
        except Exception as exc:  # Endpoint errors are the intended output of this probe.
            last_error = exc
            if attempt < attempts:
                time.sleep(1.0)

    retry_note = f" after {attempts} attempts" if attempts > 1 else ""
    print(f"FAIL  | {label:<32} | {type(last_error).__name__}: {str(last_error)[:240]}{retry_note}")
    return False


def _tushare_frame(api_name: str, token: str, params=None, fields: str = ""):
    """Call one Tushare endpoint without importing its optional SDK."""
    response = requests.post(
        "https://api.tushare.pro",
        json={
            "api_name": api_name,
            "token": token,
            "params": params or {},
            "fields": fields,
        },
        timeout=20,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"HTTP {response.status_code}; non-JSON response") from exc
    if not response.ok or payload.get("code") != 0:
        raise RuntimeError(
            f"HTTP {response.status_code}; code={payload.get('code')}; "
            f"msg={str(payload.get('msg') or '')[:180]}"
        )
    data = payload.get("data") or {}
    columns = data.get("fields") or []
    items = data.get("items") or []
    if not columns or not items:
        raise ValueError("empty rows or fields")
    return pd.DataFrame(items, columns=columns)


def _shift_month(date_text: str, months: int) -> str:
    observed = datetime.strptime(date_text, "%Y-%m-%d")
    month_index = observed.year * 12 + observed.month - 1 + months
    year, month_zero_based = divmod(month_index, 12)
    return f"{year:04d}-{month_zero_based + 1:02d}-{observed.day:02d}"


def _probe_optional_tushare():
    """Test paid/points-gated exact fallbacks only when a token is configured."""
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    print("\n=== Tushare exact China macro fallbacks (optional token) ===")
    if not token:
        print("SKIP  | Tushare cn_m / sf_month / repo_daily | set TUSHARE_TOKEN in .env to test; token is never printed")
        return

    def report_frame(label, api_name, *, params, date_keywords, value_keywords, valid_range, max_age_days):
        started = time.monotonic()
        try:
            frame = _tushare_frame(api_name, token, params=params)
            pairs, date_col, value_col = _usable_pairs(
                frame, date_keywords, value_keywords, valid_range
            )
            if not pairs:
                raise ValueError(f"no finite in-range rows; cols={list(frame.columns)!r}")
            latest_date, latest_value = pairs[-1]
            age_days = (date.today() - datetime.strptime(latest_date, "%Y-%m-%d").date()).days
            status = "PASS" if age_days <= max_age_days else "STALE"
            print(
                f"{status:<5} | {label:<32} | rows={len(pairs)}, "
                f"latest={latest_date} value={latest_value:g}, age={age_days}d, "
                f"columns=({date_col}, {value_col}), {time.monotonic() - started:.1f}s"
            )
            return frame, pairs
        except Exception as exc:
            print(f"FAIL  | {label:<32} | {type(exc).__name__}: {str(exc)[:240]}")
            return None, []

    report_frame(
        "Tushare M2 YoY (cn_m)",
        "cn_m",
        params={},
        date_keywords=["month"],
        value_keywords=["m2_yoy"],
        valid_range=(0, 50),
        max_age_days=75,
    )
    social_frame, social_pairs = report_frame(
        "Tushare social-finance stock",
        "sf_month",
        params={},
        date_keywords=["month"],
        value_keywords=["stk_endval"],
        valid_range=(0, 10000),
        max_age_days=75,
    )
    if social_frame is not None and social_pairs:
        stock_by_month = dict(social_pairs)
        latest_date, latest_stock = social_pairs[-1]
        base_stock = stock_by_month.get(_shift_month(latest_date, -12))
        if base_stock and base_stock > 0:
            print(
                f"INFO  | Tushare social-finance stock YoY | latest={latest_date}, "
                f"computed_yoy={(latest_stock / base_stock - 1) * 100:.2f}%"
            )
        else:
            print("WARN  | Tushare social-finance stock YoY | needs a matching observation 12 months earlier")
    end_date = date.today()
    report_frame(
        "Tushare DR007 (repo_daily)",
        "repo_daily",
        params={
            "ts_code": "DR007.IB",
            "start_date": (end_date - timedelta(days=365)).strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d"),
        },
        date_keywords=["trade_date"],
        value_keywords=["weight", "close"],
        valid_range=(0, 20),
        max_age_days=10,
    )


def main() -> int:
    if load_dotenv is not None:
        load_dotenv(dotenv_path=Path.cwd() / ".env")
    try:
        import akshare as ak
    except ImportError:
        print("AKShare is not installed. Activate .venv and run: pip install -r requirements.txt")
        return 2

    print("macro-dashboard China macro source probe")
    print("mode: read-only (no SQLite writes, no configuration changes, no AI calls)")
    print(f"AKShare version: {getattr(ak, '__version__', 'unknown')}")
    print("status | candidate                        | result")
    print("-" * 118)

    repo_end = date.today()
    repo_start = repo_end - timedelta(days=365)
    checks = [
        {
            "label": "CPI YoY (official candidate)",
            "function_name": "macro_china_cpi",
            "date_keywords": ["月份", "日期", "时间"],
            "value_keywords": ["全国-同比增长", "同比增长", "同比"],
            "valid_range": (-10, 30),
            "max_age_days": 75,
        },
        {
            "label": "CPI MoM event (old mapping)",
            "function_name": "macro_china_cpi_monthly",
            "date_keywords": ["日期", "时间"],
            "value_keywords": ["今值", "现值"],
            "valid_range": (-10, 10),
            "max_age_days": 75,
        },
        {
            "label": "PPI YoY (official candidate)",
            "function_name": "macro_china_ppi",
            "date_keywords": ["月份", "日期", "时间"],
            "value_keywords": ["当月同比增长", "同比增长", "同比"],
            "valid_range": (-30, 30),
            "max_age_days": 75,
        },
        {
            "label": "M2 YoY (Jin10 current)",
            "function_name": "macro_china_m2_yearly",
            "date_keywords": ["日期", "时间"],
            "value_keywords": ["今值", "现值"],
            "valid_range": (0, 50),
            "max_age_days": 75,
        },
        {
            "label": "M2 YoY (Sina candidate)",
            "function_name": "macro_china_supply_of_money",
            "date_keywords": ["统计时间", "月份", "日期", "时间"],
            # The table contains both the M2 *level* and M2 YoY.  Prefer the
            # precise YoY header; the old broad match incorrectly chose the
            # level and rejected it against a percentage range.
            "value_keywords": ["货币和准货币（广义货币M2）同比增长", "m2同比", "同比增长"],
            "valid_range": (0, 50),
            "max_age_days": 75,
        },
        {
            "label": "社融增量 (not stock YoY)",
            "function_name": "macro_china_shrzgm",
            "date_keywords": ["月份", "日期", "时间"],
            "value_keywords": ["社会融资规模增量", "社融"],
            "valid_range": (-100000, 100000),
            "max_age_days": 75,
        },
        {
            "label": "财新制造业 PMI",
            "function_name": "macro_china_cx_pmi_yearly",
            "date_keywords": ["日期", "时间"],
            "value_keywords": ["今值", "现值"],
            "valid_range": (20, 80),
            "max_age_days": 75,
        },
        {
            "label": "FDR007 (from DR007 trades)",
            "function_name": "repo_rate_hist",
            "date_keywords": ["date", "日期"],
            "value_keywords": ["fdr007"],
            "valid_range": (0, 20),
            "max_age_days": 10,
            "kwargs": {
                "start_date": repo_start.strftime("%Y%m%d"),
                "end_date": repo_end.strftime("%Y%m%d"),
            },
            "attempts": 3,
        },
    ]

    passed = 0
    for spec in checks:
        if _probe(ak, **spec):
            passed += 1
    print("-" * 118)
    print(f"SUMMARY | fresh and schema-valid: {passed}/{len(checks)}")
    print("Note: STALE means the endpoint returned structurally valid data, but its newest observation is older than the expected release window.")
    _probe_optional_tushare()
    # A source failure is diagnostic information, not a failed maintenance task.
    return 0


if __name__ == "__main__":
    sys.exit(main())
