"""Presentation helpers for daily Wu BTC indicator snapshots."""
import math

import pandas as pd

from config.btc_onchain_indicators import BTC_INDEX_SOURCE, BTC_INDICATORS, indicator_meta_by_series
from db.repository import query_series


def _finite(value):
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _state(value, meta, percentile):
    thresholds = meta.get("thresholds") or []
    if thresholds and _finite(value):
        if meta.get("series_id") == "AHR999":
            number = float(value)
            if number < 0.45:
                return "深度低估/历史极端积累"
            if number < 1.0:
                return "低于1：定投参考区间"
            if number < 2.0:
                return "中性估值区间"
            return "偏高估值/降低追涨意愿"
        matched = thresholds[0][1]
        for point, label in thresholds:
            if float(value) >= point:
                matched = label
        if meta.get("threshold_direction") == "lower_is_better" and float(value) <= thresholds[0][0]:
            matched = thresholds[0][1]
        return matched
    if percentile is None:
        return "暂无分位"
    if percentile >= 90:
        return "历史高位"
    if percentile >= 75:
        return "偏高"
    if percentile <= 10:
        return "历史低位"
    if percentile <= 25:
        return "偏低"
    return "历史中位"


def indicator_snapshots():
    """Return current values, historical percentiles and interpretation text."""
    rows = []
    for metric_id, meta in BTC_INDICATORS.items():
        series_id = meta["series_id"]
        frame = query_series(BTC_INDEX_SOURCE, series_id)
        if frame.empty:
            rows.append({"metric_id": metric_id, **meta, "value": None, "date": None, "percentile": None, "state": "暂无数据"})
            continue
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame = frame.dropna(subset=["value"]).sort_values("date")
        if frame.empty:
            continue
        value = float(frame.iloc[-1]["value"])
        percentile = float((frame["value"] <= value).mean() * 100)
        previous = float(frame.iloc[-2]["value"]) if len(frame) > 1 else None
        rows.append({
            "metric_id": metric_id,
            **meta,
            "value": value,
            "previous": previous,
            "change_1d": value - previous if previous is not None else None,
            "date": str(frame.iloc[-1]["date"]),
            "percentile": percentile,
            "sample_count": len(frame),
            "state": _state(value, meta, percentile),
        })
    return rows


def btc_environment_summary(rows=None):
    rows = rows if rows is not None else indicator_snapshots()
    by_category = {}
    for row in rows:
        if row.get("value") is not None:
            by_category.setdefault(row.get("category"), []).append(row)
    return {
        "周期估值": "；".join(f"{r['display_name']}：{r['state']}" for r in by_category.get("cycle_valuation", [])),
        "持有者行为": "；".join(f"{r['display_name']}：{r['state']}" for r in by_category.get("holder_behavior", [])),
        "流动性": "；".join(f"{r['display_name']}：{r['state']}" for r in by_category.get("macro_liquidity", [])),
        "情绪": "；".join(f"{r['display_name']}：{r['state']}" for r in by_category.get("sentiment", [])),
        "衍生品风险": "；".join(f"{r['display_name']}：{r['state']}" for r in by_category.get("derivatives_risk", [])),
    }
