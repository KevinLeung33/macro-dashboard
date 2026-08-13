"""真实交易的事后 AI 点评。

交易计划由用户自己记录；本模块只在交易已经发生、或用户主动点击点评后
调用 AI，不提供下单接口，也不让 AI 参与下单前的阻止/批准。
"""
import json
import logging
import os

from db.repository import get_trade_note, insert_trade_ai_review, query_trade_plan_feedback
from services.ai_json import ai_thinking_options, extract_response_content, parse_ai_json

logger = logging.getLogger("trade_review")
PROMPT_VERSION = "trade-review-v2-no-lookahead"

SYSTEM_PROMPT = """你是一个严格、克制的加密货币交易复盘助手。
你点评的是已经发生的交易或用户主动提交的交易记录，不负责下单，也不要把
观点写成确定性的买卖指令。请区分：交易理由是否自洽、执行是否符合计划、
风险是否被清楚定义、计划环境反馈与实际结果是否一致、哪些证据缺失。没有足够数据时要明确写
insufficient_data，不要编造行情、新闻或收益。
如果 review_mode=closed_trade，严格只使用 review_cutoff_at 及之前的行情、新闻和成交信息，
禁止使用截止时间之后的价格结果、新闻或走势来评价当时的决定。请评价“在当时信息集下是否合理”，
不要因为后来盈利就判定正确，也不要因为后来亏损就判定错误。

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
    plan_feedback = query_trade_plan_feedback(note_id=note["id"], limit=1)
    if plan_feedback:
        latest = plan_feedback[0]
        try:
            feedback = json.loads(latest["feedback_json"] or "{}")
        except (TypeError, ValueError):
            feedback = {}
        try:
            feedback_context = json.loads(latest["context_json"] or "{}")
        except (TypeError, ValueError):
            feedback_context = {}
        payload["plan_environment_feedback"] = {
            "created_at": latest["created_at"],
            "prompt_version": latest["prompt_version"],
            "feedback": feedback,
            "evaluation_context": feedback_context,
        }
    return payload


def _parse_time(value):
    from datetime import datetime
    text = str(value or "").replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _completed_cutoff(order_context):
    times = []
    for fill in (order_context or {}).get("fills", []):
        if str(fill.get("role") or "").lower() != "entry":
            value = fill.get("executed_at")
            parsed = _parse_time(value)
            if parsed:
                times.append((parsed, str(value)))
    if times:
        return max(times, key=lambda item: item[0])[1]
    return ""


def _historical_market_context(note, order_context, cutoff_at):
    """Fetch public candles, then hard-trim them at the exit cutoff."""
    if not cutoff_at:
        return {}
    try:
        from services.okx_readonly import OKXReadOnlyClient
        symbol = str(note["symbol"] or "").upper().replace("/", "-")
        if "-" not in symbol:
            symbol = f"{symbol[:-4]}-USDT-SWAP" if symbol.endswith("USDT") else symbol
        if not symbol.endswith(("-SWAP", "-FUTURES", "-SPOT")):
            symbol += "-SWAP"
        timeframe = str(note["analysis_timeframe"] or "1H")
        if timeframe not in {"5m", "15m", "1H", "4H", "1D"}:
            timeframe = "1H"
        rows = OKXReadOnlyClient().fetch_candles(symbol, bar=timeframe, limit=300)
        cutoff = _parse_time(cutoff_at)
        if not cutoff:
            return {}
        valid = [row for row in rows if (_parse_time(row.get("timestamp")) or cutoff) <= cutoff]
        valid = valid[-120:]
        return {
            "provider": "OKX public market API",
            "symbol": symbol,
            "bar": timeframe,
            "cutoff_at": cutoff_at,
            "future_data_excluded": True,
            "candle_count": len(valid),
            "candles": valid,
        }
    except Exception as exc:
        logger.warning("Historical market context unavailable for trade %s: %s", note["id"], exc)
        return {"cutoff_at": cutoff_at, "future_data_excluded": True, "error": str(exc)[:300]}


def review_trade_note(note_id, order_context=None, market_context=None, review_mode="holding_check"):
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

    order_context = dict(order_context or {})
    cutoff_at = str(order_context.get("review_cutoff_at") or "")
    if review_mode == "closed_trade" and not cutoff_at:
        cutoff_at = _completed_cutoff(order_context)
    if review_mode == "closed_trade":
        market_context = _historical_market_context(note, order_context, cutoff_at)
    request_payload = _note_payload(note, order_context, market_context)
    request_payload["review_mode"] = review_mode
    request_payload["review_cutoff_at"] = cutoff_at
    request_payload["lookahead_policy"] = "只允许使用截止时间及之前的数据；未来数据已排除"
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
        review_mode=review_mode,
        review_cutoff_at=cutoff_at,
        evidence={"order_context": order_context, "market_context": market_context or {}, "cutoff_at": cutoff_at},
    )
    result["review_mode"] = review_mode
    result["review_cutoff_at"] = cutoff_at
    return result
