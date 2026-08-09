"""Independent AI shadow plans for local paper-trading research.

The generator receives the symbol and a fresh factual dashboard snapshot, but
never the user's thesis, direction, entry, stop, target, quantity, or real
order.  A deterministic comparison is generated only *after* the shadow plan
has been persisted.  This keeps the later scorecard useful instead of merely
measuring how closely the model copied the user.

All resulting orders are local rows in ``paper_orders``.  This module contains
no exchange credentials and makes no request that can create, amend, cancel,
or transfer an exchange order.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

from db.repository import (
    get_trade_note,
    insert_ai_shadow_plan,
    insert_paper_order,
    insert_paper_order_event,
    update_ai_shadow_plan_comparison,
)
from services.ai_shadow_config import env_int as _env_int, shadow_constraints
from services.ai_json import ai_thinking_options, extract_response_content, parse_ai_json
from services.time_utils import app_now
from services.trade_plan_context import build_trade_plan_snapshot


logger = logging.getLogger("ai_shadow_plan")
PROMPT_VERSION = "ai-shadow-plan-v1"

ACTIONABLE_DECISIONS = {"limit", "trigger_limit", "trigger_market", "market"}
DECISIONS = ACTIONABLE_DECISIONS | {"no_trade", "watch"}
SIDES = {"long", "short", "flat"}


SYSTEM_PROMPT = """你是一个仅用于研究评估的加密货币“影子账户”计划生成器。
你看不到用户的交易计划、方向、入场价、仓位、理由或真实订单；只根据给出的当前
市场、宏观、新闻、数据新鲜度和技术快照，独立生成一份虚拟计划。这个计划绝不用于
真实下单，也不能保证收益。

你必须允许并优先考虑 no_trade 或 watch：当前风险收益比、数据质量或技术位置不合适
时，不要为了给出交易而交易。远离当前价的限价单可以存在，但必须给出明确到期时间。

硬性规则：
1. 只使用输入快照已有的事实和价格范围；数据不足时选择 no_trade/watch，不得编造行情。
2. action 为 limit、trigger_limit、trigger_market、market 时，必须给出 long 或 short、
   有效止损和目标价，且风险收益比满足输入中的最低要求。
3. limit/trigger_limit 必须给 entry_price；trigger_* 必须给 trigger_price 与
   trigger_direction（above 或 below）。market 的 entry_price 会被系统替换为当前价格。
4. risk_budget_pct 是虚拟账户单笔风险占比的小数，例如 0.005 表示 0.5%，不得超过约束。
5. expires_hours 是挂单失效时间；time_stop_hours 是成交后的最长持仓时间。
6. 输出是虚拟研究记录，不要写成催促、批准或真实执行指令。

只返回完整 JSON：
{
  "decision": "no_trade|watch|limit|trigger_limit|trigger_market|market",
  "side": "long|short|flat",
  "analysis_timeframe": "5m|15m|1H|4H|1D",
  "expected_horizon": "简短中文持仓周期",
  "entry_price": 0.0,
  "trigger_price": 0.0,
  "trigger_direction": "above|below|none",
  "stop_price": 0.0,
  "target_price": 0.0,
  "risk_budget_pct": 0.0,
  "expires_hours": 0,
  "time_stop_hours": 0,
  "rationale": "不超过500字，区分事实与推断",
  "conditions": ["需要持续满足的客观条件"],
  "evidence": ["仅输入快照中存在的证据"],
  "data_gaps": ["缺失或过期数据"],
  "no_trade_reason": "若不交易或观察，说明原因；否则为空",
  "confidence": 0.0
}
"""


def _positive_number(value):
    try:
        value = float(value)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _as_list(value, limit=12):
    if value is None:
        return []
    if isinstance(value, list):
        values = value
    elif isinstance(value, str):
        values = [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
    else:
        values = [value]
    return [str(item)[:1000] for item in values if item is not None][:limit]


def _current_price(snapshot):
    live = dict((snapshot or {}).get("live_market") or {})
    return _positive_number((live.get("last_candle") or {}).get("close"))


def _compact_snapshot(snapshot):
    """Bound prompt size without hiding the factual inputs used by the model."""
    snapshot = dict(snapshot or {})
    macro = dict(snapshot.get("macro") or {})
    news = dict(snapshot.get("news") or {})
    compact = {
        "snapshot_version": snapshot.get("snapshot_version"),
        "captured_at": snapshot.get("captured_at"),
        "plan_identity": snapshot.get("plan_identity"),
        "live_market": snapshot.get("live_market"),
        "macro": {
            "asset_bias": macro.get("asset_bias"),
            "asset_bias_is_crypto_proxy": macro.get("asset_bias_is_crypto_proxy"),
            "relevant_composite_signals": (macro.get("relevant_composite_signals") or [])[:6],
            "recent_market_moves": (macro.get("recent_market_moves") or [])[:8],
        },
        "news": {
            "articles": (news.get("articles") or [])[:6],
            "important_clusters": (news.get("important_clusters") or [])[:6],
        },
        "data_health": (snapshot.get("data_health") or [])[:12],
        "collection_errors": (snapshot.get("collection_errors") or [])[:12],
    }
    return compact


def build_independent_shadow_snapshot(symbol):
    """Build a fresh context without copying a user's decision fields.

    The only plan-specific input is the instrument being researched.  The
    generator's timeframe is its own configured default, not the user's form
    selection, so direction and entry selection remain independent.
    """
    constraints = shadow_constraints()
    clean_symbol = str(symbol or "").strip().upper()
    if not clean_symbol:
        raise ValueError("交易对为空，无法生成 AI 影子计划")
    snapshot = build_trade_plan_snapshot(
        {
            "venue": "OKX",
            "symbol": clean_symbol,
            "analysis_timeframe": constraints["analysis_timeframe"],
            "side": "",
            "trade_type": "",
            "expected_horizon": "",
            "macro_horizon": "",
            "entry_order_type": "",
            "entry_price": None,
            "trigger_price": None,
            "planned_quantity": None,
            "plan_status": "",
            "order_id": "",
        }
    )
    identity = dict(snapshot.get("plan_identity") or {})
    snapshot["plan_identity"] = {
        "symbol": identity.get("symbol", clean_symbol),
        "asset": identity.get("asset", "CRYPTO"),
        "analysis_timeframe": constraints["analysis_timeframe"],
        "generation_mode": "independent_ai_shadow",
        "user_plan_fields_included": False,
    }
    snapshot["snapshot_version"] = "ai-shadow-context-v1"
    return snapshot


def _prompt_payload(symbol, snapshot, constraints):
    return {
        "instrument": symbol,
        "important": "这是一份独立的虚拟影子计划；未提供任何用户交易计划字段。",
        "constraints": {
            "allowed_decisions": sorted(DECISIONS),
            "max_risk_pct": constraints["max_risk_pct"],
            "min_risk_pct": constraints["min_risk_pct"],
            "min_risk_reward": constraints["min_risk_reward"],
            "default_expiry_hours": constraints["default_expiry_hours"],
            "default_time_stop_hours": constraints["default_time_stop_hours"],
            "max_expiry_hours": constraints["max_expiry_hours"],
            "max_time_stop_hours": constraints["max_time_stop_hours"],
            "current_price": _current_price(snapshot),
        },
        "market_snapshot": _compact_snapshot(snapshot),
    }


def _prompt_json(payload):
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= 18000:
        return text
    compact = dict(payload)
    snapshot = dict(compact.get("market_snapshot") or {})
    macro = dict(snapshot.get("macro") or {})
    macro["relevant_composite_signals"] = (macro.get("relevant_composite_signals") or [])[:3]
    macro["recent_market_moves"] = (macro.get("recent_market_moves") or [])[:4]
    snapshot["macro"] = macro
    news = dict(snapshot.get("news") or {})
    news["articles"] = (news.get("articles") or [])[:3]
    news["important_clusters"] = (news.get("important_clusters") or [])[:3]
    snapshot["news"] = news
    snapshot["data_health"] = (snapshot.get("data_health") or [])[:8]
    compact["market_snapshot"] = snapshot
    return json.dumps(compact, ensure_ascii=False, default=str)[:18000]


def _risk_reward(side, entry_price, stop_price, target_price):
    if not all((side, entry_price, stop_price, target_price)):
        return None
    if side == "long":
        risk = entry_price - stop_price
        reward = target_price - entry_price
    else:
        risk = stop_price - entry_price
        reward = entry_price - target_price
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def _normalise_result(raw, snapshot, constraints):
    if not isinstance(raw, dict):
        raise ValueError("AI 影子计划不是 JSON 对象")
    decision = str(raw.get("decision") or "no_trade").strip().lower().replace("-", "_").replace(" ", "_")
    decision = {
        "noaction": "no_trade",
        "no_action": "no_trade",
        "observe": "watch",
    }.get(decision, decision)
    if decision not in DECISIONS:
        raise ValueError(f"AI 返回了不支持的决策：{decision or '空'}")
    side = str(raw.get("side") or "flat").strip().lower()
    side = {"buy": "long", "sell": "short", "none": "flat", "neutral": "flat"}.get(side, side)
    if side not in SIDES:
        side = "flat"
    timeframe = str(raw.get("analysis_timeframe") or constraints["analysis_timeframe"]).strip()
    timeframe = {"5M": "5m", "15M": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}.get(timeframe, timeframe)
    if timeframe not in {"5m", "15m", "1H", "4H", "1D"}:
        timeframe = constraints["analysis_timeframe"]
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5

    result = {
        "decision": decision,
        "side": side,
        "analysis_timeframe": timeframe,
        "expected_horizon": str(raw.get("expected_horizon") or "未设定")[:120],
        "entry_price": _positive_number(raw.get("entry_price")),
        "trigger_price": _positive_number(raw.get("trigger_price")),
        "trigger_direction": str(raw.get("trigger_direction") or "none").strip().lower(),
        "stop_price": _positive_number(raw.get("stop_price")),
        "target_price": _positive_number(raw.get("target_price")),
        "rationale": str(raw.get("rationale") or raw.get("no_trade_reason") or "")[:2500],
        "conditions": _as_list(raw.get("conditions")),
        "evidence": _as_list(raw.get("evidence")),
        "data_gaps": _as_list(raw.get("data_gaps")),
        "no_trade_reason": str(raw.get("no_trade_reason") or "")[:1200],
        "confidence": confidence,
    }
    result["trigger_direction"] = {
        "up": "above", "upward": "above", "down": "below", "downward": "below",
    }.get(result["trigger_direction"], result["trigger_direction"])
    if result["trigger_direction"] not in {"above", "below", "none"}:
        result["trigger_direction"] = "none"

    if decision in {"no_trade", "watch"}:
        # An observation may have a directional hypothesis, but it must not be
        # represented as a pending executable order.
        result.update(
            {
                "entry_price": None,
                "trigger_price": None,
                "trigger_direction": "none",
                "stop_price": None,
                "target_price": None,
                "risk_budget_pct": None,
                "risk_reward": None,
                "planned_quantity": None,
                "planned_notional_usd": None,
                "initial_risk_usd": None,
                "expires_hours": None,
                "time_stop_hours": None,
            }
        )
        if not result["rationale"]:
            raise ValueError("AI 选择观望但没有说明原因")
        return result

    current_price = _current_price(snapshot)
    if current_price is None:
        raise ValueError("当前 OKX 价格不可用，拒绝创建可执行的 AI 虚拟订单")
    if side not in {"long", "short"}:
        raise ValueError("可执行的 AI 虚拟订单必须明确 long 或 short")
    if decision == "market":
        result["entry_price"] = current_price
    elif decision == "trigger_market":
        if result["trigger_price"] is None:
            raise ValueError("条件市价单缺少触发价")
        result["entry_price"] = result["trigger_price"]
    elif result["entry_price"] is None:
        raise ValueError("限价/条件限价 AI 计划缺少入场价")
    if decision.startswith("trigger_"):
        if result["trigger_price"] is None or result["trigger_direction"] not in {"above", "below"}:
            raise ValueError("条件单必须具有触发价和触发方向")

    entry = result["entry_price"]
    stop = result["stop_price"]
    target = result["target_price"]
    ratio = _risk_reward(side, entry, stop, target)
    if ratio is None:
        raise ValueError("止损、目标价与方向不一致，无法计算风险收益比")
    if ratio < constraints["min_risk_reward"]:
        raise ValueError(
            f"风险收益比 {ratio:.2f} 低于影子账户最低要求 {constraints['min_risk_reward']:.2f}"
        )

    try:
        requested_risk_pct = float(raw.get("risk_budget_pct", constraints["min_risk_pct"]))
    except (TypeError, ValueError):
        requested_risk_pct = constraints["min_risk_pct"]
    risk_pct = max(constraints["min_risk_pct"], min(constraints["max_risk_pct"], requested_risk_pct))
    risk_usd = constraints["virtual_equity_usd"] * risk_pct
    per_unit_risk = abs(entry - stop)
    quantity = risk_usd / per_unit_risk
    max_quantity = constraints["max_notional_usd"] / entry
    quantity = min(quantity, max_quantity)
    notional = quantity * entry
    actual_risk = quantity * per_unit_risk
    if quantity <= 0 or actual_risk <= 0:
        raise ValueError("虚拟仓位计算失败")

    expires_hours = _env_int_value(
        raw.get("expires_hours"), constraints["default_expiry_hours"], 1, constraints["max_expiry_hours"]
    )
    time_stop_hours = _env_int_value(
        raw.get("time_stop_hours"), constraints["default_time_stop_hours"], 1, constraints["max_time_stop_hours"]
    )
    result.update(
        {
            "risk_budget_pct": actual_risk / constraints["virtual_equity_usd"],
            "risk_reward": ratio,
            "planned_quantity": quantity,
            "planned_notional_usd": notional,
            "initial_risk_usd": actual_risk,
            "expires_hours": expires_hours,
            "time_stop_hours": time_stop_hours,
        }
    )
    if not result["rationale"]:
        raise ValueError("AI 可执行计划缺少理由")
    return result


def _env_int_value(value, default, minimum, maximum):
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _user_side(value):
    value = str(value or "").strip().lower()
    if value in {"long", "buy"}:
        return "long"
    if value in {"short", "sell"}:
        return "short"
    return "flat"


def compare_user_plan_to_shadow_plan(note, shadow):
    """Build a fact-only comparison after the AI plan has already been frozen."""
    user_side = _user_side(note["side"])
    user_entry = _positive_number(note["entry_price"])
    user_stop = _positive_number(note["stop_price"])
    user_target = _positive_number(note["target_price"])
    user_rr = _risk_reward(user_side, user_entry, user_stop, user_target)
    shadow_side = shadow.get("side", "flat")
    shadow_decision = shadow.get("decision", "no_trade")
    if shadow_decision in {"no_trade", "watch"}:
        direction_relation = "AI 选择不建立可执行仓位"
    elif user_side == "flat":
        direction_relation = "用户计划方向未明确"
    elif user_side == shadow_side:
        direction_relation = "同向"
    else:
        direction_relation = "方向相反"

    differences = []
    if shadow_decision in {"no_trade", "watch"}:
        differences.append("AI 选择观望/不交易；应重点复盘这种克制是否优于强行参与。")
    if shadow_decision in ACTIONABLE_DECISIONS and user_entry and shadow.get("entry_price"):
        entry_delta = (shadow["entry_price"] / user_entry - 1) * 100
        differences.append(f"两者计划入场价相差 {entry_delta:+.2f}% 。")
    else:
        entry_delta = None
    if user_rr is None:
        differences.append("用户计划缺少可计算的入场、止损或目标价，暂不能和 AI 做风险收益比比较。")
    if shadow.get("risk_reward") is not None:
        differences.append(f"AI 影子计划风险收益比为 {shadow['risk_reward']:.2f}。")
    if user_rr is not None:
        differences.append(f"用户计划风险收益比为 {user_rr:.2f}。")

    shared_risks = []
    if not user_stop:
        shared_risks.append("用户计划没有结构化价格止损。")
    if shadow.get("data_gaps"):
        shared_risks.append("AI 快照存在数据缺口，应降低对比较结论的信任。")
    if shadow_decision in {"no_trade", "watch"}:
        summary = f"AI 独立判断为 {shadow_decision}；用户计划方向为 {user_side or '未明确'}。"
    else:
        summary = (
            f"AI 为 {shadow_side} {shadow_decision}，与用户计划关系：{direction_relation}。"
        )
    return {
        "comparison_version": "ai-shadow-comparison-v1",
        "compared_at": app_now().isoformat(),
        "independence": "AI 生成阶段未读取用户的方向、价格、仓位、理由或真实订单。",
        "user_plan": {
            "note_id": note["id"],
            "symbol": note["symbol"],
            "side": user_side,
            "entry_order_type": note["entry_order_type"],
            "entry_price": user_entry,
            "stop_price": user_stop,
            "target_price": user_target,
            "risk_reward": user_rr,
            "expected_horizon": note["expected_horizon"],
        },
        "ai_shadow": {
            "decision": shadow_decision,
            "side": shadow_side,
            "entry_price": shadow.get("entry_price"),
            "stop_price": shadow.get("stop_price"),
            "target_price": shadow.get("target_price"),
            "risk_reward": shadow.get("risk_reward"),
            "expected_horizon": shadow.get("expected_horizon"),
        },
        "direction_relation": direction_relation,
        "entry_price_delta_pct": entry_delta,
        "summary_cn": summary,
        "differences": differences,
        "shared_risks": shared_risks,
    }


def _entry_fill_price(entry_price, side, slippage_bps):
    multiplier = 1 + (slippage_bps / 10_000)
    return entry_price * multiplier if side == "long" else entry_price / multiplier


def _create_paper_order(shadow_plan_id, plan, constraints, now):
    decision = plan["decision"]
    status = "waiting_trigger" if decision.startswith("trigger_") else "pending"
    fill_price = None
    filled_at = ""
    entry_fee = 0.0
    if decision == "market":
        status = "open"
        fill_price = _entry_fill_price(plan["entry_price"], plan["side"], constraints["slippage_bps"])
        filled_at = now.isoformat()
        entry_fee = fill_price * plan["planned_quantity"] * constraints["fee_bps"] / 10_000

    expires_at = ""
    if decision != "market":
        expires_at = (now + timedelta(hours=plan["expires_hours"])).isoformat()
    time_stop_at = (now + timedelta(hours=plan["time_stop_hours"])).isoformat()
    paper_order_id = insert_paper_order(
        {
            "shadow_plan_id": shadow_plan_id,
            "symbol": plan["symbol"],
            "side": plan["side"],
            "order_type": decision,
            "status": status,
            "entry_price": plan["entry_price"],
            "trigger_price": plan.get("trigger_price"),
            "trigger_direction": plan.get("trigger_direction", ""),
            "quantity": plan["planned_quantity"],
            "notional_usd": plan["planned_notional_usd"],
            "stop_price": plan["stop_price"],
            "target_price": plan["target_price"],
            "expires_at": expires_at,
            "time_stop_at": time_stop_at,
            "submitted_at": now.isoformat(),
            "filled_price": fill_price,
            "filled_at": filled_at,
            "fee_bps": constraints["fee_bps"],
            "slippage_bps": constraints["slippage_bps"],
            "entry_fee_usd": entry_fee,
            "status_reason": "AI 独立生成的本地虚拟订单",
        }
    )
    insert_paper_order_event(
        paper_order_id,
        "created",
        now.isoformat(),
        from_status="",
        to_status=status,
        price=plan["entry_price"],
        reason="AI 独立影子计划；不会发送到 OKX",
        market_snapshot={"decision": decision, "side": plan["side"]},
    )
    if status == "open":
        insert_paper_order_event(
            paper_order_id,
            "filled",
            now.isoformat(),
            from_status="pending",
            to_status="open",
            price=fill_price,
            reason="市价影子单按快照价格加固定滑点模拟成交",
        )
    return paper_order_id, status


def generate_ai_shadow_plan(note_id):
    """Create one independent AI plan and optional local virtual order.

    It intentionally does not call ``generate_trade_plan_feedback``: that
    service receives the user's written plan and has a different purpose.
    """
    note = get_trade_note(note_id)
    if not note:
        raise ValueError(f"trade note not found: {note_id}")
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    symbol = str(note["symbol"] or "").strip().upper()
    snapshot = build_independent_shadow_snapshot(symbol)
    constraints = shadow_constraints()
    base = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("OPENAI_MODEL", "deepseek-chat")
    max_tokens = _env_int("AI_SHADOW_PLAN_MAX_TOKENS", "2600", 900, 8000)

    try:
        import openai

        client = openai.OpenAI(api_key=key, base_url=base)
        request = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "请根据以下独立市场快照生成影子账户虚拟计划：\n"
                    + _prompt_json(_prompt_payload(symbol, snapshot, constraints)),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        request.update(ai_thinking_options(model=model, base_url=base))
        response = client.chat.completions.create(**request)
        raw, metadata = extract_response_content(response)
        logger.info("AI shadow-plan response metadata=%s", metadata)
        plan = _normalise_result(parse_ai_json(raw), snapshot, constraints)
    except Exception:
        logger.exception("AI shadow-plan generation failed for note=%s", note_id)
        raise

    plan["symbol"] = symbol
    now = app_now()
    plan_status = {
        "no_trade": "no_trade",
        "watch": "watch",
        "limit": "pending",
        "trigger_limit": "waiting_trigger",
        "trigger_market": "waiting_trigger",
        "market": "open",
    }[plan["decision"]]
    expires_at = (
        (now + timedelta(hours=plan["expires_hours"])).isoformat()
        if plan["decision"] in {"limit", "trigger_limit", "trigger_market"}
        else ""
    )
    time_stop_at = (
        (now + timedelta(hours=plan["time_stop_hours"])).isoformat()
        if plan["decision"] in ACTIONABLE_DECISIONS else ""
    )
    shadow_plan_id = insert_ai_shadow_plan(
        {
            "note_id": note_id,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "decision": plan["decision"],
            "status": plan_status,
            "symbol": symbol,
            "side": plan["side"],
            "analysis_timeframe": plan["analysis_timeframe"],
            "expected_horizon": plan["expected_horizon"],
            "entry_price": plan.get("entry_price"),
            "trigger_price": plan.get("trigger_price"),
            "trigger_direction": plan.get("trigger_direction", ""),
            "planned_quantity": plan.get("planned_quantity"),
            "planned_notional_usd": plan.get("planned_notional_usd"),
            "risk_budget_pct": plan.get("risk_budget_pct"),
            "initial_risk_usd": plan.get("initial_risk_usd"),
            "stop_price": plan.get("stop_price"),
            "target_price": plan.get("target_price"),
            "risk_reward": plan.get("risk_reward"),
            "expires_at": expires_at,
            "time_stop_at": time_stop_at,
            "confidence": plan["confidence"],
            "rationale": plan["rationale"],
            "decision_json": plan,
            "snapshot_json": snapshot,
        }
    )
    comparison = compare_user_plan_to_shadow_plan(note, plan)
    update_ai_shadow_plan_comparison(shadow_plan_id, comparison)
    paper_order_id = None
    if plan["decision"] in ACTIONABLE_DECISIONS:
        paper_order_id, _status = _create_paper_order(shadow_plan_id, plan, constraints, now)

    return {
        "shadow_plan_id": shadow_plan_id,
        "paper_order_id": paper_order_id,
        "decision": plan["decision"],
        "status": plan_status,
        "plan": plan,
        "comparison": comparison,
        "snapshot": snapshot,
    }
