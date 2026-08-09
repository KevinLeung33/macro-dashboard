"""Daily Hong Kong index history from the verified AKShare/Sina endpoint."""
from datetime import date
import math
import time

import pandas as pd

from config.series_definitions import AKSHARE_HK_INDEX_SERIES
from data.incremental import filter_new_records
from db.repository import log_fetch, upsert_series_meta, upsert_time_series


SOURCE = "akshare_hk_index"
SOURCE_URL = "https://akshare.akfamily.xyz/data/index/index.html"


def _normalise_history(frame):
    """Convert the provider's OHLC table into strictly chronological close data."""
    required = {"date", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}; got={list(frame.columns)}")

    records = []
    for _, row in frame.iterrows():
        parsed_date = pd.to_datetime(row["date"], errors="coerce")
        try:
            value = float(row["close"])
        except (TypeError, ValueError):
            continue
        if pd.isna(parsed_date) or not math.isfinite(value) or value <= 0:
            continue
        records.append({
            "date": parsed_date.strftime("%Y-%m-%d"),
            "value": value,
            "source_url": SOURCE_URL,
        })

    # The endpoint normally returns chronological data, but do not rely on
    # upstream ordering when the repository's quality checks depend on it.
    by_date = {record["date"]: record for record in records}
    return [by_date[key] for key in sorted(by_date)]


def _fetch_history(ak, symbol, attempts=2, retry_delay=1.0):
    errors = []
    for attempt in range(max(1, attempts)):
        try:
            frame = ak.stock_hk_index_daily_sina(symbol=symbol)
            if frame is None or frame.empty:
                raise ValueError("Empty")
            return frame
        except Exception as exc:
            errors.append(str(exc) or type(exc).__name__)
            if attempt + 1 < max(1, attempts):
                time.sleep(max(0.0, retry_delay))
    raise RuntimeError("; ".join(errors[-2:]))


def _assert_history_is_usable(records, meta):
    minimum = max(1, int(meta.get("min_history_rows", 1)))
    if len(records) < minimum:
        raise ValueError(f"history too short: {len(records)} valid rows, expected at least {minimum}")

    latest_date = pd.to_datetime(records[-1]["date"], errors="coerce")
    if pd.isna(latest_date):
        raise ValueError("latest history date is invalid")
    age_days = (date.today() - latest_date.date()).days
    maximum_age = max(1, int(meta.get("max_stale_days", 14)))
    if age_days > maximum_age:
        raise ValueError(
            f"latest history is {age_days} days old ({records[-1]['date']}); allowed={maximum_age}"
        )


def fetch_and_store_hk_index_market(delay=0.5, incremental=True):
    """Store the exact HSTECH index while preserving last good data on failure."""
    try:
        import akshare as ak
    except ImportError:
        print("  AKShare not installed. Run: pip install akshare")
        return

    ok, failed = 0, 0
    for index, (series_id, meta) in enumerate(AKSHARE_HK_INDEX_SERIES.items()):
        if index and delay:
            time.sleep(delay)
        name = meta.get("display_name", series_id)
        try:
            frame = _fetch_history(ak, meta.get("symbol", series_id))
            raw_records = _normalise_history(frame)
            _assert_history_is_usable(raw_records, meta)
            records = (
                filter_new_records(SOURCE, series_id, raw_records, overlap_days=5)
                if incremental else raw_records
            )
            if not records:
                log_fetch(SOURCE, series_id, "success", 0)
                print(f"  {name}: no new records")
                ok += 1
                continue

            write_result = upsert_time_series(
                SOURCE,
                series_id,
                records,
                reset_existing_quality_issues=not incremental,
            )
            accepted = int(write_result.get("accepted", 0))
            if not accepted:
                raise ValueError(f"all {len(records)} records rejected by validation")
            upsert_series_meta(SOURCE, series_id, {
                "display_name": name,
                "unit": meta.get("unit", ""),
                "frequency": meta.get("frequency", "daily"),
                "category": meta.get("category", ""),
                "yaxis_label": meta.get("yaxis_label", ""),
            })
            log_fetch(SOURCE, series_id, "success", accepted)
            print(f"  {name}: {accepted} recs, latest={raw_records[-1]}")
            ok += 1
        except Exception as exc:
            log_fetch(SOURCE, series_id, "error", error_message=str(exc))
            print(f"  {name}: FAILED - {exc}")
            failed += 1

    if failed:
        print(f"  Hong Kong index: {ok} ok, {failed} failed; prior data retained for failures")
    else:
        print(f"  Hong Kong index: {ok} series loaded")
