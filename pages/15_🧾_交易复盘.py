"""Crypto 真实交易日志与事后 AI 点评。"""
import json

import pandas as pd
import streamlit as st

from db.repository import (
    insert_trade_note,
    query_trade_ai_reviews,
    query_trade_notes,
)
from db.schema import init_db
from services.access_control import render_admin_access, require_admin
from services.trade_review import review_trade_note


st.set_page_config(page_title="交易复盘", page_icon="🧾", layout="wide")
admin_access = render_admin_access()
st.title("🧾 Crypto 交易复盘")
st.caption("只记录真实交易与事后点评；本页没有下单接口，也不会接入虚拟成交。")
init_db()

st.info(
    "下单前只填写自己的交易理由、止损和目标；AI 只在你主动点击“生成 AI 点评”后运行，"
    "不参与下单前的批准或质疑。交易所只读 API 与 K 线/账户同步将在后续接入。"
)

st.subheader("记录交易计划/成交理由")
with st.form("trade_note_form", clear_on_submit=False):
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        venue = st.selectbox("交易所", ["Binance", "OKX", "Other"])
        symbol = st.text_input("交易对", placeholder="BTCUSDT")
    with c2:
        side = st.selectbox("方向", ["LONG", "SHORT"])
        order_id = st.text_input("订单 ID（可稍后补）")
    with c3:
        stop_price = st.number_input("止损价（可选）", min_value=0.0, value=0.0, format="%.8f")
        target_price = st.number_input("目标价（可选）", min_value=0.0, value=0.0, format="%.8f")
    with c4:
        horizon = st.selectbox("预期持仓周期", ["小时", "1-3天", "1-2周", "更长", "未设定"])
        setup = st.text_input("技术形态/触发条件", placeholder="突破、回踩、趋势延续……")
    thesis = st.text_area("为什么做这笔交易？", placeholder="记录宏观、新闻、市场结构和你当时的判断……")
    risk_note = st.text_area("风险与失效条件", placeholder="什么情况下承认判断错误？仓位/杠杆有什么限制？")
    saved = st.form_submit_button("保存交易记录", type="primary", disabled=not admin_access)

if saved and require_admin("保存交易记录"):
    clean_symbol = symbol.strip().upper()
    if not clean_symbol or not thesis.strip():
        st.error("交易对和交易理由不能为空。")
    else:
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
        )
        st.session_state["selected_trade_note_id"] = note_id
        st.success(f"已保存交易记录 #{note_id}。现在不会自动调用 AI。")

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
        st.caption(f"形态/触发：{selected['setup'] or '—'} · 预期周期：{selected['expected_horizon'] or '—'}")
    with detail_right:
        st.markdown(f"**风险与失效条件**\n\n{selected['risk_note'] or '—'}")
        st.caption(f"止损：{selected['stop_price'] or '—'} · 目标：{selected['target_price'] or '—'} · 订单：{selected['order_id'] or '待同步'}")

    if st.button("🧠 生成 AI 交易点评", type="primary", disabled=not admin_access):
        if require_admin("生成 AI 交易点评"):
            with st.spinner("AI 正在复盘这笔已记录交易……"):
                try:
                    review_trade_note(selected_id)
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
        df[["id", "created_at", "venue", "symbol", "side", "order_id", "expected_horizon"]].rename(columns={
            "id": "ID", "created_at": "记录时间", "venue": "交易所", "symbol": "交易对",
            "side": "方向", "order_id": "订单ID", "expected_horizon": "预期周期",
        }),
        use_container_width=True,
        hide_index=True,
    )
