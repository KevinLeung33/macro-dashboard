import requests
import pandas as pd
from datetime import datetime

from config.settings import FRED_API_KEY, FRED_BASE_URL
from config.series_definitions import FRED_SERIES
from data.incremental import observation_start
from db.repository import (
    upsert_time_series, upsert_series_meta, log_fetch,
)


def _fetch_fred_observations(series_id, api_key=FRED_API_KEY, incremental=True):
    url = f"{FRED_BASE_URL}/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "asc",
        "limit": 100000,
    }
    if incremental:
        start = observation_start("fred", series_id, overlap_days=7)
        if start:
            params["observation_start"] = start

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    records = []
    for obs in data.get("observations", []):
        val = obs.get("value", ".")
        if val == ".":
            continue
        records.append({"date": obs["date"], "value": float(val)})

    return records


def fetch_and_store_fred(series_ids=None, incremental=True):
    if series_ids is None:
        series_ids = list(FRED_SERIES.keys())

    for sid in series_ids:
        meta = FRED_SERIES.get(sid, {})
        try:
            records = _fetch_fred_observations(sid, incremental=incremental)
            if records:
                # Apply transform if defined
                tfn = meta.get("transform")
                if tfn:
                    for r in records:
                        r["value"] = tfn(r["value"])

                # Validate value ranges
                vrange = meta.get("valid_range")
                if vrange:
                    lo, hi = vrange
                    records = [r for r in records if lo <= r["value"] <= hi]

                    # Also check: if last point is an outlier vs previous 5 points
                    if len(records) >= 6:
                        prev_avg = sum(r["value"] for r in records[-6:-1]) / 5
                        last_val = records[-1]["value"]
                        if prev_avg != 0 and abs(last_val / prev_avg - 1) > 0.5:
                            print(f"  ⚠ {sid}: last point {last_val} far from 5-pt avg {prev_avg}, dropping")
                            records.pop()

                if records:
                    upsert_time_series("fred", sid, records)
                    upsert_series_meta("fred", sid, meta)
                    log_fetch("fred", sid, "success", len(records))
            else:
                log_fetch("fred", sid, "success", 0)
        except Exception as e:
            log_fetch("fred", sid, "error", error_message=str(e))
