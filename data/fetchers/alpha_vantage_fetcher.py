"""Alpha Vantage market data fetcher for key US equities."""
import os
import time

import requests

from db.repository import log_fetch, upsert_series_meta, upsert_time_series


ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"


ALPHA_VANTAGE_SYMBOLS = {
    "MSTR": {"symbol": "MSTR", "display_name": "🇺🇸 Strategy(MSTR)", "category": "crypto_equity", "yaxis_label": "美元"},
    "NVDA": {"symbol": "NVDA", "display_name": "🇺🇸 英伟达", "category": "semiconductors", "yaxis_label": "美元"},
    "MU": {"symbol": "MU", "display_name": "🇺🇸 美光科技", "category": "semiconductors", "yaxis_label": "美元"},
}


def _parse_daily_series(data):
    series = data.get("Time Series (Daily)") or data.get("Time Series (Daily Adjusted)") or {}
    records = []
    for date, row in series.items():
        close = row.get("4. close") or row.get("5. adjusted close")
        if close is None:
            continue
        records.append({"date": date, "value": float(close)})
    return sorted(records, key=lambda x: x["date"])


def fetch_and_store_alpha_vantage_market(delay=12.5, incremental=True):
    from data.incremental import filter_new_records, observation_start

    key = os.getenv("ALPHA_VANTAGE_KEY")
    if not key or "your_" in key:
        print("  Alpha Vantage market skipped: no API key")
        return

    ok, failed = 0, 0
    for logical_id, meta in ALPHA_VANTAGE_SYMBOLS.items():
        name = meta.get("display_name", logical_id)
        try:
            outputsize = "compact" if incremental and observation_start("alpha_vantage", logical_id, overlap_days=5) else "full"
            resp = requests.get(
                ALPHA_VANTAGE_URL,
                params={
                    "function": "TIME_SERIES_DAILY",
                    "symbol": meta["symbol"],
                    "outputsize": outputsize,
                    "apikey": key,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if "Note" in data or "Information" in data:
                msg = data.get("Note") or data.get("Information")
                log_fetch("alpha_vantage", logical_id, "error", error_message=msg)
                failed += 1
                print(f"  {name}: RATE/LIMIT - {msg[:80]}")
                continue

            records = _parse_daily_series(data)
            records = filter_new_records("alpha_vantage", logical_id, records, overlap_days=5) if incremental else records
            if not records:
                msg = data.get("Error Message", "Empty")
                log_fetch("alpha_vantage", logical_id, "error", error_message=msg)
                failed += 1
                continue

            upsert_time_series("alpha_vantage", logical_id, records)
            upsert_series_meta("alpha_vantage", logical_id, {
                "display_name": name,
                "unit": meta.get("unit", ""),
                "frequency": "daily",
                "category": meta.get("category", ""),
                "yaxis_label": meta.get("yaxis_label", ""),
            })
            log_fetch("alpha_vantage", logical_id, "success", len(records))
            ok += 1
            time.sleep(delay)
        except Exception as e:
            log_fetch("alpha_vantage", logical_id, "error", error_message=str(e))
            failed += 1
            print(f"  {name}: FAILED - {e}")

    if failed:
        print(f"  alpha_vantage market: {ok} ok, {failed} failed")
    else:
        print(f"  alpha_vantage market: {ok} symbols loaded")
