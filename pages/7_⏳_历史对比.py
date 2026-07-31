"""历史对比 — 当前 vs 重大危机前夜"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from db.repository import query_series
from utils.chart_utils import add_range_selector, plotly_config, render_chart_controls
from services.time_utils import app_now

st.set_page_config(page_title="历史对比", page_icon="⏳", layout="wide")
st.title("⏳ 历史对比：现在像哪一年？")
render_chart_controls()
cfg = plotly_config()
def _show(fig, note=""):
    st.plotly_chart(fig, use_container_width=True, config=cfg)
    if note: st.caption(note)

def align_from_peak(df, peak_date_str, label="", months=48):
    """从指定日期截取前后N个月，对齐到月份0"""
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    peak = pd.to_datetime(peak_date_str)
    df["offset"] = ((df["date"] - peak).dt.days / 30.44).round().astype(int)
    df = df[(df["offset"] >= -6) & (df["offset"] <= months)]
    df["label"] = label
    return df[["offset", "value", "label"]]

st.subheader("标普500：当前 vs 历次危机")

# Load current SP500
sp_raw = query_series("fred", "SP500")
if not sp_raw.empty:
    current_anchor = pd.to_datetime(sp_raw["date"].max()).strftime("%Y-%m-%d")
    now = align_from_peak(sp_raw, current_anchor, "当前数据", 36)
    dotcom = align_from_peak(sp_raw, "2000-03-24", "2000年 互联网泡沫", 36)
    gfc = align_from_peak(sp_raw, "2007-10-09", "2007年 次贷危机", 36)
    covid = align_from_peak(sp_raw, "2020-02-19", "2020年 COVID崩盘", 6)

    dfs = [now, dotcom, gfc, covid]
    fig = go.Figure()
    colors = ["#1f77b4", "#ff7f0e", "#d62728", "#2ca02c"]
    for i, d in enumerate(dfs):
        if d.empty: continue
        fig.add_trace(go.Scatter(
            x=d["offset"], y=d["value"] / d["value"].max() * 100,
            mode="lines", name=d["label"].iloc[0],
            line=dict(color=colors[i], width=2),
        ))
    fig.add_vline(x=0, line_dash="dash", line_color="gray", annotation_text="危机触发点")
    fig.update_layout(title="标普500走势对比（对齐到危机月=0，归一化到100）",
                      xaxis_title="危机前后月份", yaxis_title="归一化价格(100=最高)",
                      height=450)
    _show(add_range_selector(fig))
    st.caption("📖 每条线归一化到各自最高点=100；当前曲线以最新可用数据日期为锚点，适合观察形态，不代表预测。")

st.subheader("失业率：拐点比较")
ur_raw = query_series("fred", "UNRATE")
if not ur_raw.empty:
    current_anchor = pd.to_datetime(ur_raw["date"].max()).strftime("%Y-%m-%d")
    ur_now = align_from_peak(ur_raw, current_anchor, "当前数据", 36)
    ur_dc = align_from_peak(ur_raw, "2001-01-01", "2001衰退", 36)
    ur_gf = align_from_peak(ur_raw, "2008-01-01", "2008衰退", 36)

    fig2 = go.Figure()
    for i, d in enumerate([ur_now, ur_dc, ur_gf]):
        if d.empty: continue
        fig2.add_trace(go.Scatter(
            x=d["offset"], y=d["value"],
            mode="lines", name=d["label"].iloc[0],
            line=dict(color=["#1f77b4","#ff7f0e","#d62728"][i], width=2),
        ))
    fig2.update_layout(title="失业率走势对比", xaxis_title="月份", yaxis_title="%", height=400)
    _show(add_range_selector(fig2))
    st.caption("📖 2001 和 2008 的失业率都曾在低点后明显上升；请结合当前曲线的最新变化观察是否出现类似拐点。")

st.subheader("10Y-3M利差：倒挂→衰退的时间线")
t10_raw = query_series("fred", "T10Y3M")
if not t10_raw.empty:
    t10_raw["date"] = pd.to_datetime(t10_raw["date"])
    t10_raw = t10_raw.sort_values("date")

    # Find inversion periods
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(
        x=t10_raw["date"], y=t10_raw["value"],
        mode="lines", name="10Y-3M", line=dict(color="#1f77b4", width=2),
    ))
    fig3.add_hline(y=0, line_dash="dash", line_color="red", annotation_text="倒挂线")
    fig3.update_layout(title="10Y-3M利差 (灰色=衰退期)", yaxis_title="%", height=400)

    # Mark NBER recessions
    recessions = [("2001-03-01", "2001-11-01"), ("2007-12-01", "2009-06-01"), ("2020-02-01", "2020-04-01")]
    for start, end in recessions:
        fig3.add_vrect(x0=start, x1=end, fillcolor="gray", opacity=0.2, line_width=0)

    # Count current inversion days
    inverted = t10_raw[t10_raw["value"] < 0]
    if not inverted.empty:
        days = (pd.Timestamp(app_now().replace(tzinfo=None)) - inverted["date"].max()).days
        st.metric("最近倒挂已结束", f"{days}天前")
    else:
        current_inv = t10_raw[t10_raw["date"] > "2024-01-01"]
        inv_days = (current_inv["value"] < 0).sum()
        if inv_days > 0:
            first_inv = current_inv[current_inv["value"] < 0]["date"].min()
            total = (pd.Timestamp(app_now().replace(tzinfo=None)) - first_inv).days
            st.metric("当前倒挂已持续", f"{total}天")
        else:
            st.metric("10Y-3M", "未倒挂 ✅")

    _show(fig3)
    st.caption('📖 灰色阴影=NBER官方认定的衰退期。历史上倒挂后衰退风险通常上升。倒挂回正不一定等于"安全了"，回正有时发生在衰退前夕。')
