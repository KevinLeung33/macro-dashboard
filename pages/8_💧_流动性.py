"""美元流动性深度页面"""
import streamlit as st

from db.repository import query_series
from utils.chart_utils import line_chart, multi_line_chart, dual_axis_chart, add_range_selector, plotly_config, render_chart_controls
from utils.navigation import apply_target_window, render_research_target

st.set_page_config(page_title="流动性与融资", page_icon="💧", layout="wide")
st.title("💧 流动性与融资")
cfg = plotly_config()
target = render_research_target()
render_chart_controls()
def _show(fig, note=""):
    st.plotly_chart(fig, use_container_width=True, config=cfg)
    if note: st.caption(note)
def _q(sid): return apply_target_window(query_series("fred", sid), target)

# ——— 美元系统流动性 ———
st.subheader("美元系统水位")
c1, c2 = st.columns(2)
with c1:
    wal = _q("WALCL"); rr = _q("RRPONTSYD"); res = _q("WRESBAL"); tga = _q("WTREGEN")
    if not wal.empty:
        w2 = wal.copy(); w2["value"] = w2["value"] / 1e6
        dfs = {"Fed总资产(T)": w2}
        if not rr.empty: dfs["RRP(千亿)"] = rr
        _show(add_range_selector(dual_axis_chart(dfs, "美联储总资产 vs RRP", "万亿$", "千亿$")))
        st.caption("📖 RRP=货币基金存放在Fed的闲置资金。RRP下降→流动性从Fed流向市场→利好风险资产。2023年RRP从$2.5T降到接近零→流动性释放了近$2T。")
with c2:
    if not res.empty and not tga.empty:
        _show(add_range_selector(dual_axis_chart({"银行准备金(千亿)": res, "TGA(千亿)": tga}, "准备金 vs TGA", "千亿$", "千亿$")))
        st.caption("📖 准备金↓=银行系统流动性告急(2019年repo危机就是准备金太低)。TGA↑=财政部收税/发债抽走流动性，TGA↓=财政部花钱释放流动性。")

# ——— 融资成本 ———
st.subheader("融资成本")
c3, c4 = st.columns(2)
with c3:
    ff = _q("FEDFUNDS"); sofr = _q("SOFR"); d2 = _q("DGS2"); d10 = _q("DGS10")
    if not ff.empty:
        dfs2 = {"FF": ff}
        if not sofr.empty: dfs2["SOFR"] = sofr
        if not d2.empty: dfs2["2Y"] = d2
        _show(add_range_selector(multi_line_chart(dfs2, "短端利率", "%")))
        st.caption("📖 SOFR=实际隔夜融资成本，比FF更能反映真实市场。SOFR跳升→流动性紧张。FF-SOFR利差扩大→银行间压力。")
with c4:
    tips = _q("DFII10")
    if not tips.empty:
        dfs3 = {"10Y名义": d10} if not d10.empty else {}
        dfs3["10Y实际(TIPS)"] = tips
        _show(add_range_selector(multi_line_chart(dfs3, "名义 vs 实际利率", "%")))
        st.caption("📖 实际利率=名义利率-通胀预期。高实际利率通常压制成长股与 BTC，需以图中最新读数判断。")

# ——— 信用双轨 ———
st.subheader("信用双轨：HY vs IG")
hy = _q("BAMLH0A0HYM2"); ig = _q("BAMLC0A0CM"); nfci = _q("NFCI")
c5, c6 = st.columns(2)
with c5:
    if not hy.empty:
        dfs4 = {"高收益HY": hy}
        if not ig.empty: dfs4["投资级IG"] = ig
        _show(add_range_selector(dual_axis_chart(dfs4, "信用利差", "HY(bp)", "IG(bp)", height=400)))
        st.caption("📖 HY-IG利差扩张=风险集中在弱企业。两者同步扩张=系统性信用收缩→2008模式。IG单独跳升=危机扩散到优质企业→更危险。")
with c6:
    if not nfci.empty:
        _show(add_range_selector(line_chart(nfci, "芝加哥金融条件NFCI", "", color="#d62728", height=400)))
        st.caption("📖 NFCI整合多项金融条件指标。低于 -0.5 通常偏宽松，高于 0 偏紧；请以图中最新读数判断。")

# ——— 融资格局总览 ———
st.subheader("融资格局速查")
liq_latest = {}
for sid in ["RRPONTSYD","WRESBAL","WTREGEN","SOFR","BAMLC0A0CM"]:
    df = _q(sid)
    if not df.empty:
        liq_latest[sid] = df["value"].iloc[-1]

if liq_latest:
    cols = st.columns(len(liq_latest))
    labels = {"RRPONTSYD":"RRP(千亿)","WRESBAL":"准备金(千亿)","WTREGEN":"TGA(千亿)","SOFR":"SOFR%","BAMLC0A0CM":"IG利差(bp)"}
    for i, (sid, val) in enumerate(liq_latest.items()):
        with cols[i]:
            st.metric(labels.get(sid,sid), f"{val:.1f}")
