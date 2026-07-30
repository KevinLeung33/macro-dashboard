"""信号复盘 — 组合信号出现后的资产表现"""
import pandas as pd
import streamlit as st

from db.schema import init_db
from db.repository import (
    query_composite_signal_reviews,
    query_composite_signal_snapshots,
    query_signal_review_summary,
)
from services.signal_review import save_signal_snapshots
from services.signal_stats import signal_effectiveness


st.set_page_config(page_title="信号复盘", page_icon="📈", layout="wide")
st.title("📈 信号复盘")
st.caption("记录组合信号出现时的资产价格，并随着新数据到来刷新 1D/3D/7D 后续表现。")

init_db()

c1, c2 = st.columns([2, 1])
with c1:
    st.caption("当前版本使用“后续第 N 个可用数据点”计算收益，适合处理周末和市场休市。")
with c2:
    if st.button("保存/刷新今日信号", use_container_width=True, type="primary"):
        with st.spinner("正在保存组合信号并刷新复盘..."):
            result = save_signal_snapshots()
        st.success(f"已保存 {result['saved']} 个信号，刷新 {result['reviewed']} 条资产复盘")

st.divider()

stats = signal_effectiveness()
if stats["by_signal"]:
    st.subheader("信号有效性统计")
    top = stats.get("top_signal")
    m1, m2, m3 = st.columns(3)
    with m1:
        st.metric("复盘样本", stats["sample_count"])
    with m2:
        st.metric("覆盖信号", len(stats["by_signal"]))
    with m3:
        st.metric("最大7D均值信号", top["signal_name"] if top else "—", f"{top['avg_7d']:+.2f}%" if top and top.get("avg_7d") is not None else None)

    summary_df = pd.DataFrame(stats["by_signal"])
    display_cols = [
        "signal_name", "review_count",
        "valid_1d", "avg_1d", "positive_rate_1d",
        "valid_3d", "avg_3d", "positive_rate_3d",
        "valid_7d", "avg_7d", "positive_rate_7d",
    ]
    for col in ["avg_1d", "avg_3d", "avg_7d", "positive_rate_1d", "positive_rate_3d", "positive_rate_7d"]:
        if col in summary_df:
            summary_df[col] = summary_df[col].map(lambda x: None if x is None else round(x, 2))
    st.dataframe(
        summary_df[display_cols].rename(columns={
            "signal_name": "信号",
            "review_count": "总样本",
            "valid_1d": "1D有效",
            "avg_1d": "1D均值%",
            "positive_rate_1d": "1D上涨占比%",
            "valid_3d": "3D有效",
            "avg_3d": "3D均值%",
            "positive_rate_3d": "3D上涨占比%",
            "valid_7d": "7D有效",
            "avg_7d": "7D均值%",
            "positive_rate_7d": "7D上涨占比%",
        }),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("还没有复盘样本。点击右上角保存今日信号，或等每日沉淀自动生成。")

snapshots = query_composite_signal_snapshots(limit=50)
reviews = query_composite_signal_reviews(limit=300)

tab1, tab2, tab3 = st.tabs(["信号快照", "资产复盘明细", "信号×资产"])

with tab1:
    if not snapshots:
        st.info("暂无信号快照。")
    else:
        for row in snapshots:
            icon = {"red": "🔴", "yellow": "🟡", "green": "🟢", "blue": "🔵"}.get(row["level"], "⚪")
            st.markdown(f"**{icon} {row['signal_date']} · {row['signal_name']}**")
            st.caption(f"{row['category']} · {row['direction']} · score {row['score']}/{row['max_score']} · 资产 {row['assets'] or '—'}")
            st.write(row["summary"] or "暂无摘要")
            st.divider()

with tab2:
    if not reviews:
        st.info("暂无资产复盘明细。")
    else:
        df = pd.DataFrame([dict(r) for r in reviews])
        for col in ["return_1d", "return_3d", "return_7d"]:
            df[col] = df[col].map(lambda x: None if x is None else round(x, 2))
        st.dataframe(
            df.rename(columns={
                "signal_date": "日期",
                "signal_name": "信号",
                "level": "级别",
                "score": "分数",
                "max_score": "满分",
                "asset": "资产",
                "start_date": "起点日期",
                "start_value": "起点价格",
                "return_1d": "1D%",
                "return_3d": "3D%",
                "return_7d": "7D%",
                "updated_at": "更新时间",
            })[
                ["日期", "信号", "级别", "分数", "满分", "资产", "起点日期", "起点价格", "1D%", "3D%", "7D%", "更新时间"]
            ],
            use_container_width=True,
            hide_index=True,
        )

with tab3:
    rows = stats.get("by_signal_asset", [])
    if not rows:
        st.info("暂无信号×资产统计。")
    else:
        df = pd.DataFrame(rows)
        for col in ["avg_1d", "avg_3d", "avg_7d", "positive_rate_1d", "positive_rate_3d", "positive_rate_7d"]:
            if col in df:
                df[col] = df[col].map(lambda x: None if x is None else round(x, 2))
        st.dataframe(
            df[[
                "signal_name", "asset", "review_count",
                "valid_7d", "avg_7d", "median_7d", "positive_rate_7d",
                "avg_3d", "positive_rate_3d",
            ]].rename(columns={
                "signal_name": "信号",
                "asset": "资产",
                "review_count": "总样本",
                "valid_7d": "7D有效",
                "avg_7d": "7D均值%",
                "median_7d": "7D中位数%",
                "positive_rate_7d": "7D上涨占比%",
                "avg_3d": "3D均值%",
                "positive_rate_3d": "3D上涨占比%",
            }),
            use_container_width=True,
            hide_index=True,
        )
