import pandas as pd
import json
import math
import hashlib
import logging

from db.schema import ensure_news_cluster_schema, get_db
from db.sqlite_compat import sqlite_date
from services.news_identity import article_hash, canonicalize_url, title_fingerprint

logger = logging.getLogger(__name__)
_NEWS_CLUSTER_SCHEMA_READY = False


def _ensure_news_cluster_ready():
    global _NEWS_CLUSTER_SCHEMA_READY
    if not _NEWS_CLUSTER_SCHEMA_READY:
        ensure_news_cluster_schema()
        _NEWS_CLUSTER_SCHEMA_READY = True


def _default_source_url(source, series_id):
    templates = {
        "fred": f"https://fred.stlouisfed.org/series/{series_id}",
        "yfinance": f"https://finance.yahoo.com/quote/{series_id}",
        "stooq": "https://stooq.com/",
        "alpha_vantage": "https://www.alphavantage.co/",
        "akshare": "https://akshare.akfamily.xyz/",
        "akshare_hk_index": "https://akshare.akfamily.xyz/data/index/index.html",
        "binance_spot": "https://api.binance.com/api/v3/klines",
        "crypto_liquidity": "https://defillama.com/stablecoins",
    }
    return templates.get(source, "")


def _valid_range(source, series_id):
    from config.series_definitions import AKSHARE_HK_INDEX_SERIES, AKSHARE_SERIES, FRED_SERIES

    if source == "fred":
        meta = FRED_SERIES.get(series_id, {})
    elif source == "akshare_hk_index":
        meta = AKSHARE_HK_INDEX_SERIES.get(series_id, {})
    else:
        meta = AKSHARE_SERIES.get(series_id, {})
    return meta.get("valid_range")


def _prepare_time_series_records(source, series_id, records):
    valid_range = _valid_range(source, series_id)
    prepared = []
    previous_date = None
    rejected = []

    for index, record in enumerate(records or []):
        raw_date = record.get("date")
        parsed_date = pd.to_datetime(raw_date, errors="coerce")
        value = record.get("value")
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = None

        if pd.isna(parsed_date):
            rejected.append((index, "invalid date"))
            continue
        if value is None or not math.isfinite(value):
            rejected.append((index, "invalid numeric value"))
            continue
        if valid_range and not (valid_range[0] <= value <= valid_range[1]):
            rejected.append((index, f"outside valid range {valid_range}"))
            continue

        date_text = parsed_date.strftime("%Y-%m-%d")
        quality_messages = []
        if previous_date is not None and parsed_date < previous_date:
            quality_messages.append("source records were not in chronological order")
        previous_date = parsed_date
        prepared.append({
            "date": date_text,
            "value": value,
            "release_at": record.get("release_at") or record.get("published_at"),
            "source_url": record.get("source_url") or _default_source_url(source, series_id),
            "vintage_at": record.get("vintage_at"),
            "revision_number": int(record.get("revision_number") or 0),
            "is_revised": int(bool(record.get("is_revised", False))),
            "quality_status": "warning" if quality_messages else "valid",
            "quality_message": "; ".join(quality_messages),
        })

    return prepared, rejected


def _record_data_quality_issue(
    conn, *, fingerprint, source, series_id, observed_date, issue_type, message, raw_value
):
    """Persist an issue and reopen it if the exact problem recurs.

    Resolved issues are retained as audit history.  Reopening the same
    fingerprint is important: a later full-source revalidation may clear an
    old parser problem, but a recurrence must become visible again.
    """
    conn.execute(
        """INSERT INTO data_quality_issues
           (fingerprint, source, series_id, observed_date, issue_type, message, raw_value)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(fingerprint) DO UPDATE SET
               observed_date = excluded.observed_date,
               issue_type = excluded.issue_type,
               message = excluded.message,
               raw_value = excluded.raw_value,
               resolved = 0,
               created_at = CURRENT_TIMESTAMP""",
        (fingerprint, source, series_id, observed_date, issue_type, message, raw_value),
    )


def upsert_time_series(source, series_id, records, *, reset_existing_quality_issues=False):
    prepared, rejected = _prepare_time_series_records(source, series_id, records)
    with get_db() as conn:
        resolved_existing = 0
        if reset_existing_quality_issues:
            # A non-incremental rebuild is an explicit revalidation of the
            # complete series.  Clear the old audit baseline first; any issue
            # still present in this response is immediately recreated below.
            cur = conn.execute(
                """UPDATE data_quality_issues
                   SET resolved = 1
                   WHERE source = ? AND series_id = ? AND resolved = 0""",
                (source, series_id),
            )
            resolved_existing = max(0, int(cur.rowcount or 0))
        for r in prepared:
            conn.execute(
                """INSERT OR REPLACE INTO time_series
                   (source, series_id, date, value, fetched_at, release_at, source_url,
                    vintage_at, revision_number, is_revised, quality_status, quality_message)
                   VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source, series_id, r["date"], r["value"], r["release_at"],
                    r["source_url"], r["vintage_at"], r["revision_number"],
                    r["is_revised"], r["quality_status"], r["quality_message"],
                ),
            )
        for index, message in rejected:
            record = (records or [])[index]
            observed_date = str(record.get("date") or "")
            raw_value = str(record.get("value") or "")
            fingerprint = hashlib.sha256(
                f"{source}|{series_id}|{observed_date}|{message}|{raw_value}".encode("utf-8")
            ).hexdigest()
            _record_data_quality_issue(
                conn,
                fingerprint=fingerprint,
                source=source,
                series_id=series_id,
                observed_date=observed_date,
                issue_type="rejected_record",
                message=message,
                raw_value=raw_value,
            )
        for record in prepared:
            if record["quality_status"] != "valid":
                fingerprint = hashlib.sha256(
                    f"{source}|{series_id}|{record['date']}|{record['quality_message']}".encode("utf-8")
                ).hexdigest()
                _record_data_quality_issue(
                    conn,
                    fingerprint=fingerprint,
                    source=source,
                    series_id=series_id,
                    observed_date=record["date"],
                    issue_type="warning",
                    message=record["quality_message"],
                    raw_value=str(record["value"]),
                )
    return {
        "accepted": len(prepared),
        "rejected": rejected,
        "resolved_existing": resolved_existing,
    }


def upsert_series_meta(source, series_id, meta):
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO series_meta
               (source, series_id, display_name, unit, frequency, category, yaxis_label)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                source, series_id,
                meta.get("display_name", ""),
                meta.get("unit", ""),
                meta.get("frequency", ""),
                meta.get("category", ""),
                meta.get("yaxis_label", ""),
            ),
        )


def query_series(source, series_id, start_date=None, end_date=None):
    start_date = sqlite_date(start_date)
    end_date = sqlite_date(end_date)
    # Older databases may contain legacy rows with human-readable error text
    # in the date column.  They must never participate in time-series queries.
    query = (
        "SELECT date, value FROM time_series "
        "WHERE source = ? AND series_id = ? AND date GLOB '????-??-??' "
        "AND value IS NOT NULL"
    )
    params = [source, series_id]
    if start_date:
        query += " AND date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND date <= ?"
        params.append(end_date)
    query += " ORDER BY date ASC"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return pd.DataFrame(rows, columns=["date", "value"])


def query_latest_values(source, category=None):
    with get_db() as conn:
        if category:
            rows = conn.execute(
                """SELECT t.series_id, t.date, t.value, m.display_name, m.category
                   FROM time_series t
                   JOIN series_meta m ON t.source = m.source AND t.series_id = m.series_id
                   WHERE t.source = ? AND m.category = ?
                   AND t.date GLOB '????-??-??'
                   AND t.value IS NOT NULL
                   AND t.date = (SELECT MAX(t2.date) FROM time_series t2
                                 WHERE t2.source = t.source AND t2.series_id = t.series_id
                                 AND t2.date GLOB '????-??-??'
                                 AND t2.value IS NOT NULL)""",
                (source, category),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT t.series_id, t.date, t.value, m.display_name
                   FROM time_series t
                   JOIN series_meta m ON t.source = m.source AND t.series_id = m.series_id
                   WHERE t.source = ?
                   AND t.date GLOB '????-??-??'
                   AND t.value IS NOT NULL
                   AND t.date = (SELECT MAX(t2.date) FROM time_series t2
                                 WHERE t2.source = t.source AND t2.series_id = t.series_id
                                 AND t2.date GLOB '????-??-??'
                                 AND t2.value IS NOT NULL)""",
                (source,),
            ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["series_id", "date", "value", "display_name"])
    return pd.DataFrame(rows, columns=rows[0].keys())


def query_source_health():
    """Return one health row per source from stored time series and fetch logs."""
    from config.data_sources import DATA_SOURCES
    from config.series_definitions import AKSHARE_SERIES

    known_sources = set(DATA_SOURCES)
    active_akshare_series = [
        series_id for series_id, meta in AKSHARE_SERIES.items()
        if meta.get("enabled", True)
    ]
    active_akshare_placeholders = ", ".join("?" for _ in active_akshare_series)
    quality_series_filter = ""
    quality_params = []
    if active_akshare_placeholders:
        # Audit rows for deliberately paused China indicators remain in SQLite,
        # but cannot make the active AKShare source look unhealthy forever.
        quality_series_filter = (
            "AND (qi.source != 'akshare' OR qi.series_id IN "
            f"({active_akshare_placeholders}))"
        )
        quality_params = active_akshare_series
    with get_db() as conn:
        rows = conn.execute(
            """SELECT
                   ts.source,
                   COUNT(DISTINCT CASE
                       WHEN ts.date GLOB '????-??-??' AND ts.value IS NOT NULL
                       THEN ts.series_id END) AS series_count,
                   (SELECT COUNT(*) FROM data_quality_issues qi
                    WHERE qi.source = ts.source AND qi.resolved = 0
                    """ + quality_series_filter + """
                   ) AS quality_issue_count,
                   MAX(CASE WHEN ts.date GLOB '????-??-??' THEN ts.date END) AS latest_data_date,
                   MAX(ts.fetched_at) AS latest_fetched_at,
                   fl.series_id AS last_series_id,
                   fl.status AS last_status,
                   fl.error_message AS last_error,
                   fl.created_at AS last_fetch_attempt
               FROM time_series ts
               LEFT JOIN fetch_log fl ON fl.id = (
                   SELECT fl2.id FROM fetch_log fl2
                   WHERE fl2.source = ts.source
                   ORDER BY fl2.created_at DESC
                   LIMIT 1
               )
               GROUP BY ts.source
               ORDER BY ts.source""",
            quality_params,
        ).fetchall()

        # A source with no successfully stored rows is otherwise invisible in
        # the health panel.  Include its latest failed/skipped fetch attempt so
        # unavailable inputs are not mistaken for healthy missing data.
        fetch_only = conn.execute(
            """SELECT fl.source, fl.series_id, fl.created_at, fl.status, fl.error_message
               FROM fetch_log fl
               WHERE fl.id = (
                   SELECT fl2.id FROM fetch_log fl2
                   WHERE fl2.source = fl.source
                   ORDER BY fl2.created_at DESC
                   LIMIT 1
               )
               AND NOT EXISTS (
                   SELECT 1 FROM time_series ts WHERE ts.source = fl.source
               )
               ORDER BY fl.source"""
        ).fetchall()
        fetch_only = [row for row in fetch_only if row["source"] in known_sources]

        news = conn.execute(
            """SELECT
                   COUNT(*) AS article_count,
                   MAX(fetched_at) AS latest_fetched_at,
                   MAX(published_at) AS latest_data_date
               FROM news_articles"""
        ).fetchone()

        ai = conn.execute(
            """SELECT
                   COUNT(*) AS analysis_count,
                   MAX(created_at) AS latest_fetched_at
               FROM ai_analyses"""
        ).fetchone()

    out = [dict(r) for r in rows]
    out.extend({
        "source": row["source"],
        "series_count": 0,
        "quality_issue_count": 0,
        "latest_data_date": None,
        "latest_fetched_at": row["created_at"],
        "last_series_id": row["series_id"],
        "last_status": row["status"],
        "last_error": row["error_message"] or "",
        "last_fetch_attempt": row["created_at"],
    } for row in fetch_only)
    if news and news["article_count"]:
        out.append({
            "source": "news",
            "series_count": news["article_count"],
            "quality_issue_count": 0,
            "latest_data_date": news["latest_data_date"],
            "latest_fetched_at": news["latest_fetched_at"],
            "last_status": "success",
            "last_error": "",
            "last_fetch_attempt": news["latest_fetched_at"],
        })
    if ai and ai["analysis_count"]:
        out.append({
            "source": "ai",
            "series_count": ai["analysis_count"],
            "quality_issue_count": 0,
            "latest_data_date": ai["latest_fetched_at"],
            "latest_fetched_at": ai["latest_fetched_at"],
            "last_status": "success",
            "last_error": "",
            "last_fetch_attempt": ai["latest_fetched_at"],
        })
    return pd.DataFrame(out)


def query_series_snapshot(source, series_id, lookback_points=5):
    """Latest value plus short-window change for one series."""
    actual_source = source
    actual_series_id = series_id
    if source == "market":
        from services.market_data import query_market_series

        df, meta = query_market_series(series_id)
        actual_source = meta.get("provider") or source
        actual_series_id = meta.get("series_id") or series_id
    else:
        df = query_series(source, series_id)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").dropna(subset=["value"])
    if df.empty:
        return None

    latest = df.iloc[-1]
    prev_1 = df.iloc[-2] if len(df) >= 2 else None
    prev_n = df.iloc[-lookback_points - 1] if len(df) > lookback_points else None

    def _pct(prev):
        if prev is None or prev["value"] in (None, 0):
            return None
        return (latest["value"] / prev["value"] - 1) * 100

    return {
        "source": actual_source,
        "series_id": actual_series_id,
        "date": latest["date"].strftime("%Y-%m-%d"),
        "value": float(latest["value"]),
        "change_1": None if prev_1 is None else float(latest["value"] - prev_1["value"]),
        "change_1_pct": _pct(prev_1),
        "change_n": None if prev_n is None else float(latest["value"] - prev_n["value"]),
        "change_n_pct": _pct(prev_n),
    }


def upsert_daily_report(report_date, session, title, summary="", context=None, raw_markdown=""):
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO daily_reports
               (report_date, session, title, summary, context_json, raw_markdown, created_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                report_date, session, title, summary,
                json.dumps(context or {}, ensure_ascii=False, default=str),
                raw_markdown,
            ),
        )


def query_daily_reports(limit=20):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT report_date, session, title, summary, raw_markdown, created_at
               FROM daily_reports
               ORDER BY report_date DESC, created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return rows


def upsert_dashboard_snapshot(snapshot_type, payload, as_of=None,
                              data_version="", status="success", error_message=""):
    """Persist a read-optimised dashboard snapshot."""
    import json
    from datetime import datetime

    as_of = as_of or datetime.utcnow().isoformat(timespec="seconds") + "Z"
    payload_json = json.dumps(payload or {}, ensure_ascii=False, default=str)
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO dashboard_snapshots
               (snapshot_type, as_of, data_version, status, payload_json, error_message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (snapshot_type, as_of, data_version, status, payload_json, str(error_message or "")[:2000]),
        )
        return cur.lastrowid


def query_latest_dashboard_snapshot(snapshot_type):
    """Return the newest successful dashboard snapshot, if available."""
    import json

    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM dashboard_snapshots
               WHERE snapshot_type = ? AND status = 'success'
               ORDER BY as_of DESC, id DESC LIMIT 1""",
            (snapshot_type,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["payload"] = json.loads(result.pop("payload_json") or "{}")
    except (TypeError, ValueError):
        result["payload"] = {}
    return result


# ====== Research hypotheses / viewpoints / watchlist ======

def add_research_hypothesis(title, thesis, assets="", indicators="", news_topics="",
                            falsification="", status="active", confidence=0.5):
    from services.research_linker import infer_research_links

    links = infer_research_links(title, thesis, assets, indicators, news_topics, falsification)
    assets = links["assets"]
    indicators = links["indicators"]
    news_topics = links["news_topics"]
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO research_hypotheses
               (title, thesis, assets, indicators, news_topics, falsification, status, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, thesis, assets, indicators, news_topics, falsification, status, confidence),
        )
        return cur.lastrowid


def update_research_hypothesis(hypothesis_id, title, thesis, assets="", indicators="",
                               news_topics="", falsification="", status="active",
                               confidence=0.5):
    from services.research_linker import infer_research_links

    links = infer_research_links(title, thesis, assets, indicators, news_topics, falsification)
    assets = links["assets"]
    indicators = links["indicators"]
    news_topics = links["news_topics"]
    with get_db() as conn:
        conn.execute(
            """UPDATE research_hypotheses
               SET title = ?, thesis = ?, assets = ?, indicators = ?, news_topics = ?,
                   falsification = ?, status = ?, confidence = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (
                title, thesis, assets, indicators, news_topics,
                falsification, status, confidence, hypothesis_id,
            ),
        )


def query_research_hypotheses(status=None, limit=50):
    query = """SELECT id, title, thesis, assets, indicators, news_topics, falsification,
                      status, confidence, created_at, updated_at
               FROM research_hypotheses"""
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with get_db() as conn:
        return conn.execute(query, params).fetchall()


def add_viewpoint_log(hypothesis_id, view_date, area, stance, rationale="",
                      evidence="", watch_next=""):
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO viewpoint_logs
               (hypothesis_id, view_date, area, stance, rationale, evidence, watch_next)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (hypothesis_id, view_date, area, stance, rationale, evidence, watch_next),
        )
        return cur.lastrowid


def query_viewpoint_logs(limit=50, area=None):
    query = """SELECT v.id, v.hypothesis_id, h.title AS hypothesis_title,
                      v.view_date, v.area, v.stance, v.rationale,
                      v.evidence, v.watch_next, v.created_at
               FROM viewpoint_logs v
               LEFT JOIN research_hypotheses h ON v.hypothesis_id = h.id"""
    params = []
    if area:
        query += " WHERE v.area = ?"
        params.append(area)
    query += " ORDER BY v.view_date DESC, v.created_at DESC LIMIT ?"
    params.append(limit)
    with get_db() as conn:
        return conn.execute(query, params).fetchall()


def add_watchlist_item(title, trigger="", why="", linked_assets="",
                       linked_indicators="", status="active"):
    from services.research_linker import infer_research_links

    links = infer_research_links(title, why, linked_assets, linked_indicators, extra_text=trigger)
    linked_assets = links["assets"]
    linked_indicators = links["indicators"]
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO watchlist_items
               (title, trigger, why, linked_assets, linked_indicators, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, trigger, why, linked_assets, linked_indicators, status),
        )
        return cur.lastrowid


def update_watchlist_status(item_id, status):
    with get_db() as conn:
        conn.execute(
            """UPDATE watchlist_items
               SET status = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (status, item_id),
        )


def query_watchlist_items(status=None, limit=50):
    query = """SELECT id, title, trigger, why, linked_assets, linked_indicators,
                      status, created_at, updated_at
               FROM watchlist_items"""
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with get_db() as conn:
        return conn.execute(query, params).fetchall()


def query_research_context(limit=8):
    return {
        "active_hypotheses": [dict(r) for r in query_research_hypotheses(status="active", limit=limit)],
        "recent_viewpoints": [dict(r) for r in query_viewpoint_logs(limit=limit)],
        "active_watchlist": [dict(r) for r in query_watchlist_items(status="active", limit=limit)],
    }


# ====== Composite signal snapshots / reviews ======

def upsert_composite_signal_snapshot(signal_date, signal):
    signal_date = sqlite_date(signal_date)
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO composite_signal_snapshots
               (signal_date, signal_name, category, direction, level, score, max_score,
                summary, evidence_json, assets, watch_next, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                signal_date,
                signal.get("name", ""),
                signal.get("category", ""),
                signal.get("direction", ""),
                signal.get("level", ""),
                signal.get("score", 0),
                signal.get("max_score", 0),
                signal.get("summary", ""),
                json.dumps(signal.get("evidence", []), ensure_ascii=False, default=str),
                ",".join(signal.get("assets", [])),
                ",".join(signal.get("watch_next", [])),
            ),
        )
        row = conn.execute(
            """SELECT id FROM composite_signal_snapshots
               WHERE signal_date = ? AND signal_name = ?""",
            (signal_date, signal.get("name", "")),
        ).fetchone()
        return row["id"] if row else None


def upsert_composite_signal_review(snapshot_id, asset, source, series_id,
                                   start_date=None, start_value=None,
                                   return_1d=None, return_3d=None, return_7d=None):
    start_date = sqlite_date(start_date)
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO composite_signal_reviews
               (snapshot_id, asset, source, series_id, start_date, start_value,
                return_1d, return_3d, return_7d, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                snapshot_id, asset, source, series_id, start_date, start_value,
                return_1d, return_3d, return_7d,
            ),
        )


def query_composite_signal_snapshots(limit=50):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, signal_date, signal_name, category, direction, level,
                      score, max_score, summary, assets, watch_next, created_at
               FROM composite_signal_snapshots
               ORDER BY signal_date DESC, score DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return rows


def query_composite_signal_reviews(limit=200):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT r.id, r.snapshot_id, s.signal_date, s.signal_name, s.level,
                      s.score, s.max_score, r.asset, r.source, r.series_id,
                      r.start_date, r.start_value, r.return_1d, r.return_3d,
                      r.return_7d, r.updated_at
               FROM composite_signal_reviews r
               JOIN composite_signal_snapshots s ON r.snapshot_id = s.id
               ORDER BY s.signal_date DESC, s.score DESC, r.asset
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return rows


def query_signal_review_summary():
    with get_db() as conn:
        return conn.execute(
            """SELECT s.signal_name,
                      COUNT(*) AS review_count,
                      AVG(r.return_1d) AS avg_1d,
                      AVG(r.return_3d) AS avg_3d,
                      AVG(r.return_7d) AS avg_7d
               FROM composite_signal_reviews r
               JOIN composite_signal_snapshots s ON r.snapshot_id = s.id
               GROUP BY s.signal_name
               ORDER BY review_count DESC, s.signal_name"""
        ).fetchall()


def upsert_tic_holdings(records):
    with get_db() as conn:
        for r in records:
            conn.execute(
                """INSERT OR REPLACE INTO tic_holdings
                   (date, country, holdings_billions, category)
                   VALUES (?, ?, ?, ?)""",
                (r["date"], r["country"], r["holdings_billions"], r.get("category", "total")),
            )


def query_tic_holdings(date=None):
    date = sqlite_date(date)
    with get_db() as conn:
        if date:
            rows = conn.execute(
                """SELECT date, country, holdings_billions, category
                   FROM tic_holdings WHERE date = ?
                   ORDER BY holdings_billions DESC""",
                (date,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT date, country, holdings_billions, category
                   FROM tic_holdings
                   WHERE date = (SELECT MAX(date) FROM tic_holdings)
                   ORDER BY holdings_billions DESC""",
            ).fetchall()
    return pd.DataFrame(rows)


def log_fetch(source, series_id, status, records_fetched=0, error_message=""):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO fetch_log (source, series_id, status, records_fetched, error_message)
               VALUES (?, ?, ?, ?, ?)""",
            (source, series_id, status, records_fetched, error_message),
        )


def get_last_fetch_date(source, series_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT MAX(date) FROM time_series WHERE source = ? AND series_id = ?",
            (source, series_id),
        ).fetchone()
    return row[0] if row and row[0] else None


def get_last_success_fetch_at(source, series_id):
    """Return the latest successful fetch timestamp for a source marker."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT MAX(created_at) FROM fetch_log
               WHERE source = ? AND series_id = ? AND status = 'success'""",
            (source, series_id),
        ).fetchone()
    return row[0] if row and row[0] else None


def get_all_fred_series_ids():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT series_id FROM time_series WHERE source = 'fred'"
        ).fetchall()
    return [r[0] for r in rows]


def query_events(limit=50):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT date, title, description, category, impact "
            "FROM events ORDER BY date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return rows


def add_event(date, title, description="", category="market", impact="medium"):
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events (date, title, description, category, impact) "
            "VALUES (?, ?, ?, ?, ?)",
            (date, title, description, category, impact),
        )


# ====== News tables ======

def insert_news_article(source, source_type, url, title, summary="", content="",
                         published_at="", topic="", feed_kind="general", raw_json=""):
    title = str(title or "").strip()
    if not title:
        return None
    canonical_url = canonicalize_url(url)
    fingerprint = title_fingerprint(title)
    h = article_hash(url, title)
    with get_db() as conn:
        try:
            # Existing databases may still contain URL hashes from the older
            # implementation.  Check the concrete URL as well as the new
            # canonical form before relying on the hash uniqueness rule.
            if canonical_url:
                existing = conn.execute(
                    """SELECT id FROM news_articles
                       WHERE canonical_url = ? OR url = ? LIMIT 1""",
                    (canonical_url, str(url or "")),
                ).fetchone()
                if existing:
                    return None
            cur = conn.execute(
                """INSERT OR IGNORE INTO news_articles
                   (source, source_type, url, title, summary, content, published_at, topic,
                    hash, canonical_url, title_fingerprint, feed_kind, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source, source_type, url, title, summary, content, published_at, topic,
                    h, canonical_url, fingerprint, feed_kind or "general", raw_json or "",
                ),
            )
            # INSERT OR IGNORE 时 last_insert_rowid() 可能返回上一条文章的 ID，
            # 会让调用方误以为重复文章是新文章。只有真正插入才返回 ID。
            return cur.lastrowid if cur.rowcount else None
        except Exception:
            logger.exception("Failed to insert news article from source=%s", source)
            return None


def backfill_news_article_identities(limit=10000):
    """Populate identity metadata for legacy rows without deleting raw news."""
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 10000
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, url, title FROM news_articles
               WHERE COALESCE(title_fingerprint, '') = ''
               ORDER BY id ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        if not rows:
            return 0
        conn.executemany(
            """UPDATE news_articles
               SET canonical_url = ?, title_fingerprint = ?
               WHERE id = ?""",
            [
                (canonicalize_url(row["url"]), title_fingerprint(row["title"]), row["id"])
                for row in rows
            ],
        )
        return len(rows)


def get_news_feed_state(source):
    """读取 RSS 的 ETag/Last-Modified 与最近错误状态。"""
    with get_db() as conn:
        return conn.execute(
            """SELECT source, url, etag, last_modified, last_success_at, last_error, updated_at
               FROM news_feed_state WHERE source = ?""",
            (source,),
        ).fetchone()


def update_news_feed_state(source, url, etag="", last_modified="", last_success_at=None, last_error=""):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO news_feed_state
               (source, url, etag, last_modified, last_success_at, last_error, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(source) DO UPDATE SET
                 url = excluded.url,
                 etag = excluded.etag,
                 last_modified = excluded.last_modified,
                 last_success_at = COALESCE(excluded.last_success_at, news_feed_state.last_success_at),
                 last_error = excluded.last_error,
                 updated_at = CURRENT_TIMESTAMP""",
            (source, url, etag or "", last_modified or "", last_success_at, last_error or ""),
        )


def query_news_feed_states():
    with get_db() as conn:
        return conn.execute(
            """SELECT source, url, last_success_at, last_error, updated_at
               FROM news_feed_state ORDER BY source"""
        ).fetchall()


def query_recent_newsflash(limit=20, minutes=180):
    """Recent raw fast-news items for the radar and rule-based early alerts."""
    with get_db() as conn:
        return conn.execute(
            """SELECT id, source, title, summary, url, published_at, topic,
                      feed_kind, triage_status, triage_score, flash_alerted
               FROM news_articles
               WHERE feed_kind = 'newsflash'
                 AND COALESCE(published_at, fetched_at) >= datetime('now', ?)
               ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?""",
            (f"-{max(1, int(minutes))} minutes", max(1, int(limit))),
        ).fetchall()


def claim_newsflash_alert(article_id):
    with get_db() as conn:
        cur = conn.execute(
            """UPDATE news_articles SET flash_alerted = 1,
                      processing_updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND COALESCE(flash_alerted, 0) = 0""",
            (article_id,),
        )
        return cur.rowcount == 1


def get_unanalyzed_articles(limit=20):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, title, summary, source, source_type, url, published_at,
                      title_fingerprint, feed_kind, triage_status, triage_score
               FROM news_articles """
            "WHERE is_analyzed = 0 AND processing_status = 'fetched' "
            "AND COALESCE(topic, '') <> 'other' "
            "ORDER BY published_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return rows


def get_recent_analyzed_title_fingerprints(days=3):
    """Return headline identities already analyzed in the given recent window."""
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 3
    with get_db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT n.title_fingerprint
               FROM ai_analyses a
               JOIN news_articles n ON n.id = a.article_id
               WHERE COALESCE(n.published_at, a.created_at) >= datetime('now', ?)
                 AND COALESCE(n.title_fingerprint, '') <> ''""",
            (f"-{days} days",),
        ).fetchall()
    return {str(row["title_fingerprint"]) for row in rows if row["title_fingerprint"]}


def mark_articles_deduplicated(article_ids, reason="与近期已分析新闻标题重复"):
    """Keep raw duplicates for audit, but remove them from the AI work queue."""
    ids = [int(article_id) for article_id in article_ids if article_id]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    with get_db() as conn:
        cur = conn.execute(
            f"""UPDATE news_articles
                SET processing_status = 'deduplicated', processing_error = ?,
                    processing_updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders}) AND is_analyzed = 0
                  AND processing_status = 'fetched'""",
            [str(reason or "重复新闻")[:500], *ids],
        )
        return cur.rowcount


def queue_articles_for_analysis(article_ids):
    ids = [int(article_id) for article_id in article_ids if article_id]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    with get_db() as conn:
        cur = conn.execute(
            f"""UPDATE news_articles
                SET processing_status = 'queued', processing_error = '',
                    processing_updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders}) AND is_analyzed = 0
                  AND processing_status = 'fetched'""",
            ids,
        )
        return cur.rowcount


def mark_article_analyzing(article_id):
    with get_db() as conn:
        conn.execute(
            """UPDATE news_articles
               SET processing_status = 'analyzing', processing_error = '',
                   processing_attempts = processing_attempts + 1,
                   processing_updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (article_id,),
        )


def mark_article_failed(article_id, error_message):
    with get_db() as conn:
        conn.execute(
            """UPDATE news_articles
               SET processing_status = 'failed', processing_error = ?,
                   processing_updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (str(error_message or "Unknown analysis failure")[:1000], article_id),
        )


def mark_article_analyzed(article_id):
    with get_db() as conn:
        conn.execute(
            """UPDATE news_articles
               SET is_analyzed = 1, processing_status = 'analyzed', processing_error = '',
                   processing_updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (article_id,),
        )


def mark_articles_clustered(article_ids):
    ids = [int(article_id) for article_id in article_ids if article_id]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    with get_db() as conn:
        cur = conn.execute(
            f"""UPDATE news_articles
                SET processing_status = 'clustered', processing_updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders}) AND processing_status = 'analyzed'""",
            ids,
        )
        return cur.rowcount


def retry_failed_articles(limit=100):
    with get_db() as conn:
        ids = conn.execute(
            """SELECT id FROM news_articles WHERE processing_status = 'failed'
               ORDER BY processing_updated_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        if not ids:
            return 0
        values = [row["id"] for row in ids]
        placeholders = ",".join("?" for _ in values)
        conn.execute(
            f"""UPDATE news_articles
                SET processing_status = 'fetched', processing_error = '',
                    processing_updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})""",
            values,
        )
        return len(values)


def query_news_processing_summary():
    with get_db() as conn:
        rows = conn.execute(
            """SELECT processing_status, COUNT(*) AS count
               FROM news_articles GROUP BY processing_status"""
        ).fetchall()
        failed = conn.execute(
            """SELECT id, title, source, processing_error, processing_attempts,
                      processing_updated_at
               FROM news_articles WHERE processing_status = 'failed'
               ORDER BY processing_updated_at DESC LIMIT 20"""
        ).fetchall()
    return {
        "counts": {row["processing_status"]: row["count"] for row in rows},
        "failed": failed,
    }


def insert_ai_analysis(article_id, model, summary_cn, event_type, macro_channels,
                        assets_impacted, direction, severity, confidence,
                        time_horizon, is_new, why, follow_up, raw_json, prompt_version=""):
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO ai_analyses
               (article_id, model, prompt_version, summary_cn, event_type, macro_channels,
                assets_impacted, direction, severity, confidence,
                time_horizon, is_new_information, why_it_matters, follow_up_data, raw_json)
               SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
               WHERE NOT EXISTS (
                   SELECT 1 FROM ai_analyses WHERE article_id = ?
               )""",
            (article_id, model, prompt_version, summary_cn, event_type, macro_channels,
             assets_impacted, direction, severity, confidence,
             time_horizon, is_new, why, follow_up, raw_json, article_id),
        )
        return cur.lastrowid if cur.rowcount else None


def query_analyzed_news(event_type=None, min_severity=1, assets=None, limit=30, days=None):
    with get_db() as conn:
        query = """SELECT a.id AS analysis_id, a.model, a.prompt_version,
                          a.summary_cn, a.event_type, a.assets_impacted, a.direction,
                          a.severity, a.confidence, a.why_it_matters, a.macro_channels,
                          a.follow_up_data, a.created_at,
                          a.is_new_information,
                          n.title, n.source, n.url, n.published_at
                   FROM ai_analyses a
                   JOIN (
                       SELECT article_id, MAX(id) AS latest_analysis_id
                       FROM ai_analyses GROUP BY article_id
                   ) latest ON latest.latest_analysis_id = a.id
                   JOIN news_articles n ON a.article_id = n.id
                   WHERE 1=1"""
        params = []
        if event_type:
            query += " AND a.event_type = ?"
            params.append(event_type)
        if min_severity > 1:
            query += " AND a.severity >= ?"
            params.append(min_severity)
        if assets:
            likes = " OR ".join(["a.assets_impacted LIKE ?" for _ in assets])
            query += f" AND ({likes})"
            params.extend([f"%{x}%" for x in assets])
        if days is not None:
            query += " AND COALESCE(n.published_at, a.created_at) >= datetime('now', ?)"
            params.append(f"-{int(days)} days")
        query += " ORDER BY a.severity DESC, a.created_at DESC LIMIT ?"
        params.append(limit)
        return conn.execute(query, params).fetchall()


def query_recent_analyzed_articles(days=3, limit=200):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT a.id AS analysis_id, a.summary_cn, a.event_type,
                      a.assets_impacted, a.direction, a.severity,
                      a.confidence, a.why_it_matters, a.macro_channels,
                      a.follow_up_data, a.created_at,
                      n.id AS article_id, n.title, n.source, n.url,
                      n.canonical_url, n.title_fingerprint, n.published_at, n.topic
               FROM ai_analyses a
               JOIN (
                   SELECT article_id, MAX(id) AS latest_analysis_id
                   FROM ai_analyses GROUP BY article_id
               ) latest ON latest.latest_analysis_id = a.id
               JOIN news_articles n ON a.article_id = n.id
               WHERE COALESCE(n.published_at, a.created_at) >= datetime('now', ?)
               ORDER BY COALESCE(n.published_at, a.created_at) DESC
               LIMIT ?""",
            (f"-{days} days", limit),
        ).fetchall()
    return rows


def count_recent_analyzed_articles(days=3):
    """Count unique analyzed articles in the rebuild window."""
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 3
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS count
               FROM (
                   SELECT a.article_id
                   FROM ai_analyses a
                   JOIN (
                       SELECT article_id, MAX(id) AS latest_analysis_id
                       FROM ai_analyses GROUP BY article_id
                   ) latest ON latest.latest_analysis_id = a.id
                   JOIN news_articles n ON n.id = a.article_id
                   WHERE COALESCE(n.published_at, a.created_at) >= datetime('now', ?)
               )""",
            (f"-{days} days",),
        ).fetchone()
    return int(row["count"] or 0) if row else 0


def upsert_news_cluster(cluster, rebuild_token=""):
    _ensure_news_cluster_ready()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO news_clusters
               (cluster_key, title, summary, event_type, assets_impacted, direction,
                severity, confidence, first_seen_at, last_seen_at, article_count,
                primary_source, status, merged_into, rebuild_token, evidence_fingerprint, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(cluster_key) DO UPDATE SET
                   title = excluded.title,
                   summary = excluded.summary,
                   event_type = excluded.event_type,
                   assets_impacted = excluded.assets_impacted,
                   direction = excluded.direction,
                   severity = excluded.severity,
                   confidence = excluded.confidence,
                   first_seen_at = excluded.first_seen_at,
                   last_seen_at = excluded.last_seen_at,
                   article_count = excluded.article_count,
                   primary_source = excluded.primary_source,
                   status = excluded.status,
                   merged_into = NULL,
                   rebuild_token = excluded.rebuild_token,
                   evidence_fingerprint = excluded.evidence_fingerprint,
                   ai_status = CASE
                       WHEN COALESCE(excluded.evidence_fingerprint, '')
                            != COALESCE(news_clusters.evidence_fingerprint, '')
                       THEN 'pending' ELSE COALESCE(news_clusters.ai_status, 'pending') END,
                   ai_title = CASE
                       WHEN COALESCE(excluded.evidence_fingerprint, '')
                            != COALESCE(news_clusters.evidence_fingerprint, '')
                       THEN '' ELSE news_clusters.ai_title END,
                   ai_summary = CASE
                       WHEN COALESCE(excluded.evidence_fingerprint, '')
                            != COALESCE(news_clusters.evidence_fingerprint, '')
                       THEN '' ELSE news_clusters.ai_summary END,
                   ai_implications = CASE
                       WHEN COALESCE(excluded.evidence_fingerprint, '')
                            != COALESCE(news_clusters.evidence_fingerprint, '')
                       THEN '' ELSE news_clusters.ai_implications END,
                   ai_watch_next = CASE
                       WHEN COALESCE(excluded.evidence_fingerprint, '')
                            != COALESCE(news_clusters.evidence_fingerprint, '')
                       THEN '' ELSE news_clusters.ai_watch_next END,
                   ai_updated_at = CASE
                       WHEN COALESCE(excluded.evidence_fingerprint, '')
                            != COALESCE(news_clusters.evidence_fingerprint, '')
                       THEN NULL ELSE news_clusters.ai_updated_at END,
                   updated_at = CURRENT_TIMESTAMP""",
            (
                cluster["cluster_key"],
                cluster.get("title", ""),
                cluster.get("summary", ""),
                cluster.get("event_type", "other"),
                cluster.get("assets_impacted", ""),
                cluster.get("direction", ""),
                cluster.get("severity", 1),
                cluster.get("confidence", 0.5),
                cluster.get("first_seen_at", ""),
                cluster.get("last_seen_at", ""),
                cluster.get("article_count", 0),
                cluster.get("primary_source", ""),
                cluster.get("status", "active"),
                cluster.get("merged_into"),
                rebuild_token or cluster.get("rebuild_token", ""),
                cluster.get("evidence_fingerprint", ""),
            ),
        )
        row = conn.execute(
            "SELECT id FROM news_clusters WHERE cluster_key = ?",
            (cluster["cluster_key"],),
        ).fetchone()
        return row["id"] if row else None


def add_article_to_cluster(article_id, cluster_id, similarity_score=0):
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO news_article_clusters
               (article_id, cluster_id, similarity_score, created_at)
               VALUES (?, ?, ?, datetime('now'))""",
            (article_id, cluster_id, similarity_score),
        )


def clear_article_cluster_links(article_ids):
    ids = [int(article_id) for article_id in article_ids if article_id]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    with get_db() as conn:
        cur = conn.execute(
            f"DELETE FROM news_article_clusters WHERE article_id IN ({placeholders})",
            ids,
        )
        return cur.rowcount


def deactivate_stale_news_clusters(days=3):
    """Move events outside the current rebuild window out of the active feed."""
    with get_db() as conn:
        cur = conn.execute(
            """UPDATE news_clusters
               SET status = 'inactive', updated_at = CURRENT_TIMESTAMP
               WHERE status = 'active'
                 AND COALESCE(last_seen_at, updated_at) < datetime('now', ?)""",
            (f"-{int(days)} days",),
        )
        return cur.rowcount


def deactivate_unseen_news_clusters(rebuild_token, days=3):
    """Retire current-window cluster variants absent from a successful rebuild.

    This changes only their visibility status; raw articles, old links and alert
    lineage remain intact for later audit.
    """
    token = str(rebuild_token or "").strip()
    if not token:
        return 0
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 3
    with get_db() as conn:
        cur = conn.execute(
            """UPDATE news_clusters
               SET status = 'inactive', updated_at = CURRENT_TIMESTAMP
               WHERE status = 'active' AND merged_into IS NULL
                 AND COALESCE(last_seen_at, updated_at) >= datetime('now', ?)
                 AND COALESCE(rebuild_token, '') <> ?""",
            (f"-{days} days", token),
        )
        return cur.rowcount


def query_news_clusters(limit=50, min_severity=1, days=3, include_inactive=False):
    _ensure_news_cluster_ready()
    with get_db() as conn:
        conditions = ["severity >= ?"]
        params = [min_severity]
        if not include_inactive:
            conditions.append("status = 'active'")
            conditions.append("merged_into IS NULL")
        if days is not None:
            conditions.append("COALESCE(last_seen_at, updated_at) >= datetime('now', ?)")
            params.append(f"-{int(days)} days")
        params.append(limit)
        rows = conn.execute(
            f"""SELECT id, cluster_key, title, summary, event_type, assets_impacted,
                      direction, severity, confidence, first_seen_at, last_seen_at,
                      article_count, primary_source, status, merged_into,
                      ai_status, ai_title, ai_summary, ai_implications, ai_watch_next,
                      ai_updated_at, rebuild_token, evidence_fingerprint, created_at, updated_at,
                      (SELECT COUNT(DISTINCT n.source)
                         FROM news_article_clusters ac
                         JOIN news_articles n ON n.id = ac.article_id
                        WHERE ac.cluster_id = news_clusters.id) AS source_count
               FROM news_clusters
               WHERE {' AND '.join(conditions)}
               ORDER BY severity DESC, last_seen_at DESC
               LIMIT ?""",
            params,
        ).fetchall()
    return rows


def update_news_cluster_ai(cluster_id, status="complete", title="", summary="",
                           implications="", watch_next=""):
    _ensure_news_cluster_ready()
    with get_db() as conn:
        conn.execute(
            """UPDATE news_clusters
               SET ai_status = ?, ai_title = ?, ai_summary = ?,
                   ai_implications = ?, ai_watch_next = ?,
                   ai_updated_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND status = 'active'""",
            (status, title, summary, implications, watch_next, cluster_id),
        )


def merge_news_clusters(survivor_id, duplicate_ids):
    """Merge duplicate event rows while preserving article and research lineage."""
    _ensure_news_cluster_ready()
    duplicate_ids = sorted({int(item) for item in duplicate_ids if item and int(item) != int(survivor_id)})
    if not duplicate_ids:
        return 0
    placeholders = ",".join("?" for _ in duplicate_ids)
    with get_db() as conn:
        survivor = conn.execute(
            "SELECT * FROM news_clusters WHERE id = ? AND status = 'active'",
            (survivor_id,),
        ).fetchone()
        duplicates = conn.execute(
            f"SELECT * FROM news_clusters WHERE id IN ({placeholders}) AND status = 'active'",
            duplicate_ids,
        ).fetchall()
        if not survivor or not duplicates:
            return 0

        rows = [survivor, *duplicates]

        def split_csv(value):
            return {item.strip() for item in str(value or '').split(',') if item.strip()}

        assets = set().union(*(split_csv(row['assets_impacted']) for row in rows))
        directions = set().union(*(split_csv(row['direction']) for row in rows))
        first_values = [str(row['first_seen_at']) for row in rows if row['first_seen_at']]
        last_values = [str(row['last_seen_at']) for row in rows if row['last_seen_at']]
        severity = max(int(row['severity'] or 1) for row in rows)
        confidence = max(float(row['confidence'] or 0.5) for row in rows)
        article_count = conn.execute(
            f"""SELECT COUNT(DISTINCT article_id) FROM news_article_clusters
                WHERE cluster_id = ? OR cluster_id IN ({placeholders})""",
            [survivor_id, *duplicate_ids],
        ).fetchone()[0]

        for duplicate_id in duplicate_ids:
            conn.execute(
                """INSERT OR IGNORE INTO news_article_clusters
                   (article_id, cluster_id, similarity_score, created_at)
                   SELECT article_id, ?, similarity_score, created_at
                   FROM news_article_clusters WHERE cluster_id = ?""",
                (survivor_id, duplicate_id),
            )
            conn.execute(
                """INSERT OR IGNORE INTO news_cluster_indicator_links
                   (cluster_id, source, series_id, label, link_reason, created_at)
                   SELECT ?, source, series_id, label, link_reason, created_at
                   FROM news_cluster_indicator_links WHERE cluster_id = ?""",
                (survivor_id, duplicate_id),
            )
            conn.execute(
                """INSERT OR IGNORE INTO news_cluster_hypothesis_links
                   (cluster_id, hypothesis_id, match_score, match_reason, created_at)
                   SELECT ?, hypothesis_id, match_score, match_reason, created_at
                   FROM news_cluster_hypothesis_links WHERE cluster_id = ?""",
                (survivor_id, duplicate_id),
            )

        # A previously sent duplicate must not become a new alert after merging.
        sent_duplicate = conn.execute(
            f"SELECT 1 FROM news_alerts WHERE cluster_id IN ({placeholders}) AND status = 'sent' LIMIT 1",
            duplicate_ids,
        ).fetchone()
        survivor_alert = conn.execute(
            "SELECT 1 FROM news_alerts WHERE cluster_id = ?", (survivor_id,)
        ).fetchone()
        if sent_duplicate and survivor_alert:
            conn.execute(
                "UPDATE news_alerts SET status = 'sent', alerted_at = COALESCE(alerted_at, CURRENT_TIMESTAMP) WHERE cluster_id = ?",
                (survivor_id,),
            )
        elif sent_duplicate and not survivor_alert:
            conn.execute(
                """INSERT INTO news_alerts
                   (cluster_id, severity, status, alerted_at, attempt_count, updated_at)
                   VALUES (?, ?, 'sent', CURRENT_TIMESTAMP, 1, CURRENT_TIMESTAMP)""",
                (survivor_id, severity),
            )

        conn.execute(
            """UPDATE news_clusters
               SET assets_impacted = ?, direction = ?, severity = ?, confidence = ?,
                   first_seen_at = ?, last_seen_at = ?, article_count = ?,
                   status = 'active', merged_into = NULL, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (",".join(sorted(assets)), ",".join(sorted(directions)), severity,
             confidence, min(first_values) if first_values else survivor['first_seen_at'],
             max(last_values) if last_values else survivor['last_seen_at'],
             article_count, survivor_id),
        )
        conn.execute(
            f"""UPDATE news_clusters
                SET status = 'merged', merged_into = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})""",
            [survivor_id, *duplicate_ids],
        )
        conn.execute(
            f"DELETE FROM news_article_clusters WHERE cluster_id IN ({placeholders})",
            duplicate_ids,
        )
        conn.execute(
            f"DELETE FROM news_cluster_indicator_links WHERE cluster_id IN ({placeholders})",
            duplicate_ids,
        )
        conn.execute(
            f"DELETE FROM news_cluster_hypothesis_links WHERE cluster_id IN ({placeholders})",
            duplicate_ids,
        )
        conn.execute(
            f"DELETE FROM news_alerts WHERE cluster_id IN ({placeholders})",
            duplicate_ids,
        )
    return len(duplicates)


def query_cluster_articles(cluster_id):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT n.id AS article_id, n.title, n.source, n.url, n.published_at,
                      a.summary_cn, a.event_type, a.assets_impacted, a.direction,
                        a.severity, a.confidence, a.is_new_information, a.why_it_matters, a.macro_channels,
                      a.follow_up_data,
                      c.similarity_score
               FROM news_article_clusters c
               JOIN news_articles n ON c.article_id = n.id
               LEFT JOIN ai_analyses a ON a.id = (
                   SELECT MAX(a2.id) FROM ai_analyses a2
                   WHERE a2.article_id = n.id
               )
               WHERE c.cluster_id = ?
               ORDER BY COALESCE(n.published_at, a.created_at) DESC""",
            (cluster_id,),
        ).fetchall()
    return rows


def claim_news_cluster_alert(cluster_id, severity):
    """Reserve an event cluster for one alert delivery attempt.

    A sent cluster is never sent again. Failed attempts can be retried by the next
    news cycle, while the primary key prevents simultaneous duplicate delivery.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT status FROM news_alerts WHERE cluster_id = ?", (cluster_id,)
        ).fetchone()
        if row and row["status"] in ("sent", "sending"):
            return False
        if row:
            conn.execute(
                """UPDATE news_alerts
                   SET status = 'sending', severity = ?, attempt_count = attempt_count + 1,
                       last_error = '', updated_at = CURRENT_TIMESTAMP
                   WHERE cluster_id = ?""",
                (severity, cluster_id),
            )
        else:
            conn.execute(
                """INSERT INTO news_alerts
                   (cluster_id, severity, status, attempt_count, updated_at)
                   VALUES (?, ?, 'sending', 1, CURRENT_TIMESTAMP)""",
                (cluster_id, severity),
            )
        return True


def finish_news_cluster_alert(cluster_id, sent, error_message=""):
    with get_db() as conn:
        conn.execute(
            """UPDATE news_alerts
               SET status = ?, alerted_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE alerted_at END,
                   last_error = ?, updated_at = CURRENT_TIMESTAMP
               WHERE cluster_id = ?""",
            ("sent" if sent else "failed", 1 if sent else 0, str(error_message)[:1000], cluster_id),
        )


def get_runtime_setting(setting_key, default=None):
    with get_db() as conn:
        row = conn.execute(
            "SELECT value_json FROM runtime_settings WHERE setting_key = ?", (setting_key,)
        ).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value_json"])
    except (TypeError, json.JSONDecodeError):
        return default


def set_runtime_setting(setting_key, value):
    payload = json.dumps(value, ensure_ascii=False)
    with get_db() as conn:
        conn.execute(
            """INSERT INTO runtime_settings(setting_key, value_json, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(setting_key) DO UPDATE SET
                   value_json = excluded.value_json,
                   updated_at = CURRENT_TIMESTAMP""",
            (setting_key, payload),
        )


def replace_cluster_indicator_links(cluster_id, links):
    with get_db() as conn:
        conn.execute("DELETE FROM news_cluster_indicator_links WHERE cluster_id = ?", (cluster_id,))
        for link in links:
            conn.execute(
                """INSERT OR REPLACE INTO news_cluster_indicator_links
                   (cluster_id, source, series_id, label, link_reason)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    cluster_id, link["source"], link["series_id"],
                    link.get("label", ""), link.get("reason", ""),
                ),
            )


def replace_cluster_hypothesis_links(cluster_id, links):
    with get_db() as conn:
        conn.execute("DELETE FROM news_cluster_hypothesis_links WHERE cluster_id = ?", (cluster_id,))
        for link in links:
            conn.execute(
                """INSERT OR REPLACE INTO news_cluster_hypothesis_links
                   (cluster_id, hypothesis_id, match_score, match_reason)
                   VALUES (?, ?, ?, ?)""",
                (
                    cluster_id, link["hypothesis_id"], link.get("match_score", 0),
                    link.get("match_reason", ""),
                ),
            )


def query_cluster_research_links(cluster_id):
    with get_db() as conn:
        indicators = conn.execute(
            """SELECT source, series_id, label, link_reason
               FROM news_cluster_indicator_links WHERE cluster_id = ?
               ORDER BY label, series_id""",
            (cluster_id,),
        ).fetchall()
        hypotheses = conn.execute(
            """SELECT h.id, h.title, h.confidence, l.match_score, l.match_reason
               FROM news_cluster_hypothesis_links l
               JOIN research_hypotheses h ON h.id = l.hypothesis_id
               WHERE l.cluster_id = ?
               ORDER BY l.match_score DESC, h.updated_at DESC""",
            (cluster_id,),
        ).fetchall()
    return {"indicators": indicators, "hypotheses": hypotheses}


def query_ai_analyses_for_review(limit=500):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT a.id AS analysis_id, a.model, a.prompt_version, a.event_type,
                      a.assets_impacted, a.direction, a.created_at,
                      n.published_at, n.title
               FROM ai_analyses a JOIN news_articles n ON n.id = a.article_id
               ORDER BY a.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return rows


def upsert_ai_analysis_review(analysis_id, asset, source, series_id, predicted_direction,
                              start_date, start_value, return_1d, return_3d,
                              return_7d, return_30d):
    start_date = sqlite_date(start_date)
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO ai_analysis_reviews
               (analysis_id, asset, source, series_id, predicted_direction,
                start_date, start_value, return_1d, return_3d, return_7d, return_30d,
                updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                analysis_id, asset, source, series_id, predicted_direction,
                start_date, start_value, return_1d, return_3d, return_7d, return_30d,
            ),
        )


def query_ai_analysis_reviews(limit=2000):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT r.analysis_id, r.asset, r.source, r.series_id,
                      r.predicted_direction, r.start_date, r.start_value,
                      r.return_1d, r.return_3d, r.return_7d, r.return_30d, r.updated_at,
                      a.model, a.prompt_version, a.event_type, a.confidence,
                      n.title, n.source, n.published_at
               FROM ai_analysis_reviews r
               JOIN ai_analyses a ON a.id = r.analysis_id
               JOIN news_articles n ON n.id = a.article_id
               ORDER BY r.updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return rows


def get_recent_fingerprints(days=7):
    """获取最近N天的AI分析指纹，用于去重新信息"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT event_type, assets_impacted, direction FROM ai_analyses
               WHERE created_at > datetime('now', ?)
               ORDER BY created_at DESC LIMIT 100""",
            (f"-{days} days",),
        ).fetchall()
    return set(f"{r['event_type']}|{r['assets_impacted']}|{r['direction']}" for r in rows)


# ====== Read-only crypto trade journal ======

def _json_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def upsert_trade_order(order):
    """保存交易所同步到的订单；仅用于只读同步，不触发任何下单动作。"""
    values = (
        order.get("venue", ""), order.get("account_label", ""), str(order.get("order_id", "")),
        order.get("client_order_id", ""), order.get("symbol", ""), order.get("instrument_type", "perpetual"),
        order.get("side", ""), order.get("position_side", ""), order.get("order_type", ""),
        order.get("status", ""), order.get("price"), order.get("avg_price"), order.get("quantity"),
        order.get("filled_quantity", 0), order.get("fee", 0), order.get("fee_asset", ""),
        order.get("realized_pnl"), order.get("leverage"), int(bool(order.get("reduce_only", False))),
        order.get("placed_at", ""), order.get("updated_at", ""), _json_text(order.get("raw_json", {})),
    )
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO trade_orders
               (venue, account_label, order_id, client_order_id, symbol, instrument_type,
                side, position_side, order_type, status, price, avg_price, quantity,
                filled_quantity, fee, fee_asset, realized_pnl, leverage, reduce_only,
                placed_at, updated_at, raw_json, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(venue, account_label, order_id) DO UPDATE SET
                 client_order_id=excluded.client_order_id,
                 symbol=excluded.symbol, instrument_type=excluded.instrument_type,
                 side=excluded.side, position_side=excluded.position_side,
                 order_type=excluded.order_type, status=excluded.status,
                 price=excluded.price, avg_price=excluded.avg_price, quantity=excluded.quantity,
                 filled_quantity=excluded.filled_quantity, fee=excluded.fee,
                 fee_asset=excluded.fee_asset, realized_pnl=excluded.realized_pnl,
                 leverage=excluded.leverage, reduce_only=excluded.reduce_only,
                 placed_at=excluded.placed_at, updated_at=excluded.updated_at,
                 raw_json=excluded.raw_json, synced_at=CURRENT_TIMESTAMP""",
            values,
        )
        return cur.lastrowid


def upsert_trade_fill(fill):
    values = (
        fill.get("venue", ""), fill.get("account_label", ""), str(fill.get("fill_id", "")),
        str(fill.get("order_id", "")), fill.get("symbol", ""), fill.get("side", ""),
        fill.get("price"), fill.get("quantity"), fill.get("fee", 0), fill.get("fee_asset", ""),
        fill.get("realized_pnl"), fill.get("executed_at", ""), _json_text(fill.get("raw_json", {})),
    )
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO trade_fills
               (venue, account_label, fill_id, order_id, symbol, side, price, quantity,
                fee, fee_asset, realized_pnl, executed_at, raw_json, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(venue, account_label, fill_id) DO UPDATE SET
                 order_id=excluded.order_id, symbol=excluded.symbol, side=excluded.side,
                 price=excluded.price, quantity=excluded.quantity, fee=excluded.fee,
                 fee_asset=excluded.fee_asset, realized_pnl=excluded.realized_pnl,
                 executed_at=excluded.executed_at, raw_json=excluded.raw_json,
                 synced_at=CURRENT_TIMESTAMP""",
            values,
        )
        return cur.lastrowid


def insert_trade_note(venue, symbol, order_id="", side="", thesis="", setup="",
                      entry_order_type="manual", entry_price=None, trigger_price=None,
                      planned_quantity=None, stop_price=None, target_price=None,
                      expected_horizon="", risk_note="",
                      market_snapshot=None, trade_type="swing", macro_horizon="",
                      analysis_timeframe="", entry_trigger="", time_stop="",
                      plan_status="planned", plan_intent_status="active",
                      plan_expires_at="", context_captured_at=""):
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO trade_notes
                (venue, symbol, order_id, side, thesis, setup, entry_order_type, entry_price,
                trigger_price, planned_quantity, stop_price, target_price, expected_horizon,
                trade_type, macro_horizon, analysis_timeframe, entry_trigger, time_stop,
                plan_status, plan_intent_status, plan_expires_at, context_captured_at, risk_note,
                market_snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (venue, symbol, order_id, side, thesis, setup, entry_order_type or "manual", entry_price,
             trigger_price, planned_quantity, stop_price, target_price, expected_horizon,
             trade_type or "swing", macro_horizon or "", analysis_timeframe or "",
             entry_trigger or "", time_stop or "", plan_status or "planned",
             plan_intent_status or "active", plan_expires_at or "", context_captured_at or "",
             risk_note, _json_text(market_snapshot or {})),
        )
        return cur.lastrowid


def get_trade_note(note_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM trade_notes WHERE id = ?", (note_id,)
        ).fetchone()


def query_trade_notes(limit=100, symbol=None):
    with get_db() as conn:
        if symbol:
            return conn.execute(
                """SELECT * FROM trade_notes WHERE symbol = ?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (symbol, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM trade_notes ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()


def update_trade_note_context(note_id, market_snapshot, context_captured_at=""):
    """Persist the deterministic environment captured when a plan is created.

    The plan's original snapshot is intentionally kept separate from later AI
    feedback contexts so a post-trade review can avoid using future data.
    """
    with get_db() as conn:
        conn.execute(
            """UPDATE trade_notes
               SET market_snapshot_json = ?, context_captured_at = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (_json_text(market_snapshot or {}), context_captured_at or "", note_id),
        )


def update_trade_note_order_plan(note_id, *, order_id="", entry_order_type="manual",
                                 entry_price=None, trigger_price=None, planned_quantity=None,
                                 plan_status="planned", plan_expires_at=""):
    """Update a user's planned entry and optional linked read-only order.

    This only edits the local journal.  It never creates, amends, cancels, or
    otherwise changes an exchange order.
    """
    with get_db() as conn:
        cur = conn.execute(
            """UPDATE trade_notes
               SET order_id = ?, entry_order_type = ?, entry_price = ?, trigger_price = ?,
                   planned_quantity = ?, plan_status = ?, plan_expires_at = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (
                order_id or "", entry_order_type or "manual", entry_price, trigger_price,
                planned_quantity, plan_status or "planned", plan_expires_at or "", note_id,
            ),
        )
        return bool(cur.rowcount)


PLAN_ORDER_ROLES = {"entry", "take_profit", "stop_loss", "manual_exit", "other_exit"}
PLAN_INTENT_STATUSES = {"active", "paused", "abandoned", "archived"}


def _float_or_zero(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _same_number(left, right):
    return math.isclose(_float_or_zero(left), _float_or_zero(right), rel_tol=1e-12, abs_tol=1e-12)


def _insert_trade_plan_order_event(conn, *, note_id, link_id, venue, account_label,
                                    order_id, role, event_type, from_status="", to_status="",
                                    previous_filled_quantity=None, filled_quantity=None,
                                    avg_price=None, exchange_updated_at=""):
    conn.execute(
        """INSERT INTO trade_plan_order_events
           (note_id, plan_order_link_id, venue, account_label, order_id, role, event_type,
            from_status, to_status, previous_filled_quantity, filled_quantity, avg_price,
            exchange_updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            note_id, link_id, venue or "", account_label or "", str(order_id or ""), role or "",
            event_type or "", from_status or "", to_status or "", previous_filled_quantity,
            filled_quantity, avg_price, exchange_updated_at or "",
        ),
    )


def update_trade_note_intent_status(note_id, intent_status):
    """Update only the user's research intent, never the exchange execution state."""
    status = str(intent_status or "active").strip().lower()
    if status not in PLAN_INTENT_STATUSES:
        raise ValueError(f"unsupported plan intent status: {intent_status}")
    with get_db() as conn:
        cur = conn.execute(
            """UPDATE trade_notes
               SET plan_intent_status = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (status, note_id),
        )
        return bool(cur.rowcount)


def link_trade_plan_order(note_id, *, venue, account_label="", order_id, role="entry", link_note=""):
    """Link one already-synchronised exchange order to a local research plan.

    The relationship is local metadata only.  It is deliberately not an order-routing
    operation and therefore cannot amend, cancel, or create anything at an exchange.
    """
    role = str(role or "entry").strip().lower()
    if role not in PLAN_ORDER_ROLES:
        raise ValueError(f"unsupported plan-order role: {role}")
    venue = str(venue or "").strip()
    account_label = str(account_label or "").strip()
    order_id = str(order_id or "").strip()
    if not venue or not order_id:
        raise ValueError("venue and order_id are required")

    with get_db() as conn:
        note = conn.execute("SELECT id FROM trade_notes WHERE id = ?", (note_id,)).fetchone()
        if not note:
            raise ValueError(f"trade plan not found: {note_id}")
        order = conn.execute(
            """SELECT * FROM trade_orders
               WHERE venue = ? AND account_label = ? AND order_id = ?""",
            (venue, account_label, order_id),
        ).fetchone()
        if not order:
            raise ValueError("该订单尚未同步到本地；请先同步 OKX 后再关联。")
        other_plan = conn.execute(
            """SELECT note_id FROM trade_plan_order_links
               WHERE venue = ? AND account_label = ? AND order_id = ? AND note_id <> ?
               LIMIT 1""",
            (venue, account_label, order_id, note_id),
        ).fetchone()
        if other_plan:
            raise ValueError(f"该订单已关联到计划 #{other_plan['note_id']}；请先解除原关联。")

        existing = conn.execute(
            """SELECT * FROM trade_plan_order_links
               WHERE note_id = ? AND venue = ? AND account_label = ? AND order_id = ?""",
            (note_id, venue, account_label, order_id),
        ).fetchone()
        data = dict(order)
        conn.execute(
            """INSERT INTO trade_plan_order_links
               (note_id, venue, account_label, order_id, role, link_note,
                last_exchange_status, last_filled_quantity, last_avg_price,
                last_exchange_updated_at, linked_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(note_id, venue, account_label, order_id) DO UPDATE SET
                 role=excluded.role, link_note=excluded.link_note,
                 last_exchange_status=excluded.last_exchange_status,
                 last_filled_quantity=excluded.last_filled_quantity,
                 last_avg_price=excluded.last_avg_price,
                 last_exchange_updated_at=excluded.last_exchange_updated_at,
                 updated_at=CURRENT_TIMESTAMP""",
            (
                note_id, venue, account_label, order_id, role, link_note or "",
                data.get("status") or "", _float_or_zero(data.get("filled_quantity")),
                data.get("avg_price"), data.get("updated_at") or "",
            ),
        )
        link = conn.execute(
            """SELECT * FROM trade_plan_order_links
               WHERE note_id = ? AND venue = ? AND account_label = ? AND order_id = ?""",
            (note_id, venue, account_label, order_id),
        ).fetchone()
        if role == "entry":
            # Keep the legacy field populated for existing reports, while the link
            # table becomes the source of truth for the complete execution chain.
            conn.execute(
                """UPDATE trade_notes
                   SET order_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (order_id, note_id),
            )
        elif existing and str(existing["role"] or "") == "entry":
            replacement = conn.execute(
                """SELECT order_id FROM trade_plan_order_links
                   WHERE note_id = ? AND role = 'entry'
                   ORDER BY linked_at DESC, id DESC LIMIT 1""",
                (note_id,),
            ).fetchone()
            conn.execute(
                """UPDATE trade_notes
                   SET order_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                ((replacement["order_id"] if replacement else ""), note_id),
            )
        _insert_trade_plan_order_event(
            conn,
            note_id=note_id,
            link_id=link["id"],
            venue=venue,
            account_label=account_label,
            order_id=order_id,
            role=role,
            event_type="linked" if not existing else "relinked",
            from_status=(existing["last_exchange_status"] if existing else ""),
            to_status=data.get("status") or "",
            previous_filled_quantity=(existing["last_filled_quantity"] if existing else None),
            filled_quantity=_float_or_zero(data.get("filled_quantity")),
            avg_price=data.get("avg_price"),
            exchange_updated_at=data.get("updated_at") or "",
        )
        return dict(link)


def unlink_trade_plan_order(note_id, link_id):
    """Remove a mistaken local plan-order link without touching the exchange order."""
    with get_db() as conn:
        link = conn.execute(
            "SELECT * FROM trade_plan_order_links WHERE id = ? AND note_id = ?",
            (link_id, note_id),
        ).fetchone()
        if not link:
            return False
        conn.execute("DELETE FROM trade_plan_order_events WHERE plan_order_link_id = ?", (link_id,))
        conn.execute("DELETE FROM trade_plan_order_links WHERE id = ?", (link_id,))
        if str(link["role"] or "") == "entry":
            replacement = conn.execute(
                """SELECT order_id FROM trade_plan_order_links
                   WHERE note_id = ? AND role = 'entry'
                   ORDER BY linked_at DESC, id DESC LIMIT 1""",
                (note_id,),
            ).fetchone()
            conn.execute(
                """UPDATE trade_notes SET order_id = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                ((replacement["order_id"] if replacement else ""), note_id),
            )
        return True


def query_trade_plan_order_links(note_id=None, venue=None, account_label=None, limit=200):
    with get_db() as conn:
        clauses = []
        values = []
        for column, value in (("l.note_id", note_id), ("l.venue", venue), ("l.account_label", account_label)):
            if value not in (None, ""):
                clauses.append(f"{column} = ?")
                values.append(value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        values.append(limit)
        return conn.execute(
            f"""SELECT l.*,
                       o.symbol AS order_symbol, o.instrument_type AS order_instrument_type,
                       o.side AS order_side, o.position_side AS order_position_side,
                       o.order_type AS order_type, o.status AS order_status,
                       o.price AS order_price, o.avg_price AS order_avg_price,
                       o.quantity AS order_quantity, o.filled_quantity AS order_filled_quantity,
                       o.reduce_only AS order_reduce_only, o.placed_at AS order_placed_at,
                       o.updated_at AS order_updated_at, o.synced_at AS order_synced_at
                FROM trade_plan_order_links l
                LEFT JOIN trade_orders o
                  ON o.venue = l.venue AND o.account_label = l.account_label
                 AND o.order_id = l.order_id
                {where}
                ORDER BY CASE l.role WHEN 'entry' THEN 0 ELSE 1 END,
                         l.linked_at ASC, l.id ASC
                LIMIT ?""",
            values,
        ).fetchall()


def query_trade_plan_order_events(note_id, limit=200):
    with get_db() as conn:
        return conn.execute(
            """SELECT * FROM trade_plan_order_events WHERE note_id = ?
               ORDER BY created_at DESC, id DESC LIMIT ?""",
            (note_id, limit),
        ).fetchall()


def refresh_trade_plan_order_links(venue=None, account_label=None):
    """Capture synced order state/fill changes for linked plans.

    This function reads only the local trade-order cache after the read-only OKX
    synchroniser has updated it.  It never calls an exchange API itself.
    """
    with get_db() as conn:
        clauses = []
        values = []
        for column, value in (("l.venue", venue), ("l.account_label", account_label)):
            if value not in (None, ""):
                clauses.append(f"{column} = ?")
                values.append(value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"""SELECT l.*, o.status AS order_status, o.filled_quantity AS order_filled_quantity,
                       o.avg_price AS order_avg_price, o.updated_at AS order_updated_at
                FROM trade_plan_order_links l
                JOIN trade_orders o
                  ON o.venue = l.venue AND o.account_label = l.account_label
                 AND o.order_id = l.order_id
                {where}""",
            values,
        ).fetchall()
        changed = 0
        for row in rows:
            data = dict(row)
            previous_status = str(data.get("last_exchange_status") or "")
            next_status = str(data.get("order_status") or "")
            previous_filled = _float_or_zero(data.get("last_filled_quantity"))
            next_filled = _float_or_zero(data.get("order_filled_quantity"))
            status_changed = previous_status != next_status
            fill_changed = not _same_number(previous_filled, next_filled)
            if status_changed or fill_changed:
                event_type = "status_and_fill" if status_changed and fill_changed else (
                    "status_changed" if status_changed else "fill_progress"
                )
                _insert_trade_plan_order_event(
                    conn,
                    note_id=data["note_id"],
                    link_id=data["id"],
                    venue=data["venue"],
                    account_label=data["account_label"],
                    order_id=data["order_id"],
                    role=data["role"],
                    event_type=event_type,
                    from_status=previous_status,
                    to_status=next_status,
                    previous_filled_quantity=previous_filled,
                    filled_quantity=next_filled,
                    avg_price=data.get("order_avg_price"),
                    exchange_updated_at=data.get("order_updated_at") or "",
                )
                changed += 1
            conn.execute(
                """UPDATE trade_plan_order_links
                   SET last_exchange_status = ?, last_filled_quantity = ?, last_avg_price = ?,
                       last_exchange_updated_at = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    next_status, next_filled, data.get("order_avg_price"),
                    data.get("order_updated_at") or "", data["id"],
                ),
            )
        return {"checked": len(rows), "changed": changed}


def _delete_trade_plan_research_records(conn, note_ids):
    """Delete only locally authored plan/AI research records, never exchange caches."""
    note_ids = [int(value) for value in note_ids if value is not None]
    if not note_ids:
        return {
            "plans": 0, "links": 0, "execution_events": 0, "feedback": 0,
            "reviews": 0, "shadow_plans": 0, "paper_orders": 0, "paper_events": 0,
        }
    placeholders = ",".join("?" for _ in note_ids)

    def count(table, where, params):
        return conn.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE {where}", params).fetchone()["count"]

    shadow_ids = [
        row["id"] for row in conn.execute(
            f"SELECT id FROM ai_shadow_plans WHERE note_id IN ({placeholders})", note_ids
        ).fetchall()
    ]
    paper_ids = []
    if shadow_ids:
        shadow_placeholders = ",".join("?" for _ in shadow_ids)
        paper_ids = [
            row["id"] for row in conn.execute(
                f"SELECT id FROM paper_orders WHERE shadow_plan_id IN ({shadow_placeholders})", shadow_ids
            ).fetchall()
        ]

    counts = {
        "plans": count("trade_notes", f"id IN ({placeholders})", note_ids),
        "links": count("trade_plan_order_links", f"note_id IN ({placeholders})", note_ids),
        "execution_events": count("trade_plan_order_events", f"note_id IN ({placeholders})", note_ids),
        "feedback": count("trade_plan_feedback", f"note_id IN ({placeholders})", note_ids),
        "reviews": count("trade_ai_reviews", f"note_id IN ({placeholders})", note_ids),
        "shadow_plans": len(shadow_ids),
        "paper_orders": len(paper_ids),
        "paper_events": 0,
    }
    if paper_ids:
        paper_placeholders = ",".join("?" for _ in paper_ids)
        counts["paper_events"] = count(
            "paper_order_events", f"paper_order_id IN ({paper_placeholders})", paper_ids
        )
        conn.execute(f"DELETE FROM paper_order_events WHERE paper_order_id IN ({paper_placeholders})", paper_ids)
        conn.execute(f"DELETE FROM paper_orders WHERE id IN ({paper_placeholders})", paper_ids)
    if shadow_ids:
        shadow_placeholders = ",".join("?" for _ in shadow_ids)
        conn.execute(f"DELETE FROM ai_shadow_plans WHERE id IN ({shadow_placeholders})", shadow_ids)
    conn.execute(f"DELETE FROM trade_plan_order_events WHERE note_id IN ({placeholders})", note_ids)
    conn.execute(f"DELETE FROM trade_plan_order_links WHERE note_id IN ({placeholders})", note_ids)
    conn.execute(f"DELETE FROM trade_plan_feedback WHERE note_id IN ({placeholders})", note_ids)
    conn.execute(f"DELETE FROM trade_ai_reviews WHERE note_id IN ({placeholders})", note_ids)
    conn.execute(f"DELETE FROM trade_notes WHERE id IN ({placeholders})", note_ids)
    return counts


def delete_trade_plan_research_data(note_id):
    """Hard-delete one local plan and its dependent local AI/paper records only."""
    with get_db() as conn:
        return _delete_trade_plan_research_records(conn, [note_id])


def clear_all_trade_plan_research_data():
    """Hard-delete all local plan/AI research records; exchange caches are retained."""
    with get_db() as conn:
        note_ids = [row["id"] for row in conn.execute("SELECT id FROM trade_notes").fetchall()]
        return _delete_trade_plan_research_records(conn, note_ids)


def insert_trade_plan_feedback(note_id, model, prompt_version, status, context, feedback,
                               summary_cn="", plan_classification="", macro_alignment="",
                               realtime_alignment="", technical_alignment="", risk_flags=None,
                               data_gaps=None):
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO trade_plan_feedback
               (note_id, model, prompt_version, status, context_json, feedback_json, summary_cn,
                plan_classification, macro_alignment, realtime_alignment, technical_alignment,
                risk_flags, data_gaps)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                note_id, model or "", prompt_version or "", status or "completed",
                _json_text(context or {}), _json_text(feedback or {}), summary_cn or "",
                plan_classification or "", macro_alignment or "", realtime_alignment or "",
                technical_alignment or "", _json_text(risk_flags or []), _json_text(data_gaps or []),
            ),
        )
        return cur.lastrowid


def query_trade_plan_feedback(note_id=None, limit=20):
    with get_db() as conn:
        if note_id:
            return conn.execute(
                """SELECT * FROM trade_plan_feedback WHERE note_id = ?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (note_id, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM trade_plan_feedback ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()


def upsert_trade_account_snapshot(venue, account_label="", observed_at="", equity=None,
                                  available_balance=None, unrealized_pnl=None,
                                  margin_ratio=None, account_mode="", margin_mode="cross",
                                  raw_json=None):
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO trade_account_snapshots
               (venue, account_label, observed_at, equity, available_balance,
                unrealized_pnl, margin_ratio, account_mode, margin_mode, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (venue, account_label, observed_at, equity, available_balance, unrealized_pnl,
             margin_ratio, account_mode or "", margin_mode or "cross", _json_text(raw_json or {})),
        )
        return cur.lastrowid


def query_latest_trade_account_snapshot(venue=None, account_label=""):
    with get_db() as conn:
        if venue:
            return conn.execute(
                """SELECT * FROM trade_account_snapshots
                   WHERE venue = ? AND account_label = ?
                   ORDER BY observed_at DESC, id DESC LIMIT 1""",
                (venue, account_label),
            ).fetchone()
        return conn.execute(
            """SELECT * FROM trade_account_snapshots
               ORDER BY observed_at DESC, id DESC LIMIT 1"""
        ).fetchone()


def upsert_okx_account_bill(bill):
    """Persist one OKX bill idempotently for local long-term accounting."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO okx_account_bills
               (venue, account_label, bill_id, bill_type, bill_subtype,
                inst_type, inst_id, currency, amount, pnl, interest, fee,
                bill_ts, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(venue, account_label, bill_id) DO UPDATE SET
                 bill_type=excluded.bill_type,
                 bill_subtype=excluded.bill_subtype,
                 amount=excluded.amount, pnl=excluded.pnl,
                 interest=excluded.interest, fee=excluded.fee,
                 raw_json=excluded.raw_json""",
            (
                bill.get("venue", "OKX"), bill.get("account_label", ""),
                str(bill.get("bill_id", "")), str(bill.get("bill_type", "")),
                str(bill.get("bill_subtype", "")), str(bill.get("inst_type", "")),
                str(bill.get("inst_id", "")), str(bill.get("currency", "")),
                bill.get("amount"), bill.get("pnl"), bill.get("interest"),
                bill.get("fee"), bill.get("bill_ts", ""),
                _json_text(bill.get("raw_json", {})),
            ),
        )


def query_okx_account_bills(account_label=None, begin=None, end=None, limit=10000):
    with get_db() as conn:
        clauses, values = [], []
        if account_label:
            clauses.append("account_label = ?")
            values.append(account_label)
        if begin:
            clauses.append("bill_ts >= ?")
            values.append(begin)
        if end:
            clauses.append("bill_ts < ?")
            values.append(end)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        values.append(limit)
        return conn.execute(
            f"SELECT * FROM okx_account_bills{where} ORDER BY bill_ts ASC, id ASC LIMIT ?",
            values,
        ).fetchall()


def upsert_trade_position(position):
    """保存 OKX/其他交易所只读同步到的当前持仓。"""
    values = (
        position.get("venue", ""), position.get("account_label", ""), position.get("symbol", ""),
        position.get("instrument_type", "perpetual"), position.get("margin_mode", "cross"),
        position.get("position_side", ""), position.get("quantity", 0), position.get("entry_price"),
        position.get("mark_price"), position.get("liquidation_price"), position.get("leverage"),
        position.get("unrealized_pnl"), position.get("unrealized_pnl_ratio"), position.get("margin"),
        position.get("notional"), position.get("updated_at", ""), _json_text(position.get("raw_json", {})),
    )
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO trade_positions
               (venue, account_label, symbol, instrument_type, margin_mode, position_side,
                quantity, entry_price, mark_price, liquidation_price, leverage,
                unrealized_pnl, unrealized_pnl_ratio, margin, notional, updated_at, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(venue, account_label, symbol, position_side) DO UPDATE SET
                 instrument_type=excluded.instrument_type, margin_mode=excluded.margin_mode,
                 quantity=excluded.quantity, entry_price=excluded.entry_price,
                 mark_price=excluded.mark_price, liquidation_price=excluded.liquidation_price,
                 leverage=excluded.leverage, unrealized_pnl=excluded.unrealized_pnl,
                 unrealized_pnl_ratio=excluded.unrealized_pnl_ratio, margin=excluded.margin,
                 notional=excluded.notional, updated_at=excluded.updated_at,
                 raw_json=excluded.raw_json, synced_at=CURRENT_TIMESTAMP""",
            values,
        )
        return cur.lastrowid


def clear_trade_positions(venue, account_label=""):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM trade_positions WHERE venue = ? AND account_label = ?",
            (venue, account_label),
        )


def query_trade_positions(venue=None, account_label="", symbol=None, limit=100):
    with get_db() as conn:
        clauses = []
        values = []
        if venue:
            clauses.append("venue = ?")
            values.append(venue)
        if account_label:
            clauses.append("account_label = ?")
            values.append(account_label)
        if symbol:
            clauses.append("symbol = ?")
            values.append(symbol)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        values.append(limit)
        return conn.execute(
            f"SELECT * FROM trade_positions{where} ORDER BY updated_at DESC, id DESC LIMIT ?",
            values,
        ).fetchall()


def query_trade_orders(venue=None, account_label="", symbol=None, order_id=None, limit=100,
                       statuses=None, reduce_only=None):
    with get_db() as conn:
        clauses = []
        values = []
        for column, value in (("venue", venue), ("account_label", account_label),
                              ("symbol", symbol), ("order_id", order_id)):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        if statuses:
            normalized = [str(status).strip().lower() for status in statuses if str(status).strip()]
            if normalized:
                placeholders = ",".join("?" for _ in normalized)
                clauses.append(f"LOWER(COALESCE(status, '')) IN ({placeholders})")
                values.extend(normalized)
        if reduce_only is not None:
            clauses.append("reduce_only = ?")
            values.append(int(bool(reduce_only)))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        values.append(limit)
        return conn.execute(
            f"SELECT * FROM trade_orders{where} ORDER BY COALESCE(updated_at, placed_at) DESC, id DESC LIMIT ?",
            values,
        ).fetchall()


def query_trade_fills(venue=None, account_label="", symbol=None, order_id=None, limit=200):
    with get_db() as conn:
        clauses = []
        values = []
        for column, value in (("venue", venue), ("account_label", account_label),
                              ("symbol", symbol), ("order_id", order_id)):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        values.append(limit)
        return conn.execute(
            f"SELECT * FROM trade_fills{where} ORDER BY executed_at DESC, id DESC LIMIT ?",
            values,
        ).fetchall()


def insert_trade_ai_review(note_id, order_id, model, prompt_version, status, review,
                           summary_cn="", strengths=None, weaknesses=None, risk_flags=None,
                           execution_review="", review_mode="holding_check",
                           review_cutoff_at="", evidence=None):
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO trade_ai_reviews
               (note_id, order_id, model, prompt_version, status, review_json, summary_cn,
                strengths, weaknesses, risk_flags, execution_review, review_mode,
                review_cutoff_at, evidence_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (note_id, order_id or "", model or "", prompt_version or "", status or "completed",
             _json_text(review or {}), summary_cn or "", _json_text(strengths or []),
             _json_text(weaknesses or []), _json_text(risk_flags or []), execution_review or "",
             review_mode or "holding_check", review_cutoff_at or "", _json_text(evidence or {})),
        )
        return cur.lastrowid


def query_trade_ai_reviews(note_id=None, limit=100):
    with get_db() as conn:
        if note_id:
            return conn.execute(
                """SELECT * FROM trade_ai_reviews WHERE note_id = ?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (note_id, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM trade_ai_reviews ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()


# ====== AI shadow plans and local paper orders ======

def insert_ai_shadow_plan(plan):
    """Persist an independently generated AI shadow plan.

    ``plan`` is a local research record.  It never contains exchange credentials
    and it is deliberately separate from ``trade_orders`` / ``trade_positions``.
    """
    values = (
        plan.get("note_id"), plan.get("model", ""), plan.get("prompt_version", ""),
        plan.get("decision", "no_trade"), plan.get("status", "no_trade"),
        plan.get("symbol", ""), plan.get("side", "flat"),
        plan.get("analysis_timeframe", ""), plan.get("expected_horizon", ""),
        plan.get("entry_price"), plan.get("trigger_price"), plan.get("trigger_direction", ""),
        plan.get("planned_quantity"), plan.get("planned_notional_usd"),
        plan.get("risk_budget_pct"), plan.get("initial_risk_usd"),
        plan.get("stop_price"), plan.get("target_price"), plan.get("risk_reward"),
        plan.get("expires_at", ""), plan.get("time_stop_at", ""), plan.get("confidence"),
        plan.get("rationale", ""), _json_text(plan.get("decision_json", {})),
        _json_text(plan.get("snapshot_json", {})), _json_text(plan.get("comparison_json", {})),
        plan.get("validation_error", ""),
    )
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO ai_shadow_plans
               (note_id, model, prompt_version, decision, status, symbol, side,
                analysis_timeframe, expected_horizon, entry_price, trigger_price,
                trigger_direction, planned_quantity, planned_notional_usd, risk_budget_pct,
                initial_risk_usd, stop_price, target_price, risk_reward, expires_at,
                time_stop_at, confidence, rationale, decision_json, snapshot_json,
                comparison_json, validation_error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
        return cur.lastrowid


def get_ai_shadow_plan(shadow_plan_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM ai_shadow_plans WHERE id = ?", (shadow_plan_id,)
        ).fetchone()


def query_ai_shadow_plans(note_id=None, statuses=None, symbol=None, limit=100):
    with get_db() as conn:
        clauses = []
        values = []
        for column, value in (("note_id", note_id), ("symbol", symbol)):
            if value not in (None, ""):
                clauses.append(f"{column} = ?")
                values.append(value)
        if statuses:
            if isinstance(statuses, str):
                statuses = [statuses]
            statuses = [str(item) for item in statuses if str(item)]
            if statuses:
                clauses.append("status IN (" + ", ".join("?" for _ in statuses) + ")")
                values.extend(statuses)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        values.append(limit)
        return conn.execute(
            f"SELECT * FROM ai_shadow_plans{where} ORDER BY created_at DESC, id DESC LIMIT ?",
            values,
        ).fetchall()


def update_ai_shadow_plan_status(shadow_plan_id, status, validation_error=None):
    """Update local shadow-plan lifecycle state; never calls an exchange."""
    with get_db() as conn:
        if validation_error is None:
            cur = conn.execute(
                """UPDATE ai_shadow_plans
                   SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (status, shadow_plan_id),
            )
        else:
            cur = conn.execute(
                """UPDATE ai_shadow_plans
                   SET status = ?, validation_error = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (status, validation_error or "", shadow_plan_id),
            )
        return bool(cur.rowcount)


def update_ai_shadow_plan_comparison(shadow_plan_id, comparison):
    with get_db() as conn:
        cur = conn.execute(
            """UPDATE ai_shadow_plans
               SET comparison_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (_json_text(comparison or {}), shadow_plan_id),
        )
        return bool(cur.rowcount)


def insert_paper_order(order):
    """Create one AI-only virtual order.  No exchange request is made here."""
    values = (
        order.get("shadow_plan_id"), order.get("symbol", ""), order.get("side", ""),
        order.get("order_type", ""), order.get("status", "pending"), order.get("entry_price"),
        order.get("trigger_price"), order.get("trigger_direction", ""), order.get("quantity"),
        order.get("notional_usd"), order.get("stop_price"), order.get("target_price"),
        order.get("expires_at", ""), order.get("time_stop_at", ""), order.get("submitted_at", ""),
        order.get("triggered_at", ""), order.get("filled_price"), order.get("filled_at", ""),
        order.get("close_price"), order.get("closed_at", ""), order.get("close_reason", ""),
        order.get("fee_bps", 5), order.get("slippage_bps", 2), order.get("entry_fee_usd", 0),
        order.get("exit_fee_usd", 0), order.get("gross_pnl_usd"), order.get("net_pnl_usd"),
        order.get("r_multiple"), order.get("last_market_at", ""), order.get("last_checked_at", ""),
        order.get("status_reason", ""),
    )
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO paper_orders
               (shadow_plan_id, symbol, side, order_type, status, entry_price, trigger_price,
                trigger_direction, quantity, notional_usd, stop_price, target_price, expires_at,
                time_stop_at, submitted_at, triggered_at, filled_price, filled_at, close_price,
                closed_at, close_reason, fee_bps, slippage_bps, entry_fee_usd, exit_fee_usd,
                gross_pnl_usd, net_pnl_usd, r_multiple, last_market_at, last_checked_at,
                status_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
        return cur.lastrowid


def get_paper_order(paper_order_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM paper_orders WHERE id = ?", (paper_order_id,)
        ).fetchone()


def get_paper_order_by_shadow_plan(shadow_plan_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM paper_orders WHERE shadow_plan_id = ?", (shadow_plan_id,)
        ).fetchone()


def query_paper_orders(shadow_plan_id=None, statuses=None, symbol=None, limit=200):
    with get_db() as conn:
        clauses = []
        values = []
        for column, value in (("shadow_plan_id", shadow_plan_id), ("symbol", symbol)):
            if value not in (None, ""):
                clauses.append(f"{column} = ?")
                values.append(value)
        if statuses:
            if isinstance(statuses, str):
                statuses = [statuses]
            statuses = [str(item) for item in statuses if str(item)]
            if statuses:
                clauses.append("status IN (" + ", ".join("?" for _ in statuses) + ")")
                values.extend(statuses)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        values.append(limit)
        return conn.execute(
            f"SELECT * FROM paper_orders{where} ORDER BY submitted_at DESC, id DESC LIMIT ?",
            values,
        ).fetchall()


_PAPER_ORDER_UPDATE_FIELDS = {
    "status", "entry_price", "trigger_price", "trigger_direction", "quantity", "notional_usd",
    "stop_price", "target_price", "expires_at", "time_stop_at", "triggered_at", "filled_price",
    "filled_at", "close_price", "closed_at", "close_reason", "fee_bps", "slippage_bps",
    "entry_fee_usd", "exit_fee_usd", "gross_pnl_usd", "net_pnl_usd", "r_multiple",
    "last_market_at", "last_checked_at", "status_reason",
}


def update_paper_order(paper_order_id, **changes):
    """Apply a controlled local lifecycle update to a virtual order."""
    allowed = {key: value for key, value in changes.items() if key in _PAPER_ORDER_UPDATE_FIELDS}
    unknown = set(changes) - set(allowed)
    if unknown:
        raise ValueError(f"Unsupported paper order fields: {sorted(unknown)}")
    if not allowed:
        return False
    assignments = [f"{field} = ?" for field in allowed]
    values = list(allowed.values())
    assignments.append("updated_at = CURRENT_TIMESTAMP")
    values.append(paper_order_id)
    with get_db() as conn:
        cur = conn.execute(
            f"UPDATE paper_orders SET {', '.join(assignments)} WHERE id = ?", values
        )
        return bool(cur.rowcount)


def insert_paper_order_event(paper_order_id, event_type, event_at, from_status="", to_status="",
                             price=None, reason="", market_snapshot=None):
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO paper_order_events
               (paper_order_id, event_type, from_status, to_status, price, event_at, reason,
                market_snapshot_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                paper_order_id, event_type or "", from_status or "", to_status or "", price,
                event_at or "", reason or "", _json_text(market_snapshot or {}),
            ),
        )
        return cur.lastrowid


def query_paper_order_events(paper_order_id=None, limit=200):
    with get_db() as conn:
        if paper_order_id:
            return conn.execute(
                """SELECT * FROM paper_order_events WHERE paper_order_id = ?
                   ORDER BY event_at DESC, id DESC LIMIT ?""",
                (paper_order_id, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM paper_order_events ORDER BY event_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
