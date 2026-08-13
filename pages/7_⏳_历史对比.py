import pandas as pd
import streamlit as st

from db.repository import query_series
from services.dashboard_overview import render_horizon_guidance, render_quality_strip, render_snapshot_cards
from utils.chart_utils import line_chart, add_range_selector, plotly_config, render_chart_controls
from utils.indicators import latest_value, yoy_series
from utils.navigation import go_to_research


st.set_page_config(page_title="历史对比", page_icon="⏳", layout="wide")
st.title("⏳ 历史对比：现在像哪一年？")
cfg = plotly_config(); render_chart_controls()


def _q(sid): return query_series("fred", sid)
def _show(fig, note=""):
    st.plotly_chart(fig, use_container_width=True, config=cfg)
    if note: st.caption(note)


def _summary():
    render_horizon_guidance("history")
    rows = []
    for sid, label, unit in (("SP500", "标普500", "点"), ("UNRATE", "失业率", "%"), ("T10Y3M", "10Y-3M利差", "%")):
        df = _q(sid)
        if not df.empty:
            rows.append({"label": label, "unit": unit, "value": latest_value(df), "date": str(df.iloc[-1]["date"])[:10], "source": "fred", "status": "ok"})
    render_snapshot_cards(rows, columns=3)
    st.info("历史对比用于校验风险结构，不是寻找一个完全相同的历史剧本；优先比较传导链和数据组合。")
    render_quality_strip(["fred"], title="历史对比数据质量")


def _details():
    st.subheader("标普500：当前 vs 历次危机")
    sp = _q("SP500")
    if not sp.empty:
        frame = sp.copy(); frame["value"] = frame["value"] / frame["value"].iloc[0] * 100
        _show(add_range_selector(line_chart(frame, "标普500归一化走势", "起点=100")), "归一化后比较跌幅与修复速度，不比较绝对点位。")
    st.subheader("失业率：拐点比较")
    unrate = _q("UNRATE")
    if not unrate.empty: _show(add_range_selector(line_chart(unrate, "失业率", "%", color="#d62728")), "失业率通常滞后于增长和信用拐点。")
    st.subheader("10Y-3M利差：倒挂到衰退的时间线")
    curve = _q("T10Y3M")
    if not curve.empty: _show(add_range_selector(line_chart(curve, "10Y-3M利差", "%", color="#ff7f0e")), "回正不代表风险立即消失，还要看信用与就业。")
    st.subheader("增长与通胀组合")
    indpro, cpi = _q("INDPRO"), _q("CPIAUCSL")
    frames = {}
    if not indpro.empty: frames["工业产出同比"] = yoy_series(indpro)
    if not cpi.empty: frames["CPI同比"] = yoy_series(cpi)
    if frames: _show(add_range_selector(line_chart(next(iter(frames.values())), "增长/通胀详细序列", "%")), "需要在详细数据中分别核对两个序列，避免不同频率造成错觉。")


def _evidence():
    st.info("历史样本只能提供风险边界，不能替代当前的政策、信用和市场定价。建议结合货币政策、信用与流动性页。")
    if st.button("查看货币政策", use_container_width=True):
        go_to_research("pages/1_💵_货币政策.py", "货币政策", "3M")
    if st.button("查看信用与风险", use_container_width=True):
        go_to_research("pages/5_🛡️_信用与风险.py", "信用与风险", "3M")


summary_tab, detail_tab, evidence_tab = st.tabs(["状态总览", "详细数据", "事件与证据"])
with summary_tab: _summary()
with detail_tab: _details()
with evidence_tab: _evidence()
