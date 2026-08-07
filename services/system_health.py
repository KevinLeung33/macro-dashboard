"""P0/P1/P2 运行健康检查。

检查范围：
* P0：Streamlit 本机/公网入口、FastAPI 本机入口、cpolar 公网隧道。
* P1：定时任务新鲜度、数据源抓取状态、RSS 源状态、SQLite 完整性和磁盘空间。
* P2：数据库备份新鲜度/完整性、AI 处理失败、通知渠道投递状态。

检查结果只在状态变化时通过现有通知渠道发送，避免每轮检查重复打扰。
"""
import logging
import json
import os
import shutil
import time
from datetime import datetime, timezone
import threading
from pathlib import Path

import requests

from db.schema import DB_PATH, get_db
from db.repository import query_news_feed_states, query_news_processing_summary
from services.maintenance import verify_backup
from services.cpolar_monitor import check_cpolar
from services.daily_context import get_data_health
from services.notifier import notify
from services.runtime_controls import parse_notify_channels, read_task_status

logger = logging.getLogger("system_health")
_STATE_LOCK = threading.Lock()
_LAST_SIGNATURE = None
_STARTED_AT = time.time()
_BACKUP_VERIFY_CACHE = {}


def _env_float(name, default):
    try:
        return max(1.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return float(default)


def _env_int(name, default):
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _parse_timestamp(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return float(value)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OSError):
        return None


def _http_check(label, url):
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "macro-dashboard/system-health"},
            timeout=_env_float("HEALTH_HTTP_TIMEOUT_SECONDS", 10),
            allow_redirects=True,
        )
        ok = 200 <= response.status_code < 400
        return {"label": label, "url": url, "ok": ok, "error": "" if ok else f"HTTP {response.status_code}"}
    except Exception as exc:
        return {"label": label, "url": url, "ok": False, "error": str(exc)[:240]}


def _task_issues():
    statuses = read_task_status()
    now = time.time()
    startup_grace = _env_float("HEALTH_STARTUP_GRACE_MINUTES", 10) * 60
    checks = []

    definitions = [
        ("data_refresh", "宏观数据刷新", _env_float("HEALTH_DATA_MAX_AGE_SECONDS", 43200)),
        (
            "news_fast_refresh",
            "RSS 快速刷新",
            max(1800, _env_int("NEWS_FAST_REFRESH_MINUTES", 15) * 60 * 3),
        ),
        ("news_refresh", "新闻完整分析", _env_float("HEALTH_NEWS_MAX_AGE_SECONDS", 10800)),
    ]
    for task_name, label, max_age in definitions:
        status = statuses.get(task_name) or {}
        last_success = _parse_timestamp(status.get("last_success_at"))
        age = None if last_success is None else max(0, now - last_success)
        if str(status.get("status", "")).lower() == "failed":
            checks.append(f"{label}任务失败：{str(status.get('error') or '未记录原因')[:180]}")
        elif age is None:
            if now - _STARTED_AT >= startup_grace:
                checks.append(f"{label}从未成功")
        elif age > max_age:
            checks.append(f"{label}已 {age / 3600:.1f} 小时未成功")
    return checks


def _data_issues():
    critical = []
    warnings = []
    max_age = _env_float("HEALTH_DATA_SOURCE_MAX_AGE_SECONDS", 43200)
    try:
        rows = get_data_health()
    except Exception as exc:
        return [f"数据健康状态读取失败：{str(exc)[:180]}"], []

    now = time.time()
    startup_grace = _env_float("HEALTH_STARTUP_GRACE_MINUTES", 10) * 60
    for row in rows:
        source = row.get("source") or "unknown"
        if source in {"news", "ai"}:
            continue
        last_status = str(row.get("last_status") or "").lower()
        if last_status in {"error", "failed"}:
            critical.append(f"数据源 {source} 抓取失败：{str(row.get('last_error') or '未记录原因')[:160]}")
            continue
        if last_status == "skipped":
            warnings.append(f"数据源 {source} 被跳过")
        attempt_at = _parse_timestamp(row.get("last_fetch_attempt"))
        if attempt_at is None:
            if now - _STARTED_AT >= startup_grace:
                critical.append(f"数据源 {source} 没有抓取记录")
        elif now - attempt_at > max_age:
            critical.append(f"数据源 {source} 已 {((now - attempt_at) / 3600):.1f} 小时未抓取")
        quality_count = int(row.get("quality_issue_count") or 0)
        if quality_count:
            warnings.append(f"数据源 {source} 有 {quality_count} 条未解决质量问题")
    return critical, warnings


def _rss_issues():
    warnings = []
    max_age = _env_float("HEALTH_RSS_SOURCE_MAX_AGE_SECONDS", 21600)
    now = time.time()
    startup_grace = _env_float("HEALTH_STARTUP_GRACE_MINUTES", 10) * 60
    try:
        rows = query_news_feed_states()
    except Exception as exc:
        return [f"RSS 状态读取失败：{str(exc)[:180]}"]
    for row in rows:
        source = row["source"]
        if row["last_error"]:
            warnings.append(f"RSS {source} 最近失败：{str(row['last_error'])[:150]}")
        success_at = _parse_timestamp(row["last_success_at"])
        if success_at is None or now - success_at > max_age:
            if now - _STARTED_AT >= startup_grace:
                warnings.append(f"RSS {source} 超过 {max_age / 3600:.0f} 小时未成功")
    return warnings


def _storage_issues():
    critical = []
    try:
        free_bytes = shutil.disk_usage(DB_PATH.parent).free
        minimum_bytes = _env_float("HEALTH_MIN_FREE_BYTES", 524288000)
        minimum_percent = _env_float("HEALTH_MIN_FREE_PERCENT", 10)
        usage = shutil.disk_usage(DB_PATH.parent)
        free_percent = (usage.free / usage.total * 100) if usage.total else 0
        if free_bytes < minimum_bytes or free_percent < minimum_percent:
            critical.append(
                f"磁盘空间不足：剩余 {free_percent:.1f}% / {free_bytes / 1024**3:.2f} GiB"
            )
    except Exception as exc:
        critical.append(f"磁盘空间检查失败：{str(exc)[:160]}")

    try:
        with get_db() as conn:
            result = conn.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            critical.append(f"SQLite 完整性检查失败：{result}")
    except Exception as exc:
        critical.append(f"SQLite 检查失败或被锁定：{str(exc)[:180]}")
    return critical


def _p2_issues():
    """Return P2 operational warnings that do not make the dashboard unavailable."""
    warnings = []
    now = time.time()
    startup_grace = _env_float("HEALTH_STARTUP_GRACE_MINUTES", 10) * 60

    # Backup freshness and integrity. The daily report normally creates the file;
    # the monitor only verifies the newest existing file and never creates one.
    backup_dir = Path(os.getenv("BACKUP_DIR", "backups"))
    try:
        backups = sorted(
            backup_dir.glob("macro_data_*.db"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        max_age = _env_float("HEALTH_BACKUP_MAX_AGE_SECONDS", 36 * 3600)
        if not backups:
            if now - _STARTED_AT >= startup_grace:
                warnings.append("没有找到数据库备份")
        else:
            newest = backups[0]
            age = max(0, now - newest.stat().st_mtime)
            if age > max_age:
                warnings.append(f"最近数据库备份已 {age / 3600:.1f} 小时未更新")
            try:
                stat = newest.stat()
                cache_key = (str(newest), stat.st_mtime_ns, stat.st_size)
                integrity = _BACKUP_VERIFY_CACHE.get(cache_key)
                if integrity is None:
                    integrity = verify_backup(newest)
                    _BACKUP_VERIFY_CACHE.clear()
                    _BACKUP_VERIFY_CACHE[cache_key] = integrity
                if integrity.get("status") != "ok":
                    warnings.append(f"最近数据库备份完整性异常：{integrity.get('integrity') or integrity.get('status')}")
            except Exception as exc:
                warnings.append(f"数据库备份完整性检查失败：{str(exc)[:150]}")
    except OSError as exc:
        warnings.append(f"备份目录检查失败：{str(exc)[:150]}")

    # AI pipeline: distinguish a few old failed articles from a growing failure pile.
    try:
        summary = query_news_processing_summary()
        failed_rows = summary.get("failed") or []
        failed_count = int((summary.get("counts") or {}).get("failed", 0))
        threshold = _env_int("HEALTH_AI_FAILED_ARTICLES_MAX", 5)
        if failed_count > threshold:
            warnings.append(f"AI 新闻处理失败 {failed_count} 条，超过阈值 {threshold}")
        elif failed_rows:
            recent_failed = 0
            cutoff = now - _env_float("HEALTH_AI_FAILURE_RECENT_SECONDS", 86400)
            for row in failed_rows:
                updated = _parse_timestamp(row["processing_updated_at"])
                if updated is not None and updated >= cutoff:
                    recent_failed += 1
            if recent_failed:
                warnings.append(f"最近 24 小时有 {recent_failed} 条新闻 AI 处理失败")
    except Exception as exc:
        warnings.append(f"AI 处理状态读取失败：{str(exc)[:150]}")

    # Notification delivery is intentionally a local diagnostic. If Feishu itself
    # is down, a Feishu recovery alert cannot be delivered through that same path.
    status_path = Path(os.getenv("RUNTIME_LOCK_DIR", "runtime/locks")).parent / "notification_status.json"
    configured_channels = [
        item.strip().lower() for item in os.getenv("NOTIFY_CHANNELS", "telegram").split(",") if item.strip()
    ]
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
        updated = _parse_timestamp(payload.get("updated_at"))
        results = payload.get("results") or {}
        if updated is not None and now - updated < _env_float("HEALTH_NOTIFICATION_STATUS_MAX_AGE_SECONDS", 86400):
            failed_channels = [channel for channel in configured_channels if results.get(channel) is False]
            if failed_channels:
                warnings.append("通知渠道最近投递失败：" + ", ".join(failed_channels))
        elif configured_channels and now - _STARTED_AT >= startup_grace:
            warnings.append("尚未记录通知渠道投递结果：" + ", ".join(configured_channels))
    except (OSError, ValueError, TypeError) as exc:
        warnings.append(f"通知状态读取失败：{str(exc)[:150]}")

    return warnings


def _send_transition(state, critical, warnings):
    if state == "ok":
        message = "宏观看板 P0/P1/P2 健康检查已恢复正常。"
        notify(message, parse_notify_channels(), title="宏观看板健康恢复", level="info")
        return
    title = "宏观看板 P0/P1/P2 严重告警" if state == "critical" else "宏观看板 P1/P2 预警"
    lines = [title]
    if critical:
        lines.append("严重问题：")
        lines.extend(f"- {item}" for item in critical[:12])
    if warnings:
        lines.append("需要关注：")
        lines.extend(f"- {item}" for item in warnings[:12])
    notify("\n".join(lines), parse_notify_channels(), title=title, level="error" if state == "critical" else "warning")


def check_system_health():
    """执行一次 P0/P1/P2 检查，并在状态变化时通知。"""
    critical = []
    warnings = []

    streamlit_url = os.getenv("CPOLAR_LOCAL_URL", "http://127.0.0.1:8501").strip()
    api_port = os.getenv("API_PORT", "8080")
    local_checks = [
        _http_check("本机 Streamlit", streamlit_url),
        _http_check("本机 API", f"http://127.0.0.1:{api_port}/api/health"),
    ]
    critical.extend(
        f"{item['label']}不可用：{item['error']}" for item in local_checks if not item["ok"]
    )

    cpolar = check_cpolar(notify_on_transition=False)
    if cpolar.get("enabled"):
        critical.extend(
            f"{item['label']}不可用：{item.get('error') or '检查失败'}"
            for item in cpolar.get("checks", []) if item.get("configured") and item.get("ok") is False
        )
        if not os.getenv("CPOLAR_PUBLIC_URL", "").strip():
            warnings.append("未配置 CPOLAR_PUBLIC_URL，无法验证公网隧道是否 inactive")
    else:
        warnings.append("cpolar 健康检查未启用")

    critical.extend(_task_issues())
    data_critical, data_warnings = _data_issues()
    critical.extend(data_critical)
    warnings.extend(data_warnings)
    warnings.extend(_rss_issues())
    critical.extend(_storage_issues())
    p2_warnings = _p2_issues()
    warnings.extend(p2_warnings)

    state = "critical" if critical else ("warning" if warnings else "ok")
    signature = state + "|" + "|".join(sorted(critical + warnings))
    global _LAST_SIGNATURE
    with _STATE_LOCK:
        previous = _LAST_SIGNATURE
        if previous != signature:
            if previous is not None:
                _send_transition(state, critical, warnings)
            elif state != "ok":
                _send_transition(state, critical, warnings)
            _LAST_SIGNATURE = signature

    logger.info("System health state=%s critical=%s warnings=%s", state, len(critical), len(warnings))
    return {
        "state": state,
        "critical": critical,
        "warnings": warnings,
        "local_checks": local_checks,
        "cpolar": cpolar,
        "p2": {"warnings": p2_warnings},
    }
