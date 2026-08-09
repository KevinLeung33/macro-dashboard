import streamlit as st

from db.repository import query_series
from services.dashboard_overview import build_cross_asset_tape, render_quality_strip, render_snapshot_cards
from utils.chart_utils import line_chart, dual_axis_chart, add_range_selector, plotly_config, render_chart_controls
from utils.event_overlays import add_event_markers, get_chart_events
from utils.indicators import latest_value
from utils.navigation import apply_target_window, go_to_research, render_research_target


st.set_page_config(page_title="信用与风险", page_icon="🛡️", layout="wide")
st.title("🛡️ 信用与风险")
cfg = plotly_config(); target = render_research_target(); render_chart_controls()


def _q(sid): return apply_target_window(query_series("fred", sid), target)
def _show(fig, note=""):
    st.plotly_chart(fig, use_container_width=True, config=cfg)
    if note: st.caption(note)


def _summary():
    render_snapshot_cards(build_cross_asset_tape(["risk", "rates", "credit"]), columns=4)
    hy = _q("BAMLH0A0HYM2"); nfci = _q("NFCI"); vix = _q("VIXCLS")
    hy_v, nfci_v, vix_v = latest_value(hy), latest_value(nfci), latest_value(vix)
    if hy_v is not None:
        state = "信用紧张" if hy_v > 500 else ("信用观察" if hy_v > 350 else "信用相对宽松")
        st.info(f"风险状态：HY OAS {hy_v:.0f}bp（{state}）" + (f"，NFCI {nfci_v:.2f}" if nfci_v is not None else "") + (f"，VIX {vix_v:.1f}" if vix_v is not None else "") + "。")
    render_quality_strip(["fred", "yfinance"], title="信用与风险摘要数据质量")


def _details():
    st.subheader("信用压力")
    hy, t10, nfci = _q("BAMLH0A0HYM2"), _q("T10Y3M"), _q("NFCI")
    if not hy.empty:
        dfs = {"HY OAS(bp)": hy}
        if not t10.empty:
            curve = t10.copy(); curve["value"] *= 100; dfs["10Y-3M(bp)"] = curve
        _show(add_event_markers(add_range_selector(dual_axis_chart(dfs, "高收益利差 vs 期限利差", "bp", "bp")), get_chart_events(asset=["SP500", "NASDAQ", "BTC"], event_types=["credit", "liquidity", "fed_policy"], start_date=hy["date"].min())), "HY OAS 和期限利差同步恶化时，风险更偏系统性。")
    if not nfci.empty:
        _show(add_range_selector(line_chart(nfci, "金融条件指数 NFCI", "", color="#d62728")), "NFCI 大于 0 通常代表金融条件偏紧。")

    st.subheader("风险情绪")
    vix, sp = _q("VIXCLS"), _q("SP500")
    c1, c2 = st.columns(2)
    with c1:
        if not vix.empty: _show(add_range_selector(line_chart(vix, "VIX", "点", color="#d62728")), "VIX 上升要和信用利差确认。")
    with c2:
        if not sp.empty:
            frame = sp.copy(); frame["value"] = frame["value"].pct_change(252) * 100; frame = frame.dropna()
            _show(add_range_selector(line_chart(frame, "标普滚动一年回报", "%", color="#2ca02c")), "长期回报不等于短期风险，需结合波动和信用。")

    st.subheader("实际利率与通胀预期")
    tips, breakeven = _q("DFII10"), _q("T10YIE")
    c1, c2 = st.columns(2)
    with c1:
        if not tips.empty: _show(add_range_selector(line_chart(tips, "10Y TIPS实际利率", "%", color="#1f77b4")), "实际利率上行通常压制黄金、BTC和成长股估值。")
    with c2:
        if not breakeven.empty: _show(add_range_selector(line_chart(breakeven, "10Y盈亏平衡通胀率", "%", color="#ff7f0e")), "观察通胀预期是否重新脱锚。")

    st.subheader("消费者与能源")
    confidence, retail = _q("UMCSENT"), _q("RSAFS")
    if not confidence.empty: _show(add_range_selector(line_chart(confidence, "消费者信心", "", color="#2ca02c")), "信心是领先项，消费落地需要看零售销售。")
    if not retail.empty:
        retail = retail.copy(); retail["value"] /= 1000
        _show(add_range_selector(line_chart(retail, "零售销售", "十亿美元", color="#9467bd")))
    oil, gas = _q("DCOILWTICO"), _q("DHHNGSP")
    if not oil.empty: _show(add_range_selector(dual_axis_chart({"WTI": oil, "天然气": gas} if not gas.empty else {"WTI": oil}, "能源", "$/桶", "$/百万BTU")), "能源价格同时包含供给、地缘和需求信息。")


def _evidence():
    st.info("判断风险资产时，优先看 HY OAS、NFCI、VIX 是否同向恶化；单一 VIX 高点不等于系统性危机。")
    if st.button("查看流动性", use_container_width=True):
        go_to_research("pages/8_💧_流动性.py", "流动性", "3M")
    if st.button("查看历史对比", use_container_width=True):
        go_to_research("pages/7_⏳_历史对比.py", "历史对比", "3M")


summary_tab, detail_tab, evidence_tab = st.tabs(["状态总览", "详细数据", "事件与证据"])
with summary_tab: _summary()
with detail_tab: _details()
with evidence_tab: _evidence()
