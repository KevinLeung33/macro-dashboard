import streamlit as st
import pandas as pd

from db.repository import query_series
from services.dashboard_overview import build_cross_asset_tape, render_horizon_guidance, render_quality_strip, render_snapshot_cards
from services.market_data import query_market_series
from utils.chart_utils import line_chart, multi_line_chart, dual_axis_chart, add_range_selector, plotly_config, render_chart_controls
from utils.event_overlays import add_event_markers, get_chart_events
from utils.navigation import apply_target_window, go_to_research, render_research_target


st.set_page_config(page_title="市场数据", page_icon="📊", layout="wide")
st.title("📊 市场数据")
cfg = plotly_config()
target = render_research_target()
render_chart_controls()


def _q(source, series_id):
    return apply_target_window(query_series(source, series_id), target)


def _market(series_id):
    frame, meta = query_market_series(series_id)
    return apply_target_window(frame, target), meta


def _show(fig, note=""):
    st.plotly_chart(fig, use_container_width=True, config=cfg)
    if note:
        st.caption(note)


def _summary():
    render_horizon_guidance("market")
    render_snapshot_cards(build_cross_asset_tape(["risk", "rates", "fx", "commodity"]), columns=4)
    render_quality_strip(["fred", "yfinance"], title="市场数据质量")
    st.info("优先观察：股债是否同向、美元是否走强、VIX 是否确认风险偏好变化。详细数据中再核对各自历史结构。")


def _details():
    tab1, tab2 = st.tabs(["美股与风险", "汇率与大宗"])
    with tab1:
        sp = _q("fred", "SP500")
        nasdaq = _q("fred", "NASDAQCOM")
        if not sp.empty:
            dfs = {"标普500": sp}
            if not nasdaq.empty: dfs["纳斯达克"] = nasdaq
            _show(add_range_selector(multi_line_chart(dfs, "美股指数", "点")), "纳指相对标普走强通常代表成长风格占优。")
        dji = _q("fred", "DJIA")
        if not dji.empty:
            _show(add_range_selector(line_chart(dji, "道琼斯工业指数", "点", color="#2ca02c")), "道指偏传统行业，用于观察价值/防御风格。")
        vix = _q("fred", "VIXCLS")
        if not vix.empty:
            st.subheader("VIX 恐慌指数")
            _show(add_event_markers(add_range_selector(line_chart(vix, "VIX", "点", color="#d62728")), get_chart_events(asset="SP500", event_types=["credit", "geopolitics", "liquidity"], start_date=vix["date"].min())), "VIX 上升需要结合信用利差和指数价格确认。")
        qqq, mags, tlt = _market("QQQ")[0], _market("MAGS")[0], _market("TLT")[0]
        etf_frames = {label: frame for label, frame in (("QQQ", qqq), ("MAGS", mags), ("TLT", tlt)) if not frame.empty}
        if etf_frames:
            _show(add_range_selector(multi_line_chart(etf_frames, "重点 ETF", "美元")), "QQQ/MAGS 观察成长风格，TLT 观察久期资产定价。")
    with tab2:
        dxy, dxy_meta = _market("DX-Y.NYB")
        if not dxy.empty:
            _show(add_event_markers(add_range_selector(line_chart(dxy, "美元指数 DXY", "点", color="#1f77b4")), get_chart_events(asset="DXY", event_types=["fed_policy", "liquidity", "inflation", "growth"], start_date=dxy["date"].min())), f"DXY provider={dxy_meta['provider']}。")
        eu = _q("fred", "DEXUSEU")
        jp = _q("fred", "DEXJPUS")
        usdjpy, _ = _market("USDJPY=X")
        fx_frames = {"USD/EUR": eu, "JPY/USD": jp}
        if not usdjpy.empty: fx_frames["USDJPY"] = usdjpy
        fx_frames = {label: frame for label, frame in fx_frames.items() if not frame.empty}
        if len(fx_frames) >= 2:
            _show(add_range_selector(dual_axis_chart(fx_frames, "主要汇率", "汇率", "汇率")), "美元、日元和人民币要结合利率差与风险偏好一起判断。")
        oil = _q("fred", "DCOILWTICO")
        copper = _q("fred", "PCOPPUSDM")
        gold, _ = _market("GC=F")
        commodity = {label: frame for label, frame in (("WTI", oil), ("铜", copper), ("黄金", gold)) if not frame.empty}
        if commodity:
            _show(add_range_selector(multi_line_chart(commodity, "大宗商品", "价格")), "黄金偏避险/实际利率，铜偏全球需求，原油兼具供给与地缘属性。")


def _evidence():
    st.info("如果股市上涨但美元、实际利率和信用利差同时恶化，先不要把上涨直接解释为风险偏好改善；建议联动查看流动性与信用页。")
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
