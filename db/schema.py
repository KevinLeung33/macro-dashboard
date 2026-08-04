import sqlite3
import os
import threading
import time
from contextlib import contextmanager

from config.settings import DB_PATH


SQLITE_TIMEOUT_SECONDS = float(os.getenv("SQLITE_TIMEOUT_SECONDS", "60"))
SQLITE_BUSY_TIMEOUT_MS = int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "60000"))
SQLITE_RETRY_ATTEMPTS = int(os.getenv("SQLITE_RETRY_ATTEMPTS", "5"))
SQLITE_RETRY_BASE_SLEEP = float(os.getenv("SQLITE_RETRY_BASE_SLEEP", "0.2"))
SQLITE_JOURNAL_MODE = os.getenv("SQLITE_JOURNAL_MODE", "WAL")
SQLITE_SYNCHRONOUS = os.getenv("SQLITE_SYNCHRONOUS", "NORMAL")

_DB_LOCK = threading.RLock()

_COMPATIBLE_COLUMNS = {
    "time_series": {
        "release_at": "TIMESTAMP",
        "source_url": "TEXT DEFAULT ''",
        "vintage_at": "TIMESTAMP",
        "revision_number": "INTEGER DEFAULT 0",
        "is_revised": "INTEGER DEFAULT 0",
        "quality_status": "TEXT DEFAULT 'valid'",
        "quality_message": "TEXT DEFAULT ''",
    },
    "news_articles": {
        "processing_status": "TEXT DEFAULT 'fetched'",
        "processing_error": "TEXT DEFAULT ''",
        "processing_attempts": "INTEGER DEFAULT 0",
        "processing_updated_at": "TIMESTAMP",
    },
    "ai_analyses": {
        "prompt_version": "TEXT DEFAULT ''",
    },
    "news_clusters": {
        "merged_into": "INTEGER",
        "ai_status": "TEXT DEFAULT 'pending'",
        "ai_title": "TEXT DEFAULT ''",
        "ai_summary": "TEXT DEFAULT ''",
        "ai_implications": "TEXT DEFAULT ''",
        "ai_watch_next": "TEXT DEFAULT ''",
        "ai_updated_at": "TIMESTAMP",
    },
}


def _ensure_column(conn, table, column):
    """Add an explicitly approved compatibility column without rebuilding a table."""
    definition = _COMPATIBLE_COLUMNS.get(table, {}).get(column)
    if definition is None:
        raise ValueError(f"Unsupported compatibility column: {table}.{column}")
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _is_locked_error(exc):
    msg = str(exc).lower()
    return "database is locked" in msg or "database is busy" in msg or "locked" in msg


def _connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=SQLITE_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    return conn


def _commit_with_retry(conn):
    for attempt in range(SQLITE_RETRY_ATTEMPTS):
        try:
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if not _is_locked_error(exc) or attempt == SQLITE_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(SQLITE_RETRY_BASE_SLEEP * (2 ** attempt))


@contextmanager
def get_db():
    with _DB_LOCK:
        conn = _connect()
        try:
            yield conn
            _commit_with_retry(conn)
        except Exception:
            try:
                conn.rollback()
            except sqlite3.OperationalError:
                pass
            raise
        finally:
            conn.close()


def init_db():
    with get_db() as conn:
        try:
            conn.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE}")
            conn.execute(f"PRAGMA synchronous={SQLITE_SYNCHRONOUS}")
        except sqlite3.OperationalError:
            pass
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS time_series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                series_id TEXT NOT NULL,
                date TEXT NOT NULL,
                value REAL,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                release_at TIMESTAMP,
                source_url TEXT DEFAULT '',
                vintage_at TIMESTAMP,
                revision_number INTEGER DEFAULT 0,
                is_revised INTEGER DEFAULT 0,
                quality_status TEXT DEFAULT 'valid',
                quality_message TEXT DEFAULT '',
                UNIQUE(source, series_id, date)
            );

            CREATE INDEX IF NOT EXISTS idx_ts_lookup
                ON time_series(source, series_id, date);

            CREATE TABLE IF NOT EXISTS data_quality_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT UNIQUE NOT NULL,
                source TEXT NOT NULL,
                series_id TEXT NOT NULL,
                observed_date TEXT DEFAULT '',
                issue_type TEXT NOT NULL,
                message TEXT NOT NULL,
                raw_value TEXT DEFAULT '',
                resolved INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_quality_issues_source
                ON data_quality_issues(source, resolved, created_at);

            CREATE TABLE IF NOT EXISTS series_meta (
                source TEXT NOT NULL,
                series_id TEXT NOT NULL,
                display_name TEXT,
                unit TEXT,
                frequency TEXT,
                category TEXT,
                yaxis_label TEXT,
                PRIMARY KEY (source, series_id)
            );

            CREATE TABLE IF NOT EXISTS tic_holdings (
                date TEXT NOT NULL,
                country TEXT NOT NULL,
                holdings_billions REAL,
                category TEXT DEFAULT 'total',
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (date, country, category)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                category TEXT DEFAULT 'market',
                impact TEXT DEFAULT 'medium',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS fetch_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                series_id TEXT DEFAULT '',
                status TEXT NOT NULL,
                records_fetched INTEGER DEFAULT 0,
                error_message TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_fetch_log_latest
                ON fetch_log(source, created_at DESC);

            CREATE TABLE IF NOT EXISTS daily_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL,
                session TEXT DEFAULT 'daily',
                title TEXT NOT NULL,
                summary TEXT DEFAULT '',
                context_json TEXT DEFAULT '',
                raw_markdown TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(report_date, session)
            );

            CREATE INDEX IF NOT EXISTS idx_daily_reports_date
                ON daily_reports(report_date, session);

            CREATE TABLE IF NOT EXISTS research_hypotheses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                thesis TEXT NOT NULL,
                assets TEXT DEFAULT '',
                indicators TEXT DEFAULT '',
                news_topics TEXT DEFAULT '',
                falsification TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                confidence REAL DEFAULT 0.5,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_hypotheses_status
                ON research_hypotheses(status, updated_at);

            CREATE TABLE IF NOT EXISTS viewpoint_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hypothesis_id INTEGER,
                view_date TEXT NOT NULL,
                area TEXT DEFAULT 'general',
                stance TEXT DEFAULT 'neutral',
                rationale TEXT DEFAULT '',
                evidence TEXT DEFAULT '',
                watch_next TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (hypothesis_id) REFERENCES research_hypotheses(id)
            );

            CREATE INDEX IF NOT EXISTS idx_viewpoint_logs_date
                ON viewpoint_logs(view_date, area);

            CREATE TABLE IF NOT EXISTS watchlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                trigger TEXT DEFAULT '',
                why TEXT DEFAULT '',
                linked_assets TEXT DEFAULT '',
                linked_indicators TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_watchlist_status
                ON watchlist_items(status, updated_at);

            CREATE TABLE IF NOT EXISTS composite_signal_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_date TEXT NOT NULL,
                signal_name TEXT NOT NULL,
                category TEXT DEFAULT '',
                direction TEXT DEFAULT '',
                level TEXT DEFAULT '',
                score REAL DEFAULT 0,
                max_score REAL DEFAULT 0,
                summary TEXT DEFAULT '',
                evidence_json TEXT DEFAULT '',
                assets TEXT DEFAULT '',
                watch_next TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(signal_date, signal_name)
            );

            CREATE INDEX IF NOT EXISTS idx_signal_snapshots_date
                ON composite_signal_snapshots(signal_date, level);

            CREATE TABLE IF NOT EXISTS composite_signal_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                asset TEXT NOT NULL,
                source TEXT NOT NULL,
                series_id TEXT NOT NULL,
                start_date TEXT,
                start_value REAL,
                return_1d REAL,
                return_3d REAL,
                return_7d REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(snapshot_id, asset),
                FOREIGN KEY (snapshot_id) REFERENCES composite_signal_snapshots(id)
            );

            CREATE INDEX IF NOT EXISTS idx_signal_reviews_asset
                ON composite_signal_reviews(asset, updated_at);

            -- News articles (raw source)
            CREATE TABLE IF NOT EXISTS news_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_type TEXT DEFAULT 'rss',
                url TEXT,
                title TEXT NOT NULL,
                summary TEXT DEFAULT '',
                content TEXT DEFAULT '',
                published_at TEXT,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                language TEXT DEFAULT 'en',
                topic TEXT DEFAULT '',
                hash TEXT UNIQUE NOT NULL,
                is_analyzed INTEGER DEFAULT 0,
                processing_status TEXT DEFAULT 'fetched',
                processing_error TEXT DEFAULT '',
                processing_attempts INTEGER DEFAULT 0,
                processing_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                raw_json TEXT DEFAULT ''
            );

            -- AI analysis results
            CREATE TABLE IF NOT EXISTS ai_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER,
                model TEXT DEFAULT 'deepseek-chat',
                prompt_version TEXT DEFAULT '',
                summary_cn TEXT,
                event_type TEXT,
                macro_channels TEXT,
                assets_impacted TEXT,
                direction TEXT,
                severity INTEGER DEFAULT 1,
                confidence REAL DEFAULT 0.5,
                time_horizon TEXT DEFAULT 'days',
                is_new_information INTEGER DEFAULT 1,
                why_it_matters TEXT,
                follow_up_data TEXT,
                raw_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (article_id) REFERENCES news_articles(id)
            );

            CREATE INDEX IF NOT EXISTS idx_news_published ON news_articles(published_at);
            CREATE INDEX IF NOT EXISTS idx_news_hash ON news_articles(hash);
            CREATE INDEX IF NOT EXISTS idx_ai_article ON ai_analyses(article_id);

            CREATE TABLE IF NOT EXISTS news_cluster_indicator_links (
                cluster_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                series_id TEXT NOT NULL,
                label TEXT DEFAULT '',
                link_reason TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (cluster_id, source, series_id),
                FOREIGN KEY (cluster_id) REFERENCES news_clusters(id)
            );

            CREATE TABLE IF NOT EXISTS news_cluster_hypothesis_links (
                cluster_id INTEGER NOT NULL,
                hypothesis_id INTEGER NOT NULL,
                match_score REAL DEFAULT 0,
                match_reason TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (cluster_id, hypothesis_id),
                FOREIGN KEY (cluster_id) REFERENCES news_clusters(id),
                FOREIGN KEY (hypothesis_id) REFERENCES research_hypotheses(id)
            );

            CREATE INDEX IF NOT EXISTS idx_cluster_hypothesis_hypothesis
                ON news_cluster_hypothesis_links(hypothesis_id, cluster_id);

            CREATE TABLE IF NOT EXISTS ai_analysis_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id INTEGER NOT NULL,
                asset TEXT NOT NULL,
                source TEXT NOT NULL,
                series_id TEXT NOT NULL,
                predicted_direction TEXT DEFAULT '',
                start_date TEXT,
                start_value REAL,
                return_1d REAL,
                return_3d REAL,
                return_7d REAL,
                return_30d REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(analysis_id, asset),
                FOREIGN KEY (analysis_id) REFERENCES ai_analyses(id)
            );

            CREATE INDEX IF NOT EXISTS idx_ai_reviews_analysis
                ON ai_analysis_reviews(analysis_id, asset);

            CREATE TABLE IF NOT EXISTS news_clusters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_key TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                summary TEXT DEFAULT '',
                event_type TEXT DEFAULT 'other',
                assets_impacted TEXT DEFAULT '',
                direction TEXT DEFAULT '',
                severity INTEGER DEFAULT 1,
                confidence REAL DEFAULT 0.5,
                first_seen_at TEXT,
                last_seen_at TEXT,
                article_count INTEGER DEFAULT 0,
                primary_source TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                merged_into INTEGER,
                ai_status TEXT DEFAULT 'pending',
                ai_title TEXT DEFAULT '',
                ai_summary TEXT DEFAULT '',
                ai_implications TEXT DEFAULT '',
                ai_watch_next TEXT DEFAULT '',
                ai_updated_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_news_clusters_recent
                ON news_clusters(last_seen_at, severity);
            CREATE INDEX IF NOT EXISTS idx_news_clusters_status
                ON news_clusters(status, last_seen_at, severity);

            CREATE TABLE IF NOT EXISTS news_article_clusters (
                article_id INTEGER NOT NULL,
                cluster_id INTEGER NOT NULL,
                similarity_score REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (article_id, cluster_id),
                FOREIGN KEY (article_id) REFERENCES news_articles(id),
                FOREIGN KEY (cluster_id) REFERENCES news_clusters(id)
            );

            CREATE INDEX IF NOT EXISTS idx_article_clusters_cluster
                ON news_article_clusters(cluster_id);

            CREATE TABLE IF NOT EXISTS news_alerts (
                cluster_id INTEGER PRIMARY KEY,
                severity INTEGER DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'sending',
                alerted_at TIMESTAMP,
                last_error TEXT DEFAULT '',
                attempt_count INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cluster_id) REFERENCES news_clusters(id)
            );

            CREATE INDEX IF NOT EXISTS idx_news_alerts_status
                ON news_alerts(status, updated_at);

            CREATE TABLE IF NOT EXISTS runtime_settings (
                setting_key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL DEFAULT '{}',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Translation cache
            CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text_hash TEXT UNIQUE NOT NULL,
                source_text TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                source_lang TEXT DEFAULT 'en',
                target_lang TEXT DEFAULT 'zh',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_trans_hash ON translations(text_hash);
        """)

        # Add lineage and quality columns to databases created before this schema version.
        _ensure_column(conn, "time_series", "release_at")
        _ensure_column(conn, "time_series", "source_url")
        _ensure_column(conn, "time_series", "vintage_at")
        _ensure_column(conn, "time_series", "revision_number")
        _ensure_column(conn, "time_series", "is_revised")
        _ensure_column(conn, "time_series", "quality_status")
        _ensure_column(conn, "time_series", "quality_message")
        _ensure_column(conn, "news_articles", "processing_status")
        _ensure_column(conn, "news_articles", "processing_error")
        _ensure_column(conn, "news_articles", "processing_attempts")
        _ensure_column(conn, "news_articles", "processing_updated_at")
        _ensure_column(conn, "ai_analyses", "prompt_version")
        _ensure_column(conn, "news_clusters", "merged_into")
        _ensure_column(conn, "news_clusters", "ai_status")
        _ensure_column(conn, "news_clusters", "ai_title")
        _ensure_column(conn, "news_clusters", "ai_summary")
        _ensure_column(conn, "news_clusters", "ai_implications")
        _ensure_column(conn, "news_clusters", "ai_watch_next")
        _ensure_column(conn, "news_clusters", "ai_updated_at")
        # This index must be created after the compatibility migration above.
        # Older databases do not yet have processing_status when executescript runs.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_processing "
            "ON news_articles(processing_status, published_at)"
        )
        conn.execute(
            """UPDATE news_articles SET processing_status = 'analyzed'
               WHERE is_analyzed = 1 AND processing_status = 'fetched'"""
        )
        conn.execute(
            """UPDATE news_articles SET processing_status = 'clustered'
               WHERE processing_status = 'analyzed' AND id IN
               (SELECT article_id FROM news_article_clusters)"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ts_quality "
            "ON time_series(source, quality_status, date)"
        )
