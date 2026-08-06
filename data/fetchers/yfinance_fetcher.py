import time
import logging
import pandas as pd

from config.series_definitions import YFINANCE_SYMBOLS
from data.incremental import filter_new_records, observation_start
from db.repository import (
    upsert_time_series, upsert_series_meta, log_fetch,
)

logger = logging.getLogger(__name__)

def fetch_and_store_crypto(delay=2.0, incremental=True):
    """Backward-compatible entry point; crypto spot now uses Binance."""
    from data.fetchers.binance_spot_fetcher import fetch_and_store_binance_spot

    return fetch_and_store_binance_spot(incremental=incremental)


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

            close = hist["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close = close.dropna()
            records = []
            for idx, val in close.items():
                parsed_date = pd.to_datetime(idx, errors="coerce")
                if pd.isna(parsed_date):
                    continue
                records.append({
                    "date": parsed_date.strftime("%Y-%m-%d"),
                    "value": float(val),
                })
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
