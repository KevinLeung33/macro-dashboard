"""报告生成器 — 日报/周报"""
import json
from datetime import timedelta

from utils.alerts import check_alerts
from utils.indicators import compute_zscores
from db.repository import query_latest_values, query_events, upsert_daily_report
from services.daily_context import get_data_health, get_market_moves, get_multi_window_trends, get_news_trends
from services.time_utils import app_now


def _val(df, sid):
    row = df[df["series_id"] == sid]
    return row["value"].iloc[0] if not row.empty else None


def _fmt(v, decimals=1, suffix=""):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{decimals}f}{suffix}"
    return f"{v}{suffix}"


def build_report(report_type="daily"):
    now = app_now()
    date_str = now.strftime("%Y-%m-%d")

    latest = query_latest_values("fred")
    alerts = check_alerts()
    zscores = compute_zscores()
    weekly_trends = get_multi_window_trends() if report_type == "weekly" else []
    weekly_news = get_news_trends(days=7) if report_type == "weekly" else []
    weekly_moves = get_market_moves(lookback_points=5, limit=6) if report_type == "weekly" else []

    # Market snapshot
    sp = _val(latest, "SP500")
    vix = _val(latest, "VIXCLS")
    d10 = _val(latest, "DGS10")
    ff = _val(latest, "FEDFUNDS")
    t10 = _val(latest, "T10Y3M")
    hy = _val(latest, "BAMLH0A0HYM2")
    oil = _val(latest, "DCOILWTICO")
    btc = _val(latest, "CBBTCUSD")
    dxy = _val(latest, "DEXUSEU")
    unemp = _val(latest, "UNRATE")
    conf = _val(latest, "UMCSENT")
    nfci = _val(latest, "NFCI")

    # Build report
    lines = []
    emoji = "📊" if report_type == "daily" else "📋"
    title = "日报" if report_type == "daily" else "周度中短期对比报告"
    lines.append(f"{emoji} **{title} — {date_str}**")
    if report_type == "weekly":
        lines.append("本报告用于解释近一周发生了什么、30天趋势是否延续，以及90天背景是否支持当前判断；它不是交易指令。")
    lines.append("")

    # Market snapshot
    lines.append("**市场快照**")
    lines.append(f"标普: {_fmt(sp,0)} | VIX: {_fmt(vix,2)} | 10Y: {_fmt(d10,2)}% | FF: {_fmt(ff,2)}% | 10Y-3M: {_fmt(t10,2)}%")
    lines.append(f"HY OAS: {_fmt(hy,0)}bp | NFCI: {_fmt(nfci,2)} | 失业率: {_fmt(unemp,1)}% | 消费者信心: {_fmt(conf,0)}")
    lines.append(f"WTI: ${_fmt(oil,0)} | BTC: ${_fmt(btc,0)} | USD/EUR: {_fmt(dxy,3)}")
    lines.append("")

    if report_type == "weekly":
        lines.append("**中短期趋势比较**")
        lines.append("比较口径：市场数据使用近5个数据点作为短期，宏观指标同时观察 7D、30D、90D；不同频率指标不强行换算成同一频率。")
        if weekly_moves:
            lines.append("近一周变化靠前的指标：")
            for item in weekly_moves[:6]:
                pct = item.get("change_n_pct")
                change = _fmt(pct, 2, "%") if pct is not None else _fmt(item.get("change_n"), 2, item.get("unit", ""))
                lines.append(f"  - {item['name']}: 当前 {_fmt(item.get('value'), 2, item.get('unit', ''))}，近5期 {change}")
        for item in weekly_trends[:8]:
            parts = []
            for key in ("7d", "30d", "90d"):
                window = item.get("windows", {}).get(key, {})
                if window.get("change_pct") is not None:
                    parts.append(f"{key} {_fmt(window['change_pct'], 2, '%')}")
                elif window.get("change") is not None:
                    parts.append(f"{key} {_fmt(window['change'], 2, item.get('unit', ''))}")
            lines.append(f"  - {item['name']}: {'；'.join(parts) or '暂无足够历史'}")
        if not weekly_trends and not weekly_moves:
            lines.append("  - 暂无足够的趋势数据。")
        lines.append("")

        lines.append("**新闻主题与传导线索（近7天）**")
        if weekly_news:
            for item in weekly_news[:6]:
                assets = ",".join(item.get("top_assets") or []) or "未明确资产"
                lines.append(
                    f"  - {item['event_type']}: {item['count']}篇，平均严重度 {item['avg_severity']:.1f}，"
                    f"主要资产 {assets}；最新摘要：{item.get('latest') or '暂无'}"
                )
        else:
            lines.append("  - 近7天暂无达到分析阈值的新闻主题。")
        lines.append("")

    # Alerts
    if alerts:
        lines.append("**🚨 告警**")
        for icon, name, value, reason in alerts:
            lines.append(f"  {icon} {name}: {value} — {reason}")
    else:
        lines.append("**✅ 无告警** — 所有指标在正常范围")
    lines.append("")

    # Z-score extremes
    extremes = [z for z in zscores[:5] if abs(z["z_score"]) > 1.5]
    if extremes:
        lines.append("**📊 极端指标 (Z-score)**")
        for z in extremes[:5]:
            direction = "高" if z["z_score"] > 0 else "低"
            lines.append(f"  {z['level']} {z['display_name']}: Z={z['z_score']:+.1f}σ (历史{'' if z['z_score']>0 else '极'}{direction})")
        lines.append("")

    # Recent events (last 24h for daily, last 7d for weekly)
    days = 1 if report_type == "daily" else 7
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    events = query_events(limit=30)
    recent_events = []
    if events:
        for e in events:
            if e["date"] >= cutoff:
                recent_events.append(e)

    if recent_events:
        lines.append(f"**🕐 近{'24小时' if report_type == 'daily' else '7天'}重要事件 ({len(recent_events)}条)**")
        for e in recent_events[:10]:
            # Try to parse AI analysis from description
            analysis = ""
            if e.get("description"):
                try:
                    ad = json.loads(e["description"])
                    if "sentiment" in ad:
                        s = {"bullish":"🟢","bearish":"🔴","neutral":"⚪"}.get(ad.get("sentiment",""),"")
                        analysis = f" [{s} {ad.get('summary_cn','')[:30]}]"
                except (json.JSONDecodeError, TypeError):
                    if len(str(e["description"])) < 50:
                        analysis = f" — {e['description']}"

            icon = {"high":"🔴","medium":"🟡","low":"⚪"}.get(e.get("impact",""), "⚪")
            lines.append(f"  {icon} {e['title'][:80]}{analysis}")
        lines.append("")

    if report_type == "weekly":
        stale = [item for item in get_data_health() if item.get("status") in ("quality_warning", "stale", "old", "error", "unavailable")]
        lines.append("**数据质量与下周观察**")
        if stale:
            for item in stale[:6]:
                lines.append(f"  - {item.get('source')}: {item.get('status')}，最新数据 {item.get('latest_data_date') or '—'}")
        else:
            lines.append("  - 当前数据源没有明显过期或质量告警。")
        lines.append("  - 下周优先观察：10Y实际利率、DXY、HY OAS、VIX、BTC，以及新闻主题是否继续扩散。")

    markdown = "\n".join(lines)
    if report_type == "weekly":
        upsert_daily_report(
            report_date=date_str,
            session="weekly",
            title=f"{date_str} 周度中短期对比报告",
            summary="；".join(line.strip(" -") for line in lines if line.startswith("  - "))[:500],
            context={"report_type": "weekly", "generated_at": now.isoformat()},
            raw_markdown=markdown,
        )
    return markdown
