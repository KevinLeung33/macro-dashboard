"""Read-only probes for replacement data and news sources.

This script is intentionally separate from the scheduled pipeline:

* it does not write SQLite, modify ``.env``, or invoke AI;
* it sends only one small request to each candidate;
* API-key candidates are skipped unless their key is already present in ``.env``.

Run on the server from the project root:

    source .venv/bin/activate
    python probe_candidate_sources.py

Paste the complete output back before adding any successful candidate to the
production source registry.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
TIMEOUT_SECONDS = 20
DEFAULT_RSS_UA = "macro-dashboard/1.0 RSS reader"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _env_file_values():
    """Read only simple ``KEY=value`` values without printing secrets."""
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


_ENV_FILE = _env_file_values()


def _env(name, default=""):
    return os.getenv(name) or _ENV_FILE.get(name, default)


def _version(package):
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "NOT_INSTALLED"


def _format_result(status, label, details):
    print(f"{status:<4} | {label:<34} | {details}")


def _feed_entry_count(content):
    """Validate RSS/Atom without depending on feedparser being installed."""
    try:
        import feedparser

        feed = feedparser.parse(content)
        if feed.entries:
            return len(feed.entries), "feedparser"
    except Exception:
        pass

    root = ET.fromstring(content)
    rss_items = root.findall(".//item")
    atom_entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    entries = rss_items or atom_entries
    return len(entries), "xml"


def probe_feed(label, url, user_agent):
    started = time.monotonic()
    try:
        response = requests.get(
            url,
            headers={"User-Agent": user_agent, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
            timeout=TIMEOUT_SECONDS,
        )
        elapsed = time.monotonic() - started
        if not 200 <= response.status_code < 300:
            _format_result("FAIL", label, f"HTTP {response.status_code}; {elapsed:.1f}s")
            return False
        count, parser = _feed_entry_count(response.content)
        if not count:
            _format_result("FAIL", label, f"HTTP {response.status_code}; valid XML but 0 entries; {elapsed:.1f}s")
            return False
        _format_result("PASS", label, f"HTTP {response.status_code}; {count} entries via {parser}; {elapsed:.1f}s")
        return True
    except Exception as exc:
        _format_result("FAIL", label, f"{type(exc).__name__}: {str(exc)[:160]}")
        return False


def probe_json(label, url, headers=None, validator=None):
    started = time.monotonic()
    try:
        response = requests.get(url, headers=headers or {}, timeout=TIMEOUT_SECONDS)
        elapsed = time.monotonic() - started
        if not 200 <= response.status_code < 300:
            _format_result("FAIL", label, f"HTTP {response.status_code}; {elapsed:.1f}s")
            return False
        payload = response.json()
        valid, detail = validator(payload) if validator else (True, "JSON received")
        _format_result("PASS" if valid else "FAIL", label, f"HTTP {response.status_code}; {detail}; {elapsed:.1f}s")
        return bool(valid)
    except Exception as exc:
        _format_result("FAIL", label, f"{type(exc).__name__}: {str(exc)[:160]}")
        return False


def _probe_rss_candidates():
    print("\n=== RSS: current failures and candidate replacements ===")
    configured_ua = _env("NEWS_USER_AGENT", DEFAULT_RSS_UA)
    # BLS actively publishes these official feeds.  Test both the application's
    # configured UA and a browser-shaped UA to tell an access-policy problem
    # from an invalid feed URL.  A browser UA is diagnostic only, not a default
    # production workaround.
    probe_feed("BLS Latest (configured UA)", "https://www.bls.gov/feed/bls_latest.rss", configured_ua)
    probe_feed("BLS Latest (browser UA)", "https://www.bls.gov/feed/bls_latest.rss", BROWSER_UA)
    probe_feed("BLS CPI (configured UA)", "https://www.bls.gov/feed/cpi.rss", configured_ua)

    # The first entry is the broken URL currently in the application.  The two
    # following URLs are candidates only: Reuters periodically retires feeds,
    # so do not add either unless this server verifies it.
    probe_feed(
        "Reuters current configured URL",
        "https://www.reutersagency.com/feed/?taxonomy=best-sectors&post_type=best&best-sectors=business-finance",
        configured_ua,
    )
    probe_feed("Reuters legacy business candidate", "https://feeds.reuters.com/reuters/businessNews", configured_ua)
    probe_feed("Reuters Arc business candidate", "https://www.reuters.com/arc/outboundfeeds/rss/category/business/", configured_ua)

    # rsshub.app is a public third-party instance, not an official Caixin API.
    # The mirror is tested only to establish reachability; a passing mirror is
    # not a production recommendation because public mirrors can disappear.
    probe_feed("Caixin current RSSHub", "https://rsshub.app/caixin/latest", configured_ua)
    probe_feed("Caixin RSSHub mirror (trial)", "https://rss.ovh/caixin/latest", configured_ua)
    probe_feed("The Block current configured URL", "https://www.theblock.co/rss.xml", configured_ua)

    # Official, low-maintenance additions for macro release awareness.  They
    # are intentionally tested but are not registered in RSS_FEEDS yet.
    probe_feed("NBS China latest releases", "https://www.stats.gov.cn/sj/zxfb/rss.xml", configured_ua)
    probe_feed("NBS China data interpretation", "https://www.stats.gov.cn/sj/sjjd/rss.xml", configured_ua)
    probe_feed("ECB press releases", "https://www.ecb.europa.eu/rss/press.html", configured_ua)
    probe_feed("ECB statistical releases", "https://www.ecb.europa.eu/rss/statpress.html", configured_ua)


def _probe_bls_api():
    print("\n=== Official BLS data API fallback (no RSS, no key required for v1) ===")

    def valid(payload):
        status = str(payload.get("status", ""))
        series = ((payload.get("Results") or {}).get("series") or [])
        points = len(series[0].get("data") or []) if series else 0
        return status == "REQUEST_SUCCEEDED" and points > 0, f"status={status or '-'}; observations={points}"

    probe_json(
        "BLS API CPI-U (CUUR0000SA0)",
        "https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0",
        headers={"User-Agent": _env("NEWS_USER_AGENT", DEFAULT_RSS_UA)},
        validator=valid,
    )


def _probe_akshare():
    print("\n=== AKShare China macro function probes ===")
    try:
        import akshare as ak
    except Exception as exc:
        _format_result("SKIP", "AKShare", f"unavailable: {type(exc).__name__}: {str(exc)[:120]}")
        return

    _format_result("INFO", "AKShare version", _version("akshare"))
    candidates = [
        ("AKShare current PMI (Eastmoney)", "macro_china_pmi", {}),
        ("AKShare PMI candidate (Jin10)", "macro_china_pmi_yearly", {}),
        ("AKShare Caixin PMI", "macro_china_cx_pmi_yearly", {}),
        ("AKShare M2 yearly", "macro_china_m2_yearly", {}),
    ]
    for label, function_name, kwargs in candidates:
        func = getattr(ak, function_name, None)
        if func is None:
            _format_result("FAIL", label, f"function {function_name} is absent")
            continue
        started = time.monotonic()
        try:
            frame = func(**kwargs)
            elapsed = time.monotonic() - started
            rows = 0 if frame is None else len(frame)
            columns = [] if frame is None else [str(item) for item in list(frame.columns)[:6]]
            if not rows:
                _format_result("FAIL", label, f"0 rows; cols={columns}; {elapsed:.1f}s")
            else:
                _format_result("PASS", label, f"rows={rows}; cols={columns}; {elapsed:.1f}s")
        except Exception as exc:
            _format_result("FAIL", label, f"{type(exc).__name__}: {str(exc)[:160]}")


def _probe_finnhub():
    print("\n=== Finnhub market/news API candidate (optional key) ===")
    token = _env("FINNHUB_API_KEY")
    if not token:
        _format_result("SKIP", "Finnhub general + crypto news", "set FINNHUB_API_KEY in .env to test; key is never printed")
        return

    def valid(payload):
        sample = payload[0] if isinstance(payload, list) and payload else {}
        required = {"headline", "datetime", "url"}
        present = sorted(required.intersection(sample))
        return len(present) == len(required), f"articles={len(payload) if isinstance(payload, list) else 0}; fields={present}"

    headers = {"X-Finnhub-Token": token, "User-Agent": _env("NEWS_USER_AGENT", DEFAULT_RSS_UA)}
    probe_json("Finnhub general news", "https://finnhub.io/api/v1/news?category=general", headers=headers, validator=valid)
    probe_json("Finnhub crypto news", "https://finnhub.io/api/v1/news?category=crypto", headers=headers, validator=valid)


def _probe_tushare():
    print("\n=== Tushare China macro candidate (optional token) ===")
    token = _env("TUSHARE_TOKEN")
    if not token:
        _format_result("SKIP", "Tushare cn_cpi", "set TUSHARE_TOKEN in .env to test; token is never printed")
        return
    try:
        response = requests.post(
            "https://api.tushare.pro",
            json={
                "api_name": "cn_cpi",
                "token": token,
                "params": {},
                "fields": "",
            },
            headers={"User-Agent": _env("NEWS_USER_AGENT", DEFAULT_RSS_UA)},
            timeout=TIMEOUT_SECONDS,
        )
        payload = response.json()
        result = payload.get("data") or {}
        items = result.get("items") or []
        code = payload.get("code")
        if response.ok and code == 0 and items:
            _format_result("PASS", "Tushare cn_cpi", f"HTTP {response.status_code}; rows={len(items)}")
        else:
            _format_result("FAIL", "Tushare cn_cpi", f"HTTP {response.status_code}; code={code}; msg={str(payload.get('msg') or '')[:120]}")
    except Exception as exc:
        _format_result("FAIL", "Tushare cn_cpi", f"{type(exc).__name__}: {str(exc)[:160]}")


def main():
    print("macro-dashboard candidate source probe")
    print(f"project_root: {ROOT}")
    print("mode: read-only (no database writes, no configuration changes, no AI calls)")
    print("status | candidate                          | result")
    print("-" * 96)
    _probe_rss_candidates()
    _probe_bls_api()
    _probe_akshare()
    _probe_finnhub()
    _probe_tushare()
    print("\nEND PROBE")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProbe interrupted by user", file=sys.stderr)
        raise SystemExit(130)
