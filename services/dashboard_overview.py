"""Shared summary and data-quality helpers for the macro/trading workbenches.

The detailed pages keep their existing data queries and charts.  This module
provides the small, consistent summary layer shown before those details.
"""
from datetime import date, timedelta

import streamlit as st

from db.repository import query_events, query_series_snapshot
from services.daily_context import get_data_health
from services.market_data import query_market_series


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
