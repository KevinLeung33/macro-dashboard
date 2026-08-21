"""cpolar/看板外部可用性检查。

只检查 HTTP 可达性，不调用 cpolar 的管理接口，也不保存 cpolar token。
公网 URL 是识别“8501 正常但 cpolar 隧道 inactive”的关键；未配置时只做
本机 Streamlit 检查，并明确记录无法验证公网隧道。
"""
import logging
import os
import threading
import json
import time
from pathlib import Path

import requests

from services.notifier import notify
from services.runtime_controls import parse_notify_channels

logger = logging.getLogger("cpolar_monitor")
_STATE_LOCK = threading.Lock()
_LAST_STATE = None


def _state_path():
    return Path(os.getenv("RUNTIME_LOCK_DIR", "runtime/locks")).parent / "cpolar_health.json"


def _threshold(name, default):
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _read_state():
    try:
        payload = json.loads(_state_path().read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_state(payload):
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        logger.debug("Could not persist cpolar health state", exc_info=True)


def _enabled():
    return os.getenv("CPOLAR_HEALTH_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def _timeout():
    try:
        return max(3.0, float(os.getenv("CPOLAR_HEALTH_TIMEOUT_SECONDS", "15")))
    except ValueError:
        return 15.0


def _check_url(label, url):
    if not url:
        return {"label": label, "configured": False, "ok": None, "error": "未配置"}

    try:
        response = requests.get(
            url,
            headers={"User-Agent": "macro-dashboard/cpolar-monitor"},
            timeout=_timeout(),
            allow_redirects=True,
        )
        body = (response.text or "")[:8000].lower()
        error_markers = (
            "tunnel inactive",
            "tunnel not found",
            "no tunnel",
            "tunnel unavailable",
            "502 bad gateway",
        )
        marker = next((item for item in error_markers if item in body), "")
        ok = 200 <= response.status_code < 400 and not marker
        error = "" if ok else (marker or f"HTTP {response.status_code}")
        return {
            "label": label,
            "configured": True,
            "ok": ok,
            "status_code": response.status_code,
            "final_url": response.url,
            "error": error,
        }
    except Exception as exc:
        return {"label": label, "configured": True, "ok": False, "error": str(exc)[:300]}


def _notify_transition(state, checks):
    failed = [item for item in checks if item.get("configured") and item.get("ok") is False]
    if state == "ok":
        message = "\n".join([
            "宏观看板网络恢复",
            "cpolar 公网隧道和本机 Streamlit 检查已恢复正常。",
        ])
        notify(message, channels=parse_notify_channels(), title="宏观看板网络恢复", level="info")
        return

    lines = [
        "宏观看板运行告警",
        "cpolar/看板健康检查失败，公网页面可能无法访问。",
    ]
    for item in failed:
        lines.append(f"{item['label']}: {item.get('error') or '检查失败'}")
    lines.append("请检查 cpolar 隧道状态、/usr/local/etc/cpolar/cpolar.yml 和 8501 端口。")
    notify(
        "\n".join(lines),
        channels=parse_notify_channels(),
        title="cpolar 隧道告警",
        level="error",
    )


def check_cpolar(notify_on_transition=True):
    """检查公网入口；连续失败/恢复达到阈值才改变有效状态。"""
    global _LAST_STATE
    if not _enabled():
        logger.info("cpolar health check disabled")
        return {"enabled": False}

    local_url = os.getenv("CPOLAR_LOCAL_URL", "http://127.0.0.1:8501").strip()
    public_url = os.getenv("CPOLAR_PUBLIC_URL", "").strip()
    checks = [_check_url("本机 Streamlit", local_url)]
    if public_url:
        checks.append(_check_url("cpolar 公网隧道", public_url))
    else:
        logger.warning("CPOLAR_PUBLIC_URL is not configured; only local 8501 is monitored")

    configured_checks = [item for item in checks if item.get("configured")]
    raw_state = "ok" if configured_checks and all(item.get("ok") for item in configured_checks) else "failed"
    with _STATE_LOCK:
        persisted = _read_state()
        previous = persisted.get("state") or _LAST_STATE
        failures = int(persisted.get("consecutive_failures") or 0)
        successes = int(persisted.get("consecutive_successes") or 0)
        if raw_state == "failed":
            failures += 1
            successes = 0
            if previous not in {"failed"} and failures < _threshold("CPOLAR_FAILURE_THRESHOLD", 3):
                state = previous or "pending_failure"
            else:
                state = "failed"
        else:
            successes += 1
            failures = 0
            if previous == "failed" and successes < _threshold("CPOLAR_RECOVERY_THRESHOLD", 2):
                state = "failed"
            else:
                state = "ok"
        if notify_on_transition and state != previous and state in {"ok", "failed"}:
            _notify_transition(state, checks)
        _LAST_STATE = state
        _write_state({
            "state": state,
            "raw_state": raw_state,
            "consecutive_failures": failures,
            "consecutive_successes": successes,
            "updated_at": time.time(),
        })

    logger.info(
        "cpolar health state=%s raw_state=%s failures=%s successes=%s checks=%s",
        state,
        raw_state,
        failures,
        successes,
        "; ".join(f"{item['label']}={item.get('error') or 'ok'}" for item in checks),
    )
    return {
        "enabled": True,
        "state": state,
        "raw_state": raw_state,
        "consecutive_failures": failures,
        "consecutive_successes": successes,
        "checks": checks,
    }
