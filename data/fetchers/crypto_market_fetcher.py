"""Crypto derivatives and configurable fund-flow adapters."""
import csv
import io
import os
import re
from datetime import datetime, timezone

import requests

from db.repository import log_fetch, upsert_series_meta, upsert_time_series
from data.incremental import filter_new_records, observation_start


BINANCE_FAPI = "https://fapi.binance.com"
HEADERS = {"User-Agent": "macro-dashboard/1.0"}

SERIES_META = {
    "BTC_FUNDING_RATE": {
        "display_name": "BTC资金费率",
        "unit": "%",
        "frequency": "daily",
        "category": "crypto_derivatives",
        "yaxis_label": "%",
    },
    "BTC_OPEN_INTEREST": {
        "display_name": "BTC合约持仓量",
        "unit": "USD/BTC",
        "frequency": "daily",
        "category": "crypto_derivatives",
        "yaxis_label": "USD/BTC",
    },
    "BTC_ETF_NETFLOW": {
        "display_name": "BTC ETF净流入",
        "unit": "USD",
        "frequency": "daily",
        "category": "crypto_flows",
        "yaxis_label": "美元",
    },
    "BTC_EXCHANGE_NETFLOW": {
        "display_name": "BTC交易所净流入",
        "unit": "BTC",
        "frequency": "daily",
        "category": "crypto_flows",
        "yaxis_label": "BTC",
    },
}


def _get_json(url, params=None):
    response = requests.get(url, params=params, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def _date_from_ms(value):
    return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _store(series_id, records, source_url, incremental=True):
    if incremental and observation_start("crypto_market", series_id, overlap_days=3):
        records = filter_new_records("crypto_market", series_id, records, overlap_days=3)
    if not records:
        log_fetch("crypto_market", series_id, "success", 0)
        return 0
    for record in records:
        record["source_url"] = source_url
    upsert_time_series("crypto_market", series_id, records)
    upsert_series_meta("crypto_market", series_id, SERIES_META[series_id])
    log_fetch("crypto_market", series_id, "success", len(records))
    return len(records)


def fetch_funding_rate(symbol="BTCUSDT", incremental=True):
    url = f"{BINANCE_FAPI}/fapi/v1/fundingRate"
    data = _get_json(url, {"symbol": symbol, "limit": 1000})
    records = [
        {"date": _date_from_ms(row["fundingTime"]), "value": float(row["fundingRate"]) * 100}
        for row in data
        if row.get("fundingTime") is not None and row.get("fundingRate") is not None
    ]
    by_date = {row["date"]: row for row in records}
    return _store("BTC_FUNDING_RATE", list(by_date.values()), url, incremental=incremental)


def fetch_open_interest(symbol="BTCUSDT", incremental=True):
    url = f"{BINANCE_FAPI}/futures/data/openInterestHist"
    data = _get_json(url, {"symbol": symbol, "period": "1d", "limit": 500})
    records = []
    for row in data:
        value = row.get("sumOpenInterestValue") or row.get("sumOpenInterest")
        timestamp = row.get("timestamp")
        if value is not None and timestamp is not None:
            records.append({"date": _date_from_ms(timestamp), "value": float(value)})
    return _store("BTC_OPEN_INTEREST", records, url, incremental=incremental)


def _parse_flow_rows(payload, content_type=""):
    if "json" in content_type:
        data = payload if isinstance(payload, list) else payload.get("data", payload.get("rows", payload.get("result", [])))
        if isinstance(data, dict):
            data = [data]
        return data or []
    return list(csv.DictReader(io.StringIO(payload)))


def _parse_number(value):
    text = str(value or "").strip().replace(",", "").replace("$", "")
    text = re.sub(r"[^0-9eE+\-.]", "", text)
    return float(text) if text and text not in (".", "-") else None


def fetch_configured_flow(series_id, env_name, unit, incremental=True):
    url = os.getenv(env_name, "").strip()
    if not url:
        log_fetch("crypto_flows", series_id, "skipped", error_message=f"{env_name} is not configured")
        return 0

    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    payload = response.json() if "json" in content_type else response.text
    rows = _parse_flow_rows(payload, content_type)
    records = []
    for row in rows:
        lowered = {str(key).lower(): value for key, value in row.items()}
        date_value = next((lowered.get(key) for key in ("date", "day", "timestamp", "time") if lowered.get(key)), None)
        flow_value = next((lowered.get(key) for key in ("net_flow", "netflow", "flow", "value", "amount") if lowered.get(key) is not None), None)
        if not date_value or flow_value is None:
            continue
        try:
            date_text = _date_from_ms(date_value) if str(date_value).isdigit() and len(str(date_value)) >= 12 else str(date_value)[:10]
            value = _parse_number(flow_value)
            if value is not None:
                records.append({"date": date_text, "value": value})
        except (TypeError, ValueError, OverflowError):
            continue

    if incremental and observation_start("crypto_flows", series_id, overlap_days=3):
        records = filter_new_records("crypto_flows", series_id, records, overlap_days=3)
    if not records:
        log_fetch("crypto_flows", series_id, "error", error_message="No usable rows from configured flow URL")
        return 0
    for record in records:
        record["source_url"] = url
    upsert_time_series("crypto_flows", series_id, records)
    meta = dict(SERIES_META[series_id])
    meta["unit"] = unit
    upsert_series_meta("crypto_flows", series_id, meta)
    log_fetch("crypto_flows", series_id, "success", len(records))
    return len(records)


def fetch_and_store_crypto_market(incremental=True):
    results = {}
    for series_id, fetcher in (
        ("BTC_FUNDING_RATE", fetch_funding_rate),
        ("BTC_OPEN_INTEREST", fetch_open_interest),
    ):
        try:
            results[series_id] = fetcher(incremental=incremental)
        except Exception as exc:
            log_fetch("crypto_market", series_id, "error", error_message=str(exc))
            results[series_id] = 0

    try:
        results["BTC_ETF_NETFLOW"] = fetch_configured_flow(
            "BTC_ETF_NETFLOW", "BTC_ETF_FLOWS_URL", "USD", incremental=incremental
        )
    except Exception as exc:
        log_fetch("crypto_flows", "BTC_ETF_NETFLOW", "error", error_message=str(exc))
        results["BTC_ETF_NETFLOW"] = 0
    try:
        results["BTC_EXCHANGE_NETFLOW"] = fetch_configured_flow(
            "BTC_EXCHANGE_NETFLOW", "BTC_EXCHANGE_NETFLOW_URL", "BTC", incremental=incremental
        )
    except Exception as exc:
        log_fetch("crypto_flows", "BTC_EXCHANGE_NETFLOW", "error", error_message=str(exc))
        results["BTC_EXCHANGE_NETFLOW"] = 0
    return results
