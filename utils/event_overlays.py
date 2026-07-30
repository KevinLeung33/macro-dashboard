"""Plotly overlays for news clusters and composite signal snapshots."""
import pandas as pd
from db.repository import get_db
from db.sqlite_compat import sqlite_date


NEWS_COLORS = {5: "#d62728", 4: "#d62728", 3: "#ffb000", 2: "#999999", 1: "#bbbbbb"}
SIGNAL_COLORS = {"red": "#d62728", "yellow": "#ffb000", "green": "#2ca02c", "blue": "#1f77b4"}


def _csv_has(text, values):
    if not values:
        return True
    hay = {x.strip().lower() for x in (text or "").split(",") if x.strip()}
    return bool(hay & {v.lower() for v in values})


def get_chart_events(asset=None, event_types=None, start_date=None, limit=12):
    start_date = sqlite_date(start_date)
    assets = [asset] if isinstance(asset, str) and asset else (asset or [])
    events = []
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, title, event_type, assets_impacted, severity, last_seen_at,
                      article_count, primary_source
               FROM news_clusters
               WHERE (? IS NULL OR last_seen_at >= ?)
               ORDER BY severity DESC, last_seen_at DESC
               LIMIT 100""",
            (start_date, start_date),
        ).fetchall()

        sig_rows = conn.execute(
            """SELECT id, signal_date, signal_name, level, score, max_score, assets
               FROM composite_signal_snapshots
               WHERE (? IS NULL OR signal_date >= ?)
               ORDER BY signal_date DESC, score DESC
               LIMIT 100""",
            (start_date, start_date),
        ).fetchall()

    for row in rows:
        if event_types and row["event_type"] not in event_types:
            continue
        if assets and not _csv_has(row["assets_impacted"], assets):
            continue
        events.append({
            "date": row["last_seen_at"],
            "kind": "news",
            "title": row["title"],
            "label": f"{row['event_type']} · {row['article_count']}篇",
            "color": NEWS_COLORS.get(row["severity"], "#999999"),
            "severity": row["severity"],
            "hover": f"{row['primary_source'] or ''}<br>{row['title']}",
        })

    for row in sig_rows:
        if assets and not _csv_has(row["assets"], assets):
            continue
        events.append({
            "date": row["signal_date"],
            "kind": "signal",
            "title": row["signal_name"],
            "label": f"signal · {row['score']}/{row['max_score']}",
            "color": SIGNAL_COLORS.get(row["level"], "#9467bd"),
            "severity": 3 if row["level"] in ("red", "yellow") else 1,
            "hover": f"{row['signal_name']}<br>score {row['score']}/{row['max_score']}",
        })

    valid = []
    for event in events:
        dt = pd.to_datetime(event["date"], errors="coerce")
        if pd.isna(dt):
            continue
        event["date"] = dt.strftime("%Y-%m-%d")
        valid.append(event)

    valid.sort(key=lambda x: (x["severity"], x["date"]), reverse=True)
    return valid[:limit]


def add_event_markers(fig, events, marker_y=1.02):
    if not events:
        return fig

    for event in events:
        fig.add_vline(
            x=event["date"],
            line_width=1,
            line_dash="dot",
            line_color=event["color"],
            opacity=0.55,
        )

    for event in events:
        symbol = "◆" if event["kind"] == "signal" else "●"
        fig.add_annotation(
            x=event["date"],
            y=marker_y,
            xref="x",
            yref="paper",
            text=symbol,
            showarrow=False,
            font=dict(color=event["color"], size=13),
            hovertext=event["hover"],
        )
    return fig
