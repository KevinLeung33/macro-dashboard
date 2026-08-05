"""AI trend report built from the daily research context."""
import json
import logging
import os
import time

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
语言使用中文，表达要短而明确。
输出优先给出文字判断，不要把输入中的数字原样堆成清单：executive_summary 必须明确说明当前处于什么状态、短期与中期是否一致、最重要的驱动是什么；key_changes 和 trend_evolution 必须写出“为什么重要”和“这意味着什么”。数字只作为结论后的证据，不要替代结论。"""


def _compact_context(context):
    """Keep the payload small and stable before sending it to the model."""
    research = context.get("research_context") or {}
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
        "research_context": {
            "active_hypotheses": research.get("active_hypotheses", [])[:6],
            "recent_viewpoints": research.get("recent_viewpoints", [])[:6],
            "active_watchlist": research.get("active_watchlist", [])[:6],
        },
    }


def _call_ai(context):
    key = os.getenv("OPENAI_API_KEY")
    base = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("OPENAI_MODEL", "deepseek-chat")
    try:
        max_tokens = int(os.getenv("AI_DAILY_MAX_TOKENS", "8192"))
    except ValueError as exc:
        from services.runtime_controls import notify_runtime_error

        notify_runtime_error(
            "daily_report",
            exc,
            "日报已回退到规则结论版；请检查 AI_DAILY_MAX_TOKENS 配置",
        )
        return None
    if not key or "sk-your" in key:
        from services.runtime_controls import notify_runtime_error

        notify_runtime_error(
            "daily_report",
            "OPENAI_API_KEY is not configured or still uses the placeholder",
            "日报已回退到规则结论版；请检查 systemd 使用的 .env 文件",
        )
        return None

    try:
        import openai

        client = openai.OpenAI(api_key=key, base_url=base)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(_compact_context(context), ensure_ascii=False, default=str),
            },
        ]
        last_error = None
        # DeepSeek/OpenAI-compatible gateways can occasionally return an empty
        # body in JSON mode. Retry once without response_format so the local
        # parser can still extract a JSON object from ordinary model text.
        for attempt, use_json_mode in enumerate((True, False), start=1):
            try:
                request = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.2 if use_json_mode else 0.1,
                    "max_tokens": max_tokens if use_json_mode else min(max_tokens * 2, 8192),
                }
                if use_json_mode:
                    request["response_format"] = {"type": "json_object"}
                response = client.chat.completions.create(**request)
                if not response.choices:
                    raise ValueError("AI response has no choices")
                choice = response.choices[0]
                content = getattr(choice.message, "content", None)
                finish_reason = getattr(choice, "finish_reason", None)
                if isinstance(content, list):
                    content = "".join(
                        str(part.get("text", ""))
                        for part in content if isinstance(part, dict)
                    )
                if not content or not str(content).strip():
                    raise ValueError(
                        f"empty AI response (attempt={attempt}, "
                        f"json_mode={use_json_mode}, finish_reason={finish_reason})"
                    )
                result = parse_ai_json(content)
                if not isinstance(result, dict):
                    raise ValueError("AI daily report response is not an object")
                logger.info(
                    "AI daily report succeeded (attempt=%s, json_mode=%s, finish_reason=%s)",
                    attempt, use_json_mode, finish_reason,
                )
                return result
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "AI daily report attempt %s failed (json_mode=%s): %s",
                    attempt, use_json_mode, exc,
                )
                if attempt == 1:
                    time.sleep(1)
        raise last_error or RuntimeError("AI daily report failed")
    except Exception as exc:
        logger.warning("AI daily report failed after retries; using rule fallback: %s", exc)
        from services.runtime_controls import notify_runtime_error

        notify_runtime_error(
            "daily_report",
            exc,
            "AI 日报已回退到规则结论版，数据任务继续运行",
        )
        return None


def _pct_text(value):
    if value is None:
        return "数据不足"
    return f"{value:+.2f}%"


def _direction_text(value):
    if value is None or abs(value) < 0.1:
        return "基本稳定"
    return "上行" if value > 0 else "下行"


def _rule_fallback_markdown(context):
    """Produce readable conclusions when the model is unavailable."""
    lines = [f"**宏观市场日报（规则结论版）**", ""]
    lines.append(f"生成时间：{context.get('generated_at', '')}")
    lines.append("")

    trends = context.get("multi_window_trends", [])[:8]
    signals = context.get("composite_signals", [])[:5]
    clusters = context.get("important_clusters", [])[:5]
    alerts = context.get("alerts", [])[:4]

    lines.append("**核心判断**")
    if signals:
        lead = signals[0]
        lines.append(
            f"- 当前最值得关注的是“{lead.get('name', '组合信号')}”："
            f"{lead.get('summary') or '规则信号出现变化'}。"
            "这代表相关数据已经形成值得继续验证的风险或趋势线索，但不等同于交易结论。"
        )
    elif trends:
        lead = trends[0]
        lines.append(
            f"- 今日变化主要集中在{lead.get('name', '核心指标')}，"
            f"短期方向为{_direction_text((lead.get('windows', {}).get('7d') or {}).get('change_pct'))}；"
            "目前更适合结合中期趋势判断，而不是单看当天波动。"
        )
    else:
        lines.append("- 当前有效数据不足，暂时不能形成可靠的市场方向判断。")

    if trends:
        comparisons = []
        for item in trends[:4]:
            windows = item.get("windows", {})
            short = (windows.get("7d") or {}).get("change_pct")
            medium = (windows.get("30d") or {}).get("change_pct")
            long = (windows.get("90d") or {}).get("change_pct")
            if short is None and medium is None and long is None:
                continue
            if short is not None and medium is not None and short * medium < 0:
                read = "短期与中期方向背离，说明近期冲击可能还没有改变中期背景"
            elif medium is not None and long is not None and medium * long < 0:
                read = "中期与更长窗口方向背离，需要观察是反转还是阶段性波动"
            else:
                read = "短中期方向暂时一致，趋势信号相对更连贯"
            comparisons.append(
                f"{item.get('name', '指标')}近7D {_pct_text(short)}、近30D {_pct_text(medium)}、"
                f"近90D {_pct_text(long)}；{read}。"
            )
        if comparisons:
            lines.append(f"- 短中期对比：{' '.join(comparisons[:2])}")

    if clusters:
        event = clusters[0]
        lines.append(
            f"- 新闻传导方面，当前最重要的事件是“{event.get('title', '近期重要事件')}”，"
            f"已有{event.get('article_count', 0)}篇相关报道，主要影响{event.get('assets_impacted') or '待确认'}。"
            "后续应核对原始来源和对应市场指标，避免把报道数量当成影响强度。"
        )
    else:
        lines.append("- 新闻层面暂无足够高严重度事件，今天的判断主要依赖市场数据和组合信号。")

    if alerts:
        lines.append(
            "- 风险提醒：" + "；".join(
                f"{item.get('name', '指标')} {item.get('reason', '')}" for item in alerts[:3]
            ) + "。"
        )
    lines.append("")
    lines.append("**接下来观察**")
    if trends:
        for item in trends[:3]:
            lines.append(
                f"- {item.get('name', '指标')}：观察近7D变化是否继续扩大，"
                "以及30D/90D背景是否同步确认。"
            )
    else:
        lines.append("- 等待核心数据更新后再确认方向，当前不对缺失数据做外推。")

    evidence = build_context_markdown(context)
    evidence_lines = evidence.splitlines()
    if evidence_lines and evidence_lines[0].startswith("### "):
        evidence = "\n".join(evidence_lines[1:]).lstrip()
    lines.extend(["", "**数字证据**", evidence])
    return "\n".join(lines)


def _render_ai_markdown(result, context):
    title = result.get("title") or "AI 趋势日报"
    lines = [f"**{title}**", ""]
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
        return None, _rule_fallback_markdown(context), context
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
        summary = "AI日报未生成，已使用包含文字结论的规则版日报；请查看服务日志确认 AI 调用原因。"
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
