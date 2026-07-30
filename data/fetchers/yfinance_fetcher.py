import time
import logging
import requests

from config.series_definitions import YFINANCE_SYMBOLS
from data.incremental import filter_new_records, observation_start
from db.repository import (
    upsert_time_series, upsert_series_meta, log_fetch,
)

COINGECKO_URL = "https://api.coingecko.com/api/v3"
HEADERS = {"User-Agent": "Mozilla/5.0"}
logger = logging.getLogger(__name__)

COINS = {
    "BTC-USD": {"id": "bitcoin", "display_name": "比特币", "category": "crypto"},
    "ETH-USD": {"id": "ethereum", "display_name": "以太坊", "category": "crypto"},
}


def fetch_and_store_crypto(delay=2.0, incremental=True):
    for sym, info in COINS.items():
        try:
            url = f"{COINGECKO_URL}/coins/{info['id']}/market_chart"
            days = "30" if incremental and observation_start("yfinance", sym, overlap_days=3) else "1825"
            params = {"vs_currency": "usd", "days": days}
            resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
            if resp.status_code == 429:
                print(f"  {info['display_name']}: rate limited, retrying in 60s...")
                time.sleep(60)
                resp = requests.get(url, params=params, headers=HEADERS, timeout=30)

            resp.raise_for_status()
            data = resp.json()
            prices = data.get("prices", [])

            from datetime import datetime
            records = [
                {"date": datetime.utcfromtimestamp(p[0] / 1000).strftime("%Y-%m-%d"),
                 "value": float(p[1])}
                for p in prices
            ]
            records = filter_new_records("yfinance", sym, records, overlap_days=3) if incremental else records

            upsert_time_series("yfinance", sym, records)
            upsert_series_meta("yfinance", sym, {
                "display_name": info["display_name"],
                "unit": "USD",
                "frequency": "daily",
                "category": info["category"],
                "yaxis_label": "美元",
            })
            log_fetch("yfinance", sym, "success", len(records))
            print(f"  {info['display_name']}: {len(records)} records")

            if sym != list(COINS.keys())[-1]:
                time.sleep(delay)
        except Exception as e:
            log_fetch("yfinance", sym, "error", error_message=str(e))
            print(f"  {info['display_name']}: FAILED - {e}")


def fetch_and_store_yfinance_market(period="5y", delay=1.0, incremental=True):
    try:
        import yfinance as yf
    except ImportError:
        print("  yfinance not installed. Run: pip install yfinance")
        return

    symbols = list(YFINANCE_SYMBOLS.keys())
    ok, failed = 0, 0
    for sym in symbols:
        meta = YFINANCE_SYMBOLS[sym]
        name = meta.get("display_name", sym)
        try:
            yf_period = "1mo" if incremental and observation_start("yfinance", sym, overlap_days=5) else period
            hist = yf.download(sym, period=yf_period, interval="1d", progress=False, auto_adjust=False)
            if hist is None or hist.empty:
                log_fetch("yfinance", sym, "error", error_message="Empty")
                failed += 1
                continue

            close = hist["Close"].dropna()
            records = [
                {"date": idx.strftime("%Y-%m-%d"), "value": float(val)}
                for idx, val in close.items()
            ]
            records = filter_new_records("yfinance", sym, records, overlap_days=5) if incremental else records
            if not records:
                log_fetch("yfinance", sym, "error", error_message="No close data")
                failed += 1
                continue

            upsert_time_series("yfinance", sym, records)
            upsert_series_meta("yfinance", sym, {
                "display_name": name, "unit": meta.get("unit", ""),
                "frequency": "daily", "category": meta.get("category", ""),
                "yaxis_label": meta.get("yaxis_label", ""),
            })
            log_fetch("yfinance", sym, "success", len(records))
            ok += 1
        except Exception as exc:
            log_fetch("yfinance", sym, "error", error_message=str(exc))
            logger.warning("yfinance fetch failed for %s: %s", sym, exc)
            failed += 1

    if failed > 0:
        print(f"  yfinance: {ok} ok, {failed} failed (network/rate-limit, server deployment will resolve)")
    else:
        print(f"  yfinance: {ok} symbols loaded")
