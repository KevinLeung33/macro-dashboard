"""Daily research context: data freshness, market moves, news and alerts."""
from datetime import datetime

import pandas as pd

from db.repository import (
    query_analyzed_news,
    query_research_context,
    query_series,
    query_series_snapshot,
    query_source_health,
    upsert_daily_report,
)
from services.composite_signals import compute_composite_signals
from services.market_data import query_market_series
from services.news_clusterer import get_important_clusters
from services.research_linker import enrich_research_context
from services.signal_review import save_signal_snapshots
from services.time_utils import app_now
from utils.alerts import check_alerts
from utils.indicators import compute_zscores


WATCH_SERIES = [
    ("fred", "SP500", "标普500", "点"),
    ("fred", "NASDAQCOM", "纳斯达克", "点"),
    ("fred", "VIXCLS", "VIX", "点"),
    ("market", "DX-Y.NYB", "DXY", "点"),
    ("fred", "DGS10", "10Y美债", "%"),
    ("fred", "DGS2", "2Y美债", "%"),
    ("fred", "DFII10", "10Y实际利率", "%"),
    ("fred", "T10Y3M", "10Y-3M", "%"),
    ("fred", "BAMLH0A0HYM2", "HY OAS", "bp"),
    ("fred", "NFCI", "NFCI", ""),
    ("fred", "DCOILWTICO", "WTI原油", "美元"),
    ("fred", "CBBTCUSD", "BTC", "美元"),
    ("crypto_liquidity", "STABLE_TOTAL_MCAP", "稳定币总市值", "美元"),
    ("crypto_liquidity", "STABLE_MAJOR_MCAP", "USDT+USDC市值", "美元"),
    ("crypto_liquidity", "ETHBTC", "ETH/BTC", ""),
    ("akshare", "CN_PMI", "中国PMI", ""),
    ("akshare", "CN_CAIXIN_PMI", "财新PMI", ""),
]


def _fmt_num(value, decimals=2):
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}"


def _move_score(item):
    pct = item.get("change_n_pct")
    abs_change = item.get("change_n")
    if pct is not None:
        return abs(pct)
    if abs_change is not None:
        return abs(abs_change)
    return 0


def _nullable(value):
    """Convert pandas missing values to normal Python ``None`` values.

    ``query_source_health`` combines rows with slightly different columns
    (market data, news and AI).  Pandas represents absent fields as ``NaN``;
    those values are neither useful to the dashboard nor valid in FastAPI's
    strict JSON responses.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        # Health rows only contain scalars, but keep this helper safe if a
        # future query adds a non-scalar value.
        pass
    return value


def _health_int(value):
    value = _nullable(value)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def get_data_health():
    df = query_source_health()
    if df.empty:
        return []

    rows = []
    now = app_now().replace(tzinfo=None)
    for _, row in df.iterrows():
        fetched_at = _nullable(row.get("latest_fetched_at"))
        age_hours = None
        if fetched_at:
            try:
                ts = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00")).replace(tzinfo=None)
                age_hours = (now - ts).total_seconds() / 3600
            except ValueError:
                age_hours = None

        if age_hours is None:
            status = "unknown"
        elif age_hours <= 24:
            status = "fresh"
        elif age_hours <= 72:
            status = "stale"
        else:
            status = "old"

        last_status = str(_nullable(row.get("last_status", "")) or "").lower()
        if last_status == "error":
            status = "error"
        elif last_status == "skipped":
            status = "unavailable"

        rows.append({
            "source": _nullable(row.get("source")) or "unknown",
            "series_count": _health_int(row.get("series_count")),
            "quality_issue_count": _health_int(row.get("quality_issue_count")),
            "latest_data_date": _nullable(row.get("latest_data_date")),
            "latest_fetched_at": fetched_at,
            "last_fetch_attempt": _nullable(row.get("last_fetch_attempt")) or fetched_at,
            "last_series_id": _nullable(row.get("last_series_id")) or "",
            "age_hours": age_hours,
            "status": status,
            "last_error": _nullable(row.get("last_error")) or "",
            "last_status": last_status,
        })
        if rows[-1]["quality_issue_count"] > 0 and rows[-1]["status"] == "fresh":
            rows[-1]["status"] = "quality_warning"
    return rows


def get_market_moves(lookback_points=5, limit=8):
    moves = []
    for source, series_id, name, unit in WATCH_SERIES:
        if source == "market":
            df, meta = query_market_series(series_id)
            snap = None
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").dropna(subset=["value"])
                latest = df.iloc[-1]
                prev = df.iloc[-lookback_points - 1] if len(df) > lookback_points else None
                pct = None
                if prev is not None and prev["value"] not in (None, 0):
                    pct = (latest["value"] / prev["value"] - 1) * 100
                snap = {
                    "source": meta["provider"],
                    "series_id": meta["series_id"],
                    "date": latest["date"].strftime("%Y-%m-%d"),
                    "value": float(latest["value"]),
                    "change_1": None,
                    "change_1_pct": None,
                    "change_n": None if prev is None else float(latest["value"] - prev["value"]),
                    "change_n_pct": pct,
                }
        else:
            snap = query_series_snapshot(source, series_id, lookback_points=lookback_points)
        if not snap:
            continue
        snap["name"] = name
        snap["unit"] = unit
        snap["score"] = _move_score(snap)
        moves.append(snap)
    return sorted(moves, key=lambda x: x["score"], reverse=True)[:limit]


def get_multi_window_trends(windows=(7, 30, 90)):
    trends = []
    for source, series_id, name, unit in WATCH_SERIES:
        if source == "market":
            df, meta = query_market_series(series_id)
            source_label = meta.get("provider", source)
            actual_id = meta.get("series_id", series_id)
        else:
            df = query_series(source, series_id)
            source_label = source
            actual_id = series_id
        if df.empty:
            continue
        rows = df.copy()
        rows["date"] = pd.to_datetime(rows["date"])
        rows = rows.sort_values("date").dropna(subset=["value"])
        if rows.empty:
            continue
        latest = rows.iloc[-1]
        item = {
            "source": source_label,
            "series_id": actual_id,
            "name": name,
            "unit": unit,
            "date": latest["date"].strftime("%Y-%m-%d"),
            "value": float(latest["value"]),
            "windows": {},
        }
        for window in windows:
            cutoff = latest["date"] - pd.Timedelta(days=window)
            prev_rows = rows[rows["date"] <= cutoff]
            if prev_rows.empty:
                continue
            prev = prev_rows.iloc[-1]
            change = float(latest["value"] - prev["value"])
            pct = None if prev["value"] in (None, 0) else float((latest["value"] / prev["value"] - 1) * 100)
            item["windows"][f"{window}d"] = {"change": change, "change_pct": pct}
        if item["windows"]:
            item["importance"] = max(abs(v.get("change_pct") or v.get("change") or 0) for v in item["windows"].values())
            trends.append(item)
    return sorted(trends, key=lambda x: x.get("importance", 0), reverse=True)[:12]


def get_news_trends(days=7):
    rows = query_analyzed_news(min_severity=2, limit=80, days=days)
    buckets = {}
    for row in rows:
        item = dict(row)
        key = item.get("event_type") or "other"
        bucket = buckets.setdefault(key, {
            "event_type": key,
            "count": 0,
            "avg_severity": 0,
            "directions": {},
            "assets": {},
            "latest": None,
        })
        bucket["count"] += 1
        bucket["avg_severity"] += float(item.get("severity") or 0)
        direction = item.get("direction") or "unknown"
        bucket["directions"][direction] = bucket["directions"].get(direction, 0) + 1
        for asset in str(item.get("assets_impacted") or "").replace("，", ",").split(","):
            asset = asset.strip()
            if asset:
                bucket["assets"][asset] = bucket["assets"].get(asset, 0) + 1
        if not bucket["latest"]:
            bucket["latest"] = item.get("summary_cn") or item.get("title")
    out = []
    for bucket in buckets.values():
        if bucket["count"]:
            bucket["avg_severity"] = bucket["avg_severity"] / bucket["count"]
        bucket["top_assets"] = sorted(bucket["assets"], key=bucket["assets"].get, reverse=True)[:5]
        out.append(bucket)
    return sorted(out, key=lambda x: (x["avg_severity"], x["count"]), reverse=True)[:8]


def build_daily_context(lookback_points=5):
    alerts = check_alerts()
    zscores = compute_zscores()
    news = query_analyzed_news(min_severity=3, limit=8)
    moves = get_market_moves(lookback_points=lookback_points)
    health = get_data_health()
    research = enrich_research_context(query_research_context(limit=8), lookback_points=lookback_points)
    signals = compute_composite_signals(lookback=lookback_points)
    clusters = get_important_clusters(limit=8, min_severity=3)

    context = {
        "generated_at": app_now().strftime("%Y-%m-%d %H:%M:%S%z"),
        "lookback_points": lookback_points,
        "data_health": health,
        "market_moves": moves,
        "multi_window_trends": get_multi_window_trends(),
        "news_trends": get_news_trends(),
        "alerts": [
            {"level": icon, "name": name, "value": value, "reason": reason}
            for icon, name, value, reason in alerts
        ],
        "extreme_zscores": zscores[:8],
        "important_news": [dict(row) for row in news],
        "research_context": research,
        "composite_signals": signals,
        "important_clusters": clusters,
    }
    return context


def build_context_markdown(context):
    lines = [f"### 每日研究包 ({context['generated_at']})", ""]

    moves = context.get("market_moves", [])
    if moves:
        lines.append("**近期变化最大的指标**")
        for item in moves[:6]:
            pct = item.get("change_n_pct")
            chg = item.get("change_n")
            change = f"{pct:+.2f}%" if pct is not None else f"{chg:+.2f}"
            lines.append(
                f"- {item['name']}: {_fmt_num(item['value'])}{item['unit']} "
                f"({item['date']}, 近{context['lookback_points']}期 {change})"
            )
        lines.append("")

    trends = context.get("multi_window_trends", [])
    if trends:
        lines.append("**多窗口趋势**")
        for item in trends[:6]:
            parts = []
            for key in ("7d", "30d", "90d"):
                value = item.get("windows", {}).get(key, {})
                pct = value.get("change_pct")
                chg = value.get("change")
                if pct is not None:
                    parts.append(f"{key} {pct:+.2f}%")
                elif chg is not None:
                    parts.append(f"{key} {chg:+.2f}")
            lines.append(f"- {item['name']}: {_fmt_num(item['value'])}{item['unit']} ({'；'.join(parts)})")
        lines.append("")

    alerts = context.get("alerts", [])
    if alerts:
        lines.append("**告警**")
        for item in alerts[:6]:
            lines.append(f"- {item['level']} {item['name']}: {item['value']} — {item['reason']}")
        lines.append("")

    signals = context.get("composite_signals", [])
    if signals:
        lines.append("**组合信号**")
        for item in signals[:5]:
            lines.append(
                f"- {item['name']}: {item['level']} {item['score']}/{item['max_score']} — {item['summary']}"
            )
        lines.append("")

    news = context.get("important_news", [])
    clusters = context.get("important_clusters", [])
    if clusters:
        lines.append("**重要事件流**")
        for item in clusters[:5]:
            lines.append(
                f"- [{item['severity']}] {item['event_type']} · {item['article_count']}篇 · "
                f"{item['title']} ({item['assets_impacted'] or '—'})"
            )
        lines.append("")

    news_trends = context.get("news_trends", [])
    if news_trends:
        lines.append("**新闻主题动向**")
        for item in news_trends[:5]:
            assets = ",".join(item.get("top_assets") or []) or "—"
            lines.append(
                f"- {item['event_type']}: {item['count']}篇，平均严重度 {item['avg_severity']:.1f}，资产 {assets}"
            )
        lines.append("")

    if news:
        lines.append("**重要新闻/AI分析**")
        for item in news[:5]:
            lines.append(
                f"- [{item['severity']}] {item['summary_cn'] or item['title']} "
                f"({item['event_type']}, {item['assets_impacted']})"
            )
        lines.append("")

    zscores = context.get("extreme_zscores", [])
    if zscores:
        lines.append("**极端分位指标**")
        for item in zscores[:5]:
            lines.append(
                f"- {item['level']} {item['display_name']}: "
                f"Z={item['z_score']:+.1f}, 分位 {item['percentile']:.0f}%"
            )
        lines.append("")

    research = context.get("research_context") or {}
    hypotheses = research.get("active_hypotheses", [])
    viewpoints = research.get("recent_viewpoints", [])
    watchlist = research.get("active_watchlist", [])
    if hypotheses or viewpoints or watchlist:
        lines.append("**你的研究框架**")
        for item in hypotheses[:3]:
            lines.append(f"- 假设：{item['title']} | 资产 {item.get('assets') or '—'} | 置信 {float(item.get('confidence') or 0):.0%}")
            linked_data = item.get("linked_data") or []
            for data in linked_data[:2]:
                pct = data.get("change_n_pct")
                if pct is not None:
                    lines.append(f"  - 关联指标：{data.get('label', data['series_id'])} 近{context['lookback_points']}期 {pct:+.2f}%")
        for item in viewpoints[:3]:
            lines.append(f"- 观点：{item['view_date']} {item['area']} `{item['stance']}` — {item.get('rationale') or ''}")
        for item in watchlist[:3]:
            lines.append(f"- 观察：{item['title']} | 触发 {item.get('trigger') or '—'}")

    return "\n".join(lines)


def save_daily_context(session="daily"):
    context = build_daily_context()
    report_date = app_now().strftime("%Y-%m-%d")
    review_result = save_signal_snapshots(signal_date=report_date)
    context["signal_review"] = review_result
    markdown = build_context_markdown(context)
    summary = " | ".join(
        f"{m['name']} {m.get('change_n_pct'):+.2f}%"
        for m in context.get("market_moves", [])[:3]
        if m.get("change_n_pct") is not None
    )
    upsert_daily_report(
        report_date=report_date,
        session=session,
        title=f"{report_date} 每日研究包",
        summary=summary,
        context=context,
        raw_markdown=markdown,
    )
    return context, markdown
