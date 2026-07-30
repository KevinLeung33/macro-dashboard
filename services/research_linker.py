"""Auto-link user research text to dashboard assets, indicators and news topics."""
import re

import pandas as pd

from db.repository import query_analyzed_news, query_series_snapshot


ASSET_KEYWORDS = {
    "BTC": ["btc", "bitcoin", "比特币"],
    "ETH": ["eth", "ethereum", "以太坊"],
    "MSTR": ["mstr", "strategy"],
    "NASDAQ": ["nasdaq", "纳斯达克", "科技股"],
    "SP500": ["sp500", "s&p", "标普"],
    "DXY": ["dxy", "美元指数", "美元"],
    "Gold": ["gold", "黄金"],
    "Oil": ["oil", "原油", "wti"],
    "CNH": ["cnh", "人民币", "usdcnh"],
}

INDICATOR_KEYWORDS = {
    ("market", "DX-Y.NYB", "DXY"): ["dxy", "美元指数", "美元走强", "美元流动性"],
    ("fred", "DFII10", "10Y实际利率"): ["实际利率", "tips", "dfii10"],
    ("fred", "DGS10", "10Y美债"): ["10y", "十年期", "美债"],
    ("fred", "BAMLH0A0HYM2", "HY OAS"): ["信用利差", "hy", "高收益"],
    ("fred", "NFCI", "NFCI"): ["金融条件", "nfci"],
    ("fred", "CBBTCUSD", "BTC"): ["btc", "bitcoin", "比特币"],
    ("crypto_liquidity", "STABLE_TOTAL_MCAP", "稳定币总市值"): ["稳定币", "stablecoin", "usdt", "usdc"],
    ("crypto_liquidity", "STABLE_MAJOR_MCAP", "USDT+USDC市值"): ["usdt", "usdc", "稳定币"],
    ("crypto_liquidity", "ETHBTC", "ETH/BTC"): ["ethbtc", "eth/btc", "风险偏好"],
    ("fred", "VIXCLS", "VIX"): ["vix", "波动率", "恐慌"],
    ("fred", "NASDAQCOM", "纳斯达克"): ["nasdaq", "纳斯达克", "科技股"],
    ("akshare", "CN_PMI", "中国PMI"): ["中国", "pmi", "制造业"],
}

TOPIC_KEYWORDS = {
    "crypto": ["btc", "bitcoin", "eth", "crypto", "稳定币", "加密", "mstr"],
    "liquidity": ["流动性", "美元", "stablecoin", "rrp", "tga", "准备金"],
    "fed_policy": ["fed", "美联储", "降息", "加息", "利率"],
    "credit": ["信用", "利差", "违约", "高收益"],
    "china_macro": ["中国", "pmi", "社融", "lpr", "人民币"],
    "growth": ["增长", "就业", "衰退", "pmi"],
    "energy": ["oil", "原油", "能源", "wti"],
}


def _tokens(text):
    return str(text or "").lower()


def _split_existing(value):
    if not value:
        return []
    return [x.strip() for x in re.split(r"[,，\s]+", str(value)) if x.strip()]


def _merge(existing, inferred):
    out = []
    for item in list(existing) + list(inferred):
        if item and item not in out:
            out.append(item)
    return ",".join(out)


def infer_research_links(title="", thesis="", assets="", indicators="", news_topics="", extra_text=""):
    text = _tokens(" ".join([title or "", thesis or "", extra_text or ""]))

    inferred_assets = []
    for asset, keywords in ASSET_KEYWORDS.items():
        if any(k.lower() in text for k in keywords):
            inferred_assets.append(asset)

    inferred_indicators = []
    for (_source, series_id, _label), keywords in INDICATOR_KEYWORDS.items():
        if any(k.lower() in text for k in keywords):
            inferred_indicators.append(series_id)

    inferred_topics = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(k.lower() in text for k in keywords):
            inferred_topics.append(topic)

    return {
        "assets": _merge(_split_existing(assets), inferred_assets),
        "indicators": _merge(_split_existing(indicators), inferred_indicators),
        "news_topics": _merge(_split_existing(news_topics), inferred_topics),
        "inferred_assets": inferred_assets,
        "inferred_indicators": inferred_indicators,
        "inferred_news_topics": inferred_topics,
    }


def enrich_research_context(research, lookback_points=5):
    enriched = {
        "active_hypotheses": [],
        "recent_viewpoints": research.get("recent_viewpoints", []),
        "active_watchlist": [],
    }

    for item in research.get("active_hypotheses", []):
        row = dict(item)
        inferred = infer_research_links(
            title=row.get("title"),
            thesis=row.get("thesis"),
            assets=row.get("assets"),
            indicators=row.get("indicators"),
            news_topics=row.get("news_topics"),
            extra_text=row.get("falsification"),
        )
        row["auto_links"] = inferred
        row["linked_data"] = _linked_data(inferred["indicators"], lookback_points)
        row["linked_news"] = _linked_news(inferred["assets"], inferred["news_topics"])
        enriched["active_hypotheses"].append(row)

    for item in research.get("active_watchlist", []):
        row = dict(item)
        inferred = infer_research_links(
            title=row.get("title"),
            thesis=row.get("why"),
            assets=row.get("linked_assets"),
            indicators=row.get("linked_indicators"),
            extra_text=row.get("trigger"),
        )
        row["auto_links"] = inferred
        row["linked_data"] = _linked_data(inferred["indicators"], lookback_points)
        row["linked_news"] = _linked_news(inferred["assets"], inferred["news_topics"])
        enriched["active_watchlist"].append(row)

    return enriched


def _indicator_source(series_id):
    for source, sid, label in INDICATOR_KEYWORDS:
        if sid == series_id:
            return source, sid, label
    return None, series_id, series_id


def _linked_data(indicators, lookback_points):
    rows = []
    for series_id in _split_existing(indicators)[:8]:
        source, sid, label = _indicator_source(series_id)
        if not source:
            continue
        if source == "market":
            snap = _market_snapshot(sid, lookback_points)
        else:
            snap = query_series_snapshot(source, sid, lookback_points=lookback_points)
        if snap:
            snap["label"] = label
            rows.append(snap)
    return rows


def _market_snapshot(series_id, lookback_points):
    from services.market_data import query_market_series

    df, meta = query_market_series(series_id)
    if df.empty:
        return None
    rows = df.copy()
    rows["date"] = pd.to_datetime(rows["date"])
    rows = rows.sort_values("date").dropna(subset=["value"])
    if rows.empty:
        return None
    latest = rows.iloc[-1]
    prev = rows.iloc[-lookback_points - 1] if len(rows) > lookback_points else None
    pct = None
    if prev is not None and prev["value"] not in (None, 0):
        pct = float((latest["value"] / prev["value"] - 1) * 100)
    return {
        "source": meta.get("provider", "market"),
        "series_id": meta.get("series_id", series_id),
        "date": latest["date"].strftime("%Y-%m-%d"),
        "value": float(latest["value"]),
        "change_n": None if prev is None else float(latest["value"] - prev["value"]),
        "change_n_pct": pct,
    }


def _linked_news(assets, topics):
    asset_list = _split_existing(assets)
    topic_list = _split_existing(topics)
    rows = []
    for topic in topic_list[:3]:
        for item in query_analyzed_news(event_type=topic, min_severity=2, assets=asset_list or None, limit=3):
            rows.append(dict(item))
    if not rows and asset_list:
        for item in query_analyzed_news(min_severity=2, assets=asset_list, limit=3):
            rows.append(dict(item))
    return rows[:5]
