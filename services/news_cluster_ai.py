"""AI-assisted consolidation and event-level conclusions for news clusters."""
import json
import logging
import os
import re
import time
from datetime import datetime
from datetime import timedelta

from db.repository import (
    merge_news_clusters,
    query_cluster_articles,
    query_news_clusters,
    update_news_cluster_ai,
)
from services.ai_json import parse_ai_json


logger = logging.getLogger("news_cluster_ai")


SYSTEM_PROMPT = """你是宏观新闻事件整理助手。输入是一组由规则聚类得到的候选事件簇。
请判断它们是否在报道同一个具体事件，而不是仅仅属于同一个大主题。

只有在以下条件大体同时满足时才合并：核心主体/政策/公司/数据事件相同，时间接近，且新闻只是不同来源或后续报道。
如果只是都涉及同一资产或同一宏观主题，但事实主体不同，必须不合并。

请只返回 JSON：
{
  "merge": true,
  "title": "一个简短的中文事件标题",
  "conclusion": "一句话说明这个事件目前反映了什么",
  "implications": "一句话说明对相关资产或宏观传导的影响",
  "watch_next": "一句话说明下一步观察什么",
  "confidence": 0.0
}
不要编造输入之外的事实，不要输出买卖建议。"""


def _tokens(value):
    text = str(value or "").lower()
    words = re.findall(r"[a-z][a-z0-9]{2,}", text)
    cjk = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    return set(words + cjk)


def _similarity(left, right):
    left_tokens = _tokens(f"{left.get('title', '')} {left.get('summary', '')}")
    right_tokens = _tokens(f"{right.get('title', '')} {right.get('summary', '')}")
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _parse_time(value):
    text = str(value or "").replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _time_gap_hours(left, right):
    left_time = _parse_time(left.get("last_seen_at")) or _parse_time(left.get("first_seen_at"))
    right_time = _parse_time(right.get("last_seen_at")) or _parse_time(right.get("first_seen_at"))
    if not left_time or not right_time:
        return 9999
    return abs((left_time - right_time).total_seconds()) / 3600


def _assets(value):
    return {item.strip().lower() for item in str(value or "").split(",") if item.strip()}


def _candidate_groups(clusters):
    """Build small, high-similarity candidate groups before calling the model."""
    parent = {int(row["id"]): int(row["id"]) for row in clusters}

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for index, left in enumerate(clusters):
        for right in clusters[index + 1:]:
            gap = _time_gap_hours(left, right)
            if gap > 120:
                continue
            similarity = _similarity(left, right)
            same_type = str(left["event_type"] or "") == str(right["event_type"] or "")
            asset_overlap = bool(_assets(left["assets_impacted"]) & _assets(right["assets_impacted"]))
            if similarity >= 0.72 or (similarity >= 0.38 and (same_type or asset_overlap)):
                union(int(left["id"]), int(right["id"]))

    groups = {}
    for row in clusters:
        groups.setdefault(find(int(row["id"])), []).append(row)
    return [group for group in groups.values() if len(group) > 1]


def _article_payload(cluster_id):
    return [
        {
            "source": row["source"],
            "title": row["title"],
            "summary": row["summary_cn"] or "",
            "published_at": row["published_at"],
        }
        for row in query_cluster_articles(cluster_id)[:4]
    ]


def _group_payload(group):
    return [
        {
            "cluster_id": int(row["id"]),
            "title": row["title"],
            "summary": row["summary"],
            "event_type": row["event_type"],
            "assets": row["assets_impacted"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "article_count": row["article_count"],
            "articles": _article_payload(row["id"]),
        }
        for row in group
    ]


def _call_ai(group):
    key = os.getenv("OPENAI_API_KEY")
    if not key or "sk-your" in key:
        from services.runtime_controls import notify_runtime_error

        notify_runtime_error(
            "news_refresh",
            "OPENAI_API_KEY is not configured or still uses the placeholder",
            "事件流保留规则聚类；未执行事件级 AI 合并",
        )
        return None

    base = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("OPENAI_MODEL", "deepseek-chat")
    try:
        max_tokens = int(os.getenv("AI_NEWS_CLUSTER_MAX_TOKENS", "1600"))
    except ValueError as exc:
        from services.runtime_controls import notify_runtime_error

        notify_runtime_error(
            "news_refresh",
            exc,
            "事件流保留规则聚类；请检查 AI_NEWS_CLUSTER_MAX_TOKENS 配置",
        )
        return None
    payload = json.dumps(_group_payload(group), ensure_ascii=False, default=str)
    try:
        import openai

        client = openai.OpenAI(api_key=key, base_url=base)
        last_error = None
        for attempt, use_json_mode in enumerate((True, False), start=1):
            try:
                request = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": payload},
                    ],
                    "temperature": 0.1,
                    "max_tokens": max_tokens if use_json_mode else min(max_tokens * 2, 4096),
                }
                if use_json_mode:
                    request["response_format"] = {"type": "json_object"}
                response = client.chat.completions.create(**request)
                if not response.choices:
                    raise ValueError("AI response has no choices")
                content = getattr(response.choices[0].message, "content", None)
                if isinstance(content, list):
                    content = "".join(
                        str(part.get("text", "")) for part in content if isinstance(part, dict)
                    )
                if not content or not str(content).strip():
                    raise ValueError(f"empty AI response (attempt={attempt}, json_mode={use_json_mode})")
                parsed = parse_ai_json(content)
                if not isinstance(parsed, dict):
                    raise ValueError("AI event consolidation response is not an object")
                merge_value = parsed.get("merge", False)
                if isinstance(merge_value, str):
                    merge_value = merge_value.strip().lower() in {"1", "true", "yes", "是"}
                return {
                    "merge": bool(merge_value),
                    "title": str(parsed.get("title") or "")[:160],
                    "conclusion": str(parsed.get("conclusion") or "")[:500],
                    "implications": str(parsed.get("implications") or "")[:500],
                    "watch_next": str(parsed.get("watch_next") or "")[:500],
                }
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "News event AI consolidation attempt %s failed (json_mode=%s): %s",
                    attempt, use_json_mode, exc,
                )
                if attempt == 1:
                    time.sleep(1)
        raise last_error or RuntimeError("AI event consolidation failed")
    except Exception as exc:
        logger.warning("News event AI consolidation failed: %s", exc)
        from services.runtime_controls import notify_runtime_error

        notify_runtime_error(
            "news_refresh",
            exc,
            "事件流保留规则聚类，未应用事件级 AI 合并结论",
        )
        return None


def _survivor(group):
    return max(
        group,
        key=lambda row: (
            int(row["severity"] or 1),
            int(row["article_count"] or 0),
            str(row["last_seen_at"] or ""),
        ),
    )


def _is_exact_group(group):
    return all(_similarity(group[0], row) >= 0.88 for row in group[1:])


def _failed_attempt_is_recent(group, hours=6):
    now = datetime.utcnow()
    for row in group:
        if str(row.get("ai_status") or "") != "failed":
            return False
        updated = _parse_time(row.get("ai_updated_at"))
        if not updated or now - updated >= timedelta(hours=hours):
            return False
    return True


def consolidate_news_clusters(days=3, limit=100):
    """Merge candidate clusters and persist one AI conclusion for each merged event."""
    ai_enabled = os.getenv("AI_NEWS_CLUSTER_MERGE_ENABLED", "true").lower() not in {"0", "false", "no"}

    clusters = [
        dict(row)
        for row in query_news_clusters(limit=limit, min_severity=1, days=days)
    ]
    groups = _candidate_groups(clusters)
    merged = ai_conclusions = 0
    for group in groups:
        if all(str(row.get("ai_status") or "") in {"complete", "separate"} for row in group):
            continue
        if _failed_attempt_is_recent(group):
            continue
        survivor = _survivor(group)
        duplicate_ids = [int(row["id"]) for row in group if int(row["id"]) != int(survivor["id"])]
        result = _call_ai(group) if ai_enabled else None
        if result is None:
            # Exact duplicate headlines remain safe to collapse even when the AI endpoint is down.
            if not _is_exact_group(group):
                for row in group:
                    update_news_cluster_ai(row["id"], status="failed")
                continue
            result = {
                "merge": True,
                "title": survivor["title"] or "",
                "conclusion": survivor["summary"] or "",
                "implications": "",
                "watch_next": "",
            }

        if result["merge"]:
            merged += merge_news_clusters(survivor["id"], duplicate_ids)
            update_news_cluster_ai(
                survivor["id"], status="complete", title=result["title"],
                summary=result["conclusion"], implications=result["implications"],
                watch_next=result["watch_next"],
            )
            ai_conclusions += 1
        else:
            for row in group:
                update_news_cluster_ai(row["id"], status="separate")

    return {
        "groups": len(groups),
        "merged": merged,
        "ai_conclusions": ai_conclusions,
    }
