import logging

import pandas as pd
import streamlit as st

from config.series_definitions import AKSHARE_SERIES
from db.repository import add_event, query_events, query_series
from services.dashboard_overview import build_cross_asset_tape, render_horizon_guidance, render_quality_strip, render_snapshot_cards
from services.market_data import query_market_series
from utils.chart_utils import line_chart, multi_line_chart, dual_axis_chart, add_range_selector, plotly_config, render_chart_controls, horizontal_bar
from utils.indicators import latest_value
from utils.navigation import apply_target_window, go_to_research, render_research_target


st.set_page_config(page_title="全球市场", page_icon="🌍", layout="wide")
st.title("🌍 全球市场")
cfg = plotly_config()
target = render_research_target()
render_chart_controls()
logger = logging.getLogger(__name__)


def _q(source, series_id):
    return apply_target_window(query_series(source, series_id), target)


def _ak(series_id):
    if not AKSHARE_SERIES.get(series_id, {}).get("enabled", True):
        return pd.DataFrame(columns=["date", "value"])
    return _q("akshare", series_id)


def _market(series_id):
    frame, meta = query_market_series(series_id)
    return apply_target_window(frame, target), meta


def _show(fig, note=""):
    st.plotly_chart(fig, use_container_width=True, config=cfg)
    if note:
        st.caption(note)


def _summary():
    render_horizon_guidance("global")
    render_snapshot_cards(build_cross_asset_tape(["china", "fx", "commodity"]), columns=4)
    pmi = _ak("CN_PMI")
    cpi = _ak("CN_CPI")
    pmi_value = latest_value(pmi)
    cpi_value = latest_value(cpi)
    if pmi_value is not None:
        pmi_state = "扩张" if pmi_value >= 50 else "收缩"
        st.info(f"中国周期：官方 PMI {pmi_value:.1f}，当前处于{pmi_state}区间。CPI同比最新读数 {cpi_value:.1f}。请在详细数据中核对日期和源状态。" if cpi_value is not None else f"中国周期：官方 PMI {pmi_value:.1f}，当前处于{pmi_state}区间。")
    else:
        st.warning("中国宏观摘要暂无有效 PMI 数据。")
    render_quality_strip(["akshare", "yfinance", "akshare_hk_index"], title="全球市场摘要数据质量")


def _details():
    st.subheader("中国宏观周期")
    cn_pmi, cn_cx = _ak("CN_PMI"), _ak("CN_CAIXIN_PMI")
    cn_cpi, cn_ppi, cn_m2 = _ak("CN_CPI"), _ak("CN_PPI"), _ak("CN_M2_YOY")
    if not cn_pmi.empty:
        dfs = {"官方PMI": cn_pmi}
        if not cn_cx.empty: dfs["财新PMI"] = cn_cx
        _show(add_range_selector(multi_line_chart(dfs, "中国 PMI", "")), "PMI 大于 50 通常代表制造业扩张；官方与财新分歧时要观察结构差异。")
    if not cn_cpi.empty or not cn_ppi.empty:
        dfs = {}
        if not cn_cpi.empty: dfs["CPI同比"] = cn_cpi
        if not cn_ppi.empty: dfs["PPI同比"] = cn_ppi
        _show(add_range_selector(multi_line_chart(dfs, "中国通胀", "%")), "CPI 看需求，PPI 看工业品价格和企业利润压力。")
    if not cn_m2.empty:
        _show(add_range_selector(line_chart(cn_m2, "中国 M2 同比", "%", color="#17becf")), "M2 是流动性背景指标，不应单独替代社融或信用脉冲。")
    lpr = _q("akshare", "CN_LPR_1Y")
    if not lpr.empty:
        _show(add_range_selector(line_chart(lpr, "LPR 1年期", "%", color="#d62728")), "LPR 反映贷款定价环境。")
    paused = [meta.get("display_name", sid).replace("🇨🇳 ", "") for sid, meta in AKSHARE_SERIES.items() if not meta.get("enabled", True)]
    if paused:
        st.caption("质量保护：" + "、".join(paused) + " 暂不纳入实时结论。")

    st.subheader("中国资产与人民币")
    usdcnh, _ = _market("USDCNH=X")
    usdcny = _q("yfinance", "USDCNY=X")
    csi300, _ = _market("000300.SS")
    chinext = _q("yfinance", "399006.SZ")
    hstech, _ = _market("HSTECH")
    assets = {label: frame for label, frame in (("USDCNH", usdcnh), ("沪深300", csi300), ("创业板指", chinext), ("恒生科技", hstech), ("USDCNY", usdcny)) if not frame.empty}
    if assets:
        normalized = {}
        for label, frame in assets.items():
            item = frame.copy()
            first = item["value"].iloc[0]
            if first not in (None, 0):
                item["value"] = item["value"] / first * 100
            normalized[label] = item
        _show(add_range_selector(multi_line_chart(normalized, "中国资产与人民币相对走势", "起点=100")), "这里比较相对变化；绝对价格请在各自详细序列中查看。")

    st.subheader("全球联动")
    dxy, dxy_meta = _market("DX-Y.NYB")
    eu, jp = _q("fred", "DEXUSEU"), _q("fred", "DEXJPUS")
    if not dxy.empty:
        _show(add_range_selector(line_chart(dxy, "DXY", "点", color="#1f77b4")), f"DXY provider={dxy_meta['provider']}。")
    if not eu.empty and not jp.empty:
        _show(add_range_selector(dual_axis_chart({"USD/EUR": eu, "JPY/USD": jp}, "主要汇率", "USD/EUR", "JPY/USD")), "美元、欧元和日元需要结合利差与风险偏好判断。")
    oil, copper = _q("fred", "DCOILWTICO"), _q("fred", "PCOPPUSDM")
    if not oil.empty or not copper.empty:
        _show(add_range_selector(multi_line_chart({k: v for k, v in (("WTI", oil), ("铜", copper)) if not v.empty}, "油铜与全球需求", "价格")), "铜偏全球工业需求，油价还包含供给和地缘冲击。")


def _evidence():
    with st.expander("添加人工事件", expanded=False):
        with st.form("global_event"):
            event_date = st.date_input("日期")
            title = st.text_input("标题")
            category = st.selectbox("类别", ["market", "geopolitics", "fed", "data_release", "crypto", "global_cycle"])
            impact = st.select_slider("重要度", ["low", "medium", "high"])
            if st.form_submit_button("添加") and title:
                add_event(str(event_date), title, "", category, impact)
                st.success("已添加事件")
    rows = query_events(20)
    if rows:
        for row in rows:
            icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(row["impact"], "⚪")
            st.caption(f"{icon} {row['date']} · {row['category']} · {row['title']}")
    else:
        st.info("暂无人工事件；新闻事件请在新闻雷达查看。")


summary_tab, detail_tab, evidence_tab = st.tabs(["状态总览", "详细数据", "事件与证据"])
with summary_tab:
    _summary()
with detail_tab:
    _details()
with evidence_tab:
    _evidence()
