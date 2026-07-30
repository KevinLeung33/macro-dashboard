"""AI trend report built from the daily research context."""
import json
import logging
import os

from db.repository import upsert_daily_report
from services.ai_json import parse_ai_json
from services.daily_context import build_context_markdown, build_daily_context
from services.signal_review import save_signal_snapshots
from services.time_utils import app_now


logger = logging.getLogger("daily_ai_report")


SYSTEM_PROMPT = """你是一个面向中美宏观与crypto投资研究的分析助手。
你会收到一份机器整理的每日研究包，里面包括数据健康、近期市场变化、7/30/90天多窗口趋势、组合信号、告警、极端分位、重要事件流、新闻主题动向、重要新闻和用户研究假设。

请只基于输入内容分析，不要编造外部事实。输出 JSON，字段必须完整：
{
  "title": "一句话标题",
  "executive_summary": "3-5句话总结今天最重要的宏观变化",
  "regime_view": {
    "us_macro": "positive|neutral|negative|mixed",
    "china_macro": "positive|neutral|negative|mixed",
    "liquidity": "loose|neutral|tight|mixed",
    "crypto": "positive|neutral|negative|mixed"
  },
  "key_changes": [
    {"topic": "变化主题", "evidence": "数据或新闻证据", "interpretation": "为什么重要"}
  ],
  "trend_evolution": [
    {"topic": "趋势主题", "short_term": "7天变化", "medium_term": "30/90天变化", "read_through": "趋势延续、反转还是噪音"}
  ],
  "asset_implications": [
    {"asset": "BTC|NASDAQ|SP500|DXY|Oil|Gold|CNH|HSTECH", "bias": "bullish|bearish|neutral|mixed", "reason": "原因"}
  ],
  "watchlist": [
    {"item": "接下来要观察什么", "trigger": "触发条件", "why": "为什么重要"}
  ],
  "hypothesis_updates": [
    {"hypothesis": "用户假设名称", "status": "supported|weakened|unchanged|needs_data", "reason": "为什么"}
  ],
  "confidence": 0.0-1.0,
  "data_warnings": ["数据不足、过期或需要谨慎解读之处"]
}
如果输入里有用户研究假设、观点日志或观察项，请结合 auto_links、linked_data、linked_news 明确说明今天的证据支持、削弱还是没有改变这些想法。
多窗口趋势用于区分短期噪音和中期方向：7天变化代表短线冲击，30/90天变化代表趋势背景。
组合信号是规则系统给出的初步判断锚点；可以引用，但如果证据不足或缺数据，需要指出置信度限制。
语言使用中文，表达要短而明确。"""


def _compact_context(context):
    """Keep the payload small and stable before sending it to the model."""
    return {
        "generated_at": context.get("generated_at"),
        "lookback_points": context.get("lookback_points"),
        "data_health": context.get("data_health", [])[:8],
        "market_moves": context.get("market_moves", [])[:8],
        "multi_window_trends": context.get("multi_window_trends", [])[:12],
        "composite_signals": context.get("composite_signals", [])[:8],
        "alerts": context.get("alerts", [])[:8],
        "extreme_zscores": context.get("extreme_zscores", [])[:8],
        "important_clusters": context.get("important_clusters", [])[:8],
        "news_trends": context.get("news_trends", [])[:8],
        "important_news": context.get("important_news", [])[:8],
        "research_context": context.get("research_context", {}),
    }


def _call_ai(context):
    key = os.getenv("OPENAI_API_KEY")
    base = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("OPENAI_MODEL", "deepseek-chat")
    max_tokens = int(os.getenv("AI_DAILY_MAX_TOKENS", "8192"))
    if not key or "sk-your" in key:
        return None

    try:
        import openai

        client = openai.OpenAI(api_key=key, base_url=base)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(_compact_context(context), ensure_ascii=False, default=str),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return parse_ai_json(resp.choices[0].message.content)
    except Exception as e:
        logger.warning(f"AI daily report failed: {e}")
        return None


def _render_ai_markdown(result, context):
    title = result.get("title") or "AI 趋势日报"
    lines = [f"### {title}", ""]
    lines.append(f"生成时间：{context.get('generated_at', '')}")
    lines.append("")

    summary = result.get("executive_summary")
    if summary:
        lines.append("**核心摘要**")
        lines.append(summary)
        lines.append("")

    regime = result.get("regime_view") or {}
    if regime:
        labels = {
            "us_macro": "美国宏观",
            "china_macro": "中国宏观",
            "liquidity": "流动性",
            "crypto": "Crypto",
        }
        lines.append("**Regime 判断**")
        for key, label in labels.items():
            if key in regime:
                lines.append(f"- {label}: `{regime[key]}`")
        lines.append("")

    changes = result.get("key_changes") or []
    if changes:
        lines.append("**关键变化**")
        for item in changes[:6]:
            lines.append(f"- **{item.get('topic', '变化')}**: {item.get('evidence', '')}。{item.get('interpretation', '')}")
        lines.append("")

    trend_evolution = result.get("trend_evolution") or []
    if trend_evolution:
        lines.append("**趋势演化**")
        for item in trend_evolution[:5]:
            lines.append(
                f"- **{item.get('topic', '趋势')}**: 短期 {item.get('short_term', '')}；"
                f"中期 {item.get('medium_term', '')}。{item.get('read_through', '')}"
            )
        lines.append("")

    assets = result.get("asset_implications") or []
    if assets:
        lines.append("**资产影响**")
        for item in assets[:8]:
            lines.append(f"- `{item.get('asset', '')}` {item.get('bias', '')}: {item.get('reason', '')}")
        lines.append("")

    watchlist = result.get("watchlist") or []
    if watchlist:
        lines.append("**Watchlist**")
        for item in watchlist[:6]:
            lines.append(f"- {item.get('item', '')}: {item.get('trigger', '')}。{item.get('why', '')}")
        lines.append("")

    updates = result.get("hypothesis_updates") or []
    if updates:
        lines.append("**假设更新**")
        for item in updates[:6]:
            lines.append(f"- {item.get('hypothesis', '')}: `{item.get('status', '')}` — {item.get('reason', '')}")
        lines.append("")

    warnings = result.get("data_warnings") or []
    if warnings:
        lines.append("**数据提醒**")
        for item in warnings[:5]:
            lines.append(f"- {item}")
        lines.append("")

    confidence = result.get("confidence")
    if confidence is not None:
        lines.append(f"置信度：{float(confidence):.0%}")

    return "\n".join(lines)


def build_ai_trend_report(context=None):
    context = context or build_daily_context()
    result = _call_ai(context)
    if not result:
        return None, build_context_markdown(context), context
    return result, _render_ai_markdown(result, context), context


def save_ai_trend_report(session="ai_daily"):
    result, markdown, context = build_ai_trend_report()
    report_date = app_now().strftime("%Y-%m-%d")
    context["signal_review"] = save_signal_snapshots(signal_date=report_date)
    if result:
        title = result.get("title") or f"{report_date} AI 趋势日报"
        summary = result.get("executive_summary", "")
        context_payload = {"daily_context": context, "ai_result": result}
    else:
        title = f"{report_date} 每日研究包"
        summary = "AI未配置或调用失败，已保存本地研究包。"
        context_payload = {"daily_context": context, "ai_result": None}

    upsert_daily_report(
        report_date=report_date,
        session=session,
        title=title,
        summary=summary,
        context=context_payload,
        raw_markdown=markdown,
    )
    return result, markdown, context
