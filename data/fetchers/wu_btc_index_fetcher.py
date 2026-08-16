"""Daily BTC indicator adapter for Wu Blockchain's public Coinglass proxy."""
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from config.btc_onchain_indicators import BTC_INDEX_ENDPOINT, BTC_INDEX_SOURCE, BTC_INDICATORS
from db.repository import (
    get_last_success_fetch_at,
    log_fetch,
    upsert_series_meta,
    upsert_time_series,
)


HEADERS = {
    "User-Agent": "macro-dashboard/1.0 (+daily research fetch)",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://data.wublock123.com/",
}
MARKER = "__daily_batch__"


def _date_from_ms(value):
    return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _records(payload, meta):
    """Normalize the proxy's two observed response shapes into time/value rows."""
    if not payload:
        return []
    if isinstance(payload, list):
        raw_rows = payload
        field = meta.get("value_field")
        rows = []
        for row in raw_rows:
            if not isinstance(row, dict) or row.get("timestamp") is None:
                continue
            raw_value = row.get(field) if field else row.get("value")
            # The M2 endpoint has changed its field name between releases.
            if raw_value is None and meta.get("series_id") == "BTC_VS_M2":
                raw_value = row.get("global_m2_yoy_growth", row.get("us_m2_yoy_growth"))
            try:
                if raw_value is not None:
                    rows.append({"date": _date_from_ms(row["timestamp"]), "value": float(raw_value)})
            except (TypeError, ValueError, OverflowError):
                continue
        return list({row["date"]: row for row in rows}.values())

    times = payload.get("time_list") or []
    values = payload.get("data_list") or []
    price_list = payload.get("price_list") or []
    field = meta.get("value_field")
    rows = []
    for index, (timestamp, raw_value) in enumerate(zip(times, values)):
        if isinstance(raw_value, dict):
            if field:
                raw_value = raw_value.get(field)
            elif len(raw_value) == 1:
                raw_value = next(iter(raw_value.values()))
            elif "value" in raw_value:
                raw_value = raw_value.get("value")
            else:
                # Preserve the first numeric field for source variants whose
                # field name differs from the catalogue description.
                raw_value = next((v for v in raw_value.values() if isinstance(v, (int, float))), None)
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
            if pd.isna(value):
                continue
            rows.append({"date": _date_from_ms(timestamp), "value": value})
        except (TypeError, ValueError, OverflowError):
            continue
    return list({row["date"]: row for row in rows}.values())


def _store(series_id, records, meta, endpoint):
    if not records:
        raise ValueError("empty or unsupported metric payload")
    for row in records:
        row["source_url"] = endpoint
    upsert_time_series(BTC_INDEX_SOURCE, series_id, records)
    upsert_series_meta(BTC_INDEX_SOURCE, series_id, {
        "display_name": meta["display_name"],
        "unit": meta["unit"],
        "frequency": "daily",
        "category": meta["category"],
        "yaxis_label": meta["unit"],
    })
    return len(records)


def fetch_and_store_wu_btc_index(incremental=True, force=False, request_delay=0.5):
    """Fetch the selected daily indicators once per local calendar day.

    ``force=True`` is intended for a manual diagnostic or first import.  The
    source is deliberately optional: one failed metric must not stop the
    macro/OKX pipelines.
    """
    if incremental and not force and get_last_success_fetch_at(BTC_INDEX_SOURCE, MARKER):
        last = get_last_success_fetch_at(BTC_INDEX_SOURCE, MARKER)
        try:
            last_day = pd.Timestamp(last).date()
            if last_day == pd.Timestamp.now(tz="UTC").date():
                return {"skipped": True, "reason": "already fetched today"}
        except Exception:
            pass

    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        directory_response = session.get(BTC_INDEX_ENDPOINT, timeout=20)
        directory_response.raise_for_status()
        directory = directory_response.json()
        available = {item.get("id") for item in (directory.get("data", {}).get("metrics", []) or [])}
    except Exception as exc:
        log_fetch(BTC_INDEX_SOURCE, MARKER, "error", error_message=f"directory: {exc}")
        raise

    results = {}
    errors = []
    for index, (metric_id, meta) in enumerate(BTC_INDICATORS.items()):
        if metric_id not in available:
            message = "metric id not advertised by endpoint"
            log_fetch(BTC_INDEX_SOURCE, meta["series_id"], "error", error_message=message)
            results[meta["series_id"]] = 0
            errors.append(f"{metric_id}: {message}")
            continue
        endpoint = f"{BTC_INDEX_ENDPOINT}?id={metric_id}"
        try:
            response = session.get(endpoint, timeout=25)
            response.raise_for_status()
            payload = response.json().get("data")
            records = _records(payload, meta)
            if not records and meta.get("allow_empty"):
                # Some advertised historical metrics are currently returned as
                # JSON null by the public proxy.  This is an unavailable metric,
                # not a failure of the whole Wu source.
                log_fetch(BTC_INDEX_SOURCE, meta["series_id"], "skipped", error_message="endpoint returned no data")
                results[meta["series_id"]] = 0
                continue
            count = _store(meta["series_id"], records, meta, endpoint)
            results[meta["series_id"]] = count
            log_fetch(BTC_INDEX_SOURCE, meta["series_id"], "success", count)
        except Exception as exc:
            results[meta["series_id"]] = 0
            errors.append(f"{metric_id}: {exc}")
            log_fetch(BTC_INDEX_SOURCE, meta["series_id"], "error", error_message=str(exc))
        if index < len(BTC_INDICATORS) - 1:
            time.sleep(request_delay)

    log_fetch(
        BTC_INDEX_SOURCE,
        MARKER,
        "success" if any(results.values()) else "error",
        sum(results.values()),
        "; ".join(errors)[:2000],
    )
    print(f"  Wu BTC index: {sum(1 for value in results.values() if value)} metrics stored")
    if errors:
        print("  Wu BTC index warnings: " + " | ".join(errors[:5]))
    return results
