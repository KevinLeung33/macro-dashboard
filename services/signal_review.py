"""Persist composite signal snapshots and review forward asset returns."""
import pandas as pd

from db.repository import (
    query_series,
    upsert_composite_signal_review,
    upsert_composite_signal_snapshot,
)
from services.composite_signals import compute_composite_signals
from services.market_data import query_market_series
from services.time_utils import app_now


ASSET_SERIES = {
    "BTC": ("fred", "CBBTCUSD"),
    "ETH": ("binance_spot", "ETH-USD"),
    "NASDAQ": ("fred", "NASDAQCOM"),
    "SP500": ("fred", "SP500"),
    "DXY": ("market", "DX-Y.NYB"),
    "Gold": ("market", "GC=F"),
    "Oil": ("fred", "DCOILWTICO"),
    "MSTR": ("market", "MSTR"),
}


def _clean_asset(asset):
    aliases = {
        "Treasuries": None,
        "HY Credit": None,
    }
    return aliases.get(asset, asset)


def _series_window(source, series_id, signal_date):
    if source == "market":
        df, meta = query_market_series(series_id)
        source = meta.get("provider") or source
        series_id = meta.get("series_id") or series_id
    else:
        df = query_series(source, series_id)
    if df.empty:
        return df, source, series_id
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values("date").dropna(subset=["value"])
    cutoff = pd.to_datetime(signal_date)
    before = out[out["date"] <= cutoff]
    if before.empty:
        return out[out["date"] >= cutoff], source, series_id
    start_idx = before.index[-1]
    start_pos = out.index.get_loc(start_idx)
    return out.iloc[start_pos:], source, series_id


def _return_at(rows, start_value, offset):
    if rows is None or len(rows) <= offset or start_value in (None, 0):
        return None
    value = rows.iloc[offset]["value"]
    return float((value / start_value - 1) * 100)


def _review_asset(snapshot_id, asset, signal_date):
    mapped = _clean_asset(asset)
    if not mapped or mapped not in ASSET_SERIES:
        return False

    source, series_id = ASSET_SERIES[mapped]
    rows, actual_source, actual_series_id = _series_window(source, series_id, signal_date)
    if rows.empty:
        return False

    start = rows.iloc[0]
    start_value = float(start["value"])
    upsert_composite_signal_review(
        snapshot_id=snapshot_id,
        asset=mapped,
        source=actual_source,
        series_id=actual_series_id,
        start_date=start["date"].strftime("%Y-%m-%d"),
        start_value=start_value,
        return_1d=_return_at(rows, start_value, 1),
        return_3d=_return_at(rows, start_value, 3),
        return_7d=_return_at(rows, start_value, 7),
    )
    return True


def save_signal_snapshots(signal_date=None, min_score=1):
    """Save current composite signals and forward-return review rows."""
    signal_date = signal_date or app_now().strftime("%Y-%m-%d")
    signals = compute_composite_signals()
    saved = 0
    reviewed = 0

    for signal in signals:
        if signal.get("level") == "unknown":
            continue
        if signal.get("score", 0) < min_score and signal.get("level") == "green":
            continue
        snapshot_id = upsert_composite_signal_snapshot(signal_date, signal)
        if not snapshot_id:
            continue
        saved += 1
        for asset in signal.get("assets", []):
            if _review_asset(snapshot_id, asset, signal_date):
                reviewed += 1

    return {"signal_date": signal_date, "saved": saved, "reviewed": reviewed}


def refresh_signal_reviews():
    """Recompute today's snapshots. Historical rows update when price data extends."""
    return save_signal_snapshots()
