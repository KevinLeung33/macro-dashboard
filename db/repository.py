import pandas as pd
import json
import math
import hashlib
import logging

from db.schema import get_db
from db.sqlite_compat import sqlite_date

logger = logging.getLogger(__name__)


def _default_source_url(source, series_id):
    templates = {
        "fred": f"https://fred.stlouisfed.org/series/{series_id}",
        "yfinance": f"https://finance.yahoo.com/quote/{series_id}",
        "stooq": "https://stooq.com/",
        "alpha_vantage": "https://www.alphavantage.co/",
        "akshare": "https://akshare.akfamily.xyz/",
        "crypto_liquidity": "https://defillama.com/stablecoins",
    }
    return templates.get(source, "")


def _valid_range(source, series_id):
    from config.series_definitions import AKSHARE_SERIES, FRED_SERIES

    meta = FRED_SERIES.get(series_id, {}) if source == "fred" else AKSHARE_SERIES.get(series_id, {})
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


def upsert_time_series(source, series_id, records):
    prepared, rejected = _prepare_time_series_records(source, series_id, records)
    with get_db() as conn:
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
            conn.execute(
                """INSERT OR IGNORE INTO data_quality_issues
                   (fingerprint, source, series_id, observed_date, issue_type, message, raw_value)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (fingerprint, source, series_id, observed_date, "rejected_record", message, raw_value),
            )
        for record in prepared:
            if record["quality_status"] != "valid":
                fingerprint = hashlib.sha256(
                    f"{source}|{series_id}|{record['date']}|{record['quality_message']}".encode("utf-8")
                ).hexdigest()
                conn.execute(
                    """INSERT OR IGNORE INTO data_quality_issues
                       (fingerprint, source, series_id, observed_date, issue_type, message, raw_value)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        fingerprint, source, series_id, record["date"], "warning",
                        record["quality_message"], str(record["value"]),
                    ),
                )
    return {"accepted": len(prepared), "rejected": rejected}


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
    query = "SELECT date, value FROM time_series WHERE source = ? AND series_id = ?"
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
                   AND t.date = (SELECT MAX(t2.date) FROM time_series t2
                                 WHERE t2.source = t.source AND t2.series_id = t.series_id)""",
                (source, category),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT t.series_id, t.date, t.value, m.display_name
                   FROM time_series t
                   JOIN series_meta m ON t.source = m.source AND t.series_id = m.series_id
                   WHERE t.source = ?
                   AND t.date = (SELECT MAX(t2.date) FROM time_series t2
                                 WHERE t2.source = t.source AND t2.series_id = t.series_id)""",
                (source,),
            ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["series_id", "date", "value", "display_name"])
    return pd.DataFrame(rows, columns=rows[0].keys())


def query_source_health():
    """Return one health row per source from stored time series and fetch logs."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT
                   ts.source,
                   COUNT(DISTINCT ts.series_id) AS series_count,
                   (SELECT COUNT(*) FROM data_quality_issues qi
                    WHERE qi.source = ts.source AND qi.resolved = 0) AS quality_issue_count,
                   MAX(ts.date) AS latest_data_date,
                   MAX(ts.fetched_at) AS latest_fetched_at,
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
               ORDER BY ts.source"""
        ).fetchall()

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
        "source": source,
        "series_id": series_id,
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
                         published_at="", topic=""):
    import hashlib
    h = hashlib.md5((url or title).encode()).hexdigest()[:16]
    with get_db() as conn:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO news_articles
                   (source, source_type, url, title, summary, content, published_at, topic, hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source, source_type, url, title, summary, content, published_at, topic, h),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        except Exception:
            logger.exception("Failed to insert news article from source=%s", source)
            return None


def get_unanalyzed_articles(limit=20):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, title, summary, source FROM news_articles "
            "WHERE is_analyzed = 0 AND processing_status = 'fetched' "
            "ORDER BY published_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return rows


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
        conn.execute(
            """INSERT INTO ai_analyses
               (article_id, model, prompt_version, summary_cn, event_type, macro_channels,
                assets_impacted, direction, severity, confidence,
                time_horizon, is_new_information, why_it_matters, follow_up_data, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (article_id, model, prompt_version, summary_cn, event_type, macro_channels,
             assets_impacted, direction, severity, confidence,
             time_horizon, is_new, why, follow_up, raw_json),
        )


def query_analyzed_news(event_type=None, min_severity=1, assets=None, limit=30):
    with get_db() as conn:
        query = """SELECT a.id AS analysis_id, a.model, a.prompt_version,
                          a.summary_cn, a.event_type, a.assets_impacted, a.direction,
                          a.severity, a.confidence, a.why_it_matters, a.macro_channels,
                          a.follow_up_data, a.created_at,
                          a.is_new_information,
                          n.title, n.source, n.url, n.published_at
                   FROM ai_analyses a JOIN news_articles n ON a.article_id = n.id
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
                      n.published_at, n.topic
               FROM ai_analyses a
               JOIN news_articles n ON a.article_id = n.id
               WHERE COALESCE(n.published_at, a.created_at) >= datetime('now', ?)
               ORDER BY COALESCE(n.published_at, a.created_at) DESC
               LIMIT ?""",
            (f"-{days} days", limit),
        ).fetchall()
    return rows


def upsert_news_cluster(cluster):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO news_clusters
               (cluster_key, title, summary, event_type, assets_impacted, direction,
                severity, confidence, first_seen_at, last_seen_at, article_count,
                primary_source, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
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


def query_news_clusters(limit=50, min_severity=1):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, cluster_key, title, summary, event_type, assets_impacted,
                      direction, severity, confidence, first_seen_at, last_seen_at,
                      article_count, primary_source, status, created_at, updated_at
               FROM news_clusters
               WHERE severity >= ?
               ORDER BY severity DESC, last_seen_at DESC
               LIMIT ?""",
            (min_severity, limit),
        ).fetchall()
    return rows


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
               LEFT JOIN ai_analyses a ON a.article_id = n.id
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
