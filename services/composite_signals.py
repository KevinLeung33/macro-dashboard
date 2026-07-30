"""Composite macro signals built from multiple market and macro clues."""
import pandas as pd

from db.repository import query_series
from services.market_data import query_market_series


def _prepare(source, series_id):
    if source == "market":
        df, _meta = query_market_series(series_id)
    else:
        df = query_series(source, series_id)
    if df.empty:
        return df
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values("date").dropna(subset=["value"])
    return out


def _snapshot(source, series_id, lookback=5):
    df = _prepare(source, series_id)
    if df.empty:
        return None
    latest = df.iloc[-1]
    prev = df.iloc[-lookback - 1] if len(df) > lookback else None
    change = None if prev is None else float(latest["value"] - prev["value"])
    pct = None
    if prev is not None and prev["value"] not in (None, 0):
        pct = float((latest["value"] / prev["value"] - 1) * 100)
    return {
        "date": latest["date"].strftime("%Y-%m-%d"),
        "value": float(latest["value"]),
        "change": change,
        "pct": pct,
    }


def _fmt_pct(value):
    return "—" if value is None else f"{value:+.2f}%"


def _fmt_bp(value):
    return "—" if value is None else f"{value * 100:+.0f}bp"


def _fmt_abs(value, unit=""):
    return "—" if value is None else f"{value:+.2f}{unit}"


def _fmt_money(value):
    if value is None:
        return "—"
    abs_value = abs(value)
    if abs_value >= 1e12:
        return f"{value / 1e12:+.2f}万亿美元"
    if abs_value >= 1e9:
        return f"{value / 1e9:+.2f}十亿美元"
    if abs_value >= 1e6:
        return f"{value / 1e6:+.2f}百万美元"
    return f"{value:+.0f}美元"


def _evidence(label, value, score, detail="", status=None):
    if status is None:
        if score > 0:
            status = "support"
        elif score < 0:
            status = "offset"
        else:
            status = "neutral"
    return {
        "label": label,
        "value": value,
        "score": score,
        "status": status,
        "detail": detail,
    }


def _missing(label):
    return _evidence(label, "缺数据", 0, status="missing")


def _level(score, max_score, has_data=True):
    if not has_data:
        return "unknown"
    ratio = score / max_score if max_score else 0
    if score >= 4 or ratio >= 0.67:
        return "red"
    if score >= 2 or ratio >= 0.34:
        return "yellow"
    if score <= -2:
        return "blue"
    return "green"


def _signal(name, category, direction, evidence, assets=None, watch_next=None):
    active = [e for e in evidence if e["status"] != "missing"]
    max_score = sum(max(1, abs(e["score"])) for e in active) or 1
    score = sum(e["score"] for e in active)
    supports = [e for e in evidence if e["status"] == "support"]
    offsets = [e for e in evidence if e["status"] == "offset"]
    missing = [e for e in evidence if e["status"] == "missing"]

    if supports:
        summary = "；".join(f"{e['label']} {e['value']}" for e in supports[:3])
    elif offsets:
        summary = "反向证据：" + "；".join(f"{e['label']} {e['value']}" for e in offsets[:2])
    elif missing and len(missing) == len(evidence):
        summary = "关键数据不足，暂无法判断"
    else:
        summary = "暂未形成明确信号"

    return {
        "name": name,
        "category": category,
        "direction": direction,
        "level": _level(score, max_score, has_data=bool(active)),
        "score": score,
        "max_score": max_score,
        "summary": summary,
        "evidence": evidence,
        "assets": assets or [],
        "watch_next": watch_next or [],
    }


def _dxy_up(lookback):
    snap = _snapshot("market", "DX-Y.NYB", lookback)
    if not snap:
        return _missing("DXY")
    pct = snap["pct"]
    score = 2 if pct is not None and pct >= 1.5 else (1 if pct is not None and pct >= 0.5 else (-1 if pct is not None and pct <= -0.5 else 0))
    return _evidence("DXY近5期变化", _fmt_pct(pct), score)


def _dxy_not_tightening(lookback):
    snap = _snapshot("market", "DX-Y.NYB", lookback)
    if not snap:
        return _missing("DXY")
    pct = snap["pct"]
    if pct is None:
        score = 0
    elif pct <= -0.5:
        score = 1
    elif pct >= 1.0:
        score = -1
    else:
        score = 0
    return _evidence("DXY未明显走强", _fmt_pct(pct), score)


def _rate_change(series_id, label, lookback, threshold_bp=15):
    snap = _snapshot("fred", series_id, lookback)
    if not snap:
        return _missing(label)
    change = snap["change"]
    score = 1 if change is not None and change * 100 >= threshold_bp else (-1 if change is not None and change * 100 <= -threshold_bp else 0)
    return _evidence(label, _fmt_bp(change), score)


def _point_change(source, series_id, label, lookback, threshold, unit=""):
    snap = _snapshot(source, series_id, lookback)
    if not snap:
        return _missing(label)
    change = snap["change"]
    score = 1 if change is not None and change >= threshold else (-1 if change is not None and change <= -threshold else 0)
    return _evidence(label, _fmt_abs(change, unit), score)


def _money_change(source, series_id, label, lookback, threshold):
    snap = _snapshot(source, series_id, lookback)
    if not snap:
        return _missing(label)
    change = snap["change"]
    score = 1 if change is not None and change >= threshold else (-1 if change is not None and change <= -threshold else 0)
    return _evidence(label, _fmt_money(change), score)


def _asset_pct(source, series_id, label, lookback, down_support=True, threshold=2.0):
    snap = _snapshot(source, series_id, lookback)
    if not snap:
        return _missing(label)
    pct = snap["pct"]
    if pct is None:
        score = 0
    elif down_support:
        score = 1 if pct <= -threshold else (-1 if pct >= threshold else 0)
    else:
        score = 1 if pct >= threshold else (-1 if pct <= -threshold else 0)
    return _evidence(label, _fmt_pct(pct), score)


def _level_threshold(source, series_id, label, high=None, low=None, support_high=True):
    snap = _snapshot(source, series_id, 1)
    if not snap:
        return _missing(label)
    value = snap["value"]
    score = 0
    if high is not None and value >= high:
        score = 1 if support_high else -1
    elif low is not None and value <= low:
        score = 1 if not support_high else -1
    return _evidence(label, f"{value:.2f}", score)


def _yoy_change(series_id, label, lookback=4):
    df = _prepare("fred", series_id)
    if df.empty or len(df) < 13 + lookback:
        return _missing(label)
    yoy = df.copy()
    yoy["value"] = yoy["value"].pct_change(12) * 100
    yoy = yoy.dropna(subset=["value"])
    if len(yoy) <= lookback:
        return _missing(label)
    latest = yoy.iloc[-1]["value"]
    prev = yoy.iloc[-lookback - 1]["value"]
    change = float(latest - prev)
    score = 1 if change <= -0.5 else (-1 if change >= 0.5 else 0)
    return _evidence(label, f"{latest:.2f}% YoY ({change:+.2f}pp)", score)


def _yoy_level(series_id, label, high=None, low=None):
    df = _prepare("fred", series_id)
    if df.empty or len(df) < 13:
        return _missing(label)
    yoy = df.copy()
    yoy["value"] = yoy["value"].pct_change(12) * 100
    yoy = yoy.dropna(subset=["value"])
    if yoy.empty:
        return _missing(label)
    latest = float(yoy.iloc[-1]["value"])
    score = 0
    if high is not None and latest >= high:
        score = 1
    elif low is not None and latest <= low:
        score = -1
    return _evidence(label, f"{latest:.2f}% YoY", score)


def compute_composite_signals(lookback=5):
    """Compute reusable rule-based signals for dashboard, reports and AI context."""
    signals = []

    signals.append(_signal(
        name="美元流动性收紧",
        category="liquidity",
        direction="tightening",
        evidence=[
            _dxy_up(lookback),
            _rate_change("DFII10", "10Y实际利率", lookback, threshold_bp=15),
            _rate_change("DGS10", "10Y美债", lookback, threshold_bp=20),
            _point_change("fred", "BAMLH0A0HYM2", "HY OAS", lookback, threshold=25, unit="bp"),
            _asset_pct("fred", "NASDAQCOM", "纳斯达克", lookback, down_support=True, threshold=2.0),
            _asset_pct("fred", "CBBTCUSD", "BTC", lookback, down_support=True, threshold=4.0),
        ],
        assets=["BTC", "NASDAQ", "Gold", "DXY"],
        watch_next=["DX-Y.NYB", "DFII10", "BAMLH0A0HYM2", "CBBTCUSD"],
    ))

    signals.append(_signal(
        name="Fed约束增强",
        category="fed",
        direction="restrictive",
        evidence=[
            _level_threshold("fred", "FEDFUNDS", "FF利率高位", high=4.5),
            _rate_change("DGS2", "2Y美债", lookback, threshold_bp=15),
            _level_threshold("fred", "T10Y3M", "10Y-3M倒挂", low=-0.25, support_high=False),
            _yoy_level("CPIAUCSL", "CPI同比偏高", high=3.0),
            _level_threshold("fred", "UNRATE", "失业率未显著恶化", high=4.5, support_high=False),
        ],
        assets=["DXY", "NASDAQ", "SP500", "BTC"],
        watch_next=["DGS2", "DFII10", "CPIAUCSL", "UNRATE"],
    ))

    signals.append(_signal(
        name="信用风险扩散",
        category="credit",
        direction="stress",
        evidence=[
            _point_change("fred", "BAMLH0A0HYM2", "HY OAS", lookback, threshold=30, unit="bp"),
            _point_change("fred", "BAMLC0A0CM", "IG OAS", lookback, threshold=15, unit="bp"),
            _level_threshold("fred", "NFCI", "NFCI收紧", high=0.0),
            _asset_pct("fred", "VIXCLS", "VIX", lookback, down_support=False, threshold=10.0),
            _asset_pct("fred", "SP500", "标普500", lookback, down_support=True, threshold=2.0),
        ],
        assets=["SP500", "NASDAQ", "HY Credit", "BTC"],
        watch_next=["BAMLH0A0HYM2", "BAMLC0A0CM", "NFCI", "VIXCLS"],
    ))

    signals.append(_signal(
        name="美国增长放缓",
        category="growth",
        direction="slowing",
        evidence=[
            _yoy_change("INDPRO", "工业产出动能", lookback=4),
            _point_change("fred", "ICSA", "初请失业金", lookback, threshold=20000, unit="人"),
            _level_threshold("fred", "UNRATE", "失业率偏高", high=4.2),
            _level_threshold("fred", "UMCSENT", "消费者信心偏弱", low=70, support_high=False),
            _level_threshold("fred", "T10Y3M", "收益率曲线倒挂", low=0, support_high=False),
        ],
        assets=["SP500", "NASDAQ", "DXY", "Treasuries"],
        watch_next=["INDPRO", "ICSA", "UNRATE", "UMCSENT"],
    ))

    signals.append(_signal(
        name="Crypto宏观压力",
        category="crypto",
        direction="macro_pressure",
        evidence=[
            _asset_pct("fred", "CBBTCUSD", "BTC", lookback, down_support=True, threshold=4.0),
            _dxy_up(lookback),
            _rate_change("DFII10", "10Y实际利率", lookback, threshold_bp=15),
            _asset_pct("fred", "NASDAQCOM", "纳斯达克", lookback, down_support=True, threshold=2.0),
            _asset_pct("fred", "VIXCLS", "VIX", lookback, down_support=False, threshold=10.0),
        ],
        assets=["BTC", "MSTR", "NASDAQ", "DXY"],
        watch_next=["CBBTCUSD", "DX-Y.NYB", "DFII10", "NASDAQCOM"],
    ))

    signals.append(_signal(
        name="Crypto内生流动性改善",
        category="crypto",
        direction="native_liquidity",
        evidence=[
            _money_change("crypto_liquidity", "STABLE_TOTAL_MCAP", "稳定币总市值", lookback, threshold=5e9),
            _money_change("crypto_liquidity", "STABLE_MAJOR_MCAP", "USDT+USDC市值", lookback, threshold=3e9),
            _asset_pct("crypto_liquidity", "ETHBTC", "ETH/BTC", lookback, down_support=False, threshold=2.0),
            _asset_pct("fred", "CBBTCUSD", "BTC", lookback, down_support=False, threshold=4.0),
            _dxy_not_tightening(lookback),
        ],
        assets=["BTC", "ETH", "MSTR", "NASDAQ"],
        watch_next=["STABLE_TOTAL_MCAP", "STABLE_MAJOR_MCAP", "ETHBTC", "CBBTCUSD", "DX-Y.NYB"],
    ))

    return sorted(signals, key=lambda x: (x["level"] == "green", -x["score"], x["name"]))
