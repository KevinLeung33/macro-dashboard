import streamlit as st
import logging
import pandas as pd

from config.series_definitions import AKSHARE_SERIES
from db.repository import query_series, query_events, add_event
from services.market_data import query_market_series
from utils.chart_utils import line_chart, multi_line_chart, dual_axis_chart, add_range_selector, plotly_config, render_chart_controls
from utils.event_overlays import add_event_markers, get_chart_events
from utils.indicators import latest_value
from utils.navigation import apply_target_window, render_research_target
from services.access_control import render_admin_access, require_admin

st.set_page_config(page_title="全球市场",page_icon="🌍",layout="wide")
admin_access = render_admin_access()
st.title("🌍 全球市场")
cfg=plotly_config()
logger = logging.getLogger(__name__)
target = render_research_target()
render_chart_controls()
def _q(source, series_id): return apply_target_window(query_series(source, series_id), target)
def _ak_series(series_id):
    """Never surface a deliberately disabled, stale China macro series."""
    if not AKSHARE_SERIES.get(series_id, {}).get("enabled", True):
        return pd.DataFrame(columns=["date", "value"])
    return _q("akshare", series_id)
def _market(series_id):
    frame, meta = query_market_series(series_id)
    return apply_target_window(frame, target), meta
def _show(fig,note=""):
    st.plotly_chart(fig,use_container_width=True,config=cfg)
    if note: st.caption(note)

# China
st.subheader("🇨🇳 中国宏观周期")
cn_pmi=_ak_series("CN_PMI"); cn_cx=_ak_series("CN_CAIXIN_PMI")
cn_cpi=_ak_series("CN_CPI"); cn_ppi=_ak_series("CN_PPI")
cn_m2=_ak_series("CN_M2_YOY")
usdcnh=_q("yfinance","USDCNH=X")
usdcny=_q("yfinance","USDCNY=X")
csi300=_q("yfinance","000300.SS")
chinext=_q("yfinance","399006.SZ")
hstech, _hstech_meta = _market("HSTECH")

if any(not df.empty for df in (cn_pmi, cn_cx, cn_cpi, cn_ppi, cn_m2)):
    a,b=st.columns(2)
    with a:
        if not cn_pmi.empty:
            dfs={"官方PMI":cn_pmi}
            if not cn_cx.empty: dfs["财新PMI"]=cn_cx
            _show(add_range_selector(multi_line_chart(dfs,"中国PMI","")),
                  "📖 PMI>50=制造业扩张，<50=收缩。官方PMI采样大/国企为主，财新PMI偏中小/出口企业。两者方向一致=趋势确定，分歧=结构温差。")
    with b:
        if not cn_cpi.empty:
            dfs2={"CPI":cn_cpi}
            if not cn_ppi.empty: dfs2["PPI"]=cn_ppi
            _show(add_range_selector(multi_line_chart(dfs2,"中国通胀","%")),
                  "📖 CPI<0=通缩→消费需求不足。PPI<0=工业品降价→企业利润承压。CPI-PPI剪刀差扩大=下游利润改善。")
    c,d=st.columns(2)
    with c:
        sf=_ak_series("CN_SOCIAL_FINANCING")
        if not sf.empty:
            _show(add_range_selector(line_chart(sf,"社融增量(亿元)","亿元",color="#1f77b4",height=350)),
                  "📖 社融=实体经济从金融体系获得的资金总量，含贷款+债券+股票+表外。是GDP的领先指标(约6个月)。")
    with d:
        lpr=_q("akshare","CN_LPR_1Y")
        if not lpr.empty:
            _show(add_range_selector(line_chart(lpr,"LPR 1年期","%",color="#d62728",height=350)),
                  "📖 LPR=中国实际政策利率。下降=降息刺激经济，上升=收紧。当前持续下行→宽松信号。")
    liquidity={}
    if not cn_m2.empty: liquidity["M2同比"]=cn_m2
    if liquidity:
        _show(add_range_selector(multi_line_chart(liquidity,"中国货币供给","%")),
              "📖 M2反映广义货币供给。社融存量同比当前没有经过字段校验的公开源，暂不展示，避免把社融增量误当同比。")
    paused = [
        meta.get("display_name", series_id).replace("🇨🇳 ", "")
        for series_id, meta in AKSHARE_SERIES.items()
        if not meta.get("enabled", True) and series_id in {
            "CN_CAIXIN_PMI", "CN_SOCIAL_FINANCING", "CN_M2_YOY"
        }
    ]
    if paused:
        st.info("数据质量保护：" + "、".join(paused) + " 当前源已过期或不可用，暂不纳入实时宏观判断。")
else:
    st.warning("中国PMI数据未拉取。请先 pip install akshare 并点击主页刷新。")

st.divider()

st.subheader("🇨🇳 中国资产与人民币")
asset_cols=st.columns(3)
for col, df, label, unit in zip(
    asset_cols,
    (usdcnh, csi300, hstech),
    ("USDCNH", "沪深300", "恒生科技"),
    ("CNH", "点", "点"),
):
    with col:
        if not df.empty:
            _show(add_range_selector(line_chart(df,label,unit)),
                  "📖 人民币或离岸资产变化需要结合美元、政策和资金流判断，不单独作为风险结论。")
        else:
            st.caption(f"{label} 暂无数据")
if not usdcny.empty or not chinext.empty:
    cols=st.columns(2)
    with cols[0]:
        if not usdcny.empty: _show(add_range_selector(line_chart(usdcny,"USDCNY","CNY")))
    with cols[1]:
        if not chinext.empty: _show(add_range_selector(line_chart(chinext,"创业板指","点")))

st.divider()

# Global linkage
st.subheader("🌐 全球联动")
a2,b2=st.columns(2)
with a2:
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
              f"📖 DXY是全球美元压力的核心代理。DXY上行通常意味着美元融资条件收紧，风险资产和非美资产承压。provider={dxy_meta['provider']}")
    if not eu.empty and not jp.empty:
        _show(add_range_selector(dual_axis_chart({"USD/EUR":eu,"JPY/USD":jp},"主要汇率","USD/EUR","JPY/USD")),
              "📖 两者使用不同Y轴。USD/EUR反映美元兑欧元，JPY/USD在FRED中表示1美元兑多少日元，数值上升通常代表日元贬值。")
with b2:
    oil=_q("fred","DCOILWTICO"); copper=_q("fred","PCOPPUSDM")
    if not oil.empty:
        dfs_c={}
        dfs_c["WTI原油"]=oil
        if not copper.empty: dfs_c["铜"]=copper
        copper_latest = latest_value(copper)
        _show(add_range_selector(dual_axis_chart(dfs_c,"油铜比(全球需求代理)","$/桶","$/吨")),
              f"📖 铜=工业需求代理。油铜比升可能偏供给冲击，油铜比降可能偏需求走弱。当前铜价约 ${copper_latest:,.0f}/吨。" if copper_latest is not None else "📖 铜=工业需求代理。油铜比升可能偏供给冲击，油铜比降可能偏需求走弱。")

# BTC
st.subheader("BTC (Coinbase)")
btc_=_q("fred","CBBTCUSD")
if not btc_.empty:
    _show(add_range_selector(line_chart(btc_,"BTC/USD","$",color="#f7931a")),
          "📖 BTC在FRED上也有数据(CB BTC)。关联因素：实际利率↑=利空BTC，DXY↑=利空BTC，MSTR卖币=额外抛压。")

# TIC
st.subheader("美债持仓(TIC)")
try:
    from db.repository import query_tic_holdings
    from utils.chart_utils import horizontal_bar
    tic_df=query_tic_holdings()
    if not tic_df.empty:
        tic_t=tic_df[tic_df["category"]=="total"].nlargest(10,"holdings_billions")
        st.plotly_chart(horizontal_bar(tic_t[["country","holdings_billions"]],"前十大持有国(十亿美元)",height=400),use_container_width=True,config=cfg)
        top = tic_t.iloc[0] if not tic_t.empty else None
        st.caption(f"📖 TIC反映海外官方和私人部门持有美债的结构。当前最大持有方为 {top['country']}，约 {top['holdings_billions']:,.0f} 十亿美元。" if top is not None else "📖 TIC反映海外官方和私人部门持有美债的结构。")
except Exception as exc:
    logger.warning("TIC data unavailable: %s", exc)
    st.info("TIC未拉取")

# Events
st.subheader("🕐 事件")
with st.expander("➕ 添加"):
    with st.form("ev"):
        ed=st.date_input("日期"); et=st.text_input("标题")
        ec=st.selectbox("类别",["market","geopolitics","fed","data_release","crypto","global_cycle"])
        ei=st.select_slider("重要度",["low","medium","high"])
        if st.form_submit_button("添加", disabled=not admin_access) and require_admin("添加事件"):
            add_event(str(ed), et, "", ec, ei)
            st.rerun()
evts=query_events(50)
if evts:
    import pandas as pd
    df_e=pd.DataFrame(evts,columns=["date","title","description","category","impact"])
    df_e["s"]=df_e["impact"].map({"high":0,"medium":1,"low":2})
    df_e=df_e.sort_values(["date","s"],ascending=[False,True])
    for _,r in df_e.iterrows():
        icon={"high":"🔴","medium":"🟡","low":"⚪"}.get(r["impact"],"⚪")
        st.caption(f"{icon} **{r['date']}** [{r['category']}] {r['title']}")
