"""Crypto 真实交易日志与事后 AI 点评。"""
import json
import os

import pandas as pd
import streamlit as st

from db.repository import (
    insert_trade_note,
    query_ai_shadow_plans,
    query_latest_trade_account_snapshot,
    query_paper_order_events,
    query_paper_orders,
    query_trade_ai_reviews,
    query_trade_fills,
    query_trade_notes,
    query_trade_orders,
    query_trade_plan_feedback,
    query_trade_positions,
    update_trade_note_order_plan,
)
from db.schema import init_db
from services.access_control import render_admin_access, require_admin
from services.ai_shadow_config import shadow_constraints
from services.ai_shadow_plan import generate_ai_shadow_plan
from services.paper_trading import cancel_pending_paper_order, run_paper_trading
from services.trade_review import review_trade_note
from services.okx_readonly import OKXReadOnlyClient, sync_okx_readonly_account
from services.trade_plan_context import build_trade_plan_snapshot
from services.trade_plan_feedback import generate_trade_plan_feedback


st.set_page_config(page_title="交易复盘", page_icon="🧾", layout="wide")
admin_access = render_admin_access()
st.title("🧾 Crypto 交易复盘")
st.caption("记录你的计划与真实只读订单，并用独立 AI 影子账户做本地虚拟订单比较；没有真实下单接口。")
init_db()

st.info(
    "交易计划会保存当时的宏观、新闻、数据新鲜度和 OKX 公开 K 线快照。"
    "“计划环境反馈”由你主动触发，只指出证据、矛盾和风险，不批准、阻止或执行交易；"
    "AI 影子计划生成时不会读取你的方向、价格、仓位、理由或真实订单；"
    "虚拟成交只写本地数据库，OKX 账户同步始终只读。"
)


def _row_dicts(rows):
    return [dict(row) for row in rows]


def _json_object(value):
    try:
        parsed = json.loads(value or "{}") if not isinstance(value, dict) else value
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _render_text_list(values):
    for item in values or []:
        st.markdown(f"- {item}")


def _pct_label(value):
    try:
        return f"{float(value):+.2f}%" if value is not None else "—"
    except (TypeError, ValueError):
        return "—"


ENTRY_ORDER_TYPE_LABELS = {
    "limit": "限价挂单",
    "market": "市价执行",
    "trigger_limit": "条件限价单",
    "trigger_market": "条件市价单",
    "manual": "手动/分批执行",
}
PLAN_STATUS_LABELS = {
    "planned": "仅计划（尚未挂单）",
    "waiting_trigger": "等待条件触发",
    "open": "已挂单，等待成交",
    "partially_filled": "部分成交",
    "filled": "已成交",
    "cancelled": "已撤销",
    "expired": "已过期",
    "executed": "已执行（旧记录）",
}
PENDING_PLAN_STATUSES = {"planned", "waiting_trigger", "open", "partially_filled"}
OPEN_EXCHANGE_ORDER_STATUSES = {"live", "partially_filled", "effective"}
AI_SHADOW_DECISION_LABELS = {
    "no_trade": "不交易",
    "watch": "观察等待",
    "limit": "限价虚拟挂单",
    "trigger_limit": "条件限价虚拟挂单",
    "trigger_market": "条件市价虚拟单",
    "market": "市价虚拟单",
}
PAPER_ORDER_STATUS_LABELS = {
    "waiting_trigger": "等待触发",
    "pending": "等待限价成交",
    "open": "已虚拟成交 / 持仓中",
    "closed": "已平仓",
    "expired": "挂单已过期",
    "cancelled": "已取消",
}


def _number_or_none(value):
    try:
        value = float(value)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _number_label(value):
    value = _number_or_none(value)
    if value is None:
        return "—"
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def _symbol_key(symbol):
    raw = str(symbol or "").upper().strip().replace("_", "-").replace("/", "-")
    for suffix in ("-SWAP", "-FUTURES", "-SPOT"):
        if raw.endswith(suffix):
            raw = raw[:-len(suffix)]
            break
    return raw.replace("-", "")


def _okx_chart_instrument(symbol):
    """Accept journal symbols such as BTCUSDT as well as OKX instrument IDs."""
    raw = str(symbol or "").upper().strip().replace("_", "-").replace("/", "-")
    if not raw or raw.endswith(("-SWAP", "-FUTURES", "-SPOT")):
        return raw
    if "-" in raw:
        return raw + "-SWAP"
    for quote in ("USDT", "USDC", "USD"):
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[:-len(quote)]}-{quote}-SWAP"
    return raw


def _plan_status_from_exchange(status):
    value = str(status or "").lower()
    return {
        "live": "open",
        "effective": "open",
        "partially_filled": "partially_filled",
        "filled": "filled",
        "canceled": "cancelled",
        "mmp_canceled": "cancelled",
        "order_failed": "cancelled",
    }.get(value, "planned")


def _entry_type_from_exchange(order_type):
    value = str(order_type or "").lower()
    if value in ENTRY_ORDER_TYPE_LABELS:
        return value
    if "trigger" in value or "conditional" in value:
        return "trigger_limit" if "limit" in value else "trigger_market"
    if value in {"market", "optimal_limit_ioc"}:
        return "market"
    return "limit" if value else "manual"


st.subheader("OKX 只读账户与市场")
okx_client = OKXReadOnlyClient()
st.caption("只读取跨币种保证金账户、持仓、挂单/历史订单、成交和公开 K 线；API Key 必须只有 Read 权限。")
sync_col, mode_col = st.columns([1, 3])
with sync_col:
    sync_clicked = st.button("🔄 同步 OKX 账户", disabled=not admin_access, type="primary")
if sync_clicked and require_admin("同步 OKX 账户"):
    with st.spinner("读取 OKX 账户、持仓、订单和成交……"):
        try:
            st.session_state["okx_sync_result"] = sync_okx_readonly_account(okx_client)
            st.success("OKX 只读数据已同步。")
        except Exception as exc:
            st.error(f"OKX 同步失败：{exc}")

if not okx_client.configured:
    st.warning("尚未配置 OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE；可以先使用本页的交易记录功能。")

account_label = os.getenv("OKX_ACCOUNT_LABEL", "main").strip() or "main"
snapshot = query_latest_trade_account_snapshot("OKX", account_label)
positions = query_trade_positions("OKX", account_label, limit=100)
orders = query_trade_orders("OKX", account_label, limit=100)
fills = query_trade_fills("OKX", account_label, limit=200)
paper_orders = query_paper_orders(limit=200)
shadow_plans = query_ai_shadow_plans(limit=200)
sync_result = st.session_state.get("okx_sync_result") or {}
with mode_col:
    if snapshot:
        mode_text = snapshot["account_mode"] or "未记录"
        margin_text = snapshot["margin_mode"] or "cross"
        st.caption(f"账户模式：{mode_text} · 保证金模式：{margin_text} · 最近同步：{snapshot['synced_at']}")
    else:
        st.caption("尚未有 OKX 同步快照。")

if snapshot:
    metric_cols = st.columns(4)
    metric_cols[0].metric("调整后权益", f"{snapshot['equity'] or 0:,.4f}")
    metric_cols[1].metric("可用保证金", f"{snapshot['available_balance'] or 0:,.4f}")
    metric_cols[2].metric("未实现盈亏", f"{snapshot['unrealized_pnl'] or 0:,.4f}")
    metric_cols[3].metric("保证金率", f"{snapshot['margin_ratio'] or 0:,.4f}")
    try:
        balance_payload = json.loads(snapshot["raw_json"] or "{}")
        balance_details = (balance_payload.get("balance") or {}).get("details") or []
    except (TypeError, ValueError):
        balance_details = []
    if balance_details:
        st.markdown("**跨币种余额明细**")
        balance_df = pd.DataFrame(balance_details)
        balance_columns = ["ccy", "cashBal", "eq", "availEq", "availBal", "usdVal", "upl"]
        st.dataframe(
            balance_df[[column for column in balance_columns if column in balance_df.columns]],
            use_container_width=True,
            hide_index=True,
        )
    if sync_result.get("warnings"):
        for warning in sync_result["warnings"]:
            st.warning(warning)

if positions:
    st.markdown("**当前持仓**")
    position_df = pd.DataFrame(_row_dicts(positions))
    visible = [
        "symbol", "position_side", "margin_mode", "quantity", "entry_price", "mark_price",
        "liquidation_price", "leverage", "unrealized_pnl", "unrealized_pnl_ratio", "updated_at",
    ]
    st.dataframe(position_df[[column for column in visible if column in position_df.columns]], use_container_width=True, hide_index=True)
else:
    st.caption("当前没有已同步的非零持仓。")

pending_orders = [
    dict(row) for row in orders
    if str(row["status"] or "").lower() in OPEN_EXCHANGE_ORDER_STATUSES
]
if pending_orders:
    st.markdown("**当前挂单（OKX 只读同步）**")
    pending_order_df = pd.DataFrame(pending_orders)
    pending_columns = [
        "order_id", "symbol", "side", "position_side", "order_type", "status", "price",
        "quantity", "filled_quantity", "reduce_only", "placed_at", "updated_at",
    ]
    st.dataframe(
        pending_order_df[[column for column in pending_columns if column in pending_order_df.columns]],
        use_container_width=True,
        hide_index=True,
    )

chart_plan_notes = query_trade_notes(limit=100)
symbol_options = sorted({
    str(row["symbol"])
    for row in list(positions) + list(orders) + list(fills) + list(chart_plan_notes)
    + list(paper_orders) + list(shadow_plans)
    if row["symbol"]
})
if symbol_options:
    chart_left, chart_mid, chart_right = st.columns([2, 1, 1])
    with chart_left:
        chart_symbol = st.selectbox("K 线交易对", symbol_options, key="okx_chart_symbol")
    with chart_mid:
        chart_bar = st.selectbox("周期", ["15m", "1H", "4H", "1D"], index=1, key="okx_chart_bar")
    with chart_right:
        chart_refresh = st.button("刷新 K 线", disabled=not admin_access)
    if chart_refresh:
        try:
            chart_instrument = _okx_chart_instrument(chart_symbol)
            candles = okx_client.fetch_candles(chart_instrument, bar=chart_bar, limit=200)
            if not candles:
                st.warning("OKX 没有返回 K 线。")
            else:
                import plotly.graph_objects as go

                candle_df = pd.DataFrame(candles)
                candle_df["timestamp"] = pd.to_datetime(candle_df["timestamp"], utc=True)
                fig = go.Figure(data=[go.Candlestick(
                    x=candle_df["timestamp"], open=candle_df["open"], high=candle_df["high"],
                    low=candle_df["low"], close=candle_df["close"], name=chart_symbol,
                )])
                chart_key = _symbol_key(chart_symbol)
                chart_fills = [
                    dict(row) for row in fills
                    if _symbol_key(row["symbol"]) == chart_key
                ]
                if chart_fills:
                    marker_df = pd.DataFrame(chart_fills)
                    marker_df["executed_at"] = pd.to_datetime(marker_df["executed_at"], utc=True)
                    for fill_side, color, marker in (("buy", "#00a67d", "triangle-up"), ("sell", "#e74c3c", "triangle-down")):
                        side_df = marker_df[marker_df["side"].str.lower() == fill_side]
                        if not side_df.empty:
                            fig.add_scatter(
                                x=side_df["executed_at"], y=side_df["price"], mode="markers",
                                name=f"{fill_side} 成交", marker={"color": color, "size": 10, "symbol": marker},
                                hovertext=side_df["quantity"].astype(str),
                            )
                chart_orders = [
                    dict(row) for row in orders
                    if _symbol_key(row["symbol"]) == chart_key
                    and str(row["status"] or "").lower() in OPEN_EXCHANGE_ORDER_STATUSES
                ]
                for order in chart_orders[:10]:
                    price = _number_or_none(order.get("price")) or _number_or_none(order.get("avg_price"))
                    if price is None:
                        continue
                    side_text = str(order.get("side") or "").lower()
                    color = "#00a67d" if side_text == "buy" else "#e74c3c"
                    order_id = str(order.get("order_id") or "")
                    fig.add_hline(
                        y=price,
                        line_dash="dash",
                        line_color=color,
                        annotation_text=f"OKX 挂单 #{order_id[-6:]} · {side_text or '—'}",
                        annotation_position="top right",
                        annotation_font_color=color,
                    )

                order_by_id = {str(row["order_id"]): dict(row) for row in orders if row["order_id"]}
                chart_plans = [
                    dict(row) for row in chart_plan_notes
                    if _symbol_key(row["symbol"]) == chart_key
                ]
                planned_lines = 0
                for plan in chart_plans:
                    linked = order_by_id.get(str(plan.get("order_id") or ""))
                    status = (
                        _plan_status_from_exchange(linked.get("status"))
                        if linked else str(plan.get("plan_status") or "planned").lower()
                    )
                    if status not in PENDING_PLAN_STATUSES:
                        continue
                    entry_price = _number_or_none(plan.get("entry_price"))
                    if entry_price is not None:
                        side_text = str(plan.get("side") or "").lower()
                        color = "#00a67d" if side_text in {"long", "buy"} else "#e74c3c"
                        entry_type = _entry_type_from_exchange(plan.get("entry_order_type"))
                        fig.add_hline(
                            y=entry_price,
                            line_dash="dot",
                            line_color=color,
                            annotation_text=(
                                f"计划 #{plan['id']} · {ENTRY_ORDER_TYPE_LABELS.get(entry_type, entry_type)}"
                                f" · {PLAN_STATUS_LABELS.get(status, status)}"
                            ),
                            annotation_position="bottom left",
                            annotation_font_color=color,
                        )
                        planned_lines += 1
                    trigger_price = _number_or_none(plan.get("trigger_price"))
                    if trigger_price is not None:
                        fig.add_hline(
                            y=trigger_price,
                            line_dash="dashdot",
                            line_color="#9b59b6",
                            annotation_text=f"计划 #{plan['id']} 触发价",
                            annotation_position="bottom right",
                            annotation_font_color="#9b59b6",
                        )
                    if planned_lines >= 10:
                        break
                chart_paper_orders = [
                    dict(row) for row in paper_orders
                    if _symbol_key(row["symbol"]) == chart_key
                ]
                paper_lines = 0
                paper_fills = [
                    item for item in chart_paper_orders
                    if item.get("filled_at") and _number_or_none(item.get("filled_price")) is not None
                ]
                if paper_fills:
                    paper_fill_df = pd.DataFrame(paper_fills)
                    paper_fill_df["filled_at"] = pd.to_datetime(paper_fill_df["filled_at"], utc=True)
                    fig.add_scatter(
                        x=paper_fill_df["filled_at"], y=paper_fill_df["filled_price"], mode="markers",
                        name="AI 虚拟成交", marker={"color": "#9b59b6", "size": 9, "symbol": "diamond"},
                        hovertext=paper_fill_df["status"].astype(str),
                    )
                for paper in chart_paper_orders[:10]:
                    paper_status = str(paper.get("status") or "")
                    if paper_status not in {"waiting_trigger", "pending", "open"}:
                        continue
                    paper_id = str(paper.get("id") or "")
                    entry_price = _number_or_none(paper.get("entry_price"))
                    side_text = str(paper.get("side") or "").lower()
                    color = "#7d3c98" if side_text == "long" else "#884c33"
                    if entry_price is not None:
                        fig.add_hline(
                            y=entry_price,
                            line_dash="dashdot" if paper_status != "open" else "solid",
                            line_color=color,
                            annotation_text=(
                                f"AI 虚拟 #{paper_id} · "
                                f"{PAPER_ORDER_STATUS_LABELS.get(paper_status, paper_status)}"
                            ),
                            annotation_position="top left",
                            annotation_font_color=color,
                        )
                    trigger_price = _number_or_none(paper.get("trigger_price"))
                    if paper_status == "waiting_trigger" and trigger_price is not None:
                        fig.add_hline(
                            y=trigger_price,
                            line_dash="dot",
                            line_color="#f39c12",
                            annotation_text=f"AI 虚拟 #{paper_id} 触发价",
                            annotation_position="bottom right",
                            annotation_font_color="#f39c12",
                        )
                    if paper_status == "open":
                        for level, label, line_color in (
                            (paper.get("stop_price"), "止损", "#e74c3c"),
                            (paper.get("target_price"), "目标", "#00a67d"),
                        ):
                            level = _number_or_none(level)
                            if level is not None:
                                fig.add_hline(
                                    y=level,
                                    line_dash="dot",
                                    line_color=line_color,
                                    annotation_text=f"AI 虚拟 #{paper_id} {label}",
                                    annotation_position="bottom left",
                                    annotation_font_color=line_color,
                                )
                    paper_lines += 1
                fig.update_layout(height=520, xaxis_rangeslider_visible=False, margin={"l": 20, "r": 20, "t": 30, "b": 20})
                st.plotly_chart(fig, use_container_width=True)
                if chart_orders or planned_lines or paper_lines:
                    st.caption("图中虚线为已同步的 OKX 待成交订单；点线为本地交易计划；紫色线/菱形为 AI 本地虚拟订单与成交；三角形为实际成交。")
                last = candles[-1]
                first = candles[0]
                return_pct = ((last["close"] / first["close"] - 1) * 100) if first.get("close") else None
                st.session_state["okx_market_context"] = {
                    "symbol": chart_instrument, "bar": chart_bar, "last_candle": last,
                    "window_return_pct": return_pct, "fill_count_on_chart": len(chart_fills),
                    "open_order_count_on_chart": len(chart_orders),
                    "planned_entry_count_on_chart": planned_lines,
                    "ai_paper_order_count_on_chart": paper_lines,
                }
        except Exception as exc:
            st.error(f"K 线读取失败：{exc}")

if orders or fills:
    order_col, fill_col = st.columns(2)
    with order_col:
        st.markdown("**最近订单**")
        order_df = pd.DataFrame(_row_dicts(orders))
        order_columns = ["order_id", "symbol", "side", "position_side", "order_type", "status", "price", "avg_price", "quantity", "filled_quantity", "updated_at"]
        st.dataframe(order_df[[column for column in order_columns if column in order_df.columns]], use_container_width=True, hide_index=True)
    with fill_col:
        st.markdown("**最近成交**")
        fill_df = pd.DataFrame(_row_dicts(fills))
        fill_columns = ["fill_id", "order_id", "symbol", "side", "price", "quantity", "fee", "fee_asset", "realized_pnl", "executed_at"]
        st.dataframe(fill_df[[column for column in fill_columns if column in fill_df.columns]], use_container_width=True, hide_index=True)

st.subheader("创建交易计划")
with st.form("trade_note_form", clear_on_submit=False):
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        venue = st.selectbox("交易所", ["Binance", "OKX", "Other"])
        symbol = st.text_input("交易对", placeholder="BTC-USDT-SWAP 或 BTCUSDT")
    with c2:
        side = st.selectbox("方向", ["LONG", "SHORT"])
        trade_type = st.selectbox("交易类型", [
            "顺势趋势", "顺势波段", "战术回调（逆大趋势）", "事件驱动", "区间/均值回归", "其他",
        ])
    with c3:
        horizon = st.selectbox("预期持仓周期", [
            "15分钟以内", "15分钟-2小时", "2-6小时", "6-24小时", "1-3天", "3-7天", "1-2周", "更长", "未设定",
        ])
        macro_horizon = st.selectbox("宏观判断周期", ["日内", "1-3天", "1-2周", "1-3月", "更长", "不适用"])
    with c4:
        analysis_timeframe = st.selectbox("主要技术周期", ["5m", "15m", "1H", "4H", "1D", "其他"])
        plan_status = st.selectbox(
            "计划 / 挂单状态",
            list(PLAN_STATUS_LABELS),
            format_func=lambda value: PLAN_STATUS_LABELS.get(value, value),
        )
    c5, c6, c7, c8 = st.columns(4)
    with c5:
        entry_order_type = st.selectbox(
            "入场方式",
            list(ENTRY_ORDER_TYPE_LABELS),
            format_func=lambda value: ENTRY_ORDER_TYPE_LABELS.get(value, value),
        )
        entry_price = st.number_input("计划入场价 / 限价（可选）", min_value=0.0, value=0.0, format="%.8f")
    with c6:
        trigger_price = st.number_input("触发价（条件单可选）", min_value=0.0, value=0.0, format="%.8f")
        planned_quantity = st.number_input("计划数量（币/合约张数，可选）", min_value=0.0, value=0.0, format="%.8f")
    with c7:
        stop_price = st.number_input("价格止损（可选）", min_value=0.0, value=0.0, format="%.8f")
        target_price = st.number_input("目标价（可选）", min_value=0.0, value=0.0, format="%.8f")
    with c8:
        order_id = st.text_input("关联订单 ID（可稍后补）")
        plan_expires_at = st.text_input("计划到期时间（可选）", placeholder="例如 2026-08-09 08:00")
    c9, c10 = st.columns(2)
    with c9:
        entry_trigger = st.text_input("明确入场触发", placeholder="例如 1H 跌破后反抽不过")
    with c10:
        time_stop = st.text_input("时间止损", placeholder="例如 8小时未扩散则退出/重做")
    setup = st.text_input("技术形态/交易结构", placeholder="突破、回踩、趋势延续、区间边缘……")
    thesis = st.text_area("为什么做这笔交易？", placeholder="记录宏观、新闻、市场结构和你当时的判断……")
    risk_note = st.text_area("风险与失效条件", placeholder="什么情况下承认判断错误？仓位/杠杆有什么限制？")
    saved = st.form_submit_button("保存计划与环境快照", type="primary", disabled=not admin_access)

if saved and require_admin("保存交易记录"):
    clean_symbol = symbol.strip().upper()
    entry_price_value = _number_or_none(entry_price)
    trigger_price_value = _number_or_none(trigger_price)
    planned_quantity_value = _number_or_none(planned_quantity)
    if not clean_symbol or not thesis.strip():
        st.error("交易对和交易理由不能为空。")
    elif entry_order_type in {"limit", "trigger_limit"} and entry_price_value is None:
        st.error("限价单和条件限价单都需要填写计划入场价。")
    elif entry_order_type in {"trigger_limit", "trigger_market"} and trigger_price_value is None:
        st.error("条件单需要填写触发价。")
    else:
        if plan_status in {"open", "partially_filled"} and not order_id.strip():
            st.warning("已记录为挂单/部分成交，但尚未关联交易所订单 ID；可在计划详情中后补。")
        plan_payload = {
            "venue": venue, "symbol": clean_symbol, "side": side, "trade_type": trade_type,
            "expected_horizon": horizon, "macro_horizon": macro_horizon,
            "analysis_timeframe": analysis_timeframe,
            "entry_order_type": entry_order_type,
            "entry_price": entry_price_value,
            "trigger_price": trigger_price_value,
            "planned_quantity": planned_quantity_value,
            "plan_status": plan_status,
            "order_id": order_id.strip(),
        }
        with st.spinner("正在保存计划当时的宏观、新闻和公开市场快照……"):
            try:
                plan_snapshot = build_trade_plan_snapshot(plan_payload)
            except Exception as exc:
                plan_snapshot = {
                    "snapshot_version": "trade-plan-context-v2",
                    "collection_errors": [f"计划快照生成失败：{str(exc)[:240]}"],
                }
            note_id = insert_trade_note(
                venue=venue,
                symbol=clean_symbol,
                order_id=order_id.strip(),
                side=side,
                thesis=thesis.strip(),
                setup=setup.strip(),
                entry_order_type=entry_order_type,
                entry_price=entry_price_value,
                trigger_price=trigger_price_value,
                planned_quantity=planned_quantity_value,
                stop_price=stop_price or None,
                target_price=target_price or None,
                expected_horizon=horizon,
                risk_note=risk_note.strip(),
                market_snapshot=plan_snapshot,
                trade_type=trade_type,
                macro_horizon=macro_horizon,
                analysis_timeframe=analysis_timeframe,
                entry_trigger=entry_trigger.strip(),
                time_stop=time_stop.strip(),
                plan_status=plan_status,
                plan_expires_at=plan_expires_at.strip(),
                context_captured_at=plan_snapshot.get("captured_at", ""),
            )
        st.session_state["selected_trade_note_id"] = note_id
        st.success(f"已保存交易计划 #{note_id} 与当时环境快照。AI 不会自动运行。")

st.divider()
st.subheader("已记录交易")
notes = query_trade_notes(limit=100)
if not notes:
    st.info("还没有交易记录。")
else:
    note_options = {
        f"#{row['id']} · {row['created_at']} · {row['venue']} · {row['symbol']} · {row['side']}": row["id"]
        for row in notes
    }
    ids = list(note_options.values())
    default_id = st.session_state.get("selected_trade_note_id", ids[0])
    default_index = ids.index(default_id) if default_id in ids else 0
    selected_label = st.selectbox("选择一笔交易", list(note_options), index=default_index)
    selected_id = note_options[selected_label]
    st.session_state["selected_trade_note_id"] = selected_id
    selected = next(row for row in notes if row["id"] == selected_id)
    selected_status = str(selected["plan_status"] or "planned").lower()
    if selected_status not in PLAN_STATUS_LABELS:
        selected_status = "planned"
    selected_entry_type = _entry_type_from_exchange(selected["entry_order_type"])
    selected_order_id = (selected["order_id"] or "").strip()
    linked_order_rows = query_trade_orders(order_id=selected_order_id, limit=1) if selected_order_id else []
    linked_order = dict(linked_order_rows[0]) if linked_order_rows else None

    detail_left, detail_right = st.columns(2)
    with detail_left:
        st.markdown(f"**交易理由**\n\n{selected['thesis'] or '—'}")
        st.caption(
            f"类型：{selected['trade_type'] or '—'} · 预期周期：{selected['expected_horizon'] or '—'} · "
            f"宏观周期：{selected['macro_horizon'] or '—'} · 技术周期：{selected['analysis_timeframe'] or '—'}"
        )
        st.caption(f"技术结构：{selected['setup'] or '—'} · 入场触发：{selected['entry_trigger'] or '—'}")
    with detail_right:
        st.markdown(f"**风险与失效条件**\n\n{selected['risk_note'] or '—'}")
        st.caption(
            f"价格止损：{selected['stop_price'] or '—'} · 目标：{selected['target_price'] or '—'} · "
            f"时间止损：{selected['time_stop'] or '—'}"
        )
        st.caption(
            f"计划入场：{ENTRY_ORDER_TYPE_LABELS.get(selected_entry_type, selected_entry_type)} · "
            f"价格：{_number_label(selected['entry_price'])} · "
            f"触发价：{_number_label(selected['trigger_price'])} · "
            f"数量：{_number_label(selected['planned_quantity'])}"
        )
        st.caption(
            f"状态：{PLAN_STATUS_LABELS.get(selected_status, selected_status)} · "
            f"到期：{selected['plan_expires_at'] or '—'} · "
            f"订单：{selected_order_id or '尚未关联'}"
        )
        if linked_order:
            st.info(
                "已同步订单："
                f"{linked_order['status'] or '—'} · {linked_order['order_type'] or '—'} · "
                f"价格 {_number_label(linked_order['price'])} · "
                f"已成交 {_number_label(linked_order['filled_quantity'])}/"
                f"{_number_label(linked_order['quantity'])}"
            )
        elif selected_order_id:
            st.caption("该订单 ID 尚未出现在当前只读同步记录中；同步 OKX 后会自动用于展示和复盘。")

    matching_synced_orders = [
        dict(row) for row in orders
        if str(selected["venue"] or "").upper() == "OKX"
        and _symbol_key(row["symbol"]) == _symbol_key(selected["symbol"])
    ]
    order_choices = {"不从已同步订单选择（手动填写下方订单 ID）": None}
    for order in matching_synced_orders:
        label = (
            f"#{order['order_id']} · {order['side'] or '—'} {order['order_type'] or '—'} · "
            f"{order['status'] or '—'} · 价格 {_number_label(order['price'])}"
        )
        order_choices[label] = order
    order_labels = list(order_choices)
    matching_label = next(
        (label for label, order in order_choices.items() if order and order["order_id"] == selected_order_id),
        order_labels[0],
    )

    with st.expander("更新入场价 / 挂单状态 / 关联订单", expanded=False):
        st.caption("这里只更新本地交易计划；不会向 OKX 或其他交易所下单、改单或撤单。")
        with st.form(f"update_trade_order_plan_{selected_id}"):
            edit_left, edit_mid, edit_right = st.columns(3)
            entry_type_values = list(ENTRY_ORDER_TYPE_LABELS)
            status_values = list(PLAN_STATUS_LABELS)
            with edit_left:
                edit_entry_type = st.selectbox(
                    "入场方式",
                    entry_type_values,
                    index=entry_type_values.index(selected_entry_type),
                    format_func=lambda value: ENTRY_ORDER_TYPE_LABELS.get(value, value),
                )
                edit_entry_price = st.number_input(
                    "计划入场价 / 限价",
                    min_value=0.0,
                    value=_number_or_none(selected["entry_price"]) or 0.0,
                    format="%.8f",
                )
                edit_trigger_price = st.number_input(
                    "触发价（条件单可选）",
                    min_value=0.0,
                    value=_number_or_none(selected["trigger_price"]) or 0.0,
                    format="%.8f",
                )
            with edit_mid:
                edit_quantity = st.number_input(
                    "计划数量（币/合约张数）",
                    min_value=0.0,
                    value=_number_or_none(selected["planned_quantity"]) or 0.0,
                    format="%.8f",
                )
                edit_status = st.selectbox(
                    "计划 / 挂单状态",
                    status_values,
                    index=status_values.index(selected_status),
                    format_func=lambda value: PLAN_STATUS_LABELS.get(value, value),
                )
                edit_expires_at = st.text_input("计划到期时间", value=selected["plan_expires_at"] or "")
            with edit_right:
                selected_order_label = st.selectbox(
                    "从已同步订单关联（可选）",
                    order_labels,
                    index=order_labels.index(matching_label),
                )
                edit_order_id = st.text_input("订单 ID（可手动填写）", value=selected_order_id)
                use_order_values = st.checkbox(
                    "用已同步订单回填空的价格/数量，并同步状态",
                    value=True,
                )
            plan_update_saved = st.form_submit_button("保存入场与挂单信息", type="primary", disabled=not admin_access)

        if plan_update_saved and require_admin("更新交易计划挂单信息"):
            source_order = order_choices.get(selected_order_label)
            updated_entry_type = edit_entry_type
            updated_entry_price = _number_or_none(edit_entry_price)
            updated_trigger_price = _number_or_none(edit_trigger_price)
            updated_quantity = _number_or_none(edit_quantity)
            updated_status = edit_status
            updated_order_id = edit_order_id.strip()
            if source_order:
                updated_order_id = str(source_order["order_id"] or "").strip()
                if use_order_values:
                    updated_status = _plan_status_from_exchange(source_order["status"])
                    if updated_entry_price is None:
                        updated_entry_price = _number_or_none(source_order["price"]) or _number_or_none(source_order["avg_price"])
                    if updated_quantity is None:
                        updated_quantity = _number_or_none(source_order["quantity"])
                    if updated_entry_type == "manual":
                        updated_entry_type = _entry_type_from_exchange(source_order["order_type"])
            if updated_entry_type in {"limit", "trigger_limit"} and updated_entry_price is None:
                st.error("限价单和条件限价单都需要填写计划入场价。")
            elif updated_entry_type in {"trigger_limit", "trigger_market"} and updated_trigger_price is None:
                st.error("条件单需要填写触发价。")
            else:
                changed = update_trade_note_order_plan(
                    selected_id,
                    order_id=updated_order_id,
                    entry_order_type=updated_entry_type,
                    entry_price=updated_entry_price,
                    trigger_price=updated_trigger_price,
                    planned_quantity=updated_quantity,
                    plan_status=updated_status,
                    plan_expires_at=edit_expires_at.strip(),
                )
                if changed:
                    st.success("已更新本地计划和挂单关联；交易所订单没有被修改。")
                    st.rerun()
                else:
                    st.error("未找到要更新的交易计划。")

    plan_snapshot = _json_object(selected["market_snapshot_json"])
    if plan_snapshot:
        live_context = plan_snapshot.get("live_market") or {}
        snapshot_cols = st.columns(4)
        snapshot_cols[0].metric("计划快照时间", str(plan_snapshot.get("captured_at") or selected["context_captured_at"] or "—")[:19])
        snapshot_cols[1].metric("快照价格", str((live_context.get("last_candle") or {}).get("close") or "—"))
        snapshot_cols[2].metric("1 Bar变化", _pct_label((live_context.get("returns_pct") or {}).get("1_bar")))
        snapshot_cols[3].metric("24 Bar变化", _pct_label((live_context.get("returns_pct") or {}).get("24_bars")))
        with st.expander("查看计划创建时的数据快照", expanded=False):
            st.json(plan_snapshot)

    st.markdown("### 🤖 AI 独立影子计划（本地虚拟订单）")
    st.caption(
        "生成阶段只传入交易对与一份新鲜市场快照，不传入你的方向、入场价、止损、仓位、理由或真实订单。"
        "生成完成后才做对比；影子订单只写本地数据库。"
    )
    selected_shadow_plans = [
        dict(row) for row in shadow_plans if int(row["note_id"]) == int(selected_id)
    ]
    selected_shadow_ids = {item["id"] for item in selected_shadow_plans}
    selected_paper_orders = [
        dict(row) for row in paper_orders if row["shadow_plan_id"] in selected_shadow_ids
    ]
    active_paper_orders = [
        item for item in selected_paper_orders
        if item.get("status") in {"waiting_trigger", "pending", "open"}
    ]
    shadow_action_col, paper_check_col, shadow_info_col = st.columns([1.35, 1.1, 2.55])
    with shadow_action_col:
        generate_shadow = st.button(
            "✨ 生成独立 AI 影子计划",
            type="primary",
            disabled=(not admin_access) or bool(active_paper_orders),
            key=f"ai_shadow_plan_{selected_id}",
        )
    with paper_check_col:
        check_paper = st.button(
            "🔄 检查虚拟订单",
            disabled=not admin_access,
            key=f"ai_paper_check_{selected_id}",
        )
    with shadow_info_col:
        constraints = shadow_constraints()
        st.caption(
            f"虚拟账户 ${constraints['virtual_equity_usd']:,.0f} · 单笔最大风险 {constraints['max_risk_pct']:.2%} · "
            f"最低 R/R {constraints['min_risk_reward']:.2f} · 费用 {constraints['fee_bps']:.1f}bp · "
            f"滑点 {constraints['slippage_bps']:.1f}bp"
        )
        if active_paper_orders:
            st.caption("当前已有未结束的 AI 虚拟订单；为避免重复计分，请先等待其结束。")

    if generate_shadow and require_admin("生成独立 AI 影子计划"):
        with st.spinner("AI 正在基于独立市场快照生成虚拟计划……"):
            try:
                generated = generate_ai_shadow_plan(selected_id)
                st.session_state["ai_shadow_last_result"] = generated
                st.success("AI 影子计划已保存；它没有读取你的计划字段，也没有发送任何真实订单。")
                st.rerun()
            except Exception as exc:
                st.error(f"AI 影子计划生成失败：{exc}")

    if check_paper and require_admin("检查 AI 虚拟订单"):
        with st.spinner("正在读取 OKX 公开 1 分钟 K 线并推进本地虚拟订单……"):
            try:
                result = run_paper_trading()
                st.session_state["ai_paper_last_result"] = result
                st.success(f"已检查 {result.get('checked', 0)} 笔虚拟订单，状态变化 {result.get('changed', 0)} 笔。")
                st.rerun()
            except Exception as exc:
                st.error(f"虚拟订单检查失败：{exc}")

    latest_paper_result = st.session_state.get("ai_paper_last_result") or {}
    if latest_paper_result.get("errors"):
        st.caption("最近一次虚拟订单检查有数据读取问题：" + "；".join(latest_paper_result["errors"][:2]))

    if not selected_shadow_plans:
        st.info("尚未生成独立 AI 影子计划。它可以选择不交易、观察、限价挂单或条件单。")
    else:
        latest_shadow = selected_shadow_plans[0]
        latest_decision = _json_object(latest_shadow["decision_json"])
        latest_comparison = _json_object(latest_shadow["comparison_json"])
        latest_paper = next(
            (item for item in selected_paper_orders if item["shadow_plan_id"] == latest_shadow["id"]),
            None,
        )
        shadow_cols = st.columns(5)
        shadow_cols[0].metric("AI 决策", AI_SHADOW_DECISION_LABELS.get(latest_shadow["decision"], latest_shadow["decision"]))
        shadow_cols[1].metric("方向", str(latest_shadow["side"] or "flat").upper())
        shadow_cols[2].metric("虚拟入场", _number_label(latest_shadow["entry_price"]))
        shadow_cols[3].metric("风险收益比", "—" if latest_shadow["risk_reward"] is None else f"{latest_shadow['risk_reward']:.2f}")
        shadow_cols[4].metric(
            "虚拟状态",
            PAPER_ORDER_STATUS_LABELS.get(
                (latest_paper or {}).get("status", latest_shadow["status"]),
                (latest_paper or {}).get("status", latest_shadow["status"]),
            ),
        )
        st.write(latest_shadow["rationale"] or latest_decision.get("no_trade_reason") or "AI 未提供理由。")
        st.caption(
            f"AI 快照：{str(latest_shadow['created_at'])[:19]} · 周期：{latest_shadow['analysis_timeframe'] or '—'} · "
            f"预期持仓：{latest_shadow['expected_horizon'] or '—'} · 置信度：{(latest_shadow['confidence'] or 0):.0%}"
        )
        if latest_shadow["decision"] in {"no_trade", "watch"}:
            st.caption("AI 没有创建虚拟订单；“不交易”会被保留进长期统计，不能被当成缺失样本。")
        if latest_paper:
            st.caption(
                f"虚拟订单 #{latest_paper['id']} · 数量 {_number_label(latest_paper['quantity'])} · "
                f"止损 {_number_label(latest_paper['stop_price'])} · 目标 {_number_label(latest_paper['target_price'])} · "
                f"挂单到期 {latest_paper['expires_at'] or '—'} · 时间止损 {latest_paper['time_stop_at'] or '—'}"
            )
            if latest_paper["status"] == "closed":
                net_pnl = latest_paper["net_pnl_usd"]
                r_multiple = latest_paper["r_multiple"]
                st.caption(
                    f"已按 {latest_paper['close_reason'] or '—'} 平仓 · 净虚拟盈亏 "
                    f"${(net_pnl or 0):+,.2f} · R 倍数 {'—' if r_multiple is None else f'{r_multiple:+.2f}'}"
                )
            if latest_paper["status"] in {"waiting_trigger", "pending"}:
                if st.button(
                    "取消这笔本地虚拟挂单",
                    disabled=not admin_access,
                    key=f"cancel_ai_paper_{latest_paper['id']}",
                ) and require_admin("取消 AI 虚拟挂单"):
                    try:
                        cancel_pending_paper_order(latest_paper["id"])
                        st.success("已取消本地虚拟挂单；真实 OKX 订单没有被触碰。")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"取消虚拟挂单失败：{exc}")
            events = query_paper_order_events(latest_paper["id"], limit=20)
            if events:
                event_df = pd.DataFrame(_row_dicts(events))
                with st.expander("查看 AI 虚拟订单事件", expanded=False):
                    st.dataframe(
                        event_df[[column for column in ("event_at", "event_type", "from_status", "to_status", "price", "reason") if column in event_df.columns]],
                        use_container_width=True,
                        hide_index=True,
                    )

        st.markdown("**与你的计划比较**")
        st.write(latest_comparison.get("summary_cn") or "暂无可用对比。")
        if latest_comparison.get("independence"):
            st.caption(latest_comparison["independence"])
        comparison_cols = st.columns(3)
        comparison_cols[0].metric("方向关系", latest_comparison.get("direction_relation") or "—")
        entry_delta = latest_comparison.get("entry_price_delta_pct")
        comparison_cols[1].metric("入场价差", "—" if entry_delta is None else f"{entry_delta:+.2f}%")
        user_rr = (latest_comparison.get("user_plan") or {}).get("risk_reward")
        comparison_cols[2].metric("用户计划 R/R", "—" if user_rr is None else f"{user_rr:.2f}")
        for title, key, expanded in (("主要差异", "differences", True), ("共同风险/数据限制", "shared_risks", False)):
            values = latest_comparison.get(key) or []
            if values:
                with st.expander(title, expanded=expanded):
                    _render_text_list(values)
        for title, key in (("AI 使用的证据", "evidence"), ("AI 需要持续验证的条件", "conditions"), ("AI 数据缺口", "data_gaps")):
            values = latest_decision.get(key) or []
            if values:
                with st.expander(title, expanded=False):
                    _render_text_list(values)

    st.markdown("### 计划环境反馈")
    refresh_plan_context = st.checkbox(
        "按当前环境重新采集数据后再反馈（不会覆盖创建计划时的快照）",
        key=f"refresh_plan_context_{selected_id}",
    )
    if st.button("🧭 生成计划环境反馈", type="primary", disabled=not admin_access, key=f"plan_feedback_{selected_id}"):
        if require_admin("生成交易计划环境反馈"):
            with st.spinner("正在对照计划、宏观、新闻、数据新鲜度和实时 K 线……"):
                try:
                    generate_trade_plan_feedback(selected_id, refresh_context=refresh_plan_context)
                    st.success("计划环境反馈已保存；它不构成下单批准或指令。")
                except Exception as exc:
                    st.error(f"计划环境反馈失败：{exc}")

    plan_feedback_history = query_trade_plan_feedback(note_id=selected_id, limit=10)
    if plan_feedback_history:
        latest_plan_feedback = _json_object(plan_feedback_history[0]["feedback_json"])
        alignment_map = {
            "supportive": "支持", "neutral": "中性", "headwind": "存在逆风",
            "mixed": "多空交织", "insufficient_data": "数据不足",
        }
        classification_map = {
            "trend_following": "顺势交易", "countertrend_tactical": "逆趋势战术交易",
            "event_driven": "事件驱动", "range": "区间/均值回归", "unclear": "类型不明确",
        }
        feedback_cols = st.columns(4)
        feedback_cols[0].metric("计划类型识别", classification_map.get(latest_plan_feedback.get("plan_classification"), "类型不明确"))
        feedback_cols[1].metric("宏观关系", alignment_map.get(latest_plan_feedback.get("macro_alignment"), "数据不足"))
        feedback_cols[2].metric("实时市场", alignment_map.get(latest_plan_feedback.get("realtime_alignment"), "数据不足"))
        feedback_cols[3].metric("技术条件", alignment_map.get(latest_plan_feedback.get("technical_alignment"), "数据不足"))
        st.write(latest_plan_feedback.get("summary_cn") or "暂无总结")
        if latest_plan_feedback.get("time_horizon_assessment"):
            st.caption("周期判断：" + latest_plan_feedback["time_horizon_assessment"])
        for title, key, expanded in (
            ("支持证据", "supporting_evidence", False),
            ("相互矛盾的证据", "contradicting_evidence", True),
            ("执行前/持仓中需验证", "conditions_to_validate", True),
            ("失效与时间止损检查", "invalidation_checks", True),
            ("风险提示", "risk_flags", True),
            ("数据缺口", "data_gaps", False),
        ):
            values = latest_plan_feedback.get(key) or []
            if key == "invalidation_checks":
                values = values + (latest_plan_feedback.get("time_stop_checks") or [])
            if values:
                with st.expander(title, expanded=expanded):
                    _render_text_list(values)
        st.caption(f"最近反馈：{plan_feedback_history[0]['created_at']} · 置信度 {latest_plan_feedback.get('confidence', 0.0):.0%}")

    if st.button("🧠 生成 AI 交易点评", type="primary", disabled=not admin_access):
        if require_admin("生成 AI 交易点评"):
            with st.spinner("AI 正在复盘这笔已记录交易……"):
                try:
                    selected_order_id = (selected["order_id"] or "").strip()
                    context_orders = query_trade_orders(
                        venue=selected["venue"],
                        symbol=selected["symbol"],
                        order_id=selected_order_id or None,
                        limit=20,
                    )
                    context_fills = query_trade_fills(
                        venue=selected["venue"],
                        symbol=selected["symbol"],
                        order_id=selected_order_id or None,
                        limit=50,
                    )
                    order_context = {
                        "orders": _row_dicts(context_orders),
                        "fills": _row_dicts(context_fills),
                    }
                    review_trade_note(
                        selected_id,
                        order_context=order_context,
                        market_context=st.session_state.get("okx_market_context", {}),
                    )
                    st.success("点评已保存。")
                except Exception as exc:
                    st.error(f"AI 点评失败：{exc}")

    reviews = query_trade_ai_reviews(note_id=selected_id, limit=10)
    if reviews:
        st.markdown("### AI 点评历史")
        latest = reviews[0]
        try:
            review = json.loads(latest["review_json"] or "{}")
        except (TypeError, ValueError):
            review = {}
        verdict_map = {
            "reasonable": "相对合理",
            "mixed": "有得有失",
            "unreasonable": "存在明显问题",
            "insufficient_data": "数据不足",
        }
        st.metric("点评结论", verdict_map.get(review.get("verdict"), "数据不足"), f"置信度 {review.get('confidence', 0.0):.0%}")
        st.write(review.get("summary_cn") or "暂无总结")
        for title, key in (("做得好的地方", "strengths"), ("需要改进", "weaknesses"), ("风险提示", "risk_flags"), ("下一次复盘问题", "post_trade_questions")):
            values = review.get(key) or []
            if values:
                with st.expander(title, expanded=key in {"risk_flags", "weaknesses"}):
                    for item in values:
                        st.markdown(f"- {item}")
        if review.get("thesis_consistency"):
            st.caption("理由一致性：" + review["thesis_consistency"])
        if review.get("execution_review"):
            st.caption("执行复盘：" + review["execution_review"])

    df = pd.DataFrame([dict(row) for row in notes])
    st.dataframe(
        df[[
            "id", "created_at", "venue", "symbol", "side", "trade_type", "entry_order_type",
            "entry_price", "trigger_price", "planned_quantity", "order_id", "expected_horizon", "plan_status",
        ]].rename(columns={
            "id": "ID", "created_at": "记录时间", "venue": "交易所", "symbol": "交易对",
            "side": "方向", "trade_type": "交易类型", "entry_order_type": "入场方式",
            "entry_price": "计划入场价", "trigger_price": "触发价", "planned_quantity": "计划数量",
            "order_id": "订单ID", "expected_horizon": "预期周期", "plan_status": "计划状态",
        }),
        use_container_width=True,
        hide_index=True,
    )
