"""Optional AI feedback for a user-authored trade plan.

This service is deliberately not a trade-approval or execution engine. It
summarises evidence and conflicts in a stored market snapshot, then saves the
structured feedback for later post-trade comparison.
"""
import json
import logging
import os

from db.repository import (
    get_trade_note,
    insert_trade_plan_feedback,
    update_trade_note_context,
)
from services.ai_json import ai_thinking_options, extract_response_content, parse_ai_json
from services.trade_plan_context import build_trade_plan_snapshot

logger = logging.getLogger("trade_plan_feedback")
PROMPT_VERSION = "trade-plan-feedback-v1"

SYSTEM_PROMPT = """你是一个克制的加密货币交易计划环境反馈助手。
用户自己决定是否下单；你绝不能批准、否决、催促、预测确定收益，或给出自动
下单、加仓、平仓指令。你的职责是把用户已写好的计划与“计划当时可获得”的
宏观、新闻、数据新鲜度和实时 K 线事实逐项对照。

特别规则：
1. 宏观长期偏多并不自动否定短期回调空单。必须根据 trade_type、expected_horizon、
   macro_horizon 和 analysis_timeframe 判断它是否属于逆趋势战术交易。
2. 没有结构化技术数据或数据过期时，明确写 insufficient_data，不得编造。
3. 只评价计划条件是否清晰、证据是否一致、需要验证什么；不要输出买卖建议。
4. 把事实、推断和缺失数据区分开，引用给定快照中的来源/指标名称。

只返回 JSON，字段必须完整：
{
  "summary_cn": "不超过160字的中文总结",
  "plan_classification": "trend_following|countertrend_tactical|event_driven|range|unclear",
  "macro_alignment": "supportive|neutral|headwind|mixed|insufficient_data",
  "realtime_alignment": "supportive|neutral|headwind|mixed|insufficient_data",
  "technical_alignment": "supportive|neutral|headwind|mixed|insufficient_data",
  "time_horizon_assessment": "计划周期与宏观/技术周期的关系",
  "supporting_evidence": ["仅限快照中存在的证据"],
  "contradicting_evidence": ["仅限快照中存在的证据"],
  "conditions_to_validate": ["执行前或持仓中需要确认的客观条件"],
  "invalidation_checks": ["价格或逻辑失效检查；没有足够数据则说明"],
  "time_stop_checks": ["时间止损/计划过期检查"],
  "risk_flags": ["风险提示"],
  "data_gaps": ["缺失、过期或不可靠的数据"],
  "confidence": 0.0
}
"""


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item)[:1000] for item in value if item is not None]
    if isinstance(value, str):
        return [item.strip()[:1000] for item in value.replace("，", ",").split(",") if item.strip()]
    return [str(value)[:1000]]


def _alignment(value):
    allowed = {"supportive", "neutral", "headwind", "mixed", "insufficient_data"}
    value = str(value or "insufficient_data").strip().lower()
    return value if value in allowed else "insufficient_data"


def _normalise(result):
    if not isinstance(result, dict):
        raise ValueError("AI plan feedback is not an object")
    classification = str(result.get("plan_classification") or "unclear").strip().lower()
    if classification not in {"trend_following", "countertrend_tactical", "event_driven", "range", "unclear"}:
        classification = "unclear"
    try:
        confidence = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "summary_cn": str(result.get("summary_cn") or "")[:600],
        "plan_classification": classification,
        "macro_alignment": _alignment(result.get("macro_alignment")),
        "realtime_alignment": _alignment(result.get("realtime_alignment")),
        "technical_alignment": _alignment(result.get("technical_alignment")),
        "time_horizon_assessment": str(result.get("time_horizon_assessment") or "")[:1200],
        "supporting_evidence": _as_list(result.get("supporting_evidence")),
        "contradicting_evidence": _as_list(result.get("contradicting_evidence")),
        "conditions_to_validate": _as_list(result.get("conditions_to_validate")),
        "invalidation_checks": _as_list(result.get("invalidation_checks")),
        "time_stop_checks": _as_list(result.get("time_stop_checks")),
        "risk_flags": _as_list(result.get("risk_flags")),
        "data_gaps": _as_list(result.get("data_gaps")),
        "confidence": confidence,
    }


def _parse_snapshot(raw):
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError):
        return None


def _plan_payload(note):
    fields = (
        "id", "venue", "symbol", "side", "order_id", "thesis", "setup", "entry_order_type",
        "entry_price", "trigger_price", "planned_quantity", "entry_trigger", "stop_price",
        "target_price", "risk_note", "trade_type", "expected_horizon", "macro_horizon",
        "analysis_timeframe", "time_stop", "plan_status", "plan_expires_at",
        "created_at", "context_captured_at",
    )
    return {field: note[field] for field in fields if field in note.keys()}


def _prompt_json(payload):
    """Keep the prompt valid JSON even when a snapshot is unusually large."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= 18000:
        return text

    compact = dict(payload)
    for name in ("plan_creation_snapshot", "evaluation_snapshot"):
        snapshot = dict(compact.get(name) or {})
        macro = dict(snapshot.get("macro") or {})
        macro["relevant_composite_signals"] = (macro.get("relevant_composite_signals") or [])[:4]
        macro["recent_market_moves"] = (macro.get("recent_market_moves") or [])[:8]
        snapshot["macro"] = macro
        news = dict(snapshot.get("news") or {})
        news["articles"] = (news.get("articles") or [])[:4]
        news["important_clusters"] = (news.get("important_clusters") or [])[:4]
        snapshot["news"] = news
        snapshot["data_health"] = (snapshot.get("data_health") or [])[:10]
        compact[name] = snapshot
    text = json.dumps(compact, ensure_ascii=False, default=str)
    if len(text) <= 18000:
        return text

    def minimal_snapshot(snapshot):
        snapshot = snapshot or {}
        macro = snapshot.get("macro") or {}
        news = snapshot.get("news") or {}
        return {
            "captured_at": snapshot.get("captured_at"),
            "plan_identity": snapshot.get("plan_identity"),
            "live_market": snapshot.get("live_market"),
            "asset_bias": macro.get("asset_bias"),
            "signals": [
                {key: item.get(key) for key in ("name", "direction", "level", "summary")}
                for item in (macro.get("relevant_composite_signals") or [])[:3]
            ],
            "news": [
                {key: item.get(key) for key in ("title", "summary_cn", "severity", "direction", "source")}
                for item in (news.get("articles") or [])[:3]
            ],
            "data_health": [
                {key: item.get(key) for key in ("source", "status", "age_hours")}
                for item in (snapshot.get("data_health") or [])[:8]
            ],
            "collection_errors": snapshot.get("collection_errors") or [],
            "context_trimmed": True,
        }

    plan = dict(compact.get("plan") or {})
    for field in ("thesis", "setup", "entry_trigger", "risk_note"):
        if plan.get(field):
            plan[field] = str(plan[field])[:1500]
    return json.dumps(
        {
            "plan": plan,
            "plan_creation_snapshot": minimal_snapshot(compact.get("plan_creation_snapshot")),
            "evaluation_snapshot": minimal_snapshot(compact.get("evaluation_snapshot"))
            if compact.get("refresh_context") else {"same_as_plan_creation": True},
            "refresh_context": bool(compact.get("refresh_context")),
        },
        ensure_ascii=False,
        default=str,
    )


def generate_trade_plan_feedback(note_id, refresh_context=False):
    """Generate optional feedback; never changes an exchange order or position.

    ``refresh_context`` captures a new context only for this feedback record.
    It deliberately leaves the original plan snapshot untouched.
    """
    note = get_trade_note(note_id)
    if not note:
        raise ValueError(f"trade note not found: {note_id}")

    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    baseline_context = _parse_snapshot(note["market_snapshot_json"])
    if baseline_context is None:
        baseline_context = build_trade_plan_snapshot(note)
        update_trade_note_context(note_id, baseline_context, baseline_context.get("captured_at", ""))

    evaluation_context = build_trade_plan_snapshot(note) if refresh_context else baseline_context
    payload = {
        "plan": _plan_payload(note),
        "plan_creation_snapshot": baseline_context,
        "evaluation_snapshot": evaluation_context if refresh_context else {"same_as_plan_creation": True},
        "refresh_context": bool(refresh_context),
    }
    base = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("OPENAI_MODEL", "deepseek-chat")
    try:
        max_tokens = max(900, int(os.getenv("AI_TRADE_PLAN_MAX_TOKENS", "2600")))
    except ValueError:
        max_tokens = 2600

    try:
        import openai

        client = openai.OpenAI(api_key=key, base_url=base)
        request = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "请对以下用户已写好的交易计划做环境反馈，不要批准或阻止交易：\n"
                    + _prompt_json(payload),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        request.update(ai_thinking_options(model=model, base_url=base))
        response = client.chat.completions.create(**request)
        raw, metadata = extract_response_content(response)
        logger.info("AI trade-plan feedback response metadata=%s", metadata)
        result = _normalise(parse_ai_json(raw))
    except Exception:
        logger.exception("AI trade-plan feedback failed for note=%s", note_id)
        raise

    insert_trade_plan_feedback(
        note_id=note_id,
        model=model,
        prompt_version=PROMPT_VERSION,
        status="completed",
        context=evaluation_context,
        feedback=result,
        summary_cn=result["summary_cn"],
        plan_classification=result["plan_classification"],
        macro_alignment=result["macro_alignment"],
        realtime_alignment=result["realtime_alignment"],
        technical_alignment=result["technical_alignment"],
        risk_flags=result["risk_flags"],
        data_gaps=result["data_gaps"],
    )
    return result
