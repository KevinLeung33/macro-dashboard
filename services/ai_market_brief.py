"""Short AI interpretation for the homepage market brief.

The homepage already has deterministic evidence. This module only adds a
compact interpretation layer and never replaces the underlying numbers.
"""
import hashlib
import json
import logging
import os
import threading
import time

from services.ai_json import parse_ai_json


logger = logging.getLogger("ai_market_brief")


SYSTEM_PROMPT = """你是宏观市场研究看板的文字解读助手。
你会收到看板当前已经计算好的数据、组合信号和近7天新闻主题。只允许使用输入内容，不能补充外部事实、不能猜测缺失数据，也不要给出买卖或仓位建议。

请输出严格 JSON，不要 Markdown，不要代码块，字段必须完整：
{
  "today": {
    "judgement": "一句话判断：今天最重要的市场状态或变化",
    "explanation": "最多两句话，解释哪些数据或新闻支持这个判断，以及它们之间的关系",
    "watch": "一句话说明接下来应该观察什么"
  },
  "week": {
    "judgement": "一句话判断：本周主要变化",
    "explanation": "最多两句话，说明近5期变化与近7天新闻是否互相印证、背离或仍不足以确认",
    "watch": "一句话说明下周要观察什么"
  },
  "medium": {
    "judgement": "一句话判断：30D/90D中期背景",
    "explanation": "最多两句话，比较短期与中期方向，说明趋势延续、分化还是可能只是噪音",
    "watch": "一句话说明中期验证条件"
  },
  "overall": "一句话总结当前研究环境；如果数据质量不足，明确写出限制"
}

表达要求：中文、短句、克制、面向研究者。不要重复罗列所有数字，只引用最有代表性的1到3项证据。出现缺失或过期数据时，明确说明“数据不足/数据偏旧”，不要把缺失当成零。"""


_CACHE = {}
_CACHE_LOCK = threading.RLock()


def _cache_seconds():
    try:
        return max(60, int(os.getenv("AI_MARKET_BRIEF_CACHE_SECONDS", "1800")))
    except ValueError:
        return 1800


def _max_tokens():
    try:
        return max(256, int(os.getenv("AI_MARKET_BRIEF_MAX_TOKENS", "1200")))
    except ValueError:
        return 1200


def _move_payload(item):
    return {
        "name": item.get("name"),
        "date": item.get("date"),
        "value": item.get("value"),
        "unit": item.get("unit"),
        "change_n": item.get("change_n"),
        "change_n_pct": item.get("change_n_pct"),
    }


def _trend_payload(item):
    return {
        "name": item.get("name"),
        "date": item.get("date"),
        "value": item.get("value"),
        "unit": item.get("unit"),
        "windows": item.get("windows", {}),
    }


def _news_payload(item):
    return {
        "event_type": item.get("event_type"),
        "count": item.get("count"),
        "avg_severity": item.get("avg_severity"),
        "top_assets": (item.get("top_assets") or [])[:5],
        "latest": item.get("latest"),
    }


def _signal_payload(item):
    return {
        "name": item.get("name"),
        "level": item.get("level"),
        "score": item.get("score"),
        "max_score": item.get("max_score"),
        "summary": item.get("summary"),
        "watch_next": (item.get("watch_next") or [])[:4],
    }


def _compact_payload(brief, cockpit):
    """Create a stable, small payload so unchanged data does not re-call AI."""
    health = []
    for item in cockpit.get("health", [])[:8]:
        health.append({
            "source": item.get("source"),
            "status": item.get("status"),
            # Freshness changes continuously; hour precision keeps the cache stable
            # across normal page reruns while still refreshing when data ages.
            "age_hours": (
                None
                if item.get("age_hours") is None
                else int(float(item["age_hours"]))
            ),
            "latest_data_date": item.get("latest_data_date"),
            "quality_issue_count": item.get("quality_issue_count"),
        })

    return {
        "data_dates": brief.get("data_dates", []),
        "rule_brief": {
            "today": brief.get("today", [])[:4],
            "week": brief.get("week", [])[:5],
            "medium": brief.get("medium", [])[:5],
        },
        "theme_conclusions": [
            {
                "theme": item.get("theme"),
                "title": item.get("title"),
                "conclusion": item.get("conclusion"),
                "level": item.get("level"),
            }
            for item in brief.get("themes", [])[:4]
        ],
        "market_moves": [_move_payload(item) for item in cockpit.get("moves", [])[:6]],
        "multi_window_trends": [_trend_payload(item) for item in cockpit.get("trends", [])[:8]],
        "news_trends": [_news_payload(item) for item in cockpit.get("news_trends", [])[:6]],
        "composite_signals": [_signal_payload(item) for item in cockpit.get("signals", [])[:6]],
        "data_health": health,
    }


def _signature(payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _call_ai(payload):
    key = os.getenv("OPENAI_API_KEY")
    if not key or "sk-your" in key:
        return None

    base = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("OPENAI_MODEL", "deepseek-chat")
    try:
        import openai

        client = openai.OpenAI(api_key=key, base_url=base)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=_max_tokens(),
        )
        return _normalize_result(parse_ai_json(response.choices[0].message.content))
    except Exception as exc:
        logger.warning("AI homepage brief failed: %s", exc)
        return None


def _normalize_result(result):
    if not isinstance(result, dict):
        raise ValueError("AI homepage brief must be a JSON object")

    normalized = {}
    for period in ("today", "week", "medium"):
        item = result.get(period) or {}
        if not isinstance(item, dict):
            item = {}
        normalized[period] = {
            "judgement": str(item.get("judgement") or ""),
            "explanation": str(item.get("explanation") or ""),
            "watch": str(item.get("watch") or ""),
        }
    normalized["overall"] = str(result.get("overall") or "")
    return normalized


def generate_ai_market_brief(brief, cockpit):
    """Return a cached AI interpretation, or ``None`` when unavailable."""
    payload = _compact_payload(brief, cockpit)
    cache_key = _signature(payload)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and now - cached["created_at"] < _cache_seconds():
            return cached["result"]

    result = _call_ai(payload)
    if result is None:
        return None

    with _CACHE_LOCK:
        _CACHE[cache_key] = {"created_at": now, "result": result}
        # Keep the process cache bounded when the data changes frequently.
        if len(_CACHE) > 8:
            oldest_key = min(_CACHE, key=lambda key: _CACHE[key]["created_at"])
            _CACHE.pop(oldest_key, None)
    return result
