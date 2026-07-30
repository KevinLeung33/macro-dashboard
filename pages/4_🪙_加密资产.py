import streamlit as st

from db.repository import query_series
from services.market_data import query_market_series
from utils.chart_utils import line_chart, multi_line_chart, dual_axis_chart, add_range_selector, plotly_config
from utils.event_overlays import add_event_markers, get_chart_events
from utils.indicators import latest_value
from utils.navigation import apply_target_window, render_research_target

st.set_page_config(page_title="加密资产",page_icon="🪙",layout="wide")
st.title("🪙 加密资产")
cfg=plotly_config()
target = render_research_target()
def _q(source, series_id): return apply_target_window(query_series(source, series_id), target)
def _market(series_id):
    frame, meta = query_market_series(series_id)
    return apply_target_window(frame, target), meta
def _show(fig,note=""):
    st.plotly_chart(fig,use_container_width=True,config=cfg)
    if note: st.caption(note)

btc=_q("fred","CBBTCUSD")
if not btc.empty:
    st.subheader("BTC 价格 (Coinbase)")
    tips=_q("fred","DFII10")
    dxy,dxy_meta=_market("DX-Y.NYB")
    tips_v=latest_value(tips)
    dxy_v=latest_value(dxy)
    macro_bits=[]
    if tips_v is not None: macro_bits.append(f"10Y实际利率 {tips_v:.2f}%")
    if dxy_v is not None: macro_bits.append(f"DXY {dxy_v:.1f}({dxy_meta['provider']})")
    macro_note = "；".join(macro_bits)
    btc_fig = add_range_selector(line_chart(btc,"BTC/USD","$",color="#f7931a"))
    btc_events = get_chart_events(
        asset="BTC",
        event_types=["crypto", "fed_policy", "liquidity", "credit"],
        start_date=btc["date"].min(),
    )
    _show(add_event_markers(btc_fig, btc_events),
          f"📖 BTC常受实际利率、美元流动性、ETF资金流和加密自身叙事共同影响。{macro_note}。" if macro_note else "📖 BTC常受实际利率、美元流动性、ETF资金流和加密自身叙事共同影响。")
else:
    st.warning("FRED BTC数据不可用 | CoinGecko/Yahoo在受限网络环境")

st.subheader("Crypto 内生流动性")
stable_total = _q("crypto_liquidity", "STABLE_TOTAL_MCAP")
stable_major = _q("crypto_liquidity", "STABLE_MAJOR_MCAP")
usdt = _q("crypto_liquidity", "USDT_MCAP")
usdc = _q("crypto_liquidity", "USDC_MCAP")
ethbtc = _q("crypto_liquidity", "ETHBTC")
funding = _q("crypto_market", "BTC_FUNDING_RATE")
open_interest = _q("crypto_market", "BTC_OPEN_INTEREST")
etf_flow = _q("crypto_flows", "BTC_ETF_NETFLOW")
exchange_flow = _q("crypto_flows", "BTC_EXCHANGE_NETFLOW")
usdt_share = _q("crypto_liquidity", "USDT_SHARE")
usdc_share = _q("crypto_liquidity", "USDC_SHARE")

liquidity_base = stable_total if not stable_total.empty else stable_major
liquidity_label = "稳定币总市值(万亿美元)" if not stable_total.empty else "USDT+USDC市值(万亿美元)"
if not liquidity_base.empty:
    liq_t = liquidity_base.copy()
    liq_t["value"] = liq_t["value"] / 1e12
    c1, c2 = st.columns(2)
    with c1:
        latest_liq = latest_value(liq_t)
        st.metric(liquidity_label, f"{latest_liq:.2f}" if latest_liq is not None else "—")
    with c2:
        proxy_note = "DefiLlama 全市场总量" if not stable_total.empty else "CoinGecko USDT+USDC 代理"
        st.metric("数据口径", proxy_note)

    if not btc.empty:
        btc_k = btc.copy()
        btc_k["value"] = btc_k["value"] / 1000
        _show(
            add_range_selector(dual_axis_chart(
                {"BTC(千美元)": btc_k, liquidity_label: liq_t},
                "BTC vs 稳定币流动性",
                "千美元",
                "万亿美元",
            )),
            "📖 稳定币市值上行通常代表链上可用美元余额增加；若 BTC 同步走强，信号更偏顺周期。"
        )
    else:
        _show(add_range_selector(line_chart(liq_t, liquidity_label, "万亿美元", color="#17becf")))
else:
    st.caption("稳定币流动性数据暂不可用：DefiLlama 与 CoinGecko 当次均未返回可落库数据。")

stable_parts = {}
if not usdt.empty:
    usdt_b = usdt.copy()
    usdt_b["value"] = usdt_b["value"] / 1e9
    stable_parts["USDT(十亿美元)"] = usdt_b
if not usdc.empty:
    usdc_b = usdc.copy()
    usdc_b["value"] = usdc_b["value"] / 1e9
    stable_parts["USDC(十亿美元)"] = usdc_b
if stable_parts:
    _show(
        add_range_selector(multi_line_chart(stable_parts, "USDT / USDC 市值", "十亿美元")),
        "📖 USDT 更偏全球/离岸交易流动性，USDC 更偏合规美元通道；二者背离时值得结合新闻事件看。"
    )

share_parts = {}
if not usdt_share.empty: share_parts["USDT占比"] = usdt_share
if not usdc_share.empty: share_parts["USDC占比"] = usdc_share
if share_parts:
    _show(
        add_range_selector(multi_line_chart(share_parts, "USDT / USDC 在主流稳定币中的占比", "%")),
        "📖 占比变化用于观察稳定币流动性的结构变化，不等同于总流动性扩张或收缩。"
    )

if not ethbtc.empty:
    _show(
        add_range_selector(line_chart(ethbtc, "ETH/BTC", "比值", color="#9467bd")),
        "📖 ETH/BTC 上行常被用作 crypto 内部风险偏好改善的辅助指标。"
    )

st.subheader("Crypto 资金与杠杆")
derivative_cols = st.columns(2)
with derivative_cols[0]:
    if not funding.empty:
        _show(add_range_selector(line_chart(funding, "BTC资金费率", "%", color="#d62728")),
              "📖 资金费率为正代表多头向空头支付资金；极端正值通常表示多头拥挤。")
    else:
        st.caption("BTC资金费率暂无数据")
with derivative_cols[1]:
    if not open_interest.empty:
        _show(add_range_selector(line_chart(open_interest, "BTC合约持仓量", "USD/BTC", color="#1f77b4")),
              "📖 持仓量上升代表杠杆规模增加，需要结合价格和资金费率判断风险。")
    else:
        st.caption("BTC合约持仓量暂无数据")

flow_cols = st.columns(2)
with flow_cols[0]:
    if not etf_flow.empty:
        _show(add_range_selector(line_chart(etf_flow, "BTC ETF净流入", "USD", color="#2ca02c")),
              "📖 ETF净流入由 BTC_ETF_FLOWS_URL 配置，需同时检查来源和发布日期。")
    else:
        st.caption("BTC ETF flows 未配置或暂无数据")
with flow_cols[1]:
    if not exchange_flow.empty:
        _show(add_range_selector(line_chart(exchange_flow, "BTC交易所净流入", "BTC", color="#9467bd")),
              "📖 交易所净流入是外部供应压力代理，由 BTC_EXCHANGE_NETFLOW_URL 配置。")
    else:
        st.caption("BTC交易所净流入未配置或暂无数据")

st.subheader("MSTR(Strategy)关键数据")
mstr_full,mstr_meta=query_market_series("MSTR")
mstr=apply_target_window(mstr_full, target)
if not mstr.empty:
    px=latest_value(mstr)
    high_52=mstr_full.tail(252)["value"].max() if len(mstr_full) >= 30 else None
    dd=(px/high_52-1)*100 if px is not None and high_52 else None
    m1,m2,m3=st.columns(3)
    with m1: st.metric("股价", f"${px:.2f}" if px is not None else "—")
    with m2: st.metric("52周高点", f"${high_52:.2f}" if high_52 else "—")
    with m3: st.metric("距52周高点", f"{dd:.0f}%" if dd is not None else "—", delta_color="inverse")
    st.caption(f"MSTR provider: {mstr_meta['provider']}")
else:
    st.info("MSTR 暂无可用行情数据；刷新数据或检查 Alpha Vantage、Stooq、Yahoo Finance 配置后再查看。")

st.subheader("MSTR困境推演")
st.markdown("""
📖 MSTR(已改名Strategy)的商业模式=发股发债→买BTC→BTC涨→股价涨→继续发股→循环。  
这个\"永动机\"的前提是BTC只涨不跌。BTC从高点跌50%后：

| 选项 | 做法 | 后果 |
|------|------|------|
| A: 卖BTC付股息 | 减小BTC持仓 | 每股持币数↓→估值锚破坏→股价再跌 |
| B: 发新股付股息 | 稀释股本 | 同上+市场看穿\"永动机\"已死 |
| C: 违约不付 | 保留BTC | 信用崩溃→永失融资能力→不能再买BTC |

这个页面只把它作为压力测试框架：当 BTC 下跌、融资成本上升、股价折价或溢价收缩时，继续融资买币的路径会变窄。
""")
