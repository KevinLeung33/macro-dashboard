"""Local, deterministic virtual-order lifecycle for AI shadow plans.

The module intentionally consumes only OKX public candles through the existing
GET-only client.  It has no exchange write path.  V1 uses the explicit policy
requested for the dashboard: a qualifying candle touch fills an entire limit
order at its limit price.  It does not pretend to model queue position or
partial fills; fees, directional slippage, expiry and time stops are recorded
so the eventual scorecard is less flattering than a frictionless backtest.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from db.repository import (
    get_paper_order,
    insert_paper_order_event,
    query_ai_shadow_plans,
    query_paper_orders,
    update_ai_shadow_plan_status,
    update_paper_order,
)
from services.ai_shadow_config import env_int, shadow_constraints
from services.okx_readonly import OKXReadOnlyClient
from services.time_utils import app_now


logger = logging.getLogger("paper_trading")
ACTIVE_STATUSES = {"waiting_trigger", "pending", "open"}
PENDING_STATUSES = {"waiting_trigger", "pending"}


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _candle_time(candle):
    parsed = _parse_time(candle.get("timestamp"))
    if parsed is not None:
        return parsed
    try:
        return datetime.fromtimestamp(float(candle.get("ts")) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _number(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _okx_inst_id(symbol):
    raw = str(symbol or "").upper().strip().replace("_", "-").replace("/", "-")
    if not raw or raw.endswith(("-SWAP", "-FUTURES", "-SPOT")):
        return raw
    if "-" in raw:
        return raw + "-SWAP"
    for quote in ("USDT", "USDC", "USD"):
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[:-len(quote)]}-{quote}-SWAP"
    return raw


def _market_snapshot(candle):
    return {
        key: candle.get(key)
        for key in ("timestamp", "open", "high", "low", "close", "volume", "confirm")
    }


def _entry_fill_price(price, side, slippage_bps):
    multiplier = 1 + (slippage_bps / 10_000)
    return price * multiplier if side == "long" else price / multiplier


def _exit_fill_price(price, side, slippage_bps):
    multiplier = 1 + (slippage_bps / 10_000)
    return price / multiplier if side == "long" else price * multiplier


def _touches_limit(order, candle):
    entry = _number(order["entry_price"])
    low = _number(candle.get("low"))
    high = _number(candle.get("high"))
    if entry is None or low is None or high is None:
        return False
    return low <= entry if order["side"] == "long" else high >= entry


def _touches_trigger(order, candle):
    trigger = _number(order["trigger_price"])
    low = _number(candle.get("low"))
    high = _number(candle.get("high"))
    if trigger is None or low is None or high is None:
        return False
    if order["trigger_direction"] == "above":
        return high >= trigger
    if order["trigger_direction"] == "below":
        return low <= trigger
    return False


def _exit_reason(order, candle):
    low = _number(candle.get("low"))
    high = _number(candle.get("high"))
    stop = _number(order["stop_price"])
    target = _number(order["target_price"])
    if None in {low, high, stop, target}:
        return None, None
    if order["side"] == "long":
        stop_hit = low <= stop
        target_hit = high >= target
    else:
        stop_hit = high >= stop
        target_hit = low <= target
    # A candle cannot reveal intrabar path.  Resolve an ambiguous target/stop
    # touch adversely, which avoids overstating AI paper-trading performance.
    if stop_hit and target_hit:
        return "ambiguous_stop_target_adverse", stop
    if stop_hit:
        return "stop_loss", stop
    if target_hit:
        return "take_profit", target
    return None, None


def _transition(order, to_status, event_type, event_at, *, price=None, reason="", snapshot=None, **changes):
    old_status = str(order["status"] or "")
    update_paper_order(order["id"], status=to_status, status_reason=reason, **changes)
    insert_paper_order_event(
        order["id"], event_type, event_at, from_status=old_status, to_status=to_status,
        price=price, reason=reason, market_snapshot=snapshot or {},
    )
    update_ai_shadow_plan_status(order["shadow_plan_id"], to_status)
    order = dict(order)
    order.update(changes)
    order["status"] = to_status
    order["status_reason"] = reason
    return order


def _fill_open_order(order, fill_price, event_at, reason, snapshot):
    quantity = _number(order["quantity"], 0) or 0
    fee_bps = _number(order["fee_bps"], 0) or 0
    entry_fee = fill_price * quantity * fee_bps / 10_000
    return _transition(
        order,
        "open",
        "filled",
        event_at,
        price=fill_price,
        reason=reason,
        snapshot=snapshot,
        filled_price=fill_price,
        filled_at=event_at,
        entry_fee_usd=entry_fee,
    )


def _close_order(order, raw_exit_price, event_at, reason, snapshot):
    quantity = _number(order["quantity"], 0) or 0
    entry = _number(order["filled_price"]) or _number(order["entry_price"])
    if entry is None or quantity <= 0:
        raise ValueError(f"paper order {order['id']} is missing filled price or quantity")
    slippage_bps = _number(order["slippage_bps"], 0) or 0
    exit_price = _exit_fill_price(raw_exit_price, order["side"], slippage_bps)
    gross = (exit_price - entry) * quantity if order["side"] == "long" else (entry - exit_price) * quantity
    exit_fee = exit_price * quantity * (_number(order["fee_bps"], 0) or 0) / 10_000
    entry_fee = _number(order["entry_fee_usd"], 0) or 0
    net = gross - entry_fee - exit_fee
    risk = abs(entry - (_number(order["stop_price"]) or entry)) * quantity
    r_multiple = net / risk if risk > 0 else None
    return _transition(
        order,
        "closed",
        "closed",
        event_at,
        price=exit_price,
        reason=reason,
        snapshot=snapshot,
        close_price=exit_price,
        closed_at=event_at,
        close_reason=reason,
        exit_fee_usd=exit_fee,
        gross_pnl_usd=gross,
        net_pnl_usd=net,
        r_multiple=r_multiple,
    )


def _expire_pending_order(order, now):
    return _transition(
        order,
        "expired",
        "expired",
        now.isoformat(),
        reason="虚拟挂单到期，未触价成交",
    )


def _process_order(order, candles, now):
    """Process one virtual order against ascending 1-minute public candles."""
    order = dict(order)
    status = str(order["status"] or "")
    if status not in ACTIVE_STATUSES:
        return {"paper_order_id": order["id"], "changed": False, "status": status}

    expires_at = _parse_time(order["expires_at"])
    if status in PENDING_STATUSES and expires_at and now.astimezone(timezone.utc) >= expires_at:
        updated = _expire_pending_order(order, now)
        return {"paper_order_id": updated["id"], "changed": True, "status": updated["status"]}

    last_marker = _parse_time(order["last_market_at"]) or _parse_time(order["submitted_at"])
    newest_market_at = None
    changed = False
    latest_candle = candles[-1] if candles else None
    earliest_candle_at = _candle_time(candles[0]) if candles else None
    if last_marker and earliest_candle_at and earliest_candle_at > last_marker + timedelta(minutes=2):
        insert_paper_order_event(
            order["id"],
            "market_data_gap",
            now.isoformat(),
            from_status=status,
            to_status=status,
            reason=(
                "本地模拟任务停顿或行情回看窗口不足；"
                "缺失区间内的触价顺序无法确认，后续结果应谨慎解读。"
            ),
        )
    time_stop_at = _parse_time(order["time_stop_at"])
    if status == "open" and time_stop_at and now.astimezone(timezone.utc) >= time_stop_at and latest_candle:
        price = _number(latest_candle.get("close"))
        if price is not None:
            updated = _close_order(
                order, price, now.isoformat(), "time_stop", _market_snapshot(latest_candle)
            )
            return {"paper_order_id": updated["id"], "changed": True, "status": updated["status"]}

    for candle in candles:
        candle_at = _candle_time(candle)
        if candle_at is None or (last_marker and candle_at <= last_marker):
            continue
        event_at = candle_at.isoformat()
        newest_market_at = event_at
        snapshot = _market_snapshot(candle)
        status = str(order["status"] or "")
        if status == "waiting_trigger" and _touches_trigger(order, candle):
            if order["order_type"] == "trigger_market":
                raw_price = _number(order["trigger_price"])
                fill_price = _entry_fill_price(raw_price, order["side"], _number(order["slippage_bps"], 0) or 0)
                order = _fill_open_order(
                    order, fill_price, event_at, "条件触发后按触发价加固定滑点模拟市价成交", snapshot
                )
            else:
                order = _transition(
                    order,
                    "pending",
                    "triggered",
                    event_at,
                    price=_number(order["trigger_price"]),
                    reason="条件触发；条件限价单从下一根 K 线开始等待限价成交",
                    snapshot=snapshot,
                    triggered_at=event_at,
                )
            changed = True
            # Never assume trigger and fill order inside the same candle for a
            # conditional limit order.  The next candle can fill it normally.
            continue

        if status == "pending" and _touches_limit(order, candle):
            fill_price = _number(order["entry_price"])
            order = _fill_open_order(order, fill_price, event_at, "价格触及限价，按限价全额模拟成交", snapshot)
            changed = True
            continue

        if status == "open":
            reason, raw_price = _exit_reason(order, candle)
            if reason and raw_price is not None:
                order = _close_order(order, raw_price, event_at, reason, snapshot)
                changed = True
                break

    if newest_market_at:
        update_paper_order(order["id"], last_market_at=newest_market_at, last_checked_at=now.isoformat())
    else:
        update_paper_order(order["id"], last_checked_at=now.isoformat())
    return {"paper_order_id": order["id"], "changed": changed, "status": order["status"]}


def _is_enabled():
    return os.getenv("AI_SHADOW_PAPER_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def run_paper_trading(client=None, now=None):
    """Advance active local virtual orders once using public OKX 1m candles."""
    if not _is_enabled():
        return {"status": "disabled", "checked": 0, "changed": 0, "errors": []}
    now = now or app_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    client = client or OKXReadOnlyClient()
    orders = [dict(row) for row in query_paper_orders(statuses=ACTIVE_STATUSES, limit=500)]
    if not orders:
        return {"status": "ok", "checked": 0, "changed": 0, "errors": []}

    candles_by_symbol = {}
    errors = []
    candle_limit = env_int("AI_SHADOW_PAPER_CANDLE_LIMIT", "300", 5, 300)
    for symbol in sorted({str(order["symbol"] or "") for order in orders}):
        inst_id = _okx_inst_id(symbol)
        try:
            candles_by_symbol[symbol] = client.fetch_candles(inst_id, bar="1m", limit=candle_limit)
        except Exception as exc:
            message = f"{symbol}: {str(exc)[:240]}"
            errors.append(message)
            logger.warning("Could not read paper-order market data: %s", message)
            candles_by_symbol[symbol] = []

    results = []
    for order in orders:
        try:
            results.append(_process_order(order, candles_by_symbol.get(order["symbol"], []), now))
        except Exception as exc:
            message = f"paper order #{order['id']}: {str(exc)[:240]}"
            errors.append(message)
            logger.exception("Paper order processing failed: %s", message)
    changed = sum(1 for result in results if result.get("changed"))
    return {
        "status": "ok" if not errors else "partial",
        "checked": len(orders),
        "changed": changed,
        "errors": errors,
        "results": results,
    }


def cancel_pending_paper_order(paper_order_id, reason="用户取消本地虚拟挂单"):
    """Cancel an unfilled local paper order; it never reaches an exchange."""
    order = get_paper_order(paper_order_id)
    if not order:
        raise ValueError(f"paper order not found: {paper_order_id}")
    order = dict(order)
    if order["status"] not in PENDING_STATUSES:
        raise ValueError("只有尚未成交的虚拟挂单可以取消")
    _transition(
        order,
        "cancelled",
        "cancelled",
        app_now().isoformat(),
        reason=reason,
    )
    return {"paper_order_id": paper_order_id, "status": "cancelled"}


def summarize_paper_trading(limit=2000):
    """Return small, presentation-friendly scorecard metrics for the UI."""
    orders = [dict(row) for row in query_paper_orders(limit=limit)]
    plans = [dict(row) for row in query_ai_shadow_plans(limit=limit)]
    closed = [order for order in orders if order.get("status") == "closed"]
    active = [order for order in orders if order.get("status") in ACTIVE_STATUSES]
    net_values = [_number(order.get("net_pnl_usd"), 0) or 0 for order in closed]
    r_values = [_number(order.get("r_multiple")) for order in closed]
    r_values = [value for value in r_values if value is not None]
    wins = [value for value in net_values if value > 0]
    equity = shadow_constraints()["virtual_equity_usd"]
    peak = equity
    max_drawdown = 0.0
    for order in sorted(closed, key=lambda item: str(item.get("closed_at") or "")):
        equity += _number(order.get("net_pnl_usd"), 0) or 0
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    no_trade = sum(1 for plan in plans if plan.get("decision") == "no_trade")
    watch = sum(1 for plan in plans if plan.get("decision") == "watch")
    return {
        "plans": len(plans),
        "no_trade": no_trade,
        "watch": watch,
        "orders": len(orders),
        "active_orders": len(active),
        "closed_orders": len(closed),
        "win_rate": (len(wins) / len(closed)) if closed else None,
        "net_pnl_usd": sum(net_values),
        "average_r": (sum(r_values) / len(r_values)) if r_values else None,
        "max_drawdown_pct": max_drawdown * 100,
    }
