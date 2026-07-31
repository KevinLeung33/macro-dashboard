import streamlit as st
import pandas as pd

from db.schema import init_db
from db.repository import query_series, query_latest_values, query_events, query_daily_reports
from data.pipeline import fetch_all
from utils.chart_utils import (
    line_chart, metric_card, multi_line_chart, dual_axis_chart,
    horizontal_bar, add_range_selector, plotly_config, render_chart_controls,
)
from utils.alerts import check_alerts
from utils.indicators import compute_zscores, latest_value, scale_series, yoy_series
from services.daily_context import get_data_health, get_market_moves
from services.composite_signals import compute_composite_signals
from services.dashboard_cockpit import build_cockpit
from services.home_brief import build_home_brief
from services.market_data import query_market_series
from services.runtime_controls import TaskBusyError, hold_task, run_with_retry
from services.time_utils import app_now
from services.access_control import render_admin_access, require_admin
from utils.navigation import go_to_research

st.set_page_config(page_title="宏观仪表盘", page_icon="📊", layout="wide")
admin_access = render_admin_access()
st.title("📊 宏观市场分析仪表盘")
st.caption("美国利率·信用·通胀·增长·全球联动 | 数据：FRED·AKShare·TIC")
render_chart_controls()

init_db()
cfg = plotly_config()

def _show(fig, note=""):
    st.plotly_chart(fig, use_container_width=True, config=cfg)
    if note:
        st.caption(note)

col1, col2, col3 = st.columns([2, 1, 1])
with col2:
    if st.button("🔄 刷新数据", use_container_width=True, type="primary", disabled=not admin_access) and require_admin("刷新数据"):
        try:
            with hold_task("data_refresh"):
                with st.spinner("拉取 FRED + 新闻 + AI分析 ..."):
                    run_with_retry(
                        "data_refresh",
                        lambda: fetch_all(include_news=True, include_global=True),
                    )
            st.rerun()
        except TaskBusyError:
            st.warning("已有数据刷新任务正在运行，请稍后再试。")
with col3:
    import os
    news_ok = "📡" if os.getenv("ALPHA_VANTAGE_KEY") or os.getenv("OPENAI_API_KEY") else "—"
    st.caption(f"FRED ✅ · 新闻 {news_ok}")

# ===== RESEARCH COCKPIT =====
cockpit = build_cockpit()
brief = build_home_brief(cockpit)

st.subheader("📝 市场简报")
date_note = "、".join(brief["data_dates"]) if brief["data_dates"] else "暂无数据日期"
health_note = f" · 数据健康提醒 {brief['health_warning_count']} 项" if brief["health_warning_count"] else ""
st.caption(f"基于当前已入库数据与近期新闻自动归纳 · 数据日期：{date_note} · 生成于 {brief['generated_at']}{health_note}")
brief_tabs = st.tabs(["今日", "本周", "中期"])
for tab, key in zip(brief_tabs, ("today", "week", "medium")):
    with tab:
        for item in brief[key]:
            st.markdown(f"- {item}")

if brief["themes"]:
    with st.expander("查看四个研究主题的当前结论", expanded=True):
        theme_cols = st.columns(min(len(brief["themes"]), 4))
        for index, item in enumerate(brief["themes"]):
            with theme_cols[index % len(theme_cols)]:
                level_text = {"red": "重点关注", "yellow": "观察中", "blue": "缓和", "green": "正常", "unknown": "数据不足"}.get(item["level"], "观察中")
                st.markdown(f"**{item['theme']} · {level_text}**")
                st.caption(item["conclusion"])
                if item["watch_next"]:
                    st.caption("继续观察：" + "、".join(item["watch_next"]))

weekly_report = next((row for row in query_daily_reports(limit=40) if row["session"] == "weekly"), None)
if weekly_report:
    with st.expander(f"最新周度中短期报告 · {weekly_report['report_date']}", expanded=False):
        st.caption("这是定期保存的详细文字报告；首页简报保持短小，完整内容在这里展开。")
        st.markdown(weekly_report["raw_markdown"] or weekly_report["summary"] or "报告暂无正文。")

st.subheader("🧭 研究驾驶舱")
top_signal = cockpit.get("top_signal")
top_move = cockpit.get("top_move")
top_trend = cockpit.get("top_trend")
top_news = cockpit.get("top_news")
review_stat = cockpit.get("signal_review", {})
asset_biases = cockpit.get("asset_biases", [])

c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    stale_count = len(cockpit.get("stale_sources", []))
    st.metric("数据状态", "需关注" if stale_count else "正常", f"{stale_count}个异常" if stale_count else "全部可用")
with c2:
    if top_signal:
        st.metric("最高优先级信号", top_signal["name"], f"{top_signal['score']}/{top_signal['max_score']}")
    else:
        st.metric("最高优先级信号", "—")
with c3:
    if top_move:
        pct = top_move.get("change_n_pct")
        delta = f"{pct:+.2f}%" if pct is not None else None
        st.metric("近期最大变化", top_move["name"], delta)
    else:
        st.metric("近期最大变化", "—")
with c4:
    if top_news:
        st.metric("新闻主线", top_news["event_type"], f"{top_news['count']}篇")
    else:
        st.metric("新闻主线", "—")
with c5:
    st.metric("研究框架", f"{cockpit['research_count']}假设", f"{cockpit['watch_count']}观察")

if top_signal or top_trend or review_stat.get("summary"):
    cols = st.columns([2, 2, 2])
    with cols[0]:
        if top_signal:
            st.markdown(f"**信号摘要**  \n{top_signal['summary']}")
            if top_signal.get("watch_next"):
                st.caption("观察：" + ", ".join(top_signal["watch_next"][:5]))
    with cols[1]:
        if top_trend:
            bits = []
            for key in ("7d", "30d", "90d"):
                data = top_trend.get("windows", {}).get(key, {})
                pct = data.get("change_pct")
                if pct is not None:
                    bits.append(f"{key} {pct:+.2f}%")
            st.markdown(f"**趋势变化**  \n{top_trend['name']}：{'；'.join(bits) or '—'}")
    with cols[2]:
        st.markdown(f"**信号复盘**  \n{review_stat.get('summary', '暂无足够样本')}")

nav_cols = st.columns(4)
with nav_cols[0]:
    if top_signal and st.button("查看最高优先级信号", key="open_top_signal", use_container_width=True):
        signal_pages = {
            "美元流动性收紧": "pages/8_💧_流动性.py",
            "Fed约束增强": "pages/1_💵_货币政策.py",
            "信用风险扩散": "pages/5_🛡️_信用与风险.py",
            "美国增长放缓": "pages/6_👷_就业市场.py",
            "Crypto宏观压力": "pages/4_🪙_加密资产.py",
            "Crypto内生流动性改善": "pages/4_🪙_加密资产.py",
        }
        go_to_research(signal_pages.get(top_signal["name"], "pages/2_📊_市场数据.py"), top_signal["name"], "3M")
with nav_cols[1]:
    if top_move and st.button("查看近期最大变化", key="open_top_move", use_container_width=True):
        go_to_research("pages/2_📊_市场数据.py", top_move["name"], "3M")
with nav_cols[2]:
    if top_news and st.button("查看相关新闻", key="open_top_news", use_container_width=True):
        go_to_research("pages/9_📡_新闻雷达.py", top_news["event_type"], "3M", topic=top_news["event_type"])
with nav_cols[3]:
    if st.button("打开证据工作台", key="open_evidence_workbench", use_container_width=True):
        signal_topics = {
            "美元流动性收紧": "美元流动性",
            "Fed约束增强": "通胀与货币政策",
            "信用风险扩散": "信用与风险偏好",
            "美国增长放缓": "增长与衰退",
            "Crypto宏观压力": "Crypto流动性",
            "Crypto内生流动性改善": "Crypto流动性",
        }
        topic = signal_topics.get(top_signal["name"], "美元流动性") if top_signal else "美元流动性"
        go_to_research("pages/13_🔎_证据工作台.py", topic, "3M", topic=topic)

if asset_biases:
    st.subheader("🧩 多资产研究偏向")
    st.caption("由当前组合信号推导，仅用于组织研究优先级；每项都可回到相应图表核对。")
    bias_cols = st.columns(4)
    for index, bias in enumerate(asset_biases):
        with bias_cols[index % 4]:
            snapshot = bias["snapshot"]
            change = snapshot.get("change_pct")
            change_text = f"近5期 {change:+.2f}%" if change is not None else "近5期暂无数据"
            st.metric(bias["asset"], bias["direction"], change_text)
            st.caption(f"置信度 {bias['confidence']:.0%} · 数据 {snapshot.get('date') or '暂无'}")
            if bias["drivers"]:
                st.caption("驱动：" + "；".join(driver["name"] for driver in bias["drivers"][:2]))
            if st.button("查看证据", key=f"bias_{bias['asset']}", use_container_width=True):
                go_to_research(bias["page"], bias["focus"], bias["window"], asset=bias["asset"])

if cockpit.get("clusters"):
    with st.expander("今日重要事件流", expanded=False):
        for item in cockpit["clusters"][:5]:
            st.caption(f"[{item['severity']}] {item['event_type']} · {item['article_count']}篇 · {item['title']}")

st.divider()

# ===== DATA HEALTH =====
health_rows = get_data_health()
if health_rows:
    st.subheader("🩺 数据状态")
    health_cols = st.columns(min(len(health_rows), 6))
    status_icon = {"fresh": "🟢", "quality_warning": "🟡", "stale": "🟡", "old": "🔴", "error": "🔴", "unknown": "⚪"}
    status_text = {"fresh": "新鲜", "quality_warning": "有质量提醒", "stale": "偏旧", "old": "过旧", "error": "失败", "unknown": "未知"}
    for i, item in enumerate(health_rows[:6]):
        with health_cols[i % len(health_cols)]:
            age = item.get("age_hours")
            age_txt = "—" if age is None else (f"{age:.0f}h" if age >= 1 else "<1h")
            st.metric(
                f"{status_icon.get(item['status'], '⚪')} {item['source']}",
                status_text.get(item["status"], item["status"]),
                f"{item['series_count']}项 · {age_txt}",
            )
    stale = [x for x in health_rows if x.get("status") in ("quality_warning", "stale", "old", "error")]
    if stale:
        with st.expander("查看数据源异常/过期详情", expanded=False):
            for item in stale:
                msg = item.get("last_error") or "最近没有错误信息"
                quality = item.get("quality_issue_count", 0)
                quality_msg = f" · 质量提醒 {quality} 条" if quality else ""
                st.caption(f"**{item['source']}** · 最新数据 {item.get('latest_data_date') or '—'} · 最近抓取 {item.get('latest_fetched_at') or '—'}{quality_msg} · {msg}")
    st.divider()

def _fred_val(df, sid):
    row = df[df["series_id"] == sid]
    return row["value"].iloc[0] if not row.empty else None

def _fred_series(sid):
    return query_series("fred", sid)

def _ak_series(sid):
    return query_series("akshare", sid)

def _yf_series(sid):
    df, _meta = query_market_series(sid)
    return df

latest = query_latest_values("fred")
yf_latest = query_latest_values("yfinance")
V = _fred_val

# ===== TODAY / RECENT MOVES =====
moves = get_market_moves(lookback_points=5, limit=6)
if moves:
    st.subheader("📌 近期变化")
    move_cols = st.columns(3)
    for i, item in enumerate(moves):
        with move_cols[i % 3]:
            pct = item.get("change_n_pct")
            chg = item.get("change_n")
            delta = f"{pct:+.2f}%" if pct is not None else (f"{chg:+.2f}" if chg is not None else "—")
            unit = item.get("unit", "")
            value = item.get("value")
            value_txt = f"{value:,.2f}{unit}" if value is not None else "—"
            st.metric(item["name"], value_txt, delta)
    st.caption("近5个数据点变化，按绝对变化幅度排序。日频约等于近一周，月频/周频代表最近几期。")
    st.divider()

# ===== COMPOSITE SIGNALS =====
signals = compute_composite_signals(lookback=5)
if signals:
    st.subheader("🧭 组合信号")
    level_icon = {"red": "🔴", "yellow": "🟡", "green": "🟢", "blue": "🔵", "unknown": "⚪"}
    level_text = {"red": "强", "yellow": "中", "green": "弱/无", "blue": "反向", "unknown": "数据不足"}
    signal_cols = st.columns(3)
    for i, sig in enumerate(signals[:6]):
        with signal_cols[i % 3]:
            st.markdown(
                f"**{level_icon.get(sig['level'], '⚪')} {sig['name']}**  \n"
                f"`{sig['score']}/{sig['max_score']}` · {level_text.get(sig['level'], sig['level'])}  \n"
                f"<small>{sig['summary']}</small>",
                unsafe_allow_html=True,
            )
            with st.expander("证据", expanded=False):
                for ev in sig["evidence"]:
                    ev_icon = {"support": "＋", "offset": "－", "neutral": "·", "missing": "?"}.get(ev["status"], "·")
                    st.caption(f"{ev_icon} {ev['label']}: {ev['value']} ({ev['score']:+})")
                if sig.get("watch_next"):
                    st.caption("观察：" + ", ".join(sig["watch_next"]))
    st.caption("组合信号由多项指标共同打分，适合做方向判断锚点；缺数据的证据不会计入分数。")
    st.divider()

# ===== STATUS LIGHTS =====
st.subheader("🔦 宏观状态灯")

def status_light(label, cur, delta, status, color, suffix=""):
    bg = {"green":"#d4edda","yellow":"#fff3cd","red":"#f8d7da","blue":"#d1ecf1"}
    fg = {"green":"#155724","yellow":"#856404","red":"#721c24","blue":"#0c5460"}
    emoji = {"green":"🟢","yellow":"🟡","red":"🔴","blue":"🔵"}
    ds = f"{delta:+.1f}{suffix}" if delta is not None else ""
    st.markdown(f"""<div style="background:{bg[color]};border-radius:12px;padding:14px 10px;text-align:center;height:110px">
    <div style="font-size:13px;color:{fg[color]};margin-bottom:4px">{emoji[color]} {label}</div>
    <div style="font-size:22px;font-weight:bold;color:{fg[color]}">{cur}{suffix}</div>
    <div style="font-size:11px;color:{fg[color]};opacity:0.7">{ds} · {status}</div></div>""", unsafe_allow_html=True)

s1,s2,s3,s4,s5 = st.columns(5)

with s1:
    indpro = _fred_series("INDPRO")
    gv, gd = None, None
    if not indpro.empty:
        indpro_yoy = yoy_series(indpro)
        gv = latest_value(indpro_yoy)
        gd = gv - indpro_yoy["value"].iloc[-4] if gv is not None and len(indpro_yoy)>3 else None
    status_light("增长", f"{gv:.1f}%" if gv is not None else "—", gd, "扩张" if (gv is not None and gv>0) else "收缩", "green" if (gv is not None and gv>0) else "red")

with s2:
    cpi_df = _fred_series("CPIAUCSL"); cv, cd = None, None
    if not cpi_df.empty:
        cpi_yoy = yoy_series(cpi_df)
        cv=latest_value(cpi_yoy)
        cd=cv-cpi_yoy["value"].iloc[-4] if cv is not None and len(cpi_yoy)>3 else None
    co = "red" if (cv and cv>3.5) else ("yellow" if (cv and cv>2.5) else "green")
    status_light("通胀", f"{cv:.1f}%" if cv else "—", cd, "偏热" if (cv and cv>3) else ("降温" if (cd and cd<0) else "稳定"), co)

with s3:
    t10v = V(latest, "T10Y3M"); td = None
    t10_df = _fred_series("T10Y3M")
    if not t10_df.empty: td = t10_df["value"].iloc[-1] - t10_df["value"].iloc[-60] if len(t10_df)>60 else None
    status_light("Fed", f"{t10v:.2f}%" if t10v else "—", td, "倒挂⚠️" if (t10v and t10v<0) else "正常", "red" if (t10v and t10v<0) else "green")

with s4:
    hv = V(latest, "BAMLH0A0HYM2"); hd = None
    hy_df = _fred_series("BAMLH0A0HYM2")
    if not hy_df.empty: hd = hy_df["value"].iloc[-1] - hy_df["value"].iloc[-60] if len(hy_df)>60 else None
    hc = "green" if (hv and hv<350) else ("yellow" if (hv and hv<500) else "red")
    status_light("信用", f"{hv:.0f}bp" if hv else "—", hd, "宽松" if (hv and hv<350) else ("紧张" if (hv and hv>500) else "观望"), hc)

with s5:
    dxy_df, dxy_meta = query_market_series("DX-Y.NYB")
    eu_df = _fred_series("DEXUSEU")
    dd = None
    if not dxy_df.empty and not dxy_meta.get("is_proxy"):
        dv = latest_value(dxy_df)
        dd = dv - dxy_df["value"].iloc[-60] if dv is not None and len(dxy_df)>60 else None
        status_light("美元", f"{dv:.1f}" if dv is not None else "—", dd, "偏强" if (dv is not None and dv>105) else "正常", "blue" if (dv is not None and dv>105) else "green")
        if dxy_meta.get("provider"):
            st.caption(f"DXY provider: {dxy_meta['provider']}")
    else:
        dv = V(latest, "DEXUSEU")
        if not eu_df.empty: dd = eu_df["value"].iloc[-1] - eu_df["value"].iloc[-60] if len(eu_df)>60 else None
        status_light("美元", f"{dv:.3f}" if dv is not None else "—", dd, "USD/EUR偏强" if (dv is not None and dv>1.12) else "正常", "blue" if (dv is not None and dv>1.12) else "green")

st.caption("💡 状态灯说明 — 增长：工业产出同比，>0=扩张 | 通胀：CPI同比，>3%=偏热 | Fed：10Y-3M利差，<0=倒挂/衰退预警 | 信用：高收益债利差，>500bp=危机 | 美元：优先使用DXY，缺失时退回USD/EUR")
st.divider()

# ===== ALERTS =====
alerts = check_alerts()
if alerts:
    st.subheader("🚨 实时告警")
    cols_a = st.columns(min(len(alerts), 3))
    for i, (icon, name, value, reason) in enumerate(alerts):
        with cols_a[i % len(cols_a)]:
            bg = "#f8d7da" if icon == "🔴" else "#fff3cd"
            fg = "#721c24" if icon == "🔴" else "#856404"
            st.markdown(f"""<div style="background:{bg};border-radius:10px;padding:12px;margin-bottom:8px">
            <b style="color:{fg}">{icon} {name}: {value}</b><br>
            <small style="color:{fg};opacity:0.8">{reason}</small></div>""", unsafe_allow_html=True)
            alert_pages = {
                "VIX": "pages/5_🛡️_信用与风险.py",
                "信用利差": "pages/5_🛡️_信用与风险.py",
                "收益率倒挂": "pages/1_💵_货币政策.py",
                "消费者信心": "pages/5_🛡️_信用与风险.py",
                "金融条件": "pages/8_💧_流动性.py",
                "失业率": "pages/6_👷_就业市场.py",
                "BTC": "pages/4_🪙_加密资产.py",
            }
            if st.button("查看相关指标", key=f"alert_{name}", use_container_width=True):
                go_to_research(alert_pages.get(name, "pages/2_📊_市场数据.py"), name, "3M", asset="BTC" if name == "BTC" else None)
else:
    st.success("✅ 所有指标正常，未触发告警阈值")

# ===== MACRO BRIEF =====
st.subheader("📋 宏观简报")
gv2 = None; cv2 = None; tv2 = None; hv2 = None; dv2 = None
indpro2 = _fred_series("INDPRO")
if not indpro2.empty:
    gv2 = latest_value(yoy_series(indpro2))
cpi2 = _fred_series("CPIAUCSL")
if not cpi2.empty:
    cv2 = latest_value(yoy_series(cpi2))
tv2 = V(latest,"T10Y3M"); hv2 = V(latest,"BAMLH0A0HYM2"); dv2 = V(latest,"DEXUSEU")
dxy_brief_df, dxy_brief_meta = query_market_series("DX-Y.NYB")
dxy2 = None if dxy_brief_meta.get("is_proxy") else latest_value(dxy_brief_df)
ffv = V(latest,"FEDFUNDS"); uv = V(latest,"UNRATE"); nv = V(latest,"NFCI")
confv = V(latest,"UMCSENT"); oilv = V(latest,"DCOILWTICO"); bv = V(latest,"CBBTCUSD")
cn_pmi_raw = query_series("akshare","CN_PMI")
cn_pmi_v = cn_pmi_raw["value"].iloc[-1] if not cn_pmi_raw.empty else None

growth_word = "扩张" if (gv2 and gv2>0) else "收缩"
infl_word = "偏热" if (cv2 and cv2>3.5) else ("温和" if (cv2 and cv2>2) else "偏低")
fed_word = "倒挂中⚠️" if (tv2 and tv2<0) else "正常"
credit_word = "宽松" if (hv2 and hv2<350) else ("紧张" if (hv2 and hv2>500) else "中性")
dollar_value = dxy2 if dxy2 is not None else dv2
dollar_label = "DXY" if dxy2 is not None else "USD/EUR"
dollar_word = "偏强" if ((dxy2 is not None and dxy2>105) or (dxy2 is None and dv2 is not None and dv2>1.12)) else "正常"
fed_part = "10Y-3M暂无数据" if tv2 is None else f"10Y-3M {'+' if tv2>=0 else ''}{tv2:.2f}%{fed_word}"

brief = f"""**{app_now().strftime('%Y年%m月%d日')}宏观简报**：经济动能{"" if gv2 is None else f"偏{growth_word}(工业产出YoY {gv2:.1f}%)"}，通胀{"" if cv2 is None else f"{infl_word}(CPI {cv2:.1f}%)"}，Fed{"维持观望" if ffv is None else f"维持{ffv:.2f}%不动"}({fed_part})，信用条件{"" if hv2 is None else f"{credit_word}(HY OAS {hv2:.0f}bp)"}，美元{"" if dollar_value is None else f"{dollar_word}({dollar_label} {dollar_value:.2f})"}。"""
if uv: brief += f"失业率{uv:.1f}%。"
if confv: brief += f"消费者信心{confv:.0f}{'' if confv>=70 else '(偏低⚠️)'}。"
if cn_pmi_v: brief += f"中国PMI {cn_pmi_v:.1f}{'扩张' if cn_pmi_v>50 else '收缩'}。"
if oilv: brief += f"WTI ${oilv:.0f}。"
if bv: brief += f"BTC ${bv:,.0f}。"

st.markdown(brief)

# AKShare status
try:
    import akshare; akshare_ok = True
except ImportError:
    akshare_ok = False
if akshare_ok and not cn_pmi_raw.empty:
    st.success(f"🇨🇳 中国数据已接入 (PMI {cn_pmi_v:.1f})")
elif akshare_ok:
    st.warning("🇨🇳 AKShare已安装但数据未拉取 — 点击🔄刷新按钮获取中国数据")
else:
    st.info("🇨🇳 中国数据未接入 — 运行 pip install akshare 后点击刷新")

st.divider()

# ===== Z-SCORE =====
with st.expander("📊 指标历史分位 (Z-Score) — 当前值距离历史均值多少个标准差", expanded=False):
    zs = compute_zscores()
    if zs:
        cols_z = st.columns(4)
        for i, z in enumerate(zs[:20]):
            with cols_z[i % 4]:
                direction = "偏高" if z["z_score"] > 0 else "偏低"
                pct_str = f"分位 {z['percentile']:.0f}%"
                st.markdown(
                    f"{z['level']} **{z['display_name']}**  \n"
                    f"<small>{z['current']:.2f} | Z={z['z_score']:+.1f}σ | {pct_str} | {direction}</small>",
                    unsafe_allow_html=True,
                )
        st.caption("🔴/🔵 >2σ 极端 | 🟡/⚪ 1-2σ 偏离 | 🟢 <1σ 正常。正Z=高于历史均值，负Z=低于。红色/蓝色哪个极端取决于该指标方向。")
    else:
        st.info("数据不足，先刷新数据")

# ===== COMBO CHARTS =====
st.subheader("📈 核心组合图")
c1,c2 = st.columns(2)
with c1:
    sp=_fred_series("SP500"); wa=_fred_series("WALCL")
    if not sp.empty and not wa.empty:
        wa2=scale_series(wa, 1e6)
        wa_latest = latest_value(wa2)
        _show(add_range_selector(dual_axis_chart({"美联储总资产(万亿)":wa2,"标普500":sp},"Fed 扩表 vs 美股","万亿美元","点")),
              f"📖 Fed资产负债表扩张(QE)通常释放流动性，缩表(QT)通常回收流动性。当前美联储总资产约 {wa_latest:.2f} 万亿美元。" if wa_latest is not None else "📖 Fed资产负债表扩张(QE)通常释放流动性，缩表(QT)通常回收流动性。")
with c2:
    hy=_fred_series("BAMLH0A0HYM2"); nfci=_fred_series("NFCI")
    if not hy.empty:
        dfs={"HY OAS":hy}
        if not nfci.empty: dfs["NFCI(金融条件)"]=nfci
        _show(add_range_selector(dual_axis_chart(dfs,"信用利差 vs 金融条件","bp","NFCI")),
              "📖 HY OAS=低评级企业发债需多付的利率溢价，>500bp=信用危机前兆。NFCI>0=金融条件收紧，>3=历史危机水平。")

c3,c4=st.columns(2)
with c3:
    ism=_fred_series("INDPRO"); unemp=_fred_series("UNRATE")
    if not ism.empty:
        ism2=ism.copy(); ism2["date"]=pd.to_datetime(ism2["date"]); ism2=ism2.sort_values("date")
        ism2["yoy"]=ism2["value"].pct_change(12)*100; ism2=ism2.dropna()
        dfs_i={"工业产出YoY":ism2[["date","yoy"]].rename(columns={"yoy":"value"})}
        if not unemp.empty: dfs_i["失业率"]=unemp
        _show(add_range_selector(dual_axis_chart(dfs_i,"增长与就业","%","%")),
              "📖 工业产出同比反映实体经济动能。与失业率反向：产出↑=失业↓。两者长时间背离需警惕。")
with c4:
    d10=_fred_series("DGS10"); ff=_fred_series("FEDFUNDS")
    if not d10.empty:
        dfs_y={"10Y":d10}
        if not ff.empty: dfs_y["FF利率"]=ff
        _show(add_range_selector(multi_line_chart(dfs_y,"美债收益率 & FF利率","%")),
              "📖 10Y=市场定价的长期利率(增长+通胀预期)。FF=美联储控制的短期利率。10Y<FF(倒挂)=市场认为Fed将被迫降息。")

# ===== 10Y-3M =====
t10_df2=_fred_series("T10Y3M")
if not t10_df2.empty:
    st.subheader("10Y-3M 利差（最经典衰退预警）")
    inv_note = "当前处于倒挂区间，需关注何时回正。" if latest_value(t10_df2) is not None and latest_value(t10_df2) < 0 else "当前未倒挂或已回正，仍需观察信用与就业是否同步恶化。"
    _show(add_range_selector(line_chart(t10_df2,"10年期-3个月利差","%",color="#ff7f0e")),
          f"📖 10Y-3M<0(倒挂)=债市定价未来经济压力。历史上倒挂与衰退风险上升相关。{inv_note}")

# ===== Events =====
st.subheader("🕐 近期事件")
evts=query_events(12)
if evts:
    cs=st.columns(2)
    for i,ev in enumerate(evts):
        with cs[i%2]:
            icon={"high":"🔴","medium":"🟡","low":"⚪"}.get(ev["impact"],"⚪")
            st.caption(f"{icon} **{ev['date']}** — {ev['title']}")
else:
    st.info("暂无事件。新闻源接入后自动填充。")

# ===== Quick view =====
st.subheader("🌎 快览")
q1,q2,q3=st.columns(3)
with q1:
    oil=_fred_series("DCOILWTICO")
    if not oil.empty:
        _show(add_range_selector(line_chart(oil,"WTI原油","$/桶",color="#d62728",height=300)),
              "📖 全球最重要的大宗商品。受OPEC+供给、地缘(霍尔木兹)、全球需求三方驱动。")
with q2:
    btc=_fred_series("CBBTCUSD")
    if not btc.empty:
        _show(add_range_selector(line_chart(btc,"BTC (Coinbase)","$",color="#f7931a",height=300)),
              "📖 实际利率↑压制BTC，美元走强抽血BTC。当前MSTR永动机叙事破裂中。")
with q3:
    copper=_fred_series("PCOPPUSDM")
    if not copper.empty:
        _show(add_range_selector(line_chart(copper,"铜价","$/吨",color="#ff7f0e",height=300)),
              "📖 铜=全球工业需求代理。铜价涨=全球制造业扩张中。\"铜博士\"领先PMI约3个月。")
