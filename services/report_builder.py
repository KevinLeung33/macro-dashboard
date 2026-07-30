"""报告生成器 — 日报/周报"""
import json
from datetime import timedelta

from utils.alerts import check_alerts
from utils.indicators import compute_zscores
from db.repository import query_latest_values, query_events
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
    lines.append(f"{emoji} **{'日报' if report_type == 'daily' else '周报'} — {date_str}**")
    lines.append("")

    # Market snapshot
    lines.append("**市场快照**")
    lines.append(f"标普: {_fmt(sp,0)} | VIX: {_fmt(vix,2)} | 10Y: {_fmt(d10,2)}% | FF: {_fmt(ff,2)}% | 10Y-3M: {_fmt(t10,2)}%")
    lines.append(f"HY OAS: {_fmt(hy,0)}bp | NFCI: {_fmt(nfci,2)} | 失业率: {_fmt(unemp,1)}% | 消费者信心: {_fmt(conf,0)}")
    lines.append(f"WTI: ${_fmt(oil,0)} | BTC: ${_fmt(btc,0)} | USD/EUR: {_fmt(dxy,3)}")
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

    return "\n".join(lines)
