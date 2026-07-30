"""Rule-based news clustering for analyzed financial news."""
import json
import logging
import re
from collections import Counter
from datetime import datetime

from db.repository import (
    add_article_to_cluster,
    mark_articles_clustered,
    query_news_clusters,
    query_recent_analyzed_articles,
    upsert_news_cluster,
)
from services.time_utils import app_now

logger = logging.getLogger(__name__)


STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with",
    "as", "by", "from", "at", "is", "are", "was", "were", "be", "will",
    "may", "after", "before", "over", "under", "amid", "says", "say",
    "said", "new", "update", "market", "markets", "stock", "stocks",
    "why", "how", "what", "this", "that", "into", "than", "more",
}


def _split_csv(text):
    if not text:
        return set()
    return {x.strip() for x in str(text).split(",") if x.strip()}


def _direction_keys(direction_text):
    if not direction_text:
        return set()
    try:
        data = json.loads(direction_text)
        if isinstance(data, dict):
            return {f"{k}:{v}" for k, v in data.items()}
    except (json.JSONDecodeError, TypeError):
        pass
    return set()


def _tokens(title):
    words = re.findall(r"[A-Za-z][A-Za-z0-9]+", title.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def _parse_time(value):
    if not value:
        return None
    text = str(value).replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _jaccard(a, b):
    if not a or not b:
        return 0
    return len(a & b) / len(a | b)


def _features(row):
    ts = _parse_time(row["published_at"]) or _parse_time(row["created_at"]) or app_now().replace(tzinfo=None)
    return {
        "article_id": row["article_id"],
        "title": row["title"] or row["summary_cn"] or "",
        "summary": row["summary_cn"] or row["title"] or "",
        "event_type": row["event_type"] or "other",
        "assets": _split_csv(row["assets_impacted"]),
        "direction": _direction_keys(row["direction"]),
        "severity": int(row["severity"] or 1),
        "confidence": float(row["confidence"] or 0.5),
        "source": row["source"] or "",
        "published_at": ts,
        "tokens": _tokens(row["title"] or row["summary_cn"] or ""),
        "why": row["why_it_matters"] or "",
    }


def _score(item, cluster):
    score = 0
    if item["event_type"] == cluster["event_type"]:
        score += 3

    asset_overlap = item["assets"] & cluster["assets"]
    if asset_overlap:
        score += 2

    direction_overlap = item["direction"] & cluster["direction"]
    if direction_overlap:
        score += 1

    token_sim = _jaccard(item["tokens"], cluster["tokens"])
    if token_sim >= 0.25:
        score += 2
    elif token_sim >= 0.15:
        score += 1

    hours = abs((item["published_at"] - cluster["last_seen_at"]).total_seconds()) / 3600
    if hours <= 48:
        score += 2
    elif hours <= 96:
        score += 1

    return score


def _cluster_key(item):
    date_key = item["published_at"].strftime("%Y%m%d")
    asset_key = "-".join(sorted(item["assets"])) or "noasset"
    top_tokens = "-".join(sorted(list(item["tokens"]))[:3]) or "notitle"
    return f"{date_key}:{item['event_type']}:{asset_key}:{top_tokens}"


def _new_cluster(item):
    return {
        "cluster_key": _cluster_key(item),
        "title": item["summary"][:160],
        "summary": item["why"] or item["summary"],
        "event_type": item["event_type"],
        "assets": set(item["assets"]),
        "direction": set(item["direction"]),
        "severity": item["severity"],
        "confidence": item["confidence"],
        "first_seen_at": item["published_at"],
        "last_seen_at": item["published_at"],
        "article_ids": [item["article_id"]],
        "sources": Counter([item["source"]]),
        "tokens": set(item["tokens"]),
        "primary_source": item["source"],
    }


def _merge(cluster, item):
    cluster["article_ids"].append(item["article_id"])
    cluster["assets"] |= item["assets"]
    cluster["direction"] |= item["direction"]
    cluster["tokens"] |= item["tokens"]
    cluster["sources"].update([item["source"]])
    cluster["first_seen_at"] = min(cluster["first_seen_at"], item["published_at"])
    cluster["last_seen_at"] = max(cluster["last_seen_at"], item["published_at"])
    cluster["confidence"] = max(cluster["confidence"], item["confidence"])
    if item["severity"] >= cluster["severity"]:
        cluster["severity"] = item["severity"]
        cluster["title"] = item["summary"][:160]
        cluster["summary"] = item["why"] or item["summary"]
        cluster["primary_source"] = item["source"]


def _serializable(cluster):
    return {
        "cluster_key": cluster["cluster_key"],
        "title": cluster["title"],
        "summary": cluster["summary"],
        "event_type": cluster["event_type"],
        "assets_impacted": ",".join(sorted(cluster["assets"])),
        "direction": ",".join(sorted(cluster["direction"])),
        "severity": cluster["severity"],
        "confidence": cluster["confidence"],
        "first_seen_at": cluster["first_seen_at"].strftime("%Y-%m-%d %H:%M"),
        "last_seen_at": cluster["last_seen_at"].strftime("%Y-%m-%d %H:%M"),
        "article_count": len(set(cluster["article_ids"])),
        "primary_source": cluster["primary_source"],
        "status": "active",
    }


def build_news_clusters(days=3, limit=200, threshold=5):
    rows = query_recent_analyzed_articles(days=days, limit=limit)
    items = [_features(row) for row in rows]
    clusters = []
    assignments = []

    for item in sorted(items, key=lambda x: x["published_at"]):
        best = None
        best_score = 0
        for cluster in clusters:
            score = _score(item, cluster)
            if score > best_score:
                best = cluster
                best_score = score

        if best and best_score >= threshold:
            _merge(best, item)
            assignments.append((item["article_id"], best, best_score))
        else:
            cluster = _new_cluster(item)
            clusters.append(cluster)
            assignments.append((item["article_id"], cluster, 10))

    saved_clusters = {}
    for cluster in clusters:
        cluster_id = upsert_news_cluster(_serializable(cluster))
        if cluster_id:
            saved_clusters[id(cluster)] = cluster_id

    linked = 0
    for article_id, cluster, score in assignments:
        cluster_id = saved_clusters.get(id(cluster))
        if cluster_id:
            add_article_to_cluster(article_id, cluster_id, score)
            linked += 1

    mark_articles_clustered([article_id for article_id, _cluster, _score in assignments])
    try:
        from services.news_research_links import refresh_news_research_links
        research_links = refresh_news_research_links()
    except Exception as exc:
        logger.warning("News research linking skipped: %s", exc)
        research_links = {"clusters": 0, "indicator_links": 0, "hypothesis_links": 0}

    return {
        "articles": len(items), "clusters": len(clusters), "linked": linked,
        "research_links": research_links,
    }


def get_important_clusters(limit=8, min_severity=3):
    return [dict(r) for r in query_news_clusters(limit=limit, min_severity=min_severity)]
