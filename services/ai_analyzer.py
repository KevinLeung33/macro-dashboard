"""AI 分析器 v2 — 结构化归因 + 新信息检测"""
import json
import logging
import os

from services.ai_json import parse_ai_json

logger = logging.getLogger("ai_analyzer")
PROMPT_VERSION = "news-structured-v3"

SYSTEM_PROMPT = """你是宏观经济学家助手。分析财经新闻，返回JSON(所有字段必填):
{
  "summary_cn": "一句话中文摘要(30字以内)",
  "event_type": "fed_policy|inflation|growth|employment|geopolitics|china_macro|crypto|energy|credit|liquidity|other",
  "macro_channels": ["real_rate","dxy","risk_appetite","credit","liquidity","china_cycle","crypto_sentiment"],
  "assets_impacted": ["SP500","NASDAQ","DXY","Oil","BTC","CNH","HSTECH","Gold"],
  "direction": {"BTC":"bearish","NASDAQ":"bearish","DXY":"bullish"},
  "time_horizon": "hours|days|weeks",
  "severity": 1-5,
  "confidence": 0.0-1.0,
  "is_new_information": true,
  "why_it_matters": "一句话解释为什么重要",
  "follow_up_data": ["DFII10","DXY"]
}
is_new_information: 这篇新闻是否报道了之前没出现过的新内容？如果是已知信息的重复/跟进= false。"""


def ai_analyze(title, content=""):
    """调用DeepSeek API分析 — 兼容OpenAI格式"""
    key = os.getenv("OPENAI_API_KEY")
    base = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("OPENAI_MODEL", "deepseek-chat")
    max_tokens = int(os.getenv("AI_ANALYZE_MAX_TOKENS", "4096"))
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    try:
        import openai
        client = openai.OpenAI(api_key=key, base_url=base)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"标题: {title}\n内容: {content[:800]}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=max_tokens,
        )
        raw = resp.choices[0].message.content
        try:
            return _normalize_result(parse_ai_json(raw))
        except Exception as parse_error:
            snippet = (raw or "").replace("\n", " ")[:500]
            logger.warning(f"AI JSON parse failed: {parse_error}; raw_prefix={snippet}")
            raise ValueError(f"AI response JSON parse failed: {parse_error}") from parse_error
    except Exception as e:
        logger.warning(f"AI analyze failed: {e}")
        raise


def _normalize_result(result):
    if not isinstance(result, dict):
        raise ValueError("AI response JSON is not an object")

    def as_list(value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(x) for x in value if x is not None]
        if isinstance(value, str):
            return [x.strip() for x in value.replace("，", ",").split(",") if x.strip()]
        return [str(value)]

    direction = result.get("direction") or {}
    if isinstance(direction, str):
        try:
            direction = parse_ai_json(direction)
        except Exception as exc:
            logger.warning("AI direction field was not valid JSON: %s", exc)
            direction = {}
    if not isinstance(direction, dict):
        direction = {}

    return {
        "summary_cn": str(result.get("summary_cn") or "")[:120],
        "event_type": str(result.get("event_type") or "other"),
        "macro_channels": as_list(result.get("macro_channels")),
        "assets_impacted": as_list(result.get("assets_impacted")),
        "direction": direction,
        "time_horizon": str(result.get("time_horizon") or "days"),
        "severity": _clamp_int(result.get("severity"), 1, 5, default=1),
        "confidence": _clamp_float(result.get("confidence"), 0.0, 1.0, default=0.5),
        "is_new_information": bool(result.get("is_new_information", True)),
        "why_it_matters": str(result.get("why_it_matters") or ""),
        "follow_up_data": as_list(result.get("follow_up_data")),
    }


def _clamp_int(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _clamp_float(value, low, high, default):
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def run_analysis_pipeline(limit=15):
    """完整的AI分析流水线：选文章 → 分析 → 存储"""
    from services.news_fetcher import select_for_analysis
    from db.repository import (
        get_recent_fingerprints, insert_ai_analysis, mark_article_analyzed,
        mark_article_analyzing, mark_article_failed, queue_articles_for_analysis,
    )

    articles = select_for_analysis(limit)
    if not articles:
        logger.info("No articles to analyze")
        return 0

    queue_articles_for_analysis([article["id"] for article in articles])

    recent_fps = get_recent_fingerprints(7)
    analyzed = 0
    failed = 0
    first_failure = None

    for art in articles:
        mark_article_analyzing(art["id"])
        try:
            result = ai_analyze(art["title"], art["summary"] or "")
        except Exception as exc:
            mark_article_failed(art["id"], str(exc))
            failed += 1
            first_failure = first_failure or exc
            continue

        # Post-check: is this really new?
        fp = f"{result.get('event_type','')}|{result.get('assets_impacted','')}|{result.get('direction','')}"
        if fp in recent_fps:
            result["is_new_information"] = False

        direction = json.dumps(result.get("direction", {}), ensure_ascii=False)
        channels = ",".join(result.get("macro_channels", []))
        assets = ",".join(result.get("assets_impacted", []))
        follow = ",".join(result.get("follow_up_data", []))

        try:
            insert_ai_analysis(
                article_id=art["id"], model=os.getenv("OPENAI_MODEL", "deepseek-chat"),
                summary_cn=result.get("summary_cn", ""),
                event_type=result.get("event_type", "other"),
                macro_channels=channels,
                assets_impacted=assets,
                direction=direction,
                severity=result.get("severity", 1),
                confidence=result.get("confidence", 0.5),
                time_horizon=result.get("time_horizon", "days"),
                is_new=1 if result.get("is_new_information", True) else 0,
                why=result.get("why_it_matters", ""),
                follow_up=follow,
                raw_json=json.dumps(result, ensure_ascii=False),
                prompt_version=PROMPT_VERSION,
            )
        except Exception as exc:
            logger.exception("Failed to persist AI analysis for article=%s", art["id"])
            mark_article_failed(art["id"], f"AI analysis persistence failed: {exc}")
            failed += 1
            first_failure = first_failure or exc
            continue
        mark_article_analyzed(art["id"])
        recent_fps.add(fp)
        analyzed += 1

    logger.info(f"AI analyzed {analyzed}/{len(articles)} articles")
    if failed:
        from services.runtime_controls import notify_runtime_error

        notify_runtime_error(
            "news_refresh",
            f"AI analysis failed for {failed}/{len(articles)} articles; first error: {first_failure}",
            "失败文章已标记，成功分析的新闻仍会继续进入事件流和日报",
        )
    try:
        from services.news_clusterer import build_news_clusters
        result = build_news_clusters(days=3)
        logger.info(f"News clusters: {result}")
        from services.news_alerts import dispatch_urgent_news_alerts
        alert_result = dispatch_urgent_news_alerts()
        logger.info("Urgent news alerts: %s", alert_result)
    except Exception as e:
        logger.warning(f"News clustering skipped: {e}")
    return analyzed
