"""Shared summary and data-quality helpers for the macro/trading workbenches.

The detailed pages keep their existing data queries and charts.  This module
provides the small, consistent summary layer shown before those details.
"""
from datetime import date, timedelta

import streamlit as st

from db.repository import query_events, query_series_snapshot
from services.daily_context import get_data_health
from services.market_data import query_market_series


HORIZON_GUIDANCE = {
    "home": {
        "short": "先看最近几天的价格、美元、利率和新闻是否同向。若数据质量异常，先核对数据日期，再降低结论和仓位的置信度。",
        "medium": "再看 30D/90D 趋势是否一致，并观察变化是否从利率、美元或信用传导到风险资产。趋势分化时，不要让单条新闻决定方向。",
        "long": "最后判断所处宏观 regime：增长、通胀、流动性和风险偏好是否形成组合。长期判断用于确定交易方向和容错，不直接替代入场触发。",
    },
    "monetary": {
        "short": "短期重点看政策利率、2Y/10Y 和实际利率的最新变化，判断市场是在交易降息、再通胀，还是期限溢价上升。",
        "medium": "中期看收益率曲线、通胀预期与核心通胀是否同向。利率下行若伴随增长恶化，和利率下行伴随软着陆，对风险资产的含义不同。",
        "long": "长期看政策 regime、通胀锚和资产负债表方向，作为估值和流动性的背景变量；不要用单次议息会议推断长期趋势。",
    },
    "market": {
        "short": "短期先看 SPX、QQQ/MAGS、TLT、VIX、DXY 的联动，重点识别风险资产上涨是否得到利率和信用确认。",
        "medium": "中期比较成长、长债、信用和大盘的相对强弱，观察是普涨、风格切换还是少数权重股推动。",
        "long": "长期用增长、通胀和利率 regime 解释资产配置，不把一段强势行情直接等同于新牛市。",
    },
    "global": {
        "short": "短期看人民币、沪深300、恒生科技、美元和大宗商品的同步性，先判断中国资产是在交易国内因素还是全球风险偏好。",
        "medium": "中期重点看 PMI、CPI/PPI、M2/社融与资产价格是否形成改善链条；宏观改善未传导到价格时，结论应保持中性。",
        "long": "长期看中国增长、政策托底、地产与全球周期的相对位置，主要用于确定资产偏好和风险预算。",
    },
    "crypto": {
        "short": "短期把 BTC 价格、资金费率、持仓量和美元/实际利率放在一起看，价格上涨但杠杆过快扩张时要防拥挤。",
        "medium": "中期看稳定币市值、ETF/交易所资金流、ETH/BTC 和风险资产联动，区分真实资金流入与衍生品推动。",
        "long": "长期看美元流动性、监管与加密资产自身周期；宏观顺风只提高交易胜率，不取消止损和仓位约束。",
    },
    "credit": {
        "short": "短期看 HY 利差、金融条件、VIX 和风险资产是否同步恶化，信用先于价格转弱时应提高警惕。",
        "medium": "中期看信用利差、银行条件和实际利率的组合，区分正常风险溢价波动与融资压力扩散。",
        "long": "长期看杠杆、违约周期和金融条件 regime，信用风险更适合用来决定风险预算，而不是单独给出方向。",
    },
    "employment": {
        "short": "短期看初请、失业率和利率市场的即时反应，重点判断就业数据是支持软着陆还是触发衰退交易。",
        "medium": "中期比较就业、工资、职位空缺和劳动参与率，观察降温是否有序，还是已经转为需求快速收缩。",
        "long": "长期看劳动周期与生产率、人口结构和通胀的关系，主要用于判断增长底盘和政策约束。",
    },
    "history": {
        "short": "短期看当前价格、利差和波动率相对历史区间的位置，避免只凭涨跌幅判断极端程度。",
        "medium": "中期看曲线、信用和资产价格是否处于同一 regime；历史相似形态只能提供条件类比，不能代替当前数据。",
        "long": "长期用危机、衰退和流动性周期建立参照，重点寻找证伪条件，而不是寻找一个完全相同的历史模板。",
    },
    "liquidity": {
        "short": "短期看 SOFR、回购、美元、短端利率和信用条件，确认市场是否出现融资紧张或流动性改善。",
        "medium": "中期看准备金、TGA/RRP、央行资产负债表和 M2/信用扩张的方向，观察流动性变化是否传导到风险资产。",
        "long": "长期判断 QE/QT、财政与银行体系共同决定的流动性 regime；流动性是背景风，不是精确择时信号。",
    },
}


CROSS_ASSET_TAPE = [
    ("market", "^GSPC", "SPX", "点", "risk"),
    ("market", "QQQ", "QQQ", "$", "risk"),
    ("market", "MAGS", "MAGS", "$", "risk"),
    ("market", "TLT", "TLT", "$", "rates"),
    ("market", "HYG", "HYG", "$", "credit"),
    ("market", "LQD", "LQD", "$", "credit"),
    ("market", "^VIX", "VIX", "点", "risk"),
    ("market", "DX-Y.NYB", "DXY", "点", "fx"),
    ("fred", "DGS10", "10Y美债", "%", "rates"),
    ("fred", "DFII10", "10Y实际利率", "%", "rates"),
    ("market", "USDCNH=X", "USDCNH", "", "fx"),
    ("market", "USDJPY=X", "USDJPY", "", "fx"),
    ("market", "GC=F", "黄金", "$", "commodity"),
    ("market", "CL=F", "WTI", "$", "commodity"),
    ("fred", "PCOPPUSDM", "铜", "$", "commodity"),
    ("market", "000300.SS", "沪深300", "点", "china"),
    ("market", "HSTECH", "恒生科技", "点", "china"),
    ("market", "BTC-USD", "BTC", "$", "crypto"),
    ("market", "ETH-USD", "ETH", "$", "crypto"),
]


def _fmt(value, unit=""):
    if value is None:
        return "—"
    try:
        decimals = 2 if abs(float(value)) < 1000 else 0
        return f"{float(value):,.{decimals}f}{unit}"
    except (TypeError, ValueError):
        return f"{value}{unit}"


def _snapshot(source, series_id, label, unit="", group=""):
    try:
        if source == "market":
            frame, meta = query_market_series(series_id)
            if frame.empty:
                return {"label": label, "unit": unit, "group": group, "status": "missing"}
            rows = frame.sort_values("date").dropna(subset=["value"])
            latest = rows.iloc[-1]
            previous = rows.iloc[-2] if len(rows) > 1 else None
            prev_n = rows.iloc[-6] if len(rows) > 5 else None
            actual_source = meta.get("provider", source)
            actual_id = meta.get("series_id", series_id)
            value = float(latest["value"])
            date_text = str(latest["date"])[:10]
            if hasattr(latest["date"], "strftime"):
                date_text = latest["date"].strftime("%Y-%m-%d")
            def pct(row):
                return None if row is None or not row["value"] else (value / float(row["value"]) - 1) * 100
            return {
                "label": label, "unit": unit, "group": group, "value": value,
                "change_1_pct": pct(previous), "change_5_pct": pct(prev_n),
                "date": date_text, "source": actual_source, "series_id": actual_id,
                "status": "ok",
            }
        snap = query_series_snapshot(source, series_id, lookback_points=5)
        if not snap:
            return {"label": label, "unit": unit, "group": group, "status": "missing"}
        return {
            "label": label, "unit": unit, "group": group, "value": snap.get("value"),
            "change_1_pct": snap.get("change_1_pct"), "change_5_pct": snap.get("change_n_pct"),
            "date": snap.get("date", ""), "source": snap.get("source", source),
            "series_id": snap.get("series_id", series_id), "status": "ok",
        }
    except Exception as exc:
        return {"label": label, "unit": unit, "group": group, "status": "error", "error": str(exc)[:140]}


def build_cross_asset_tape(groups=None):
    selected = set(groups or [])
    rows = []
    for source, series_id, label, unit, group in CROSS_ASSET_TAPE:
        if selected and group not in selected:
            continue
        rows.append(_snapshot(source, series_id, label, unit, group))
    return rows


def health_map():
    return {str(row.get("source")): row for row in get_data_health()}


def quality_label(health):
    if not health:
        return "质量未知"
    status = health.get("status", "unknown")
    return {
        "fresh": "数据新鲜", "quality_warning": "有质量提醒", "stale": "数据偏旧",
        "old": "数据过旧", "error": "来源失败", "unavailable": "来源不可用",
        "disabled": "来源已停用", "unknown": "质量未知",
    }.get(status, status)


def render_quality_strip(sources=None, *, title="数据质量"):
    health = health_map()
    rows = [health.get(source) for source in (sources or health) if health.get(source)]
    if not rows:
        st.caption(f"{title}：暂无抓取状态")
        return health
    bad = [row for row in rows if row.get("status") not in {"fresh", "disabled"}]
    label = "需要关注" if bad else "正常"
    with st.expander(f"{title}：{label}（{len(bad)}项需关注）", expanded=bool(bad)):
        for row in rows:
            age = row.get("age_hours")
            age_text = "—" if age is None else (f"{age:.0f}小时" if age >= 1 else "<1小时")
            st.caption(
                f"**{row['source']}** · {quality_label(row)} · 最新数据 {row.get('latest_data_date') or '—'} · "
                f"最近抓取 {age_text} · 质量问题 {row.get('quality_issue_count', 0)}条"
            )
            if row.get("last_error") and row.get("status") in {"error", "quality_warning"}:
                st.caption(f"　{str(row['last_error'])[:220]}")
    return health


def render_snapshot_cards(rows, *, columns=4, health=None):
    health = health or health_map()
    visible = [row for row in rows if row.get("status") == "ok"]
    if not visible:
        st.info("暂无足够新鲜数据生成状态摘要；请先刷新数据或查看详细数据页。")
        return
    cols = st.columns(min(columns, len(visible)))
    for index, row in enumerate(visible):
        with cols[index % len(cols)]:
            change = row.get("change_5_pct")
            delta = None if change is None else f"近5期 {change:+.2f}%"
            st.metric(row["label"], _fmt(row.get("value"), row.get("unit", "")), delta)
            source_health = health.get(row.get("source"), {})
            st.caption(
                f"{quality_label(source_health)} · {row.get('date') or '—'} · "
                f"{row.get('source') or '—'}"
            )


def render_horizon_guidance(key, *, brief=None, title="短中长期阅读提示"):
    """在详细图表前提供稳定的阅读顺序；首页可传入最新简报覆盖通用文字。"""
    guidance = HORIZON_GUIDANCE.get(key, HORIZON_GUIDANCE["home"])
    st.markdown(f"**{title}**")
    columns = st.columns(3)
    labels = (("short", "短期 · 交易触发"), ("medium", "中期 · 趋势验证"), ("long", "长期 · 宏观背景"))
    for column, (period, label) in zip(columns, labels):
        with column:
            st.caption(f"**{label}**")
            text = " ".join(str(item) for item in brief[period][:3]) if brief and brief.get(period) else guidance[period]
            st.write(text)


def render_event_calendar(limit=8):
    rows = query_events(limit=limit)
    upcoming = []
    today = date.today()
    horizon = today + timedelta(days=14)
    for row in rows:
        raw_date = str(row["date"] or "")[:10]
        try:
            event_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if today <= event_date <= horizon:
            upcoming.append(row)
    if upcoming:
        for row in upcoming:
            icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(row["impact"], "⚪")
            st.caption(f"{icon} **{row['date']}** · {row['title']} · {row['category']}")
    else:
        st.caption("暂无未来两周的事件日历；已有事件可在详细数据/新闻页继续查看。")


def render_workbench_tabs(summary_renderer, detail_renderer, evidence_renderer=None):
    """Render a consistent three-level page without changing the detail query code."""
    summary_tab, detail_tab, evidence_tab = st.tabs(["状态总览", "详细数据", "事件与证据"])
    with summary_tab:
        summary_renderer()
    with detail_tab:
        detail_renderer()
    with evidence_tab:
        if evidence_renderer:
            evidence_renderer()
        else:
            st.info("该主题的事件与证据关联正在建设中；可先使用新闻雷达和证据工作台。")
