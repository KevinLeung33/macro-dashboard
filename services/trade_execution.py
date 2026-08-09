"""Local lifecycle view for a user trade plan and its read-only exchange orders.

This module does not infer that an account-level OKX position belongs to a single
plan.  It calculates only the quantity attributable to explicitly linked order
IDs: entry fills minus linked exit fills.
"""
import math

from db.repository import (
    get_trade_note,
    query_trade_fills,
    query_trade_plan_order_links,
    query_trade_positions,
)


ORDER_ROLE_LABELS = {
    "entry": "入场",
    "take_profit": "止盈退出",
    "stop_loss": "止损退出",
    "manual_exit": "手动退出",
    "other_exit": "其他退出",
}

EXECUTION_STATE_LABELS = {
    "unlinked": "未关联真实订单",
    "waiting_entry": "等待入场成交",
    "partial_entry_open": "部分成交，入场单仍有效",
    "position_open": "持仓中",
    "position_open_entry_remainder_cancelled": "持仓中，入场余单已撤",
    "partially_exited": "部分退出，仍有归属仓位",
    "entry_cancelled": "未成交撤单",
    "entry_expired": "未成交过期",
    "entry_failed": "入场单失败",
    "entry_terminal_no_fill": "入场未成交结束",
    "closed_take_profit": "已退出：止盈",
    "closed_stop_loss": "已退出：止损",
    "closed_manual_exit": "已退出：手动平仓",
    "closed_other_exit": "已退出：其他",
    "closed_unattributed": "已无归属仓位，待核对退出",
}

OPEN_ORDER_STATUSES = {"live", "effective", "partially_filled"}
_CANCELLED_STATUSES = {"canceled", "cancelled", "mmp_canceled"}
_EXPIRED_STATUSES = {"expired"}
_FAILED_STATUSES = {"order_failed", "rejected", "failed"}
_EPSILON = 1e-10


def order_role_label(role):
    return ORDER_ROLE_LABELS.get(str(role or "").lower(), str(role or "未分类"))


def execution_state_label(state):
    return EXECUTION_STATE_LABELS.get(str(state or ""), str(state or "数据不足"))


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _text(value):
    return str(value or "").strip().lower()


def _effective_status(link):
    return _text(link.get("order_status") or link.get("last_exchange_status"))


def _effective_fill_quantity(link):
    cached = _number(link.get("order_filled_quantity"))
    if cached <= _EPSILON:
        cached = _number(link.get("last_filled_quantity"))
    fills = query_trade_fills(
        venue=link.get("venue") or None,
        account_label=link.get("account_label") or "",
        order_id=link.get("order_id") or None,
        limit=1000,
    )
    observed = sum(_number(row["quantity"]) for row in fills)
    # The order's cumulative filled size is authoritative when the local fill
    # history is truncated; otherwise both values should be the same.
    return max(cached, observed)


def _latest_exit_link(exit_links):
    filled = [item for item in exit_links if item["effective_filled_quantity"] > _EPSILON]
    if not filled:
        return None
    return max(
        filled,
        key=lambda item: str(
            item.get("order_updated_at") or item.get("last_exchange_updated_at") or item.get("linked_at") or ""
        ),
    )


def _state_for_links(entry_links, exit_links):
    entry_filled = sum(item["effective_filled_quantity"] for item in entry_links)
    exit_filled = sum(item["effective_filled_quantity"] for item in exit_links)
    attributed_open = max(0.0, entry_filled - exit_filled)
    entry_statuses = {_effective_status(item) for item in entry_links}
    entry_active = any(status in OPEN_ORDER_STATUSES for status in entry_statuses)
    terminal_remainder = any(
        status in _CANCELLED_STATUSES | _EXPIRED_STATUSES | _FAILED_STATUSES
        for status in entry_statuses
    )

    if not entry_links:
        return "unlinked", entry_filled, exit_filled, attributed_open, ""
    if entry_filled <= _EPSILON:
        if entry_active:
            return "waiting_entry", entry_filled, exit_filled, attributed_open, ""
        if any(status in _CANCELLED_STATUSES for status in entry_statuses):
            return "entry_cancelled", entry_filled, exit_filled, attributed_open, ""
        if any(status in _EXPIRED_STATUSES for status in entry_statuses):
            return "entry_expired", entry_filled, exit_filled, attributed_open, ""
        if any(status in _FAILED_STATUSES for status in entry_statuses):
            return "entry_failed", entry_filled, exit_filled, attributed_open, ""
        return "entry_terminal_no_fill", entry_filled, exit_filled, attributed_open, ""

    if attributed_open > _EPSILON:
        if exit_filled > _EPSILON:
            return "partially_exited", entry_filled, exit_filled, attributed_open, ""
        if entry_active:
            return "partial_entry_open", entry_filled, exit_filled, attributed_open, ""
        if terminal_remainder:
            return "position_open_entry_remainder_cancelled", entry_filled, exit_filled, attributed_open, ""
        return "position_open", entry_filled, exit_filled, attributed_open, ""

    latest_exit = _latest_exit_link(exit_links)
    if not latest_exit:
        return "closed_unattributed", entry_filled, exit_filled, attributed_open, ""
    role = _text(latest_exit.get("role"))
    return f"closed_{role}", entry_filled, exit_filled, attributed_open, role


def build_trade_plan_execution(note_id):
    """Build a deterministic execution view for one plan from local synced data."""
    note = get_trade_note(note_id)
    if not note:
        raise ValueError(f"trade plan not found: {note_id}")
    note_data = dict(note)
    raw_links = [dict(row) for row in query_trade_plan_order_links(note_id=note_id, limit=200)]
    links = []
    for link in raw_links:
        link["role_label"] = order_role_label(link.get("role"))
        link["effective_status"] = _effective_status(link)
        link["effective_filled_quantity"] = _effective_fill_quantity(link)
        links.append(link)
    entry_links = [item for item in links if _text(item.get("role")) == "entry"]
    exit_links = [item for item in links if _text(item.get("role")) != "entry"]
    state, entry_filled, exit_filled, attributed_open, exit_reason = _state_for_links(entry_links, exit_links)

    accounts = sorted({str(item.get("account_label") or "") for item in links})
    if not accounts:
        accounts = [""]
    aggregate_positions = []
    for account_label in accounts:
        aggregate_positions.extend(
            dict(row)
            for row in query_trade_positions(
                venue=note_data.get("venue") or None,
                account_label=account_label,
                symbol=note_data.get("symbol") or None,
                limit=20,
            )
        )
    exchange_position_quantity = sum(abs(_number(item.get("quantity"))) for item in aggregate_positions)
    warnings = []
    if state == "unlinked":
        warnings.append("尚未关联真实 OKX 订单；当前只保存计划与 AI 影子研究记录。")
    if entry_filled > _EPSILON and not exit_links:
        warnings.append("尚未关联实际退出订单；止盈、止损或手动平仓后请关联对应订单，才能自动归因。")
    if entry_filled > _EPSILON and exchange_position_quantity + _EPSILON < attributed_open:
        warnings.append("账户当前同交易对仓位小于已关联订单推算值，可能已有未关联的退出成交；请核对。")
    if exchange_position_quantity > attributed_open + _EPSILON:
        warnings.append("账户同交易对仓位大于本计划归属量；可能存在其他计划或未关联成交，不自动归因。")

    return {
        "note": note_data,
        "state": state,
        "state_label": execution_state_label(state),
        "exit_reason": exit_reason,
        "entry_order_count": len(entry_links),
        "exit_order_count": len(exit_links),
        "entry_filled_quantity": entry_filled,
        "exit_filled_quantity": exit_filled,
        "attributed_open_quantity": attributed_open,
        "exchange_position_quantity": exchange_position_quantity,
        "links": links,
        "entry_links": entry_links,
        "exit_links": exit_links,
        "warnings": warnings,
        "aggregate_positions": aggregate_positions,
    }
