import streamlit as st
import pandas as pd

from db.repository import query_series
from utils.chart_utils import line_chart, multi_line_chart, add_range_selector, plotly_config
from utils.indicators import latest_value, scale_series, yoy_series, mom_annualized_series
from utils.navigation import apply_target_window, render_research_target

st.set_page_config(page_title="货币政策", page_icon="💵", layout="wide")
st.title("💵 货币政策")
cfg=plotly_config()
target = render_research_target()
def _q(sid): return apply_target_window(query_series("fred", sid), target)
def _show(fig,note=""):
    st.plotly_chart(fig,use_container_width=True,config=cfg)
    if note: st.caption(note)

# FF Rate + Balance Sheet
c1,c2=st.columns(2)
with c1:
    ff=_q("FEDFUNDS")
    if not ff.empty:
        _show(add_range_selector(line_chart(ff,"联邦基金利率","%",color="#d62728")),
              "📖 美联储的政策利率。加息→抑制通胀+吸引美元回流。降息→刺激经济+美元走弱。历史极端：1980年Volcker加到20%，2020年降到0。")
with c2:
    wa=_q("WALCL")
    if not wa.empty:
        w2=scale_series(wa, 1e6)
        wa_latest = latest_value(w2)
        _show(add_range_selector(line_chart(w2,"美联储总资产","万亿美元",color="#9467bd")),
              f"📖 扩表=买债释放流动性(QE)，缩表=债券到期不续(QT)。当前美联储总资产约 {wa_latest:.2f} 万亿美元。" if wa_latest is not None else "📖 扩表=买债释放流动性(QE)，缩表=债券到期不续(QT)。")

# Yield Curve
st.subheader("收益率曲线")
d10=_q("DGS10"); d2=_q("DGS2"); ff2=_q("FEDFUNDS")
if not d10.empty:
    dfs={"10Y":d10}
    if not d2.empty: dfs["2Y"]=d2
    if not ff2.empty: dfs["FF利率"]=ff2
    _show(add_range_selector(multi_line_chart(dfs,"美债收益率","%")),
          "📖 短端(2Y/FF)反映政策预期，长端(10Y)反映增长+通胀预期。正常：10Y>2Y>FF。倒挂：10Y<2Y=市场认为加息过度/衰退要来。")

# 10Y-FF spread
if not d10.empty and not ff2.empty:
    m=pd.merge(d10,ff2,on="date",suffixes=("_10y","_ff"),how="inner")
    m["value"]=(m["value_10y"]-m["value_ff"])*100
    st.subheader("10Y-FF 利差")
    _show(add_range_selector(line_chart(m[["date","value"]],"10年期-联邦基金利率(bp)","bp",color="#ff7f0e")),
          "📖 这个利差反映长端利率相对政策利率的位置。<0=长端低于政策利率，通常代表市场预期未来增长/通胀放缓；如果利差突然走阔，可能意味着期限溢价或财政供给压力上升。")

# Real rate
st.subheader("实际利率")
pce=query_series("fred","PCEPILFE")
if not ff2.empty and not pce.empty:
    pd_p=pce.copy(); pd_p["date"]=pd.to_datetime(pd_p["date"]); pd_p["yoy"]=pd_p["value"].pct_change(12)*100
    ff_d=ff2.copy(); ff_d["date"]=pd.to_datetime(ff_d["date"])
    m2=pd.merge(ff_d,pd_p[["date","yoy"]],on="date",how="inner")
    m2["value"]=m2["value"]-m2["yoy"]
    _show(add_range_selector(line_chart(apply_target_window(m2[["date","value"]], target),"实际利率(FF-核心PCE同比)","%",color="#2ca02c")),
          "📖 名义利率减去通胀=借钱的真实成本。>0=紧缩(抑制经济)，<0=刺激(鼓励借贷)。当前实际利率接近零→政策偏中性，既不算紧也不算松。")

# Inflation
st.subheader("通胀")
cpi=query_series("fred","CPIAUCSL")
if not cpi.empty:
    cd=apply_target_window(yoy_series(cpi), target)
    core_cpi = query_series("fred","CPILFESL")
    core_yoy = apply_target_window(yoy_series(core_cpi), target) if not core_cpi.empty else None
    core_3m = mom_annualized_series(core_cpi).tail(3) if not core_cpi.empty else None
    tie=_q("T10YIE")
    dfs_i={"CPI同比":cd}
    if core_yoy is not None and not core_yoy.empty: dfs_i["核心CPI同比"] = core_yoy
    if not tie.empty: dfs_i["10Y盈亏平衡通胀"]=tie
    _show(add_range_selector(multi_line_chart(dfs_i,"通胀指标","%")),
          "📖 CPI=消费者实际支付的价格变化。10Y盈亏平衡通胀=债券市场定价的未来10年平均通胀。如果盈亏平衡追不上CPI→市场认为通胀会回落。如果追上来了→通胀预期脱锚。")
    if core_3m is not None and not core_3m.empty:
        st.caption(f"核心CPI最近月度年化：{core_3m['value'].iloc[-1]:.1f}%。这个比同比更快反映通胀是否重新加速。")
