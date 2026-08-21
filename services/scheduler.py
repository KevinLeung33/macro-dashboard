"""定时任务调度器 — 数据刷新 + 新闻抓取 + 报告生成"""
import logging
import os
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from services.runtime_controls import (
    TaskBusyError,
    hold_task,
    read_task_status,
    run_with_retry,
)
from services.time_utils import app_timezone, timezone_name

logger = logging.getLogger("scheduler")


class MacroScheduler:
    def __init__(self, data_pipeline, news_fetcher, report_builder, notifier,
                  fast_news_fetcher=None, cpolar_checker=None, health_checker=None,
                  paper_trading_runner=None, trade_execution_sync_runner=None,
                  account_snapshot_runner=None,
                  home_snapshot_builder=None):
        self.timezone = app_timezone()
        self.scheduler = BackgroundScheduler(timezone=self.timezone)
        self.data_pipeline = data_pipeline
        self.news_fetcher = news_fetcher
        self.fast_news_fetcher = fast_news_fetcher
        # cpolar_checker 保留为兼容旧调用；新版本统一使用 health_checker。
        self.health_checker = health_checker or cpolar_checker
        self.paper_trading_runner = paper_trading_runner
        self.trade_execution_sync_runner = trade_execution_sync_runner
        self.account_snapshot_runner = account_snapshot_runner
        self.home_snapshot_builder = home_snapshot_builder
        self.report_builder = report_builder
        self.notifier = notifier

    def _fetch_data(self):
        logger.info("Scheduled: fetching data at %s...", self._now())
        try:
            with hold_task("data_refresh"):
                run_with_retry("data_refresh", self.data_pipeline)
            logger.info("Data fetch complete")
        except TaskBusyError:
            logger.warning("Scheduled data fetch skipped: another data refresh is running")
        except Exception as e:
            logger.error(f"Data fetch failed: {e}")

    def _fetch_news(self):
        logger.info("Scheduled: fetching news at %s...", self._now())
        try:
            with hold_task("news_refresh"):
                count = run_with_retry("news_refresh", self.news_fetcher)
            logger.info(f"News fetch: {count} articles")
        except TaskBusyError:
            logger.warning("Scheduled news fetch skipped: another news refresh is running")
        except Exception as e:
            logger.error(f"News fetch failed: {e}")

    def _fetch_fast_news(self):
        """只抓 RSS 原文，不触发 AI；用于较低延迟地更新新闻雷达。"""
        logger.info("Scheduled: fast RSS refresh at %s...", self._now())
        try:
            with hold_task("news_fast_refresh"):
                count = run_with_retry("news_fast_refresh", self.fast_news_fetcher)
            logger.info("Fast RSS fetch: %s articles", count)
        except TaskBusyError:
            logger.warning("Fast RSS fetch skipped: another fast RSS refresh is running")
        except Exception as e:
            logger.error("Fast RSS fetch failed: %s", e)

    def _check_system_health(self):
        if not self.health_checker:
            return
        try:
            self.health_checker()
        except Exception as e:
            # 健康检查不能反过来拖垮数据、新闻和日报任务。
            logger.error("system health check failed: %s", e)

    def _run_paper_trading(self):
        """Advance local AI shadow orders using public market data only."""
        if not self.paper_trading_runner:
            return
        try:
            with hold_task("paper_trading"):
                result = run_with_retry("paper_trading", self.paper_trading_runner)
            logger.info(
                "AI paper trading check: status=%s checked=%s changed=%s errors=%s",
                result.get("status") if isinstance(result, dict) else "ok",
                result.get("checked") if isinstance(result, dict) else "?",
                result.get("changed") if isinstance(result, dict) else "?",
                len(result.get("errors") or []) if isinstance(result, dict) else 0,
            )
        except TaskBusyError:
            logger.warning("AI paper trading skipped: another paper-trading task is running")
        except Exception as e:
            logger.error("AI paper trading check failed: %s", e)

    def _sync_trade_execution(self):
        """Refresh read-only OKX order/fill state for linked trade plans."""
        if not self.trade_execution_sync_runner:
            return
        try:
            from services.okx_readonly import okx_rest_cooldown_remaining

            remaining = okx_rest_cooldown_remaining("okx_trade_sync")
            if remaining > 0:
                logger.info("OKX execution sync skipped during REST cooldown: %ss", remaining)
                return
            with hold_task("okx_trade_sync"):
                result = run_with_retry("okx_trade_sync", self.trade_execution_sync_runner)
            counts = result.get("counts") if isinstance(result, dict) else {}
            logger.info(
                "OKX read-only execution sync: orders=%s fills=%s plan_link_updates=%s",
                counts.get("orders", "?"),
                counts.get("fills", "?"),
                counts.get("plan_link_updates", "?"),
            )
        except TaskBusyError:
            logger.warning("OKX execution sync skipped: another execution sync is running")
        except Exception as e:
            logger.error("OKX read-only execution sync failed: %s", e)

    def _sync_account_snapshot(self):
        if not self.account_snapshot_runner:
            return
        try:
            with hold_task("okx_account_snapshot"):
                result = run_with_retry("okx_account_snapshot", self.account_snapshot_runner)
            if isinstance(result, dict) and result.get("status") == "cooldown":
                logger.info("OKX account snapshot skipped during REST cooldown: %ss", result.get("remaining_seconds"))
            else:
                logger.info("OKX account snapshot refreshed: %s", (result or {}).get("account_label", "?"))
        except Exception as e:
            logger.error("OKX account snapshot failed: %s", e)

    def _daily_report(self):
        logger.info("Scheduled: generating daily report at %s...", self._now())
        try:
            with hold_task("daily_report"):
                msg = run_with_retry("daily_report", lambda: self.report_builder("daily"))
            if msg:
                self.notifier(msg)
            logger.info("Daily report sent")
        except TaskBusyError:
            logger.warning("Scheduled daily report skipped: another daily report is running")
        except Exception as e:
            logger.error(f"Daily report failed: {e}")

    def _weekly_report(self):
        logger.info("Scheduled: generating weekly report at %s...", self._now())
        try:
            with hold_task("weekly_report"):
                msg = run_with_retry("weekly_report", lambda: self.report_builder("weekly"))
            if msg:
                self.notifier(msg)
            logger.info("Weekly report sent")
        except TaskBusyError:
            logger.warning("Scheduled weekly report skipped: another weekly report is running")
        except Exception as e:
            logger.error(f"Weekly report failed: {e}")

    def _refresh_home_snapshot(self):
        if not self.home_snapshot_builder:
            return
        try:
            with hold_task("home_snapshot"):
                run_with_retry("home_snapshot", self.home_snapshot_builder)
            logger.info("Home dashboard snapshot refreshed")
        except TaskBusyError:
            logger.warning("Home snapshot skipped: another snapshot task is running")
        except Exception as e:
            logger.error("Home dashboard snapshot failed: %s", e)

    def start(self):
        self._recover_missed_tasks()
        paper_enabled = (
            self.paper_trading_runner
            and os.getenv("AI_SHADOW_PAPER_ENABLED", "true").lower() in ("1", "true", "yes", "on")
        )
        okx_credentials_configured = all(
            os.getenv(name, "").strip()
            for name in ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE")
        )
        execution_sync_enabled = (
            self.trade_execution_sync_runner
            and okx_credentials_configured
            and os.getenv("OKX_READONLY_SYNC_ENABLED", "true").lower() in ("1", "true", "yes", "on")
        )
        # Data: every 6 hours
        self.scheduler.add_job(self._fetch_data, CronTrigger(hour="*/6"))
        # News: every hour
        self.scheduler.add_job(self._fetch_news, CronTrigger(minute=0))
        # RSS: every 15 minutes by default; this job only入库，不调用 AI。
        if self.fast_news_fetcher:
            try:
                fast_minutes = max(5, int(os.getenv("NEWS_FAST_REFRESH_MINUTES", "10")))
            except ValueError:
                fast_minutes = 15
            self.scheduler.add_job(
                self._fetch_fast_news,
                CronTrigger(minute=f"*/{fast_minutes}"),
                id="news_fast_refresh",
                replace_existing=True,
            )
        if self.health_checker and os.getenv("CPOLAR_HEALTH_ENABLED", "true").lower() in ("1", "true", "yes", "on"):
            try:
                health_minutes = max(1, int(os.getenv("CPOLAR_HEALTH_CHECK_MINUTES", "5")))
            except ValueError:
                health_minutes = 5
            try:
                initial_health_delay = max(5, int(os.getenv("HEALTH_INITIAL_CHECK_DELAY_SECONDS", "45")))
            except ValueError:
                initial_health_delay = 45
            # API 与 Streamlit 由不同的 systemd 服务启动。启动时同步检查会
            # 在它们完成端口绑定前误报 P0，因此改为短暂延迟后再执行首检。
            self.scheduler.add_job(
                self._check_system_health,
                DateTrigger(run_date=datetime.now(self.timezone) + timedelta(seconds=initial_health_delay)),
                id="system_health_startup_check",
                replace_existing=True,
            )
            self.scheduler.add_job(
                self._check_system_health,
                IntervalTrigger(minutes=health_minutes),
                id="system_health_check",
                replace_existing=True,
            )
        if paper_enabled:
            try:
                paper_minutes = max(1, int(os.getenv("AI_SHADOW_PAPER_INTERVAL_MINUTES", "1")))
            except ValueError:
                paper_minutes = 1
            self.scheduler.add_job(
                self._run_paper_trading,
                IntervalTrigger(minutes=paper_minutes),
                id="ai_shadow_paper_trading",
                replace_existing=True,
            )
        if execution_sync_enabled:
            try:
                execution_minutes = max(1, int(os.getenv("OKX_READONLY_SYNC_INTERVAL_MINUTES", "1")))
            except ValueError:
                execution_minutes = 1
            self.scheduler.add_job(
                self._sync_trade_execution,
                IntervalTrigger(minutes=execution_minutes),
                id="okx_readonly_trade_execution_sync",
                replace_existing=True,
            )
            self.scheduler.add_job(
                self._sync_account_snapshot,
                IntervalTrigger(
                    minutes=1,
                    start_date=datetime.now(self.timezone) + timedelta(seconds=5),
                ),
                id="okx_account_snapshot",
                replace_existing=True,
            )
        # Daily report: 8:00 AM
        self.scheduler.add_job(self._daily_report, CronTrigger(hour=8, minute=0))
        if self.home_snapshot_builder:
            self.scheduler.add_job(
                self._refresh_home_snapshot,
                CronTrigger(hour=8, minute=15),
                id="home_snapshot",
                replace_existing=True,
            )
        # Weekly report: Monday 9:00 AM
        self.scheduler.add_job(self._weekly_report, CronTrigger(day_of_week="mon", hour=9, minute=0))
        self.scheduler.start()
        logger.info(
            "Scheduler started with timezone=%s current_time=%s; jobs: "
            "data/6h, RSS/%sm, news/1h, system-health/%sm, AI-paper/%sm, OKX-execution/%sm, daily/8am, weekly/Mon9am",
            timezone_name(),
            self._now(),
            os.getenv("NEWS_FAST_REFRESH_MINUTES", "10") if self.fast_news_fetcher else "off",
            os.getenv("CPOLAR_HEALTH_CHECK_MINUTES", "5") if self.health_checker else "off",
            os.getenv("AI_SHADOW_PAPER_INTERVAL_MINUTES", "1") if paper_enabled else "off",
            os.getenv("OKX_READONLY_SYNC_INTERVAL_MINUTES", "1") if execution_sync_enabled else "off",
        )

    def _recover_missed_tasks(self):
        """Catch up jobs missed while the service was stopped."""
        if os.getenv("STARTUP_RECOVERY_ENABLED", "true").lower() not in ("1", "true", "yes"):
            logger.info("Startup recovery disabled")
            return

        statuses = read_task_status()
        now = datetime.now(self.timezone)

        def last_success(task_name):
            value = (statuses.get(task_name) or {}).get("last_success_at")
            if value is None:
                value = (statuses.get(task_name) or {}).get("updated_at")
            try:
                return datetime.fromtimestamp(float(value), self.timezone) if value else None
            except (TypeError, ValueError, OSError):
                return None

        data_last = last_success("data_refresh")
        data_max_age = float(os.getenv("STARTUP_RECOVERY_DATA_MAX_AGE_SECONDS", "28800"))
        if data_last is None or (now - data_last).total_seconds() > data_max_age:
            logger.warning("Startup recovery: data refresh is missing or stale")
            self._fetch_data()

        news_last = last_success("news_refresh")
        news_max_age = float(os.getenv("STARTUP_RECOVERY_NEWS_MAX_AGE_SECONDS", "7200"))
        if news_last is None or (now - news_last).total_seconds() > news_max_age:
            logger.warning("Startup recovery: news refresh is missing or stale")
            self._fetch_news()

        daily_last = last_success("daily_report")
        daily_due = now.hour >= 8 and (daily_last is None or daily_last.date() < now.date())
        if daily_due:
            logger.warning("Startup recovery: today's daily report is missing")
            self._daily_report()

        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        weekly_last = last_success("weekly_report")
        weekly_due = now >= week_start and (weekly_last is None or weekly_last < week_start)
        if weekly_due:
            logger.warning("Startup recovery: this week's weekly report is missing")
            self._weekly_report()

    def _now(self):
        return datetime.now(self.timezone).isoformat(timespec="seconds")

    def stop(self):
        self.scheduler.shutdown()
