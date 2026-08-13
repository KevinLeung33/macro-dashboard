import streamlit as st
import pandas as pd

from db.repository import query_series, query_series_snapshot
from services.dashboard_overview import render_horizon_guidance, render_quality_strip, render_snapshot_cards
from utils.chart_utils import line_chart, multi_line_chart, add_range_selector, plotly_config, render_chart_controls
from utils.indicators import latest_value, scale_series, yoy_series, mom_annualized_series
from utils.navigation import apply_target_window, go_to_research, render_research_target


st.set_page_config(page_title="货币政策", page_icon="💵", layout="wide")
st.title("💵 货币政策")
cfg = plotly_config()
target = render_research_target()
render_chart_controls()


def _q(sid):
    return apply_target_window(query_series("fred", sid), target)


def _show(fig, note=""):
    st.plotly_chart(fig, use_container_width=True, config=cfg)
    if note:
        st.caption(note)


def _summary():
    render_horizon_guidance("monetary")
    rows = []
    for sid, label, unit in (
        ("FEDFUNDS", "Fed利率", "%"), ("DGS10", "10Y收益率", "%"),
        ("T10Y2Y", "10Y-2Y利差", "%"), ("DFII10", "10Y实际利率", "%"),
        ("T10YIE", "10Y通胀预期", "%"), ("CPIAUCSL", "CPI指数", ""),
    ):
        snap = query_series_snapshot("fred", sid, lookback_points=5)
        if snap:
            rows.append({
                "label": label, "unit": unit, "value": snap["value"],
                "change_5_pct": snap.get("change_n_pct"), "date": snap.get("date"),
                "source": "fred", "status": "ok",
            })
    render_snapshot_cards(rows, columns=3)
    t10 = query_series_snapshot("fred", "T10Y3M", lookback_points=5)
    if t10:
        state = "倒挂，衰退风险仍需观察" if t10["value"] < 0 else "未倒挂，继续观察增长与信用"
        st.info(f"状态判断：10Y-3M 当前 {t10['value']:.2f}%——{state}。详细数据页用于核对收益率曲线、实际利率和通胀趋势。")
    render_quality_strip(["fred"], title="货币政策摘要数据质量")


def _details():
    ff = _q("FEDFUNDS")
    wa = _q("WALCL")
    c1, c2 = st.columns(2)
    with c1:
        if not ff.empty:
            _show(add_range_selector(line_chart(ff, "联邦基金利率", "%", color="#d62728")), "政策利率是短端金融条件的锚。")
    with c2:
        if not wa.empty:
            w2 = scale_series(wa, 1e6)
            _show(add_range_selector(line_chart(w2, "美联储总资产", "万亿美元", color="#9467bd")), "扩表释放流动性，缩表回收流动性。")

    st.subheader("收益率曲线")
    d10, d2, ff2 = _q("DGS10"), _q("DGS2"), _q("FEDFUNDS")
    if not d10.empty:
        dfs = {"10Y": d10}
        if not d2.empty: dfs["2Y"] = d2
        if not ff2.empty: dfs["FF利率"] = ff2
        _show(add_range_selector(multi_line_chart(dfs, "美债收益率", "%")), "短端反映政策预期，长端反映增长与通胀预期。")

    if not d10.empty and not ff2.empty:
        m = pd.merge(d10, ff2, on="date", suffixes=("_10y", "_ff"), how="inner")
        m["value"] = (m["value_10y"] - m["value_ff"]) * 100
        st.subheader("10Y-FF 利差")
        _show(add_range_selector(line_chart(m[["date", "value"]], "10年期-联邦基金利率(bp)", "bp", color="#ff7f0e")), "利差突然走阔时需区分期限溢价上升和增长预期改善。")

    st.subheader("实际利率")
    pce = query_series("fred", "PCEPILFE")
    if not ff2.empty and not pce.empty:
        pd_p = pce.copy()
        pd_p["date"] = pd.to_datetime(pd_p["date"])
        pd_p["yoy"] = pd_p["value"].pct_change(12) * 100
        ff_d = ff2.copy()
        ff_d["date"] = pd.to_datetime(ff_d["date"])
        m2 = pd.merge(ff_d, pd_p[["date", "yoy"]], on="date", how="inner")
        m2["value"] = m2["value"] - m2["yoy"]
        _show(add_range_selector(line_chart(apply_target_window(m2[["date", "value"]], target), "实际利率(FF-核心PCE同比)", "%", color="#2ca02c")), "实际利率上升通常压制成长资产估值。")

    st.subheader("通胀")
    cpi = query_series("fred", "CPIAUCSL")
    if not cpi.empty:
        cd = apply_target_window(yoy_series(cpi), target)
        core_cpi = query_series("fred", "CPILFESL")
        core_yoy = apply_target_window(yoy_series(core_cpi), target) if not core_cpi.empty else None
        tie = _q("T10YIE")
        dfs_i = {"CPI同比": cd}
        if core_yoy is not None and not core_yoy.empty: dfs_i["核心CPI同比"] = core_yoy
        if not tie.empty: dfs_i["10Y盈亏平衡通胀"] = tie
        _show(add_range_selector(multi_line_chart(dfs_i, "通胀指标", "%")), "同比看趋势，核心通胀月度年化看拐点，盈亏平衡通胀看市场预期。")
        core_3m = mom_annualized_series(core_cpi).tail(3) if not core_cpi.empty else None
        if core_3m is not None and not core_3m.empty:
            st.caption(f"核心CPI最近月度年化：{core_3m['value'].iloc[-1]:.1f}%")


def _evidence():
    st.info("建议先看：收益率曲线是否与实际利率同向、通胀预期是否重新上行，再回到流动性和信用页确认金融条件。")
    if st.button("查看美元流动性", use_container_width=True):
        go_to_research("pages/8_💧_流动性.py", "美元流动性", "3M")
    if st.button("查看信用与风险", use_container_width=True):
        go_to_research("pages/5_🛡️_信用与风险.py", "信用与风险", "3M")


summary_tab, detail_tab, evidence_tab = st.tabs(["状态总览", "详细数据", "事件与证据"])
with summary_tab:
    _summary()
with detail_tab:
    _details()
with evidence_tab:
    _evidence()
