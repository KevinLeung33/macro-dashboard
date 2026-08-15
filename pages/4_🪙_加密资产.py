import streamlit as st

from db.repository import query_series
from services.dashboard_overview import build_cross_asset_tape, render_horizon_guidance, render_quality_strip, render_snapshot_cards
from services.market_data import query_market_series
from services.btc_indicator_service import indicator_snapshots
from config.btc_onchain_indicators import BTC_INDEX_SOURCE
from utils.chart_utils import line_chart, multi_line_chart, dual_axis_chart, add_range_selector, plotly_config, render_chart_controls
from utils.event_overlays import add_event_markers, get_chart_events
from utils.indicators import latest_value
from utils.navigation import apply_target_window, go_to_research, render_research_target


st.set_page_config(page_title="加密资产", page_icon="🪙", layout="wide")
st.title("🪙 加密资产")
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
    render_horizon_guidance("crypto")
    render_snapshot_cards(build_cross_asset_tape(["crypto", "rates", "fx", "risk"]), columns=4)
    btc, _ = _market("BTC-USD")
    funding = _q("crypto_market", "BTC_FUNDING_RATE")
    oi = _q("crypto_market", "BTC_OPEN_INTEREST")
    btc_value, funding_value, oi_value = latest_value(btc), latest_value(funding), latest_value(oi)
    if btc_value is not None:
        text = f"BTC 当前 ${btc_value:,.0f}。"
        if funding_value is not None: text += f"资金费率 {funding_value:.4f}。"
        if oi_value is not None: text += f"持仓量 {oi_value:,.0f}。"
        st.info(text + "先观察价格、资金费率和持仓量是否同向，再进入交易工作台做技术分析。")
    else:
        st.warning("BTC 行情暂无可用数据。")
    render_quality_strip(["binance_spot", "crypto_market", "crypto_liquidity", "crypto_flows"], title="Crypto 摘要数据质量")
    st.subheader("BTC 日级环境指标")
    rows = indicator_snapshots()
    available = [row for row in rows if row.get("value") is not None]
    if not available:
        st.info("Wu Blockchain BTC 日级指标尚未成功抓取。")
    else:
        groups = {
            "周期估值": "cycle_valuation",
            "持有者行为": "holder_behavior",
            "流动性": "macro_liquidity",
            "情绪": "sentiment",
            "衍生品风险": "derivatives_risk",
        }
        for title, category in groups.items():
            group = [row for row in available if row.get("category") == category]
            if not group:
                continue
            st.markdown(f"**{title}**")
            cols = st.columns(min(4, len(group)))
            for col, row in zip(cols * ((len(group) + len(cols) - 1) // len(cols)), group):
                with col:
                    value = row["value"]
                    shown = f"{value:,.4f}" if abs(value) < 100 else f"{value:,.2f}"
                    st.metric(row["display_name"], shown, f"历史分位 {row['percentile']:.0f}%")
                    st.caption(f"{row['state']} · {row['date']}")
        with st.expander("查看指标使用说明、关键阈值和历史分位", expanded=False):
            for row in available:
                st.markdown(f"**{row['display_name']}** · 当前 {row['value']:.6g} · 历史分位 {row['percentile']:.1f}% · {row['state']}")
                st.caption(row["description"])
                st.caption("使用参考：" + row["use_reference"])
                if row.get("thresholds"):
                    threshold_text = "；".join(f"{point:g}：{label}" for point, label in row["thresholds"])
                    st.caption("关键位置（经验参考，非交易信号）：" + threshold_text)
                else:
                    st.caption("关键位置：没有稳定通用的绝对阈值，优先观察历史分位和趋势。")
            st.caption("历史分位按当前已保存的该指标历史样本计算；样本不足或数据源变更时，分位仅作参考。")
    render_quality_strip(["wu_btc_index"], title="BTC 日级指标数据质量")


def _details():
    btc, btc_meta = _market("BTC-USD")
    if not btc.empty:
        tips = _q("fred", "DFII10")
        dxy, dxy_meta = _market("DX-Y.NYB")
        macro_note = "；".join(bits for bits in [
            f"10Y实际利率 {latest_value(tips):.2f}%" if latest_value(tips) is not None else "",
            f"DXY {latest_value(dxy):.1f}({dxy_meta['provider']})" if latest_value(dxy) is not None else "",
        ] if bits)
        _show(add_event_markers(add_range_selector(line_chart(btc, "BTC/USD", "$", color="#f7931a")), get_chart_events(asset="BTC", event_types=["crypto", "fed_policy", "liquidity", "credit"], start_date=btc["date"].min())), f"BTC provider={btc_meta.get('provider')}。{macro_note}")
    else:
        st.warning("BTC 价格数据不可用，请检查 Binance spot / FRED 接入。")

    st.subheader("Crypto 内生流动性")
    stable_total, stable_major = _q("crypto_liquidity", "STABLE_TOTAL_MCAP"), _q("crypto_liquidity", "STABLE_MAJOR_MCAP")
    usdt, usdc = _q("crypto_liquidity", "USDT_MCAP"), _q("crypto_liquidity", "USDC_MCAP")
    ethbtc = _q("crypto_liquidity", "ETHBTC")
    liquidity = stable_total if not stable_total.empty else stable_major
    if not liquidity.empty:
        liq = liquidity.copy(); liq["value"] = liq["value"] / 1e12
        if not btc.empty:
            btc_k = btc.copy(); btc_k["value"] = btc_k["value"] / 1000
            _show(add_range_selector(dual_axis_chart({"BTC(千美元)": btc_k, "稳定币市值(万亿美元)": liq}, "BTC vs 稳定币流动性", "千美元", "万亿美元")), "稳定币市值上升且 BTC 同步走强，信号更偏顺周期。")
        else:
            _show(add_range_selector(line_chart(liquidity, "稳定币市值", "$")))
    stable_parts = {}
    if not usdt.empty:
        part = usdt.copy(); part["value"] /= 1e9; stable_parts["USDT(十亿美元)"] = part
    if not usdc.empty:
        part = usdc.copy(); part["value"] /= 1e9; stable_parts["USDC(十亿美元)"] = part
    if stable_parts:
        _show(add_range_selector(multi_line_chart(stable_parts, "USDT / USDC 市值", "十亿美元")), "结构变化需要结合总量和价格确认。")
    if not ethbtc.empty:
        _show(add_range_selector(line_chart(ethbtc, "ETH/BTC", "比值", color="#9467bd")), "ETH/BTC 可作为 Crypto 内部风险偏好的辅助观察。")

    st.subheader("Crypto 资金与杠杆")
    funding, oi = _q("crypto_market", "BTC_FUNDING_RATE"), _q("crypto_market", "BTC_OPEN_INTEREST")
    c1, c2 = st.columns(2)
    with c1:
        if not funding.empty: _show(add_range_selector(line_chart(funding, "BTC资金费率", "%", color="#d62728")), "极端正值代表多头拥挤风险。")
    with c2:
        if not oi.empty: _show(add_range_selector(line_chart(oi, "BTC合约持仓量", "USD/BTC", color="#1f77b4")), "持仓量要结合价格和资金费率看。")
    etf_flow, exchange_flow = _q("crypto_flows", "BTC_ETF_NETFLOW"), _q("crypto_flows", "BTC_EXCHANGE_NETFLOW")
    if not etf_flow.empty or not exchange_flow.empty:
        flows = {k: v for k, v in (("BTC ETF净流入", etf_flow), ("BTC交易所净流入", exchange_flow)) if not v.empty}
        _show(add_range_selector(multi_line_chart(flows, "Crypto 资金流", "流量")), "外部流量需要核对配置源和发布日期。")

    _btc_indicator_charts()

    st.subheader("MSTR")
    mstr, meta = _market("MSTR")
    if not mstr.empty:
        px = latest_value(mstr); high = mstr.tail(252)["value"].max() if len(mstr) >= 30 else None
        c1, c2, c3 = st.columns(3)
        c1.metric("股价", f"${px:.2f}" if px is not None else "—")
        c2.metric("52周高点", f"${high:.2f}" if high else "—")
        c3.metric("距高点", f"{(px / high - 1) * 100:.0f}%" if px is not None and high else "—")
        st.caption(f"provider={meta.get('provider')}")


def _btc_indicator_charts():
    """Render daily Wu BTC indicators with reference levels in the detail tab."""
    from config.btc_onchain_indicators import BTC_INDICATORS

    st.subheader("BTC 日级环境指标走势图")
    st.caption("这些指标用于周期和交易背景判断，不替代 OKX 的实时价格、K线和执行数据。")
    groups = {
        "周期估值": "cycle_valuation",
        "持有者行为": "holder_behavior",
        "流动性": "macro_liquidity",
        "情绪": "sentiment",
        "衍生品风险": "derivatives_risk",
    }
    rows = {row["series_id"]: row for row in indicator_snapshots()}
    for title, category in groups.items():
        definitions = [meta for meta in BTC_INDICATORS.values() if meta.get("category") == category]
        available = []
        for meta in definitions:
            series_id = meta["series_id"]
            frame = query_series(BTC_INDEX_SOURCE, series_id)
            if frame.empty:
                continue
            frame = apply_target_window(frame, target)
            if not frame.empty:
                available.append((meta, frame, rows.get(series_id, {})))
        if not available:
            continue
        with st.expander(f"{title} · {len(available)}项", expanded=title in {"周期估值", "衍生品风险"}):
            for offset in range(0, len(available), 2):
                cols = st.columns(2)
                for col, (meta, frame, snapshot) in zip(cols, available[offset:offset + 2]):
                    with col:
                        fig = line_chart(frame, meta["display_name"], meta["unit"], color="#f7931a")
                        for point, label in meta.get("thresholds") or []:
                            fig.add_hline(
                                y=float(point), line_dash="dash", line_color="#ef4444",
                                annotation_text=f"{label} ({point:g})",
                                annotation_position="top left",
                            )
                        _show(add_range_selector(fig), meta["description"])
                        if snapshot.get("value") is not None:
                            st.caption(
                                f"当前 {snapshot['value']:.6g} · 历史分位 {snapshot['percentile']:.1f}% · "
                                f"{snapshot['state']} · 数据日期 {snapshot['date']}"
                            )
                        st.caption("使用参考：" + meta["use_reference"])


def _evidence():
    st.info("交易前建议把宏观状态、BTC 跨资产确认和技术触发条件写入交易计划；本页只提供研究背景，不替代执行纪律。")
    if st.button("打开 Crypto 交易复盘", use_container_width=True):
        go_to_research("pages/15_🧾_交易复盘.py", "交易复盘", "3M")
    if st.button("打开 AI 影子账户", use_container_width=True):
        go_to_research("pages/16_🤖_AI影子账户.py", "AI影子账户", "3M")


summary_tab, detail_tab, evidence_tab = st.tabs(["状态总览", "详细数据", "事件与证据"])
with summary_tab:
    _summary()
with detail_tab:
    _details()
with evidence_tab:
    _evidence()
