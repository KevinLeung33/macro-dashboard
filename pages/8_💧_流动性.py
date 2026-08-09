import streamlit as st

from db.repository import query_series
from services.dashboard_overview import render_quality_strip, render_snapshot_cards
from utils.chart_utils import line_chart, multi_line_chart, dual_axis_chart, add_range_selector, plotly_config, render_chart_controls
from utils.indicators import latest_value
from utils.navigation import apply_target_window, go_to_research, render_research_target


st.set_page_config(page_title="流动性与融资", page_icon="💧", layout="wide")
st.title("💧 流动性与融资")
cfg = plotly_config(); target = render_research_target(); render_chart_controls()


def _q(sid): return apply_target_window(query_series("fred", sid), target)
def _show(fig, note=""):
    st.plotly_chart(fig, use_container_width=True, config=cfg)
    if note: st.caption(note)


def _summary():
    rows = []
    for sid, label, unit in (("WALCL", "Fed总资产", "百万$"), ("RRPONTSYD", "RRP", "十亿$"), ("WRESBAL", "银行准备金", "十亿$"), ("WTREGEN", "TGA", "十亿$"), ("SOFR", "SOFR", "%"), ("NFCI", "NFCI", "")):
        df = _q(sid)
        if not df.empty:
            current = latest_value(df); prev = df.iloc[-6]["value"] if len(df) > 5 else None
            change = None if prev in (None, 0) else (current / prev - 1) * 100
            rows.append({"label": label, "unit": unit, "value": current, "change_5_pct": change, "date": str(df.iloc[-1]["date"])[:10], "source": "fred", "status": "ok"})
    render_snapshot_cards(rows, columns=4)
    nfci = _q("NFCI")
    if not nfci.empty:
        value = latest_value(nfci)
        st.info(f"流动性状态：NFCI {value:.2f}，" + ("金融条件偏紧。" if value > 0 else "金融条件暂未明显收紧。"))
    render_quality_strip(["fred"], title="流动性摘要数据质量")


def _details():
    st.subheader("美元系统水位")
    wal, rr, res, tga = _q("WALCL"), _q("RRPONTSYD"), _q("WRESBAL"), _q("WTREGEN")
    if not wal.empty:
        w2 = wal.copy(); w2["value"] /= 1e6
        frames = {"Fed总资产(万亿)": w2}
        if not rr.empty: frames["RRP(十亿) "] = rr
        _show(add_range_selector(dual_axis_chart(frames, "Fed总资产 vs RRP", "万亿$", "十亿$")), "RRP 下降可能释放流动性，但要结合准备金与 TGA。")
    if not res.empty and not tga.empty:
        _show(add_range_selector(dual_axis_chart({"银行准备金": res, "TGA": tga}, "准备金 vs TGA", "十亿$", "十亿$")), "准备金下降或 TGA 上升可能收紧系统流动性。")

    st.subheader("融资成本")
    ff, sofr, d2, d10 = _q("FEDFUNDS"), _q("SOFR"), _q("DGS2"), _q("DGS10")
    frames = {label: df for label, df in (("FF", ff), ("SOFR", sofr), ("2Y", d2)) if not df.empty}
    if frames: _show(add_range_selector(multi_line_chart(frames, "短端利率", "%")), "SOFR 是实际隔夜融资成本，突升时要核对市场压力。")
    tips = _q("DFII10")
    frames = {label: df for label, df in (("10Y名义", d10), ("10Y实际", tips)) if not df.empty}
    if frames: _show(add_range_selector(multi_line_chart(frames, "名义 vs 实际利率", "%")), "实际利率是风险资产估值的重要折现率。")

    st.subheader("信用双轨")
    hy, ig, nfci = _q("BAMLH0A0HYM2"), _q("BAMLC0A0CM"), _q("NFCI")
    frames = {label: df for label, df in (("HY", hy), ("IG", ig)) if not df.empty}
    if frames: _show(add_range_selector(multi_line_chart(frames, "信用利差", "bp")), "HY-IG 同步扩张比单一高收益利差更值得警惕。")
    if not nfci.empty: _show(add_range_selector(line_chart(nfci, "NFCI", "", color="#d62728")), "NFCI 汇总金融条件。")


def _evidence():
    st.info("流动性结论要同时看：Fed资产负债表、RRP/TGA/准备金、融资利率和信用利差。单一指标变化不代表风险资产必然上涨或下跌。")
    if st.button("查看货币政策", use_container_width=True):
        go_to_research("pages/1_💵_货币政策.py", "货币政策", "3M")
    if st.button("查看信用与风险", use_container_width=True):
        go_to_research("pages/5_🛡️_信用与风险.py", "信用与风险", "3M")


summary_tab, detail_tab, evidence_tab = st.tabs(["状态总览", "详细数据", "事件与证据"])
with summary_tab: _summary()
with detail_tab: _details()
with evidence_tab: _evidence()
