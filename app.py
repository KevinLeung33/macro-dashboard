"""首页：宏观分析工作台与 Crypto 交易工作台入口。"""
import os
from datetime import date

import streamlit as st

from db.repository import (
    add_event,
    query_news_clusters,
    query_recent_newsflash,
    query_daily_reports,
    query_research_context,
    query_trade_notes,
)
from db.schema import init_db
from data.pipeline import fetch_all
from services.access_control import render_admin_access, require_admin
from services.ai_market_brief import generate_ai_market_brief
from services.dashboard_cockpit import build_cockpit
from services.dashboard_overview import (
    build_cross_asset_tape,
    render_event_calendar,
    render_horizon_guidance,
    render_quality_strip,
    render_snapshot_cards,
)
from services.home_brief import build_home_brief
from services.runtime_controls import TaskBusyError, hold_task, run_with_retry
from utils.navigation import go_to_research
from utils.alerts import check_alerts


st.set_page_config(page_title="宏观市场分析仪表盘", page_icon="📊", layout="wide")
admin_access = render_admin_access()
init_db()

st.title("📊 宏观分析工作台")
st.caption("先看市场状态，再看变化与催化剂；详细图表下沉到各主题页，Crypto 交易工作台保持独立。")

refresh_col, status_col = st.columns([1, 5])
with refresh_col:
    if st.button("🔄 刷新数据", use_container_width=True, type="primary", disabled=not admin_access):
        if require_admin("刷新数据"):
            try:
                with hold_task("data_refresh"):
                    with st.spinner("拉取宏观、市场、新闻与 AI 分析……"):
                        run_with_retry(
                            "data_refresh",
                            lambda: fetch_all(include_news=True, include_global=True),
                        )
                st.rerun()
            except TaskBusyError:
                st.warning("已有数据刷新任务正在运行，请稍后再试。")
            except Exception as exc:
                st.error(f"刷新失败：{exc}")
with status_col:
    st.caption(
        f"数据入口：FRED / AKShare / Yahoo Finance / Binance / OKX只读 · "
        f"新闻：{'已配置' if os.getenv('OPENAI_API_KEY') else '规则摘要'}"
    )


cockpit = build_cockpit()
brief = build_home_brief(cockpit)
ai_brief = generate_ai_market_brief(brief, cockpit)

# ===== 1. 当前环境 =====
st.subheader("🔦 当前环境")
signal_cols = st.columns(5)
top_signal = cockpit.get("top_signal")
asset_biases = cockpit.get("asset_biases", [])
summary_values = [
    ("宏观信号", ("低置信·" if cockpit.get("stale_sources") else "") + top_signal["name"] if top_signal else "暂无", f"{top_signal['score']}/{top_signal['max_score']}" if top_signal else None),
    ("风险资产", asset_biases[0]["direction"] if asset_biases else "暂无", None),
    ("数据质量", "需关注" if cockpit.get("stale_sources") else "正常", f"{len(cockpit.get('stale_sources', []))}项" if cockpit.get("stale_sources") else None),
    ("新闻主线", cockpit.get("top_news", {}).get("event_type", "暂无") if cockpit.get("top_news") else "暂无", f"{cockpit['top_news']['count']}篇" if cockpit.get("top_news") else None),
    ("研究状态", f"{cockpit.get('research_count', 0)}条假设", f"{cockpit.get('watch_count', 0)}条观察"),
]
for column, (label, value, delta) in zip(signal_cols, summary_values):
    with column:
        st.metric(label, value, delta)

st.caption("这里是当前环境的导航，不是单一指标交易信号；点击下方主题入口核对证据。")
if cockpit.get("stale_sources"):
    st.warning("当前有过期、失败或质量异常的数据源；宏观信号仅作研究线索，形成交易计划前请先核对详细数据页。")

tape_groups = {
    "风险与利率": ["risk", "rates", "credit"],
    "美元与大宗": ["fx", "commodity"],
    "中国与 Crypto": ["china", "crypto"],
}
tape_tabs = st.tabs(list(tape_groups))
for tab, (title, groups) in zip(tape_tabs, tape_groups.items()):
    with tab:
        render_snapshot_cards(build_cross_asset_tape(groups), columns=4)

# ===== 2. 数据质量前置 =====
render_quality_strip(title="当前摘要使用的数据质量")

st.subheader("🧭 宏观文字总结")
render_horizon_guidance("home", brief=brief, title="先读结论，再决定要核对哪些详细数据")

flash_preview = query_recent_newsflash(limit=5, minutes=24 * 60)
if flash_preview:
    st.subheader("⚡ 最近快讯")
    st.caption("快讯用于发现事件；重要事件仍需等待多源确认和完整分析。")
    for item in flash_preview[:3]:
        st.caption(f"{item['source']} · {item['published_at'] or '—'} · {item['title']}")

# ===== 3. 变化与催化剂 =====
st.subheader("⚡ 变化与催化剂")
left, right = st.columns(2)
with left:
    st.markdown("**最近值得解释的变化**")
    moves = cockpit.get("moves", [])
    if moves:
        for item in moves[:5]:
            pct = item.get("change_n_pct")
            delta = "—" if pct is None else f"{pct:+.2f}%"
            st.caption(f"{item['name']} · {item.get('date', '—')} · {delta} · 当前 {item.get('value', '—')}")
    else:
        st.caption("暂无足够数据计算近期变化。")
with right:
    st.markdown("**未来两周事件日历**")
    render_event_calendar()
    with st.expander("添加研究事件", expanded=False):
        with st.form("home_add_event"):
            event_date = st.date_input("日期", value=date.today())
            event_title = st.text_input("事件标题", placeholder="例如：FOMC会议 / CPI发布 / 重要行业事件")
            event_category = st.selectbox("类别", ["data_release", "fed", "china_macro", "crypto", "geopolitics", "market"])
            event_impact = st.select_slider("重要度", ["low", "medium", "high"], value="medium")
            if st.form_submit_button("保存事件", disabled=not admin_access) and require_admin("保存研究事件"):
                if event_title.strip():
                    add_event(str(event_date), event_title.strip(), "", event_category, event_impact)
                    st.success("事件已加入日历")
                else:
                    st.warning("事件标题不能为空")

clusters = query_news_clusters(limit=5, min_severity=3)
if clusters:
    st.markdown("**近期重要事件流**")
    for item in clusters:
        title = item["ai_title"] or item["title"]
        st.caption(
            f"{'🔴' if item['severity'] >= 4 else '🟡'} {item['event_type']} · "
            f"{item['article_count']}篇 · {title} · 影响 {item['assets_impacted'] or '待判断'}"
        )

alerts = check_alerts()
if alerts:
    st.markdown("**市场告警**")
    for icon, name, value, reason in alerts[:6]:
        st.caption(f"{icon} {name} · {value} · {reason}")

# ===== 4. 简报与研究上下文 =====
st.subheader("📝 当前判断摘要")
period_tabs = st.tabs(["今日", "本周", "中期"])
for tab, key in zip(period_tabs, ("today", "week", "medium")):
    with tab:
        ai_period = (ai_brief or {}).get(key)
        if ai_period and any(ai_period.values()):
            if ai_period.get("judgement"):
                st.markdown(f"**AI解读：{ai_period['judgement']}**")
            if ai_period.get("explanation"):
                st.write(ai_period["explanation"])
            if ai_period.get("watch"):
                st.caption(f"后续观察：{ai_period['watch']}")
        else:
            for item in brief.get(key, []):
                st.markdown(f"- {item}")

if ai_brief and ai_brief.get("overall"):
    with st.expander("查看 AI 总括与规则摘要", expanded=False):
        st.write(ai_brief["overall"])
        st.caption("AI 只负责整理给定证据；具体指标、数据日期和来源请回到主题页核对。")

weekly_report = next((row for row in query_daily_reports(limit=40) if row["session"] == "weekly"), None)
if weekly_report:
    with st.expander(f"查看最新周度研究报告 · {weekly_report['report_date']}", expanded=False):
        st.markdown(weekly_report["raw_markdown"] or weekly_report["summary"] or "报告暂无正文。")

# ===== 5. 研究与交易入口 =====
st.subheader("🧭 我的研究与交易")
research = query_research_context(limit=5)
plans = query_trade_notes(limit=8)
research_col, trade_col = st.columns(2)
with research_col:
    st.markdown("**研究假设与观察项**")
    if research.get("active_hypotheses"):
        for item in research["active_hypotheses"][:3]:
            st.caption(f"假设 · {item['title']} · 置信度 {float(item['confidence']):.0%}")
    else:
        st.caption("还没有活跃研究假设。")
    if research.get("active_watchlist"):
        for item in research["active_watchlist"][:3]:
            st.caption(f"观察 · {item['title']} · 触发：{item['trigger'] or '—'}")
    if st.button("打开证据工作台", use_container_width=True):
        go_to_research("pages/13_🔎_证据工作台.py", "美元流动性", "3M", topic="美元流动性")
with trade_col:
    st.markdown("**Crypto 交易工作台**")
    if plans:
        for plan in plans[:3]:
            st.caption(f"计划 #{plan['id']} · {plan['symbol']} · {plan['side']} · {plan['created_at']}")
    else:
        st.caption("还没有交易计划。")
    if st.button("打开 Crypto 交易复盘", use_container_width=True):
        go_to_research("pages/15_🧾_交易复盘.py", "交易复盘", "3M")

st.divider()
st.subheader("📚 主题研究入口")
entries = [
    ("货币政策", "pages/1_💵_货币政策.py", "利率、收益率曲线、通胀与实际利率"),
    ("市场数据", "pages/2_📊_市场数据.py", "风险资产、美元、汇率与大宗"),
    ("全球市场", "pages/3_🌍_全球市场.py", "中国宏观、中国资产与全球联动"),
    ("Crypto 资产", "pages/4_🪙_加密资产.py", "Crypto 内生流动性、资金与杠杆"),
    ("信用与风险", "pages/5_🛡️_信用与风险.py", "信用利差、金融条件与风险情绪"),
    ("流动性", "pages/8_💧_流动性.py", "美元系统水位与融资条件"),
    ("新闻雷达", "pages/9_📡_新闻雷达.py", "事件流、AI 分析与新闻复盘"),
    ("研究假设", "pages/11_🧭_研究假设.py", "观点、证伪条件与观察项"),
    ("信号复盘", "pages/12_📈_信号复盘.py", "规则信号后续表现与有效性"),
    ("每日沉淀", "pages/10_📝_每日沉淀.py", "日报、周报和研究包"),
]
entry_cols = st.columns(4)
for index, (label, page, description) in enumerate(entries):
    with entry_cols[index % 4]:
        st.markdown(f"**{label}**")
        st.caption(description)
        if st.button("打开", key=f"home_entry_{index}", use_container_width=True):
            go_to_research(page, label, "3M")
