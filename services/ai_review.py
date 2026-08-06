"""Review AI news directions against subsequent asset performance."""
import json

import pandas as pd

from db.repository import (
    query_ai_analysis_reviews,
    query_ai_analyses_for_review,
    query_series,
    upsert_ai_analysis_review,
)
from services.market_data import query_market_series


ASSET_SERIES = {
    "BTC": ("fred", "CBBTCUSD"), "ETH": ("binance_spot", "ETH-USD"),
    "NASDAQ": ("fred", "NASDAQCOM"), "SP500": ("fred", "SP500"),
    "DXY": ("market", "DX-Y.NYB"), "Gold": ("market", "GC=F"),
    "Oil": ("fred", "DCOILWTICO"), "MSTR": ("market", "MSTR"),
}
HORIZONS = {"return_1d": 1, "return_3d": 3, "return_7d": 7, "return_30d": 30}


def _split_assets(value):
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _series_window(source, series_id, start_date):
    if source == "market":
        frame, meta = query_market_series(series_id)
        source = meta.get("provider") or source
        series_id = meta.get("series_id") or series_id
    else:
        frame = query_series(source, series_id)
    if frame.empty:
        return frame, source, series_id
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values("date").dropna(subset=["value"])
    before = out[out["date"] <= pd.to_datetime(start_date)]
    start_pos = out.index.get_loc(before.index[-1]) if not before.empty else 0
    return out.iloc[start_pos:], source, series_id


def _return_at(rows, start_value, offset):
    if len(rows) <= offset or start_value in (None, 0):
        return None
    return float((float(rows.iloc[offset]["value"]) / start_value - 1) * 100)


def refresh_ai_analysis_reviews(limit=500):
    reviewed = 0
    skipped = 0
    for analysis in query_ai_analyses_for_review(limit=limit):
        try:
            direction = json.loads(analysis["direction"] or "{}")
        except (json.JSONDecodeError, TypeError):
            direction = {}
        start_date = str(analysis["published_at"] or analysis["created_at"])[:10]
        for asset in _split_assets(analysis["assets_impacted"]):
            predicted = str(direction.get(asset, "")).lower()
            if predicted not in ("bullish", "bearish") or asset not in ASSET_SERIES:
                skipped += 1
                continue
            source, series_id = ASSET_SERIES[asset]
            rows, actual_source, actual_series_id = _series_window(source, series_id, start_date)
            if rows.empty:
                skipped += 1
                continue
            start = rows.iloc[0]
            start_value = float(start["value"])
            upsert_ai_analysis_review(
                analysis["analysis_id"], asset, actual_source, actual_series_id, predicted,
                start["date"].strftime("%Y-%m-%d"), start_value,
                *[_return_at(rows, start_value, offset) for offset in HORIZONS.values()],
            )
            reviewed += 1
    return {"reviewed": reviewed, "skipped": skipped}


def ai_review_statistics(limit=2000, min_samples=2):
    rows = query_ai_analysis_reviews(limit=limit)
    if not rows:
        return {"rows": [], "summary": [], "sample_count": 0}
    frame = pd.DataFrame([dict(row) for row in rows])
    for column in HORIZONS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    summary = []
    group_columns = ["model", "prompt_version", "source", "event_type", "asset"]
    for keys, group in frame.groupby(group_columns, dropna=False):
        row = dict(zip(group_columns, keys))
        row["sample_count"] = int(len(group))
        for column, label in HORIZONS.items():
            values = group[column].dropna()
            row[f"valid_{label}"] = int(len(values))
            row[f"avg_{label}"] = float(values.mean()) if len(values) >= min_samples else None
            directional = group.loc[group[column].notna(), ["predicted_direction", column]]
            correct = [
                (value > 0 if direction == "bullish" else value < 0)
                for direction, value in directional.itertuples(index=False, name=None)
            ]
            row[f"accuracy_{label}"] = float(sum(correct) / len(correct) * 100) if len(correct) >= min_samples else None
        summary.append(row)
    summary.sort(key=lambda item: (item.get("valid_return_30d", 0), item.get("valid_return_7d", 0)), reverse=True)
    return {"rows": rows, "summary": summary, "sample_count": int(len(frame))}
