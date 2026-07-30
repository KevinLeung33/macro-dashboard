"""Persist links from clustered news events to indicators and research hypotheses."""
import re

from db.repository import (
    query_cluster_articles,
    query_news_clusters,
    query_research_hypotheses,
    replace_cluster_hypothesis_links,
    replace_cluster_indicator_links,
)
from services.research_linker import INDICATOR_KEYWORDS, infer_research_links


MACRO_CHANNEL_INDICATORS = {
    "real_rate": "DFII10",
    "dxy": "DX-Y.NYB",
    "risk_appetite": "VIXCLS",
    "credit": "BAMLH0A0HYM2",
    "liquidity": "STABLE_TOTAL_MCAP",
    "china_cycle": "CN_PMI",
    "crypto_sentiment": "ETHBTC",
}


def _split(value):
    return {item.strip() for item in re.split(r"[,，\s]+", str(value or "")) if item.strip()}


def _indicator_meta(series_id):
    for source, candidate, label in INDICATOR_KEYWORDS:
        if candidate == series_id:
            return {"source": source, "series_id": candidate, "label": label}
    return None


def _cluster_indicators(cluster):
    indicator_ids = set()
    for article in query_cluster_articles(cluster["id"]):
        indicator_ids.update(_split(article["follow_up_data"]))
        for channel in _split(article["macro_channels"]):
            mapped = MACRO_CHANNEL_INDICATORS.get(channel)
            if mapped:
                indicator_ids.add(mapped)

    links = []
    for series_id in sorted(indicator_ids):
        meta = _indicator_meta(series_id)
        if meta:
            meta["reason"] = "AI follow_up_data 或宏观传导渠道"
            links.append(meta)
    return links


def _hypothesis_links(cluster, indicators, hypotheses):
    cluster_assets = _split(cluster["assets_impacted"])
    cluster_topic = cluster["event_type"]
    indicator_ids = {item["series_id"] for item in indicators}
    links = []
    for hypothesis in hypotheses:
        inferred = infer_research_links(
            title=hypothesis["title"], thesis=hypothesis["thesis"],
            assets=hypothesis["assets"], indicators=hypothesis["indicators"],
            news_topics=hypothesis["news_topics"], extra_text=hypothesis["falsification"],
        )
        assets = _split(inferred["assets"])
        topics = _split(inferred["news_topics"])
        hypothesis_indicators = _split(inferred["indicators"])
        asset_hits = sorted(cluster_assets & assets)
        indicator_hits = sorted(indicator_ids & hypothesis_indicators)
        topic_hit = cluster_topic in topics
        score = len(asset_hits) * 2 + len(indicator_hits) + (2 if topic_hit else 0)
        if not score:
            continue
        reasons = []
        if asset_hits:
            reasons.append("资产：" + ",".join(asset_hits))
        if topic_hit:
            reasons.append("主题：" + cluster_topic)
        if indicator_hits:
            reasons.append("指标：" + ",".join(indicator_hits))
        links.append({
            "hypothesis_id": hypothesis["id"],
            "match_score": score,
            "match_reason": "；".join(reasons),
        })
    return links


def refresh_news_research_links(limit=200):
    """Rebuild explainable event-to-research links after clustering or edits."""
    clusters = query_news_clusters(limit=limit, min_severity=1)
    hypotheses = query_research_hypotheses(status="active", limit=200)
    indicator_count = 0
    hypothesis_count = 0
    for cluster in clusters:
        indicators = _cluster_indicators(cluster)
        hypotheses_links = _hypothesis_links(cluster, indicators, hypotheses)
        replace_cluster_indicator_links(cluster["id"], indicators)
        replace_cluster_hypothesis_links(cluster["id"], hypotheses_links)
        indicator_count += len(indicators)
        hypothesis_count += len(hypotheses_links)
    return {
        "clusters": len(clusters),
        "indicator_links": indicator_count,
        "hypothesis_links": hypothesis_count,
    }
