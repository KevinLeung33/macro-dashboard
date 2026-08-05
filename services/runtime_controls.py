"""Shared runtime controls for API, scheduler, and Streamlit jobs."""
import hashlib
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class TaskBusyError(RuntimeError):
    """Raised when another process is already running the same task."""

    def __init__(self, task_name):
        self.task_name = task_name
        super().__init__(f"Task is already running: {task_name}")


class RateLimitExceeded(RuntimeError):
    """Raised when an operation is called before its cooldown expires."""

    def __init__(self, operation, retry_after):
        self.operation = operation
        self.retry_after = max(1, int(retry_after))
        super().__init__(f"Rate limit exceeded for {operation}")


class TaskTimeoutError(RuntimeError):
    """Raised when a task completes after its configured watchdog timeout."""

    def __init__(self, task_name, elapsed, timeout):
        self.task_name = task_name
        self.elapsed = elapsed
        self.timeout = timeout
        super().__init__(f"Task exceeded timeout: {task_name} ({elapsed:.1f}s > {timeout:.1f}s)")


_RATE_LOCK = threading.Lock()
_LAST_CALLS = {}
_STATUS_LOCK = threading.Lock()
_RUNTIME_ERROR_LOCK = threading.Lock()
_RUNTIME_ERROR_LAST_NOTIFIED = {}
logger = logging.getLogger("runtime_controls")
_DEFAULT_COOLDOWNS = {
    "status": 5,
    "refresh": 300,
    "context": 300,
    "report": 300,
    "backup": 300,
}


def parse_notify_channels(raw=None):
    """Normalize comma-separated notification channel configuration."""
    value = os.getenv("NOTIFY_CHANNELS", "telegram") if raw is None else raw
    return [item.strip().lower() for item in str(value).split(",") if item.strip()]


def _lock_dir():
    configured = os.getenv("RUNTIME_LOCK_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "runtime" / "locks"


def _lock_path(task_name):
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", task_name)
    return _lock_dir() / f"{safe_name}.lock"


def _stale_lock(path):
    stale_after = float(os.getenv("TASK_LOCK_STALE_SECONDS", "3600"))
    try:
        return time.time() - path.stat().st_mtime > stale_after
    except FileNotFoundError:
        return False


@contextmanager
def hold_task(task_name):
    """Acquire a cross-process task lock for the duration of a job."""
    path = _lock_path(task_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = f"pid={os.getpid()} acquired_at={int(time.time())}\n"

    for attempt in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, token.encode("utf-8"))
            finally:
                os.close(fd)
            break
        except FileExistsError:
            if attempt == 0 and _stale_lock(path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            raise TaskBusyError(task_name)

    try:
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _cooldown_seconds(operation):
    env_name = f"API_{operation.upper()}_COOLDOWN_SECONDS"
    default = _DEFAULT_COOLDOWNS.get(operation, 60)
    try:
        return max(0.0, float(os.getenv(env_name, str(default))))
    except ValueError:
        return float(default)


def _task_setting(task_name, setting, default):
    env_name = f"{task_name.upper()}_{setting}"
    try:
        return max(0.0, float(os.getenv(env_name, str(default))))
    except ValueError:
        return float(default)


def _status_path():
    return _lock_dir().parent / "task_status.json"


def record_task_status(task_name, status, **details):
    """Persist the latest task state for health checks and post-mortems."""
    path = _status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _STATUS_LOCK:
        try:
            current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            current = {}
        previous = current.get(task_name, {})
        entry = {
            "status": status,
            "updated_at": time.time(),
            "last_success_at": previous.get("last_success_at"),
            **details,
        }
        if status == "success":
            entry["last_success_at"] = entry["updated_at"]
        current[task_name] = entry
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)


def read_task_status():
    """Read persisted task status; return an empty mapping if unavailable."""
    path = _status_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def notify_task_failure(task_name, error):
    """Notify configured channels after the final retry is exhausted."""
    if os.getenv("NOTIFY_ON_TASK_FAILURE", "true").lower() not in ("1", "true", "yes"):
        return {}
    try:
        from services.notifier import notify

        channels = parse_notify_channels()
        message = (
            f"宏观看板任务失败\n"
            f"任务: {task_name}\n"
            f"错误: {str(error)[:1000]}\n"
            f"状态: runtime/task_status.json"
        )
        return notify(message, channels)
    except Exception:
        return {}


def notify_runtime_error(task_name, error, details=""):
    """Notify recoverable runtime errors without flooding configured channels.

    Some tasks intentionally recover from upstream errors, such as using a
    rule-based report when an AI call fails. Those tasks are successful from
    the scheduler's perspective, so ``notify_task_failure`` is not called.
    This helper keeps the recovery behavior while notifying the operator.
    """
    if os.getenv("NOTIFY_ON_RUNTIME_ERROR", "true").lower() not in ("1", "true", "yes"):
        return {"disabled": True}

    error_text = str(error or "Unknown runtime error").strip() or "Unknown runtime error"
    error_text = error_text[:1000]
    fingerprint = hashlib.sha256(
        f"{task_name}|{error_text[:400]}".encode("utf-8", errors="replace")
    ).hexdigest()
    try:
        cooldown = max(
            0.0,
            float(os.getenv("RUNTIME_ERROR_NOTIFY_COOLDOWN_SECONDS", "900")),
        )
    except ValueError:
        cooldown = 900.0

    now = time.monotonic()
    with _RUNTIME_ERROR_LOCK:
        previous = _RUNTIME_ERROR_LAST_NOTIFIED.get(fingerprint)
        if previous is not None and now - previous < cooldown:
            return {"suppressed": True, "fingerprint": fingerprint}
        _RUNTIME_ERROR_LAST_NOTIFIED[fingerprint] = now

    detail_text = str(details or "").strip()
    message = "\n".join(
        part
        for part in (
            "宏观看板运行告警",
            f"任务: {task_name}",
            f"错误: {error_text}",
            f"处理: {detail_text}" if detail_text else "",
            f"相同错误 {int(cooldown)} 秒内不重复推送",
        )
        if part
    )
    try:
        from services.notifier import notify

        channels = parse_notify_channels()
        results = notify(
            message,
            channels=channels,
            title="宏观看板运行告警",
            level="error",
        )
        sent = any(bool(value) for value in results.values())
        if not sent:
            logger.warning(
                "Runtime error notification was not accepted by any channel: %s",
                channels,
            )
        return {
            "sent": sent,
            "channels": channels,
            "results": results,
            "fingerprint": fingerprint,
        }
    except Exception as exc:  # pragma: no cover - notification backends are external
        logger.exception("Runtime error notification failed: %s", exc)
        return {"error": str(exc), "fingerprint": fingerprint}


def run_with_retry(task_name, callback):
    """Run an idempotent task with retries and an elapsed-time watchdog."""
    import logging

    logger = logging.getLogger("runtime_controls")
    attempts = int(_task_setting("TASK", "RETRY_ATTEMPTS", 2)) + 1
    backoff = _task_setting("TASK", "RETRY_BASE_SECONDS", 5)
    timeout = _task_setting(task_name, "TIMEOUT_SECONDS", 900)
    last_error = None

    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        record_task_status(task_name, "running", attempt=attempt, max_attempts=attempts)
        try:
            result = callback()
            elapsed = time.monotonic() - started
            if elapsed > timeout:
                raise TaskTimeoutError(task_name, elapsed, timeout)
            record_task_status(task_name, "success", attempt=attempt, duration_seconds=round(elapsed, 2))
            return result
        except Exception as exc:
            elapsed = time.monotonic() - started
            last_error = exc
            record_task_status(
                task_name,
                "retrying" if attempt < attempts else "failed",
                attempt=attempt,
                max_attempts=attempts,
                duration_seconds=round(elapsed, 2),
                error=str(exc),
            )
            if attempt >= attempts:
                logger.error("Task %s failed after %s attempts: %s", task_name, attempt, exc)
                notify_task_failure(task_name, exc)
                raise
            delay = backoff * (2 ** (attempt - 1))
            logger.warning(
                "Task %s failed on attempt %s/%s; retrying in %.1fs: %s",
                task_name,
                attempt,
                attempts,
                delay,
                exc,
            )
            time.sleep(delay)

    raise last_error  # pragma: no cover


def consume_rate_limit(operation):
    """Consume an in-process API cooldown slot or raise with retry timing."""
    cooldown = _cooldown_seconds(operation)
    now = time.monotonic()
    with _RATE_LOCK:
        previous = _LAST_CALLS.get(operation)
        if previous is not None:
            remaining = cooldown - (now - previous)
            if remaining > 0:
                raise RateLimitExceeded(operation, remaining)
        _LAST_CALLS[operation] = now


def reset_rate_limits():
    """Clear in-memory rate-limit state; useful for tests and controlled restarts."""
    with _RATE_LOCK:
        _LAST_CALLS.clear()
