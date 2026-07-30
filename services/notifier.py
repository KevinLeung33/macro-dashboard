"""通知推送器 — Telegram / 飞书 / 邮件 / Webhook"""
import base64
import hashlib
import hmac
import logging
import os
import time

import requests

logger = logging.getLogger("notifier")


def send_telegram(message, bot_token=None, chat_id=None):
    """通过Telegram Bot发送消息"""
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    cid = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not cid:
        logger.info("Telegram not configured, skip")
        return False

    try:
        # Split long messages
        for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": cid, "text": chunk, "parse_mode": "Markdown"},
                timeout=15,
            )
            resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False


def send_email(message, subject="宏观日报", smtp_config=None):
    """通过SMTP发送邮件"""
    smtp_host = os.getenv("SMTP_HOST", smtp_config.get("host") if smtp_config else None)
    smtp_port = int(os.getenv("SMTP_PORT", smtp_config.get("port", 587) if smtp_config else 587))
    smtp_user = os.getenv("SMTP_USER", smtp_config.get("user") if smtp_config else None)
    smtp_pass = os.getenv("SMTP_PASS", smtp_config.get("pass") if smtp_config else None)
    to_email = os.getenv("NOTIFY_EMAIL", smtp_config.get("to") if smtp_config else None)

    if not all([smtp_host, smtp_user, smtp_pass, to_email]):
        logger.info("Email not configured, skip")
        return False

    try:
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(message, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = to_email

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.warning(f"Email send failed: {e}")
        return False


def send_webhook(message, url=None):
    """通过Webhook推送（企业微信/钉钉/飞书/Slack）"""
    hook_url = url or os.getenv("WEBHOOK_URL")
    if not hook_url:
        return False

    try:
        resp = requests.post(
            hook_url,
            json={"text": message, "msgtype": "text"},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"Webhook failed: {e}")
        return False


def _lark_sign(timestamp, secret):
    """Generate the optional Feishu/Lark incoming-webhook signature."""
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_card(message, title=None, level=None, webhook_url=None, secret=None):
    """Send a readable daily report or failure alert through a Lark group bot."""
    hook_url = webhook_url or os.getenv("LARK_WEBHOOK_URL")
    if not hook_url:
        logger.info("Lark webhook is not configured, skip")
        return False

    is_failure = level == "error" or str(message).startswith("宏观看板任务失败")
    title = title or ("宏观看板任务告警" if is_failure else "宏观看板今日内容")
    dashboard_url = os.getenv("DASHBOARD_PUBLIC_URL", "").strip()
    elements = [{"tag": "markdown", "content": str(message)[:12000]}]
    if dashboard_url:
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": "打开宏观看板"},
                "type": "danger" if is_failure else "primary",
                "url": dashboard_url,
            }],
        })

    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "red" if is_failure else "blue",
            },
            "elements": elements,
        },
    }
    signing_secret = secret if secret is not None else os.getenv("LARK_WEBHOOK_SECRET", "")
    if signing_secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = _lark_sign(timestamp, signing_secret)

    try:
        response = requests.post(hook_url, json=payload, timeout=15)
        response.raise_for_status()
        response_data = response.json()
        if response_data.get("code", 0) != 0:
            logger.warning("Lark webhook rejected message: %s", response_data)
            return False
        return True
    except Exception as exc:
        logger.warning("Lark send failed: %s", exc)
        return False


def notify(message, channels=None, title=None, level=None):
    """多渠道推送"""
    if channels is None:
        channels = ["telegram"]

    results = {}
    if "telegram" in channels:
        results["telegram"] = send_telegram(message)
    if "email" in channels:
        results["email"] = send_email(message)
    if "lark" in channels:
        results["lark"] = send_lark_card(message, title=title, level=level)
    if "webhook" in channels:
        results["webhook"] = send_webhook(message)

    return results
