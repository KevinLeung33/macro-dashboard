"""Small helpers for incremental time-series updates."""

from datetime import datetime, timedelta

from db.repository import get_last_fetch_date


def observation_start(source, series_id, overlap_days=1):
    """Return a YYYY-MM-DD start date with a small overlap for revised data."""
    last_date = get_last_fetch_date(source, series_id)
    if not last_date:
        return None
    try:
        dt = datetime.strptime(str(last_date)[:10], "%Y-%m-%d") - timedelta(days=overlap_days)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return last_date


def filter_new_records(source, series_id, records, overlap_days=1):
    """Keep only rows after the stored latest date, with overlap for idempotent replace."""
    start = observation_start(source, series_id, overlap_days=overlap_days)
    if not start:
        return records
    return [r for r in records if str(r.get("date", ""))[:10] >= start]
