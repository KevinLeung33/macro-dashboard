import time
import logging
import pandas as pd

from config.series_definitions import YFINANCE_SYMBOLS
from data.incremental import filter_new_records, observation_start
from db.repository import (
    upsert_time_series, upsert_series_meta, log_fetch,
)

logger = logging.getLogger(__name__)


def _download_history(yf, symbol, period, *, attempts=2, retry_delay=1.0):
    """Retry transient Yahoo empty responses without treating weekends as errors."""
    last_error = "Empty"
    for attempt in range(max(1, attempts)):
        try:
            history = yf.download(
                symbol, period=period, interval="1d", progress=False, auto_adjust=False
            )
            if history is not None and not history.empty:
                return history, ""
            last_error = "Empty"
        except Exception as exc:
            last_error = str(exc) or type(exc).__name__
        if attempt + 1 < max(1, attempts):
            time.sleep(max(0.0, retry_delay))
    return None, last_error

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
    for index, sym in enumerate(symbols):
        meta = YFINANCE_SYMBOLS[sym]
        name = meta.get("display_name", sym)
        try:
            if index > 0 and delay:
                time.sleep(delay)
            yf_period = "1mo" if incremental and observation_start("yfinance", sym, overlap_days=5) else period
            hist, error = _download_history(yf, sym, yf_period, retry_delay=delay)
            if hist is None:
                log_fetch("yfinance", sym, "error", error_message=error[:500])
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
                # A market holiday or an already-covered overlap window is a
                # successful polling result, not an outage of Yahoo Finance.
                log_fetch("yfinance", sym, "success", 0)
                ok += 1
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

    # Source health is determined by this run summary, rather than whichever
    # optional ticker happened to run last.  Individual symbol failures remain
    # in fetch_log for investigation without turning a healthy provider red.
    log_fetch(
        "yfinance",
        "__source__",
        "success" if ok else "error",
        ok,
        "" if ok else f"all {len(symbols)} symbols failed",
    )
    if failed > 0:
        print(f"  yfinance: {ok} ok, {failed} failed; prior data retained for failed symbols")
    else:
        print(f"  yfinance: {ok} symbols loaded")
