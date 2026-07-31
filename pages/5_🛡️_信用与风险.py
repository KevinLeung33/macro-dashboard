import streamlit as st
import pandas as pd

from db.repository import query_series
from utils.chart_utils import line_chart, multi_line_chart, dual_axis_chart, add_range_selector, plotly_config, render_chart_controls
from utils.event_overlays import add_event_markers, get_chart_events
from utils.indicators import latest_value
from utils.navigation import apply_target_window, render_research_target

st.set_page_config(page_title="信用与风险",page_icon="🛡️",layout="wide")
st.title("🛡️ 信用与风险")
cfg=plotly_config()
target = render_research_target()
render_chart_controls()
def _show(fig,note=""):
    st.plotly_chart(fig,use_container_width=True,config=cfg)
    if note: st.caption(note)
def _q(sid): return apply_target_window(query_series("fred",sid), target)

# Credit spreads
st.subheader("信用压力")
c1,c2=st.columns(2)
with c1:
    hy=_q("BAMLH0A0HYM2"); t10=_q("T10Y3M"); nfci=_q("NFCI")
    if not hy.empty:
        dfs={"HY OAS(bp)":hy}
        if not t10.empty:
            t2=t10.copy();t2["value"]=t2["value"]*100;dfs["10Y-3M(bp)"]=t2
        hy_fig = add_range_selector(dual_axis_chart(dfs,"高收益利差 vs 期限利差","bp","bp"))
        hy_events = get_chart_events(
            asset=["SP500", "NASDAQ", "BTC"],
            event_types=["credit", "liquidity", "fed_policy"],
            start_date=hy["date"].min(),
        )
        _show(add_event_markers(hy_fig, hy_events),
              "📖 HY OAS=低评级企业债券相对国债的利率溢价。>500bp=信用危机(2008年峰值2000bp)，300-500=紧张，<300=正常。10Y-3M倒挂=衰退预警。")
with c2:
    if not nfci.empty:
        nfci_latest = latest_value(nfci)
        nfci_fig = add_range_selector(line_chart(nfci,"芝加哥金融条件指数NFCI","",color="#d62728"))
        nfci_events = get_chart_events(
            asset=["SP500", "NASDAQ", "BTC"],
            event_types=["credit", "liquidity"],
            start_date=nfci["date"].min(),
        )
        _show(add_event_markers(nfci_fig, nfci_events),
              f"📖 NFCI汇总金融条件为一个数。>0=金融条件收紧，<-0.5=偏宽松，2008年峰值>3。当前 {nfci_latest:.2f}。" if nfci_latest is not None else "📖 NFCI汇总金融条件为一个数。>0=金融条件收紧，<-0.5=偏宽松，2008年峰值>3。")

# Risk appetite
st.subheader("风险情绪")
vix=_q("VIXCLS"); sp=_q("SP500")
a2,b2=st.columns(2)
with a2:
    if not vix.empty:
        vix_fig = add_range_selector(line_chart(vix,"VIX恐慌指数","",color="#d62728"))
        vix_events = get_chart_events(
            asset=["SP500", "NASDAQ"],
            event_types=["credit", "liquidity", "geopolitics"],
            start_date=vix["date"].min(),
        )
        _show(add_event_markers(vix_fig, vix_events),
              "📖 同样放在这里是从信用角度解读：VIX飙升→信用利差扩大→企业融资成本上升→可能触发违约→银行收紧信贷→恶性循环。")
with b2:
    if not sp.empty:
        sp2=sp.copy();sp2["value"]=sp2["value"].pct_change(252)*100;sp2=sp2.dropna()
        _show(add_range_selector(line_chart(sp2,"标普500年化回报率","%",color="#2ca02c")),
              "📖 滚动一年回报率。长期均值约8-10%。大幅负值=熊市中，大幅正值后往往跟随均值回归。")

# Real rates + inflation expectations
st.subheader("实际利率与通胀预期")
tips=_q("DFII10"); be=_q("T10YIE")
a3,b3=st.columns(2)
with a3:
    if not tips.empty:
        tips_latest = latest_value(tips)
        _show(add_range_selector(line_chart(tips,"10Y TIPS实际利率","%",color="#1f77b4")),
              f"📖 实际利率=名义利率-通胀预期。实际利率上升通常压制黄金、BTC和成长股估值。当前 {tips_latest:.2f}%。" if tips_latest is not None else "📖 实际利率=名义利率-通胀预期。实际利率上升通常压制黄金、BTC和成长股估值。")
with b3:
    if not be.empty:
        _show(add_range_selector(line_chart(be,"10Y盈亏平衡通胀率","%",color="#ff7f0e")),
              "📖 债券市场定价的10年平均通胀预期。>3%=预期偏高(通胀预期脱锚风险)，<2%=预期偏低(通缩风险)。")

# Consumer
st.subheader("消费者")
conf=_q("UMCSENT"); retail=_q("RSAFS")
a4,b4=st.columns(2)
with a4:
    if not conf.empty:
        conf_latest = latest_value(conf)
        conf_state = "偏弱" if conf_latest is not None and conf_latest < 70 else "正常"
        _show(add_range_selector(line_chart(conf,"密歇根消费者信心","",color="#2ca02c")),
              f"📖 <70通常代表消费者情绪偏弱。当前 {conf_latest:.1f}，状态：{conf_state}。消费者信心是消费的领先观察项之一。" if conf_latest is not None else "📖 <70通常代表消费者情绪偏弱。消费者信心是消费的领先观察项之一。")
with b4:
    if not retail.empty:
        r2=retail.copy();r2["value"]=r2["value"]/1000
        _show(add_range_selector(line_chart(r2,"零售销售(十亿美元)","十亿$",color="#9467bd")),
              "📖 实际消费落地数据。下降=消费者真的在减少支出→企业收入下降→裁员→消费再降。信心→消费→就业的传导链。")

# Energy
st.subheader("能源")
gas=_q("DHHNGSP"); oil=_q("DCOILWTICO")
if not oil.empty:
    dfs_e={"WTI原油":oil}
    if not gas.empty: dfs_e["天然气"]=gas
    _show(add_range_selector(dual_axis_chart(dfs_e,"原油 vs 天然气","$/桶","$/百万BTU")),
          "📖 天然气是欧洲能源压力的重要代理；原油和天然气需结合供给、地缘与全球需求共同解读。")
