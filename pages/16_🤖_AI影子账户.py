"""Read-only view of AI shadow plans and local virtual-order performance."""
import pandas as pd
import streamlit as st

from db.repository import query_ai_shadow_plans, query_paper_order_events, query_paper_orders
from db.schema import init_db
from services.access_control import render_admin_access, require_admin
from services.ai_shadow_config import shadow_constraints
from services.paper_trading import run_paper_trading, summarize_paper_trading


st.set_page_config(page_title="AI 影子账户", page_icon="🤖", layout="wide")
admin_access = render_admin_access()
init_db()
st.title("🤖 AI 影子账户")
st.caption("AI 独立计划与本地虚拟成交统计。不会读取或修改交易所订单，也没有任何真实下单功能。")
st.info(
    "每份 AI 计划在生成时只获得交易对和独立市场快照；随后才与用户计划比较。"
    "限价单按“触价即按限价全额成交”模拟，条件限价单从触发后的下一根 1 分钟 K 线开始等待成交；"
    "同一根 K 线同时碰到止损与止盈时按止损处理。虚拟数量按基础币/USDT 名义金额计算，"
    "不是 OKX 合约张数，因此应重点比较 R 倍数和计划质量。"
)


DECISION_LABELS = {
    "no_trade": "不交易", "watch": "观察等待", "limit": "限价挂单",
    "trigger_limit": "条件限价", "trigger_market": "条件市价", "market": "市价",
}
STATUS_LABELS = {
    "waiting_trigger": "等待触发", "pending": "等待限价成交", "open": "持仓中",
    "closed": "已平仓", "expired": "已过期", "cancelled": "已取消",
    "no_trade": "不交易", "watch": "观察等待",
}


constraints = shadow_constraints()
summary = summarize_paper_trading()
metric_cols = st.columns(6)
metric_cols[0].metric("AI 计划", summary["plans"])
metric_cols[1].metric("不交易 / 观察", f"{summary['no_trade']} / {summary['watch']}")
metric_cols[2].metric("运行中虚拟订单", summary["active_orders"])
metric_cols[3].metric("已平仓", summary["closed_orders"])
metric_cols[4].metric("虚拟净盈亏", f"${summary['net_pnl_usd']:+,.2f}")
metric_cols[5].metric("平均 R", "—" if summary["average_r"] is None else f"{summary['average_r']:+.2f}")
win_rate_label = "—" if summary["win_rate"] is None else f"{summary['win_rate']:.0%}"
st.caption(
    f"胜率 {win_rate_label} · "
    f"虚拟累计最大回撤 {summary['max_drawdown_pct']:.2f}% · "
    f"账户基准 ${constraints['virtual_equity_usd']:,.0f} · 单笔风险上限 {constraints['max_risk_pct']:.2%} · "
    f"费用 {constraints['fee_bps']:.1f}bp / 滑点 {constraints['slippage_bps']:.1f}bp"
)

if st.button("🔄 立即检查全部 AI 虚拟订单", type="primary", disabled=not admin_access):
    if require_admin("检查全部 AI 虚拟订单"):
        with st.spinner("正在读取 OKX 公开 1 分钟 K 线……"):
            try:
                result = run_paper_trading()
                st.session_state["ai_shadow_page_last_run"] = result
                st.success(f"检查 {result.get('checked', 0)} 笔，状态变化 {result.get('changed', 0)} 笔。")
                st.rerun()
            except Exception as exc:
                st.error(f"虚拟订单检查失败：{exc}")

last_run = st.session_state.get("ai_shadow_page_last_run") or {}
if last_run.get("errors"):
    st.warning("最近一次检查部分失败：" + "；".join(last_run["errors"][:3]))

plans = [dict(row) for row in query_ai_shadow_plans(limit=500)]
orders = [dict(row) for row in query_paper_orders(limit=500)]
st.divider()
st.subheader("影子计划")
if not plans:
    st.info("还没有 AI 影子计划。请先在“交易复盘”页保存你的计划，再主动生成独立影子计划。")
else:
    plan_df = pd.DataFrame(plans)
    plan_view = [
        "id", "created_at", "note_id", "symbol", "decision", "status", "side", "analysis_timeframe",
        "entry_price", "trigger_price", "stop_price", "target_price", "risk_reward", "risk_budget_pct",
        "planned_notional_usd", "expected_horizon", "confidence",
    ]
    plan_df["decision"] = plan_df["decision"].map(lambda value: DECISION_LABELS.get(value, value))
    plan_df["status"] = plan_df["status"].map(lambda value: STATUS_LABELS.get(value, value))
    st.dataframe(
        plan_df[[column for column in plan_view if column in plan_df.columns]].rename(columns={
            "id": "影子计划ID", "created_at": "生成时间", "note_id": "用户计划ID", "symbol": "交易对",
            "decision": "AI 决策", "status": "状态", "side": "方向", "analysis_timeframe": "技术周期",
            "entry_price": "计划入场", "trigger_price": "触发价", "stop_price": "止损", "target_price": "目标",
            "risk_reward": "R/R", "risk_budget_pct": "风险预算", "planned_notional_usd": "虚拟名义金额",
            "expected_horizon": "预期周期", "confidence": "置信度",
        }),
        use_container_width=True,
        hide_index=True,
    )

st.subheader("虚拟订单与模拟成交")
if not orders:
    st.caption("AI 目前只选择了不交易/观察，或尚未生成可执行影子计划。")
else:
    order_df = pd.DataFrame(orders)
    order_view = [
        "id", "shadow_plan_id", "symbol", "side", "order_type", "status", "entry_price",
        "trigger_price", "quantity", "filled_price", "stop_price", "target_price", "net_pnl_usd",
        "r_multiple", "close_reason", "submitted_at", "filled_at", "closed_at", "expires_at",
    ]
    order_df["order_type"] = order_df["order_type"].map(lambda value: DECISION_LABELS.get(value, value))
    order_df["status"] = order_df["status"].map(lambda value: STATUS_LABELS.get(value, value))
    st.dataframe(
        order_df[[column for column in order_view if column in order_df.columns]].rename(columns={
            "id": "虚拟订单ID", "shadow_plan_id": "影子计划ID", "symbol": "交易对", "side": "方向",
            "order_type": "订单类型", "status": "状态", "entry_price": "计划入场", "trigger_price": "触发价",
            "quantity": "虚拟数量", "filled_price": "模拟成交价", "stop_price": "止损", "target_price": "目标",
            "net_pnl_usd": "净虚拟盈亏", "r_multiple": "R 倍数", "close_reason": "平仓原因",
            "submitted_at": "提交时间", "filled_at": "成交时间", "closed_at": "平仓时间", "expires_at": "挂单到期",
        }),
        use_container_width=True,
        hide_index=True,
    )
    order_options = {
        f"#{item['id']} · {item['symbol']} · {STATUS_LABELS.get(item['status'], item['status'])}": item["id"]
        for item in orders
    }
    selected_label = st.selectbox("查看虚拟订单事件", list(order_options), key="ai_shadow_order_events")
    selected_order_id = order_options[selected_label]
    events = [dict(row) for row in query_paper_order_events(selected_order_id, limit=100)]
    if events:
        event_df = pd.DataFrame(events)
        st.dataframe(
            event_df[[column for column in ("event_at", "event_type", "from_status", "to_status", "price", "reason") if column in event_df.columns]],
            use_container_width=True,
            hide_index=True,
        )

with st.expander("模拟与统计边界", expanded=False):
    st.markdown(
        "- 虚拟订单不考虑盘口排队、流动性冲击和部分成交；触价即全额成交是一个简化假设。\n"
        "- 市价与条件市价按固定滑点；进出场都计入固定费率。\n"
        "- 同一根 K 线同时触及止损和止盈时，按更不利的止损结算。\n"
        "- 指标用于观察 AI 与自己在相同环境下的计划质量，不是实盘绩效承诺。"
    )
