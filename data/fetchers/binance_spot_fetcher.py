"""Daily BTC/ETH spot candles from Binance public market data."""
from datetime import datetime, timedelta, timezone

import requests

from data.incremental import filter_new_records, observation_start
from db.repository import log_fetch, upsert_series_meta, upsert_time_series


BINANCE_SPOT = "https://api.binance.com/api/v3/klines"
HEADERS = {"User-Agent": "macro-dashboard/1.0"}
MAX_PAGES = 3

COINS = {
    "BTC-USD": {
        "symbol": "BTCUSDT",
        "display_name": "比特币",
    },
    "ETH-USD": {
        "symbol": "ETHUSDT",
        "display_name": "以太坊",
    },
}


def _date_from_ms(value):
    return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _start_time_ms(last_date=None):
    if last_date:
        try:
            start = datetime.strptime(str(last_date)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return int(start.timestamp() * 1000)
        except ValueError:
            pass
    start = datetime.now(timezone.utc) - timedelta(days=1825)
    return int(start.timestamp() * 1000)


def _fetch_records(symbol, start_time_ms):
    records = []
    next_start = start_time_ms
    for _ in range(MAX_PAGES):
        response = requests.get(
            BINANCE_SPOT,
            params={
                "symbol": symbol,
                "interval": "1d",
                "startTime": next_start,
                "limit": 1000,
            },
            headers=HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        rows = response.json()
        if not rows:
            break
        for row in rows:
            if len(row) >= 5 and row[0] is not None and row[4] is not None:
                records.append({"date": _date_from_ms(row[0]), "value": float(row[4])})
        if len(rows) < 1000:
            break
        next_start = int(rows[-1][0]) + 86_400_000
    return records


def fetch_and_store_binance_spot(incremental=True):
    for series_id, info in COINS.items():
        try:
            last_date = observation_start("binance_spot", series_id, overlap_days=3) if incremental else None
            records = _fetch_records(info["symbol"], _start_time_ms(last_date))
            if incremental:
                records = filter_new_records("binance_spot", series_id, records, overlap_days=3)
            if not records:
                log_fetch("binance_spot", series_id, "success", 0)
                continue

            source_url = f"{BINANCE_SPOT}?symbol={info['symbol']}&interval=1d"
            for record in records:
                record["source_url"] = source_url
            upsert_time_series("binance_spot", series_id, records)
            upsert_series_meta("binance_spot", series_id, {
                "display_name": info["display_name"],
                "unit": "USD",
                "frequency": "daily",
                "category": "crypto",
                "yaxis_label": "美元",
            })
            log_fetch("binance_spot", series_id, "success", len(records))
        except Exception as exc:
            log_fetch("binance_spot", series_id, "error", error_message=str(exc))
