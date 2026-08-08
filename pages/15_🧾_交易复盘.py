"""Crypto 真实交易日志与事后 AI 点评。"""
import json
import os

import pandas as pd
import streamlit as st

from db.repository import (
    insert_trade_note,
    query_latest_trade_account_snapshot,
    query_trade_ai_reviews,
    query_trade_fills,
    query_trade_notes,
    query_trade_orders,
    query_trade_plan_feedback,
    query_trade_positions,
)
from db.schema import init_db
from services.access_control import render_admin_access, require_admin
from services.trade_review import review_trade_note
from services.okx_readonly import OKXReadOnlyClient, sync_okx_readonly_account
from services.trade_plan_context import build_trade_plan_snapshot
from services.trade_plan_feedback import generate_trade_plan_feedback


st.set_page_config(page_title="交易复盘", page_icon="🧾", layout="wide")
admin_access = render_admin_access()
st.title("🧾 Crypto 交易复盘")
st.caption("只记录真实交易与事后点评；本页没有下单接口，也不会接入虚拟成交。")
init_db()

st.info(
    "交易计划会保存当时的宏观、新闻、数据新鲜度和 OKX 公开 K 线快照。"
    "“计划环境反馈”由你主动触发，只指出证据、矛盾和风险，不批准、阻止或执行交易；"
    "OKX 账户同步也始终只读。"
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

symbol_options = sorted({
    str(row["symbol"])
    for row in list(positions) + list(orders) + list(fills)
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
            candles = okx_client.fetch_candles(chart_symbol, bar=chart_bar, limit=200)
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
                chart_fills = [dict(row) for row in fills if row["symbol"] == chart_symbol]
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
                fig.update_layout(height=520, xaxis_rangeslider_visible=False, margin={"l": 20, "r": 20, "t": 30, "b": 20})
                st.plotly_chart(fig, use_container_width=True)
                last = candles[-1]
                first = candles[0]
                return_pct = ((last["close"] / first["close"] - 1) * 100) if first.get("close") else None
                st.session_state["okx_market_context"] = {
                    "symbol": chart_symbol, "bar": chart_bar, "last_candle": last,
                    "window_return_pct": return_pct, "fill_count_on_chart": len(chart_fills),
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
        plan_status = st.selectbox("计划状态", ["planned", "executed", "cancelled"], format_func={
            "planned": "计划中", "executed": "已执行", "cancelled": "已取消",
        }.get)
    c5, c6, c7 = st.columns(3)
    with c5:
        stop_price = st.number_input("价格止损（可选）", min_value=0.0, value=0.0, format="%.8f")
        target_price = st.number_input("目标价（可选）", min_value=0.0, value=0.0, format="%.8f")
    with c6:
        order_id = st.text_input("订单 ID（可稍后补）")
        plan_expires_at = st.text_input("计划到期时间（可选）", placeholder="例如 2026-08-09 08:00")
    with c7:
        entry_trigger = st.text_input("明确入场触发", placeholder="例如 1H 跌破后反抽不过")
        time_stop = st.text_input("时间止损", placeholder="例如 8小时未扩散则退出/重做")
    setup = st.text_input("技术形态/交易结构", placeholder="突破、回踩、趋势延续、区间边缘……")
    thesis = st.text_area("为什么做这笔交易？", placeholder="记录宏观、新闻、市场结构和你当时的判断……")
    risk_note = st.text_area("风险与失效条件", placeholder="什么情况下承认判断错误？仓位/杠杆有什么限制？")
    saved = st.form_submit_button("保存计划与环境快照", type="primary", disabled=not admin_access)

if saved and require_admin("保存交易记录"):
    clean_symbol = symbol.strip().upper()
    if not clean_symbol or not thesis.strip():
        st.error("交易对和交易理由不能为空。")
    else:
        plan_payload = {
            "venue": venue, "symbol": clean_symbol, "side": side, "trade_type": trade_type,
            "expected_horizon": horizon, "macro_horizon": macro_horizon,
            "analysis_timeframe": analysis_timeframe,
        }
        with st.spinner("正在保存计划当时的宏观、新闻和公开市场快照……"):
            try:
                plan_snapshot = build_trade_plan_snapshot(plan_payload)
            except Exception as exc:
                plan_snapshot = {
                    "snapshot_version": "trade-plan-context-v1",
                    "collection_errors": [f"计划快照生成失败：{str(exc)[:240]}"],
                }
            note_id = insert_trade_note(
                venue=venue,
                symbol=clean_symbol,
                order_id=order_id.strip(),
                side=side,
                thesis=thesis.strip(),
                setup=setup.strip(),
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
            f"状态：{selected['plan_status'] or 'planned'} · 到期：{selected['plan_expires_at'] or '—'} · "
            f"订单：{selected['order_id'] or '待同步'}"
        )

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
        df[["id", "created_at", "venue", "symbol", "side", "trade_type", "order_id", "expected_horizon", "plan_status"]].rename(columns={
            "id": "ID", "created_at": "记录时间", "venue": "交易所", "symbol": "交易对",
            "side": "方向", "trade_type": "交易类型", "order_id": "订单ID", "expected_horizon": "预期周期",
            "plan_status": "计划状态",
        }),
        use_container_width=True,
        hide_index=True,
    )
