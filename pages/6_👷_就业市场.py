"""就业市场深度扫描"""
import streamlit as st
import pandas as pd

from db.repository import query_series
from utils.chart_utils import line_chart, multi_line_chart, dual_axis_chart, add_range_selector, plotly_config, render_chart_controls
from utils.indicators import scale_series, yoy_series
from utils.navigation import apply_target_window, render_research_target

st.set_page_config(page_title="就业市场", page_icon="👷", layout="wide")
st.title("👷 就业市场深度扫描")
cfg = plotly_config()
target = render_research_target()
render_chart_controls()
def _q(sid): return apply_target_window(query_series("fred", sid), target)
def _show(fig, note=""):
    st.plotly_chart(fig, use_container_width=True, config=cfg)
    if note: st.caption(note)

# Row 1: Unemployment + Claims
st.subheader("失业与初请")
c1, c2 = st.columns(2)
with c1:
    unemp = _q("UNRATE")
    claims = _q("ICSA")
    if not unemp.empty:
        dfs = {"失业率(%)": unemp}
        if not claims.empty: dfs["初请失业金(万)"] = scale_series(claims, 10000)
        _show(add_range_selector(dual_axis_chart(dfs, "失业率 vs 初请", "%", "万人")))
        st.caption("📖 初请=每周新增失业金申领人数，是最高频的就业指标。持续上升=裁员加速。")
with c2:
    part = _q("CIVPART")
    if not part.empty:
        _show(add_range_selector(line_chart(part, "劳动参与率", "%", color="#2ca02c")))
        st.caption("📖 参与率=就业或找工作的人口占总适龄人口比例。下降=有人退出劳动力市场(灰心工人)=失业率可能低估真实情况。")

# Row 2: JOLTS + Quits
st.subheader("职位空缺与自愿离职")
c3, c4 = st.columns(2)
with c3:
    jolts = _q("JTSJOL")
    if not jolts.empty:
        j_df = jolts.copy(); j_df["value"] = j_df["value"] / 1000
        _show(add_range_selector(line_chart(j_df, "JOLTS职位空缺(百万)", "百万", color="#1f77b4")))
        st.caption("📖 职位空缺高于失业人数通常代表劳动力市场偏紧，并可能带来工资上行压力。")
with c4:
    quits = _q("JTSQUR")
    if not quits.empty:
        _show(add_range_selector(line_chart(quits, "自主离职率(Quits Rate)", "%", color="#ff7f0e")))
        st.caption("📖 辞职率高=工人对找新工作有信心=劳动力市场强劲。辞职率下降=人们对经济前景担忧→宁愿保住现有工作。")

# Row 3: Wages + Nonfarm
st.subheader("工资与就业总量")
c5, c6 = st.columns(2)
with c5:
    wages = query_series("fred", "AHETPI")
    if not wages.empty:
        w_df = apply_target_window(yoy_series(wages), target)
        _show(add_range_selector(line_chart(w_df, "平均时薪同比", "%", color="#d62728")))
        st.caption("📖 工资涨太快→服务通胀难降→Fed不能降息。工资增速回到3-3.5%=与2%通胀目标一致。")
with c6:
    nfp = _q("PAYEMS")
    if not nfp.empty:
        n_df = nfp.copy(); n_df["value"] = n_df["value"] / 1000
        _show(add_range_selector(line_chart(n_df, "非农就业(百万)", "百万", color="#9467bd")))
        st.caption("📖 非农=美国最重要的月度经济数据。持续增长=经济在扩张。拐头向下=衰退确认。")

# Unemployment vs recession marker
st.subheader("失业率与衰退信号")
unemp2 = query_series("fred", "UNRATE")
if not unemp2.empty and len(unemp2) > 12:
    u_df = unemp2.copy()
    u_df["date"] = pd.to_datetime(u_df["date"]); u_df = u_df.sort_values("date")
    u_df["low_12m"] = u_df["value"].rolling(12).min()
    u_df.dropna(inplace=True)
    u_df["sahm"] = u_df["value"] - u_df["low_12m"]
    sahm = u_df[["date","sahm"]].rename(columns={"sahm":"value"})
    _show(add_range_selector(line_chart(apply_target_window(sahm, target), "Sahm Rule 衰退指标", "百分点", color="#d62728")))
    st.caption("📖 Sahm Rule：失业率较过去12个月低点上升 0.5 个百分点时，衰退风险显著上升；请以图中最新数据判断是否触发。")
