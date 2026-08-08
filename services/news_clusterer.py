"""Conservative, evidence-first clustering for the live news event stream.

An article is not an event.  The event stream intentionally groups several
reports about one concrete fact, but it must never group articles merely
because they share a broad topic, an asset, or a publication window.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime

from db.repository import (
    add_article_to_cluster,
    backfill_news_article_identities,
    clear_article_cluster_links,
    count_recent_analyzed_articles,
    deactivate_stale_news_clusters,
    deactivate_unseen_news_clusters,
    mark_articles_clustered,
    query_news_clusters,
    query_recent_analyzed_articles,
    upsert_news_cluster,
)
from services.news_identity import normalize_title, title_fingerprint
from services.time_utils import app_now


logger = logging.getLogger(__name__)


STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with",
    "as", "by", "from", "at", "is", "are", "was", "were", "be", "will",
    "may", "after", "before", "over", "under", "amid", "says", "say", "said",
    "new", "update", "why", "how", "what", "this", "that", "into", "than",
    "more", "about", "from", "its", "their", "your", "our", "not", "but",
}

# These words frequently occur in unrelated market headlines.  They should
# never by themselves turn two reports into the same event.
LOW_SIGNAL_TOKENS = {
    "market", "markets", "stock", "stocks", "shares", "share", "trading",
    "investors", "investor", "economy", "economic", "financial", "finance",
    "global", "world", "today", "latest", "live", "breaking", "news", "report",
    "reports", "data", "price", "prices", "rise", "rises", "fall", "falls",
    "gain", "gains", "loss", "losses", "watch", "outlook", "analysis",
    "china", "chinese", "america", "american", "united", "states", "u", "s",
}

EVENT_ANCHORS = {
    "fed_policy": {"fed", "fomc", "powell", "rate", "rates", "hike", "cut"},
    "inflation": {"cpi", "ppi", "pce", "inflation", "prices"},
    "growth": {"gdp", "pmi", "recession", "growth", "manufacturing"},
    "employment": {"payroll", "payrolls", "jobs", "jobless", "unemployment", "nfp", "wage", "wages"},
    "geopolitics": {"tariff", "sanction", "sanctions", "iran", "opec", "war", "trade"},
    "china_macro": {"pboc", "yuan", "rmb", "lpr", "pmi", "china"},
    "crypto": {"bitcoin", "btc", "ethereum", "eth", "stablecoin", "crypto"},
    "energy": {"oil", "crude", "opec", "natural", "gas"},
    "credit": {"yield", "yields", "spread", "spreads", "bond", "bonds", "default"},
    "liquidity": {"repo", "sofr", "reserve", "reserves", "tga", "rrp", "liquidity"},
}


def _env_int(name, default, minimum=1):
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name, default, minimum=0.0):
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _split_csv(text):
    if not text:
        return set()
    return {item.strip() for item in str(text).split(",") if item.strip()}


def _direction_keys(direction_text):
    if not direction_text:
        return set()
    try:
        data = json.loads(direction_text)
        if isinstance(data, dict):
            return {f"{key}:{value}" for key, value in data.items()}
    except (json.JSONDecodeError, TypeError):
        pass
    return set()


def _tokens(value):
    text = normalize_title(value)
    english = re.findall(r"[a-z][a-z0-9-]+", text)
    tokens = {
        word for word in english
        if len(word) > 2 and word not in STOPWORDS and word not in LOW_SIGNAL_TOKENS
    }
    # RSS titles from Chinese sources have no whitespace tokenization.  Stable
    # 3-character pieces are conservative enough for exact/near-exact matches;
    # they still need at least two overlaps to pass the semantic gate below.
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if len(phrase) <= 4:
            tokens.add(phrase)
        else:
            tokens.update(phrase[index:index + 3] for index in range(len(phrase) - 2))
    return tokens


def _named_tokens(value):
    raw = str(value or "")
    names = set()
    for word in re.findall(r"\b[A-Z][A-Za-z0-9-]{2,}\b", raw):
        token = word.lower()
        if token not in LOW_SIGNAL_TOKENS and token not in STOPWORDS:
            names.add(token)
    return names


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


def _jaccard(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _features(row):
    raw_title = str(row["title"] or row["summary_cn"] or "")
    ts = _parse_time(row["published_at"]) or _parse_time(row["created_at"]) or app_now().replace(tzinfo=None)
    return {
        "article_id": int(row["article_id"]),
        "title": raw_title,
        "title_norm": normalize_title(raw_title),
        "title_fingerprint": row["title_fingerprint"] or title_fingerprint(raw_title),
        "summary": str(row["summary_cn"] or raw_title),
        "event_type": str(row["event_type"] or "other"),
        "assets": _split_csv(row["assets_impacted"]),
        "direction": _direction_keys(row["direction"]),
        "severity": int(row["severity"] or 1),
        "confidence": float(row["confidence"] or 0.5),
        "source": str(row["source"] or ""),
        "published_at": ts,
        "tokens": _tokens(raw_title),
        "named_tokens": _named_tokens(raw_title),
        "why": str(row["why_it_matters"] or ""),
    }


def _semantic_match(item, cluster):
    """Return the best evidence similarity, or ``None`` for unrelated facts."""
    best = None
    for prototype in cluster["prototypes"]:
        if item["title_norm"] and item["title_norm"] == prototype["title_norm"]:
            return {"similarity": 1.0, "shared": set(), "exact": True}
        shared = item["tokens"] & prototype["tokens"]
        similarity = _jaccard(item["tokens"], prototype["tokens"])
        shared_named = item["named_tokens"] & prototype["named_tokens"]
        event_type = item["event_type"]
        anchors = EVENT_ANCHORS.get(event_type, set())

        # Strong evidence requires two meaningful title clues. A shared name
        # alone (for example, two unrelated Trump or Powell headlines) is too
        # broad to define one market event.
        valid = len(shared) >= 2 and similarity >= 0.18
        valid = valid or (bool(shared_named) and (len(shared) >= 2 or similarity >= 0.22))

        # A scheduled macro release can have divergent headlines.  Still
        # require an event anchor plus another shared clue; time/type alone is
        # intentionally never enough.
        if (
            event_type != "other"
            and event_type == cluster["event_type"]
            and shared & anchors
            and (len(shared) >= 2 or similarity >= 0.30)
        ):
            valid = True

        if not valid:
            continue
        candidate = {
            "similarity": similarity,
            "shared": shared,
            "exact": False,
            "named": shared_named,
        }
        if best is None or candidate["similarity"] > best["similarity"]:
            best = candidate
    return best


def _score(item, cluster, max_event_hours):
    gap_hours = abs((item["published_at"] - cluster["last_seen_at"]).total_seconds()) / 3600
    if gap_hours > max_event_hours:
        return 0.0
    semantic = _semantic_match(item, cluster)
    if semantic is None:
        return 0.0

    # Reject conflicting structured labels unless the headline identity is very
    # strong.  AI classification is useful supporting evidence, not identity.
    if (
        item["event_type"] != cluster["event_type"]
        and item["event_type"] != "other"
        and cluster["event_type"] != "other"
        and semantic["similarity"] < 0.65
        and not semantic.get("exact")
    ):
        return 0.0

    score = semantic["similarity"] * 10
    if semantic.get("exact"):
        score += 5
    if semantic.get("named"):
        score += 1
    if item["event_type"] == cluster["event_type"]:
        score += 1.25 if item["event_type"] != "other" else 0.25
    if item["assets"] & cluster["assets"]:
        score += 0.75
    if item["direction"] & cluster["direction"]:
        score += 0.25
    if gap_hours <= 48:
        score += 0.75
    return score


def _cluster_key(cluster):
    # The v2 prefix keeps legacy rule clusters distinct until the successful
    # rebuild retires them. The seed is a concrete headline identity, not a
    # mutable AI-generated summary.
    seed = cluster["seed_fingerprint"] or hashlib.sha1(cluster["title"].encode("utf-8")).hexdigest()[:16]
    return f"v2:{cluster['event_type']}:{seed}"


def _new_cluster(item):
    return {
        "cluster_key": "",
        "seed_fingerprint": item["title_fingerprint"],
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
        "prototypes": [{
            "tokens": set(item["tokens"]),
            "named_tokens": set(item["named_tokens"]),
            "title_norm": item["title_norm"],
        }],
        "primary_source": item["source"],
    }


def _merge(cluster, item):
    cluster["article_ids"].append(item["article_id"])
    cluster["assets"] |= item["assets"]
    cluster["direction"] |= item["direction"]
    cluster["sources"].update([item["source"]])
    cluster["first_seen_at"] = min(cluster["first_seen_at"], item["published_at"])
    cluster["last_seen_at"] = max(cluster["last_seen_at"], item["published_at"])
    cluster["confidence"] = max(cluster["confidence"], item["confidence"])
    if item["severity"] >= cluster["severity"]:
        cluster["severity"] = item["severity"]
        cluster["title"] = item["summary"][:160]
        cluster["summary"] = item["why"] or item["summary"]
        cluster["primary_source"] = item["source"]

    # Keep a small set of headline prototypes.  Unioning every token would
    # make an old, large cluster ever easier to match and recreate the bug this
    # module is intended to prevent.
    if len(cluster["prototypes"]) < 12:
        if not any(_jaccard(item["tokens"], proto["tokens"]) >= 0.8 for proto in cluster["prototypes"]):
            cluster["prototypes"].append({
                "tokens": set(item["tokens"]),
                "named_tokens": set(item["named_tokens"]),
                "title_norm": item["title_norm"],
            })


def _serializable(cluster):
    article_ids = sorted(set(cluster["article_ids"]))
    evidence = hashlib.sha256(",".join(str(item) for item in article_ids).encode("utf-8")).hexdigest()[:32]
    cluster["cluster_key"] = _cluster_key(cluster)
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
        "article_count": len(article_ids),
        "primary_source": cluster["primary_source"],
        "status": "active",
        "evidence_fingerprint": evidence,
    }


def _cluster_items(items, threshold, max_event_hours):
    """Cluster items using an inverted title-token index, not broad topic scans."""
    clusters = []
    assignments = []
    token_index = defaultdict(set)
    title_index = defaultdict(set)

    for item in sorted(items, key=lambda value: value["published_at"]):
        candidates = set(title_index.get(item["title_fingerprint"], set()))
        for token in item["tokens"]:
            candidates.update(token_index.get(token, set()))

        best_index = None
        best_score = 0.0
        for index in candidates:
            score = _score(item, clusters[index], max_event_hours)
            if score > best_score:
                best_index = index
                best_score = score

        if best_index is not None and best_score >= threshold:
            cluster = clusters[best_index]
            _merge(cluster, item)
            assignments.append((item["article_id"], cluster, best_score))
            index = best_index
        else:
            cluster = _new_cluster(item)
            clusters.append(cluster)
            assignments.append((item["article_id"], cluster, 10.0))
            index = len(clusters) - 1

        title_index[item["title_fingerprint"]].add(index)
        for token in item["tokens"]:
            token_index[token].add(index)
    return clusters, assignments


def build_news_clusters(days=3, limit=None, threshold=None):
    """Rebuild the recent live event stream from article-level evidence.

    A successful non-truncated run is authoritative for its time window: event
    variants not regenerated this time are retired from the live view rather
    than accumulating alongside the new grouping.
    """
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 3
    if limit is None:
        limit = _env_int("NEWS_CLUSTER_MAX_ARTICLES", 1000)
    else:
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = _env_int("NEWS_CLUSTER_MAX_ARTICLES", 1000)
    if threshold is None:
        threshold = _env_float("NEWS_CLUSTER_MATCH_THRESHOLD", 3.0)
    else:
        try:
            threshold = max(0.1, float(threshold))
        except (TypeError, ValueError):
            threshold = _env_float("NEWS_CLUSTER_MATCH_THRESHOLD", 3.0)
    max_event_hours = _env_float("NEWS_CLUSTER_EVENT_MAX_HOURS", 96.0, minimum=1.0)

    backfilled = backfill_news_article_identities(limit=max(limit * 2, 1000))
    total_articles = count_recent_analyzed_articles(days=days)
    rows = query_recent_analyzed_articles(days=days, limit=limit)
    truncated = total_articles > len(rows)
    items = [_features(row) for row in rows]
    clusters, assignments = _cluster_items(items, threshold=threshold, max_event_hours=max_event_hours)
    rebuild_token = f"v2-{uuid.uuid4().hex}"

    saved_clusters = {}
    for cluster in clusters:
        cluster_id = upsert_news_cluster(_serializable(cluster), rebuild_token=rebuild_token)
        if cluster_id:
            saved_clusters[id(cluster)] = cluster_id

    article_ids = [item["article_id"] for item in items]
    clear_article_cluster_links(article_ids)
    linked = 0
    for article_id, cluster, score in assignments:
        cluster_id = saved_clusters.get(id(cluster))
        if cluster_id:
            add_article_to_cluster(article_id, cluster_id, score)
            linked += 1
    mark_articles_clustered(article_ids)

    stale = deactivate_stale_news_clusters(days=days)
    retired = 0
    if not truncated:
        retired = deactivate_unseen_news_clusters(rebuild_token, days=days)
    else:
        logger.warning(
            "News event rebuild is truncated (%s/%s articles); preserving existing live clusters",
            len(rows), total_articles,
        )

    try:
        from services.news_cluster_ai import consolidate_news_clusters

        ai_consolidation = consolidate_news_clusters(days=days, limit=max(100, min(len(clusters) * 2, 500)))
    except Exception as exc:
        logger.warning("News event consolidation skipped: %s", exc)
        from services.runtime_controls import notify_runtime_error

        notify_runtime_error(
            "news_refresh",
            exc,
            "事件流保留严格规则聚类，事件级 AI 合并已跳过",
        )
        ai_consolidation = {"groups": 0, "merged": 0, "ai_conclusions": 0, "error": str(exc)}
    try:
        from services.news_research_links import refresh_news_research_links

        research_links = refresh_news_research_links()
    except Exception as exc:
        logger.warning("News research linking skipped: %s", exc)
        from services.runtime_controls import notify_runtime_error

        notify_runtime_error(
            "news_refresh",
            exc,
            "新闻仍保留，但暂未生成与指标/研究假设的关联",
        )
        research_links = {"clusters": 0, "indicator_links": 0, "hypothesis_links": 0}

    return {
        "articles": len(items),
        "total_articles": total_articles,
        "clusters": len(clusters),
        "linked": linked,
        "deactivated": stale,
        "retired": retired,
        "truncated": truncated,
        "backfilled": backfilled,
        "merged": ai_consolidation.get("merged", 0),
        "ai_conclusions": ai_consolidation.get("ai_conclusions", 0),
        "research_links": research_links,
    }


def get_important_clusters(limit=8, min_severity=3):
    return [dict(row) for row in query_news_clusters(limit=limit, min_severity=min_severity)]
