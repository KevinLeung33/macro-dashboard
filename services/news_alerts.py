"""High-impact news alerts with event-level deduplication."""
import json
import logging
import os
from datetime import datetime, timedelta

from db.repository import (
    claim_news_cluster_alert,
    finish_news_cluster_alert,
    get_runtime_setting,
    query_cluster_articles,
    query_news_clusters,
    set_runtime_setting,
    claim_newsflash_alert,
    finish_newsflash_alert,
    query_recent_newsflash,
)
from services.runtime_controls import parse_notify_channels
from services.time_utils import app_now, app_timezone


logger = logging.getLogger("news_alerts")
SETTINGS_KEY = "news_alert_config"
EVENT_TYPES = ["fed_policy", "inflation", "growth", "employment", "geopolitics", "china_macro", "crypto", "energy", "credit", "liquidity"]
ASSETS = ["BTC", "ETH", "MSTR", "NASDAQ", "SP500", "DXY", "Gold", "Oil", "CNH", "HSTECH"]
CHANNELS = ["lark", "telegram", "email", "webhook"]


def _enabled():
    return get_news_alert_config()["enabled"]


def _csv_setting(name):
    return {item.strip().lower() for item in os.getenv(name, "").split(",") if item.strip()}


def _env_bool(name, default=True):
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes")


def default_news_alert_config():
    return {
        "enabled": _env_bool("NEWS_ALERT_ENABLED", True),
        "min_severity": int(os.getenv("NEWS_ALERT_MIN_SEVERITY", "4")),
        "min_confidence": float(os.getenv("NEWS_ALERT_MIN_CONFIDENCE", "0.75")),
        "min_articles": int(os.getenv("NEWS_ALERT_MIN_ARTICLES", "1")),
        "max_age_minutes": int(os.getenv("NEWS_ALERT_MAX_AGE_MINUTES", "90")),
        "event_types": sorted(_csv_setting("NEWS_ALERT_EVENT_TYPES")),
        "assets": sorted(_csv_setting("NEWS_ALERT_ASSETS")),
        "channels": parse_notify_channels(),
    }


def get_news_alert_config():
    defaults = default_news_alert_config()
    saved = get_runtime_setting(SETTINGS_KEY, {})
    if not isinstance(saved, dict):
        return defaults
    config = {**defaults, **saved}
    config["enabled"] = bool(config["enabled"])
    config["min_severity"] = max(1, min(5, int(config["min_severity"])))
    config["min_confidence"] = max(0.0, min(1.0, float(config["min_confidence"])))
    config["min_articles"] = max(1, int(config["min_articles"]))
    config["max_age_minutes"] = max(1, int(config["max_age_minutes"]))
    config["event_types"] = [item for item in config.get("event_types", []) if item in EVENT_TYPES]
    config["assets"] = [item for item in config.get("assets", []) if item in ASSETS]
    config["channels"] = [item for item in config.get("channels", []) if item in CHANNELS]
    return config


def ensure_news_alert_config():
    """Persist the initial defaults once so the UI always edits a stored rule set."""
    saved = get_runtime_setting(SETTINGS_KEY, None)
    if isinstance(saved, dict):
        return get_news_alert_config()
    defaults = default_news_alert_config()
    set_runtime_setting(SETTINGS_KEY, defaults)
    return defaults


def save_news_alert_config(config):
    normalized = get_news_alert_config()
    normalized.update(config)
    normalized["event_types"] = [item for item in normalized.get("event_types", []) if item in EVENT_TYPES]
    normalized["assets"] = [item for item in normalized.get("assets", []) if item in ASSETS]
    normalized["channels"] = [item for item in normalized.get("channels", []) if item in CHANNELS]
    set_runtime_setting(SETTINGS_KEY, normalized)
    return get_news_alert_config()


def _as_time(value):
    if not value:
        return None
    text = str(value).replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=app_timezone())
        except ValueError:
            continue
    return None


def _is_eligible(cluster, now, config):
    try:
        severity = int(cluster["severity"] or 0)
        confidence = float(cluster["confidence"] or 0)
    except (TypeError, ValueError):
        return False
    if severity < config["min_severity"]:
        return False
    if confidence < config["min_confidence"]:
        return False
    if int(cluster["article_count"] or 0) < config["min_articles"]:
        return False

    max_age = timedelta(minutes=config["max_age_minutes"])
    first_seen = _as_time(cluster["first_seen_at"])
    if first_seen is None or first_seen < now - max_age or first_seen > now + timedelta(minutes=5):
        return False

    allowed_types = {item.lower() for item in config["event_types"]}
    if allowed_types and str(cluster["event_type"] or "").lower() not in allowed_types:
        return False
    allowed_assets = {item.lower() for item in config["assets"]}
    cluster_assets = _csv_setting_from_value(cluster["assets_impacted"])
    return not allowed_assets or bool(allowed_assets & cluster_assets)


def _csv_setting_from_value(value):
    return {item.strip().lower() for item in str(value or "").split(",") if item.strip()}


def _has_new_information(cluster_id):
    return any(bool(row["is_new_information"]) for row in query_cluster_articles(cluster_id))


def _message(cluster):
    assets = cluster["assets_impacted"] or "待确认"
    direction = cluster["direction"] or "待确认"
    return (
        "**高影响新闻事件**\n"
        f"**事件：** {cluster['event_type']} · 严重度 {cluster['severity']}/5 · 置信度 {float(cluster['confidence'] or 0):.0%}\n"
        f"**摘要：** {cluster['title']}\n"
        f"**影响资产：** {assets}\n"
        f"**方向：** {direction}\n"
        f"**为什么重要：** {cluster['summary'] or '请打开看板核验关联指标与原始新闻。'}\n"
        f"**来源：** {cluster['primary_source'] or '—'} · {cluster['article_count']} 篇"
    )


def dispatch_urgent_news_alerts(limit=20):
    """Deliver one alert per qualifying, newly emerged event cluster."""
    config = get_news_alert_config()
    if not config["enabled"]:
        return {"sent": 0, "skipped": 0, "failed": 0, "reason": "disabled"}

    now = app_now()
    sent = skipped = failed = 0
    clusters = query_news_clusters(limit=limit, min_severity=1)
    for cluster in clusters:
        if not _is_eligible(cluster, now, config) or not _has_new_information(cluster["id"]):
            skipped += 1
            continue
        if not claim_news_cluster_alert(cluster["id"], cluster["severity"]):
            skipped += 1
            continue
        try:
            from services.notifier import notify

            results = notify(
                _message(cluster),
                config["channels"],
                title="紧急宏观新闻",
                level="error",
            )
            if any(results.values()):
                finish_news_cluster_alert(cluster["id"], sent=True)
                sent += 1
            else:
                finish_news_cluster_alert(cluster["id"], sent=False, error_message="No notification channel accepted the alert")
                failed += 1
        except Exception as exc:
            logger.warning("Urgent news alert failed for cluster=%s: %s", cluster["id"], exc)
            finish_news_cluster_alert(cluster["id"], sent=False, error_message=exc)
            failed += 1
    return {"sent": sent, "skipped": skipped, "failed": failed}


FLASH_ALERT_KEYWORDS = (
    "暂停提现", "暂停提币", "停止提现", "停止提币", "被盗", "黑客攻击",
    "安全事件", "稳定币脱锚", "脱锚", "破产", "清算", "ETF获批", "现货ETF",
    "withdrawal suspended", "hacked", "exploit", "depeg", "bankrupt",
)


def dispatch_flash_rule_alerts(limit=10):
    """Send conservative early alerts before the hourly AI/event pipeline.

    This is deliberately a discovery alert, not a trading conclusion. Only
    strict high-impact phrases are allowed; the article remains pending for
    normal AI analysis and cross-source confirmation.
    """
    if not _enabled():
        return {"sent": 0, "skipped": 0, "failed": 0, "reason": "disabled"}
    config = get_news_alert_config()
    terms = tuple(item.lower() for item in FLASH_ALERT_KEYWORDS)
    sent = skipped = failed = 0
    for row in query_recent_newsflash(limit=limit, minutes=180):
        text = f"{row['title']} {row['summary']}".lower()
        matched = next((term for term in terms if term in text), None)
        if not matched or not claim_newsflash_alert(row["id"]):
            skipped += 1
            continue
        message = (
            "**Crypto重要快讯（待确认）**\n"
            f"**来源：** {row['source']}\n"
            f"**标题：** {row['title']}\n"
            f"**触发：** `{matched}`\n"
            "**说明：** 这是快速事件发现提醒，尚未完成多源交叉验证和完整AI分析。\n"
            f"**原文：** {row['url'] or '请打开新闻雷达查看'}"
        )
        try:
            from services.notifier import notify
            results = notify(message, config["channels"], title="Crypto重要快讯", level="warning")
            if any(results.values()):
                finish_newsflash_alert(row["id"], sent=True)
                sent += 1
            else:
                finish_newsflash_alert(row["id"], sent=False, error_message="No notification channel accepted the alert")
                failed += 1
        except Exception as exc:
            logger.warning("Flash rule alert failed for article=%s: %s", row["id"], exc)
            finish_newsflash_alert(row["id"], sent=False, error_message=exc)
            failed += 1
    return {"sent": sent, "skipped": skipped, "failed": failed}
