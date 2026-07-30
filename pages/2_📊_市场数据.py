import streamlit as st

from db.repository import query_series
from services.market_data import query_market_series
from utils.chart_utils import line_chart, multi_line_chart, dual_axis_chart, add_range_selector, plotly_config
from utils.event_overlays import add_event_markers, get_chart_events
from utils.navigation import apply_target_window, render_research_target

st.set_page_config(page_title="市场数据",page_icon="📊",layout="wide")
st.title("📊 市场数据")
cfg=plotly_config()
target = render_research_target()
def _q(source, series_id): return apply_target_window(query_series(source, series_id), target)
def _market(series_id):
    frame, meta = query_market_series(series_id)
    return apply_target_window(frame, target), meta
def _show(fig,note=""):
    st.plotly_chart(fig,use_container_width=True,config=cfg)
    if note: st.caption(note)

tab1,tab2=st.tabs(["美股 & VIX","汇率 & 大宗"])

with tab1:
    sp=_q("fred","SP500"); nasdaq=_q("fred","NASDAQCOM")
    if not sp.empty:
        dfs={"标普500":sp}
        if not nasdaq.empty: dfs["纳斯达克"]=nasdaq
        _show(add_range_selector(multi_line_chart(dfs,"美股指数","点")),
              "📖 标普500=500家大型公司加权，纳指=科技股为主。纳指跑赢标普=科技驱动，道指跑赢=传统板块轮动。")
    dji=_q("fred","DJIA")
    if not dji.empty:
        _show(add_range_selector(line_chart(dji,"道琼斯工业指数","点",color="#2ca02c")),
              "📖 30家蓝筹工业股。与纳指的相对走势反映市场风格切换：道指>纳指=防御/价值占优。")
    vix=_q("fred","VIXCLS")
    if not vix.empty:
        st.subheader("VIX 恐慌指数")
        _show(add_range_selector(line_chart(vix,"VIX","点",color="#d62728")),
              "📖 VIX=标普期权隐含波动率。20以下=平静，20-25=担忧，25-30=恐慌，30+=危机。VIX暴涨+标普暴跌=恐慌性抛售。")
    if not sp.empty and not dji.empty:
        import pandas as pd
        m=pd.merge(sp,dji,on="date",suffixes=("_sp","_dji"),how="inner")
        m["value"]=m["value_dji"]/m["value_sp"]*1000
        st.subheader("道指/标普比率（板块轮动）")
        _show(add_range_selector(line_chart(m[["date","value"]],"DJIA/SP500","",color="#ff7f0e")),
              "📖 比率上升=道指相对标普走强，常见于价值/防御/传统板块占优；比率下降=成长和科技权重更强。")

with tab2:
    eu=_q("fred","DEXUSEU"); jp=_q("fred","DEXJPUS")
    dxy,dxy_meta=_market("DX-Y.NYB")
    if not dxy.empty:
        dxy_fig = add_range_selector(line_chart(dxy,"美元指数DXY","点",color="#1f77b4"))
        dxy_events = get_chart_events(
            asset="DXY",
            event_types=["fed_policy", "liquidity", "inflation", "growth"],
            start_date=dxy["date"].min(),
        )
        _show(add_event_markers(dxy_fig, dxy_events),
              f"📖 DXY比单一欧元汇率更适合看全球美元压力。DXY上行通常代表美元流动性收紧，对新兴市场、黄金和BTC不友好。provider={dxy_meta['provider']}")
    if not eu.empty and not jp.empty:
        _show(add_range_selector(dual_axis_chart({"USD/EUR":eu,"JPY/USD":jp},"汇率(双轴)","USD/EUR","JPY/USD")),
              "📖 两个轴刻度不同。USD/EUR↑=美元兑欧元走强。FRED的JPY/USD表示1美元兑多少日元，数值↑=日元贬值。美元走强通常会收紧全球美元流动性。")
    oil=_q("fred","DCOILWTICO"); gold=_q("fred","GOLDAMGBD228NLBR")
    if not oil.empty:
        dfs_c={"WTI原油":oil}
        if not gold.empty: dfs_c["黄金"]=gold
        _show(add_range_selector(multi_line_chart(dfs_c,"大宗商品","美元")),
              "📖 油=工业血液+地缘温度计。金=避险+实际利率的对立面。实际利率↑=黄金↓。黄金在连跌4周说明什么？")
