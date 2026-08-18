"""服务器入口 — 启动定时任务 + API端点

用法:
  python server.py              # 仅启动定时任务
  python server.py --with-api   # 启动定时任务 + FastAPI
  python server.py --streamlit  # 启动 Streamlit 看板

环境变量 (.env):
  TELEGRAM_BOT_TOKEN=xxx         Telegram Bot Token
  TELEGRAM_CHAT_ID=xxx           Telegram Chat ID
  OPENAI_API_KEY=xxx             OpenAI Key (可选，用Ollama免费)
  ALPHA_VANTAGE_KEY=xxx          Alpha Vantage 新闻/行情 Key (可选)
  LARK_WEBHOOK_URL=xxx           飞书自定义机器人 Webhook (可选)
  LARK_WEBHOOK_SECRET=xxx        飞书签名校验密钥 (可选)
  NOTIFY_CHANNELS=telegram,lark  推送渠道
"""
import os
import subprocess
import sys
import hmac
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from config.logging_config import setup_logging
logger = setup_logging("server")


def require_api_token(authorization: Optional[str] = None):
    """Protect operational endpoints with a configured Bearer token."""
    from fastapi import HTTPException, status

    expected = os.getenv("API_AUTH_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured",
        )

    scheme, _, provided = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def main():
    from data.pipeline import fetch_all
    from db.schema import init_db
    from services.scheduler import MacroScheduler
    from services.news_fetcher import fetch_all_news, fetch_fast_rss
    from services.system_health import check_system_health
    from services.report_builder import build_report
    from services.notifier import notify
    from services.daily_context import get_data_health, save_daily_context
    from services.daily_ai_report import save_ai_trend_report
    from services.dashboard_snapshot import refresh_home_snapshot
    from services.maintenance import backup_database, runtime_status
    from services.paper_trading import run_paper_trading
    from services.okx_readonly import sync_okx_trade_execution
    from services.runtime_controls import (
        RateLimitExceeded,
        TaskBusyError,
        consume_rate_limit,
        hold_task,
        parse_notify_channels,
        run_with_retry,
        record_task_status,
        read_task_status,
    )

    background_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="macro-bg")

    def submit_background_task(task_name, callback):
        """Queue a long-running operation and return without blocking HTTP."""
        record_task_status(task_name, "queued")

        def runner():
            try:
                with hold_task(task_name):
                    return run_with_retry(task_name, callback)
            except TaskBusyError:
                record_task_status(task_name, "skipped", error="another task is already running")
                logger.warning("Background task skipped: %s is already running", task_name)
            except Exception as exc:
                # run_with_retry already records the final failure and notifies;
                # keep the worker exception from surfacing in the API thread.
                logger.exception("Background task failed: %s", task_name)

        background_executor.submit(runner)
        return {"status": "queued", "task": task_name}

    # 先完成新表/旧库迁移，再启动 RSS 快速任务。
    init_db()

    alpha_vantage_key = os.getenv("ALPHA_VANTAGE_KEY", "").strip()
    channels = parse_notify_channels()

    # Lazy imports for news fetch to include AI analysis
    def news_pipeline():
        count = fetch_all_news(alpha_vantage_key)
        from services.ai_analyzer import run_analysis_pipeline
        analyzed = run_analysis_pipeline(limit=15)
        logger.info(f"AI analyzed {analyzed} articles")
        return count

    def fast_news_pipeline():
        count = fetch_fast_rss()
        from services.news_alerts import dispatch_flash_rule_alerts
        flash_alerts = dispatch_flash_rule_alerts()
        logger.info("Fast news flash alerts: %s", flash_alerts)
        return count

    def build_scheduled_report(report_type):
        if report_type == "daily":
            _result, markdown, _context = save_ai_trend_report(session="ai_daily")
            if os.getenv("BACKUP_AFTER_DAILY_REPORT", "true").lower() in ("1", "true", "yes"):
                with hold_task("backup"):
                    backup = run_with_retry("backup", backup_database)
                logger.info(f"Database backup after daily report: {backup}")
            return markdown
        return build_report(report_type)

    scheduler = MacroScheduler(
        data_pipeline=lambda: fetch_all(include_global=True),
        news_fetcher=news_pipeline,
        fast_news_fetcher=fast_news_pipeline,
        health_checker=check_system_health,
        paper_trading_runner=run_paper_trading,
        trade_execution_sync_runner=sync_okx_trade_execution,
        report_builder=build_scheduled_report,
        home_snapshot_builder=refresh_home_snapshot,
        notifier=lambda msg: notify(msg, channels),
    )
    scheduler.start()

    streamlit_proc = None
    if "--streamlit" in sys.argv:
        port = os.getenv("STREAMLIT_PORT", "8501")
        streamlit_proc = subprocess.Popen(
            [
                sys.executable, "-m", "streamlit", "run", "app.py",
                "--server.port", port,
                "--server.headless", "true",
                "--browser.gatherUsageStats", "false",
            ],
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        logger.info(f"Streamlit started on port {port}")

    # Optional: start FastAPI
    if "--with-api" in sys.argv:
        from fastapi import Depends, FastAPI, Header, HTTPException
        import uvicorn

        app = FastAPI(title="Macro Dashboard API")

        def api_guard(operation: str):
            def dependency(authorization: Optional[str] = Header(default=None)):
                require_api_token(authorization)
                try:
                    consume_rate_limit(operation)
                except RateLimitExceeded as exc:
                    raise HTTPException(
                        status_code=429,
                        detail=f"Too many requests for {operation}; retry later",
                        headers={"Retry-After": str(exc.retry_after)},
                    )

            return dependency

        def execute_locked(task_name, callback):
            try:
                with hold_task(task_name):
                    return run_with_retry(task_name, callback)
            except TaskBusyError:
                raise HTTPException(
                    status_code=409,
                    detail=f"Task already running: {task_name}",
                    headers={"Retry-After": "30"},
                )

        @app.get("/api/health")
        def health():
            # Keep the liveness probe intentionally small.  A transient
            # upstream-data issue must not turn a healthy API process into a
            # P0 outage for the monitor.  Detailed (and now JSON-safe) source
            # health remains available from the authenticated status endpoint.
            return {"status": "ok", "service": "macro-dashboard-api"}

        @app.get("/api/status")
        def status(_token: None = Depends(api_guard("status"))):
            return {"status": "ok", "runtime": runtime_status(), "data_sources": get_data_health()}

        @app.post("/api/data/refresh")
        def refresh_data(
            incremental: bool = True,
            _token: None = Depends(api_guard("refresh")),
        ):
            return submit_background_task(
                "data_refresh",
                lambda: fetch_all(include_global=True, incremental=incremental),
            ) | {"incremental": incremental}

        @app.get("/api/task/status")
        def task_status(
            task_name: str = "data_refresh",
            _token: None = Depends(api_guard("status")),
        ):
            return {"task": task_name, "status": read_task_status().get(task_name, {})}

        @app.post("/api/context/daily")
        def create_daily_context(
            _token: None = Depends(api_guard("context")),
        ):
            _context, markdown = execute_locked(
                "daily_context",
                lambda: save_daily_context(session="daily"),
            )
            return {"status": "ok", "report": markdown}

        @app.post("/api/report/ai-daily")
        def create_ai_daily_report(
            _token: None = Depends(api_guard("report")),
        ):
            result, markdown, _context = execute_locked(
                "daily_report",
                lambda: save_ai_trend_report(session="ai_daily"),
            )
            return {"status": "ok", "ai_generated": bool(result), "report": markdown}

        @app.get("/api/report/daily")
        def daily_report(
            _token: None = Depends(api_guard("report")),
        ):
            result, markdown, _context = execute_locked(
                "daily_report",
                lambda: save_ai_trend_report(session="ai_daily"),
            )
            return {"ai_generated": bool(result), "report": markdown}

        @app.get("/api/report/weekly")
        def weekly_report(
            _token: None = Depends(api_guard("report")),
        ):
            return {"report": execute_locked("weekly_report", lambda: build_report("weekly"))}

        @app.post("/api/maintenance/backup")
        def backup(
            _token: None = Depends(api_guard("backup")),
        ):
            return execute_locked("backup", backup_database)

        api_host = os.getenv("API_HOST", "127.0.0.1")
        api_port = int(os.getenv("API_PORT", "8080"))
        logger.info(f"Starting FastAPI on {api_host}:{api_port}...")
        uvicorn.run(app, host=api_host, port=api_port, log_level=os.getenv("UVICORN_LOG_LEVEL", "info"))
    else:
        # Keep running
        import time
        logger.info("Server running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            scheduler.stop()
            if streamlit_proc:
                streamlit_proc.terminate()
            logger.info("Server stopped.")


def _ollama_available():
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


if __name__ == "__main__":
    main()
