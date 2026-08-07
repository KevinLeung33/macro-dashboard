"""定时任务调度器 — 数据刷新 + 新闻抓取 + 报告生成"""
import logging
import os
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from services.runtime_controls import (
    TaskBusyError,
    hold_task,
    read_task_status,
    run_with_retry,
)
from services.time_utils import app_timezone, timezone_name

logger = logging.getLogger("scheduler")


class MacroScheduler:
    def __init__(self, data_pipeline, news_fetcher, report_builder, notifier, fast_news_fetcher=None):
        self.timezone = app_timezone()
        self.scheduler = BackgroundScheduler(timezone=self.timezone)
        self.data_pipeline = data_pipeline
        self.news_fetcher = news_fetcher
        self.fast_news_fetcher = fast_news_fetcher
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

    def start(self):
        self._recover_missed_tasks()
        # Data: every 6 hours
        self.scheduler.add_job(self._fetch_data, CronTrigger(hour="*/6"))
        # News: every hour
        self.scheduler.add_job(self._fetch_news, CronTrigger(minute=0))
        # RSS: every 15 minutes by default; this job only入库，不调用 AI。
        if self.fast_news_fetcher:
            try:
                fast_minutes = max(5, int(os.getenv("NEWS_FAST_REFRESH_MINUTES", "15")))
            except ValueError:
                fast_minutes = 15
            self.scheduler.add_job(
                self._fetch_fast_news,
                CronTrigger(minute=f"*/{fast_minutes}"),
                id="news_fast_refresh",
                replace_existing=True,
            )
        # Daily report: 8:00 AM
        self.scheduler.add_job(self._daily_report, CronTrigger(hour=8, minute=0))
        # Weekly report: Monday 9:00 AM
        self.scheduler.add_job(self._weekly_report, CronTrigger(day_of_week="mon", hour=9, minute=0))
        self.scheduler.start()
        logger.info(
            "Scheduler started with timezone=%s current_time=%s; jobs: "
            "data/6h, RSS/%sm, news/1h, daily/8am, weekly/Mon9am",
            timezone_name(),
            self._now(),
            os.getenv("NEWS_FAST_REFRESH_MINUTES", "15") if self.fast_news_fetcher else "off",
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
