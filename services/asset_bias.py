"""Translate composite signals into traceable research biases by asset."""
from dataclasses import dataclass

from db.repository import query_series
from services.market_data import query_market_series


@dataclass(frozen=True)
class AssetDefinition:
    name: str
    page: str
    focus: str
    window: str
    market_id: str | None = None
    source: str | None = None
    series_id: str | None = None


ASSETS = (
    AssetDefinition("BTC", "pages/4_🪙_加密资产.py", "BTC 与流动性", "3M", market_id="BTC-USD"),
    AssetDefinition("MSTR", "pages/4_🪙_加密资产.py", "MSTR 与 BTC 风险", "3M", market_id="MSTR"),
    AssetDefinition("DXY", "pages/2_📊_市场数据.py", "美元指数 DXY", "3M", market_id="DX-Y.NYB"),
    AssetDefinition("SP500", "pages/2_📊_市场数据.py", "美股与 VIX", "3M", market_id="^GSPC"),
    AssetDefinition("NASDAQ", "pages/2_📊_市场数据.py", "美股与 VIX", "3M", market_id="^IXIC"),
    AssetDefinition("CNH", "pages/3_🌍_全球市场.py", "中国资产与人民币", "3M", source="yfinance", series_id="USDCNH=X"),
    AssetDefinition("HSTECH", "pages/3_🌍_全球市场.py", "中国资产与人民币", "3M", source="yfinance", series_id="^HSTECH"),
    AssetDefinition("Gold", "pages/2_📊_市场数据.py", "黄金与美元", "6M", market_id="GC=F"),
)


# +1 means that a stronger signal is supportive for the asset, -1 means adverse.
SIGNAL_EFFECTS = {
    "美元流动性收紧": {"BTC": -1, "MSTR": -1, "DXY": 1, "SP500": -1, "NASDAQ": -1, "CNH": -1, "HSTECH": -1, "Gold": -1},
    "Fed约束增强": {"BTC": -1, "MSTR": -1, "DXY": 1, "SP500": -1, "NASDAQ": -1, "CNH": -1, "HSTECH": -1, "Gold": -1},
    "信用风险扩散": {"BTC": -1, "MSTR": -1, "DXY": 1, "SP500": -1, "NASDAQ": -1, "CNH": -1, "HSTECH": -1, "Gold": 1},
    "美国增长放缓": {"BTC": -1, "MSTR": -1, "DXY": 1, "SP500": -1, "NASDAQ": -1, "CNH": -1, "HSTECH": -1, "Gold": 1},
    "Crypto宏观压力": {"BTC": -1, "MSTR": -1, "DXY": 1, "SP500": -1, "NASDAQ": -1, "CNH": -1, "HSTECH": -1, "Gold": 0},
    "Crypto内生流动性改善": {"BTC": 1, "MSTR": 1, "DXY": -1, "SP500": 1, "NASDAQ": 1, "CNH": 0, "HSTECH": 1, "Gold": 0},
}


def _asset_snapshot(asset, lookback=5):
    if asset.market_id:
        frame, meta = query_market_series(asset.market_id)
        provider = meta.get("provider")
    else:
        frame = query_series(asset.source, asset.series_id)
        provider = asset.source
    if frame.empty:
        return {"value": None, "change_pct": None, "date": None, "provider": provider}

    latest = frame.iloc[-1]
    previous = frame.iloc[-lookback - 1] if len(frame) > lookback else None
    change_pct = None
    if previous is not None and previous["value"] not in (None, 0):
        change_pct = (float(latest["value"]) / float(previous["value"]) - 1) * 100
    return {
        "value": float(latest["value"]),
        "change_pct": change_pct,
        "date": str(latest["date"]),
        "provider": provider,
    }


def _direction(score):
    if score >= 0.35:
        return "偏多", "green"
    if score <= -0.35:
        return "偏空", "red"
    return "中性/观察", "gray"


def build_asset_biases(signals, limit=8):
    """Return signal-derived research biases with their supporting evidence.

    This is a research aid, not a trading recommendation. A negative composite
    score naturally reverses the contribution of its associated signal.
    """
    rows = []
    for asset in ASSETS[:limit]:
        contributions = []
        for signal in signals:
            effect = SIGNAL_EFFECTS.get(signal.get("name"), {}).get(asset.name, 0)
            if not effect or not signal.get("max_score"):
                continue
            contribution = (float(signal.get("score", 0)) / float(signal["max_score"])) * effect
            if contribution:
                contributions.append({
                    "name": signal["name"],
                    "contribution": contribution,
                    "summary": signal.get("summary", ""),
                    "evidence": signal.get("evidence", []),
                })

        contributions.sort(key=lambda item: abs(item["contribution"]), reverse=True)
        score = sum(item["contribution"] for item in contributions)
        direction, level = _direction(score)
        active_evidence = sum(
            1 for item in contributions for evidence in item["evidence"]
            if evidence.get("status") != "missing"
        )
        confidence = min(0.9, 0.25 + 0.1 * len(contributions) + 0.025 * active_evidence)
        if not contributions:
            confidence = 0.0
        rows.append({
            "asset": asset.name,
            "direction": direction,
            "level": level,
            "score": score,
            "confidence": confidence,
            "drivers": contributions[:3],
            "snapshot": _asset_snapshot(asset),
            "page": asset.page,
            "focus": asset.focus,
            "window": asset.window,
        })
    return rows
