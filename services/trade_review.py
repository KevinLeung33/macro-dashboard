"""真实交易的事后 AI 点评。

交易计划由用户自己记录；本模块只在交易已经发生、或用户主动点击点评后
调用 AI，不提供下单接口，也不让 AI 参与下单前的阻止/批准。
"""
import json
import logging
import os

from db.repository import get_trade_note, insert_trade_ai_review
from services.ai_json import ai_thinking_options, extract_response_content, parse_ai_json

logger = logging.getLogger("trade_review")
PROMPT_VERSION = "trade-review-v1"

SYSTEM_PROMPT = """你是一个严格、克制的加密货币交易复盘助手。
你点评的是已经发生的交易或用户主动提交的交易记录，不负责下单，也不要把
观点写成确定性的买卖指令。请区分：交易理由是否自洽、执行是否符合计划、
风险是否被清楚定义、哪些证据缺失。没有足够数据时要明确写
insufficient_data，不要编造行情、新闻或收益。

只返回 JSON，字段必须完整：
{
  "verdict": "reasonable|mixed|unreasonable|insufficient_data",
  "summary_cn": "不超过120字的中文总结",
  "thesis_consistency": "交易理由与实际记录是否一致",
  "execution_review": "成交执行与计划的复盘",
  "strengths": ["做得好的地方"],
  "weaknesses": ["需要改进的地方"],
  "risk_flags": ["风险提示"],
  "evidence": ["支持判断的已知证据；没有就写数据不足"],
  "post_trade_questions": ["下一次复盘要回答的问题"],
  "confidence": 0.0
}
"""


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str):
        return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
    return [str(value)]


def _normalise(result):
    if not isinstance(result, dict):
        raise ValueError("AI trade review is not an object")
    verdict = str(result.get("verdict") or "insufficient_data").strip().lower()
    if verdict not in {"reasonable", "mixed", "unreasonable", "insufficient_data"}:
        verdict = "insufficient_data"
    try:
        confidence = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "verdict": verdict,
        "summary_cn": str(result.get("summary_cn") or "")[:500],
        "thesis_consistency": str(result.get("thesis_consistency") or "")[:1000],
        "execution_review": str(result.get("execution_review") or "")[:1000],
        "strengths": _as_list(result.get("strengths")),
        "weaknesses": _as_list(result.get("weaknesses")),
        "risk_flags": _as_list(result.get("risk_flags")),
        "evidence": _as_list(result.get("evidence")),
        "post_trade_questions": _as_list(result.get("post_trade_questions")),
        "confidence": confidence,
    }


def _note_payload(note, order_context=None, market_context=None):
    payload = {key: note[key] for key in note.keys()}
    for field in ("market_snapshot_json",):
        raw = payload.get(field) or "{}"
        try:
            payload[field] = json.loads(raw)
        except (TypeError, ValueError):
            payload[field] = raw
    if order_context:
        payload["executed_order_context"] = order_context
    if market_context:
        payload["market_context"] = market_context
    return payload


def review_trade_note(note_id, order_context=None, market_context=None):
    """主动点评一条交易记录，并把结构化结果写入 trade_ai_reviews。"""
    note = get_trade_note(note_id)
    if not note:
        raise ValueError(f"trade note not found: {note_id}")

    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    base = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("OPENAI_MODEL", "deepseek-chat")
    try:
        max_tokens = max(800, int(os.getenv("AI_TRADE_REVIEW_MAX_TOKENS", "2400")))
    except ValueError:
        max_tokens = 2400

    request_payload = _note_payload(note, order_context, market_context)
    try:
        import openai

        client = openai.OpenAI(api_key=key, base_url=base)
        request = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "请点评以下已记录交易，不要提出自动下单动作：\n"
                    + json.dumps(request_payload, ensure_ascii=False, default=str)[:12000],
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        request.update(ai_thinking_options(model=model, base_url=base))
        response = client.chat.completions.create(**request)
        raw, metadata = extract_response_content(response)
        logger.info("AI trade review response metadata=%s", metadata)
        result = _normalise(parse_ai_json(raw))
    except Exception:
        logger.exception("AI trade review failed for note=%s", note_id)
        raise

    insert_trade_ai_review(
        note_id=note_id,
        order_id=note["order_id"],
        model=model,
        prompt_version=PROMPT_VERSION,
        status="completed",
        review=result,
        summary_cn=result["summary_cn"],
        strengths=result["strengths"],
        weaknesses=result["weaknesses"],
        risk_flags=result["risk_flags"],
        execution_review=result["execution_review"],
    )
    return result
