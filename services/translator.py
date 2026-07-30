"""翻译服务 — 腾讯云机器翻译 + SQLite缓存"""
import hashlib
import logging
import os
import time

logger = logging.getLogger("translator")

# Rate limiting: max 5 req/s — 保守设为 3 次/秒
_last_call = 0
_min_interval = 0.35  # ~3 req/s, safe margin


def _rate_limit():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < _min_interval:
        time.sleep(_min_interval - elapsed)
    _last_call = time.time()


def _hash_text(text):
    return hashlib.md5(text.encode()).hexdigest()[:16]


def translate(text, source="en", target="zh", secret_id=None, secret_key=None, region="ap-guangzhou"):
    """翻译文本，带缓存 + 限流重试"""
    sid = secret_id or os.getenv("TENCENTCLOUD_SECRET_ID")
    skey = secret_key or os.getenv("TENCENTCLOUD_SECRET_KEY")
    if not sid or not skey:
        return text

    h = _hash_text(text)

    # Check cache
    from db.schema import get_db
    with get_db() as conn:
        cached = conn.execute(
            "SELECT translated_text FROM translations WHERE text_hash = ? AND source_lang = ? AND target_lang = ?",
            (h, source, target),
        ).fetchone()
        if cached:
            return cached["translated_text"]

    # Call API (retry on rate limit)
    for attempt in range(3):
        _rate_limit()
        try:
            from tencentcloud.common import credential
            from tencentcloud.common.profile.client_profile import ClientProfile
            from tencentcloud.common.profile.http_profile import HttpProfile
            from tencentcloud.tmt.v20180321 import tmt_client, models

            cred = credential.Credential(sid, skey)
            hp = HttpProfile()
            hp.endpoint = "tmt.tencentcloudapi.com"
            cp = ClientProfile()
            cp.httpProfile = hp
            client = tmt_client.TmtClient(cred, region, cp)

            req = models.TextTranslateRequest()
            req.SourceText = text
            req.Source = source
            req.Target = target
            req.ProjectId = 0

            resp = client.TextTranslate(req)
            result = resp.TargetText

            # Cache result
            with get_db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO translations (text_hash, source_text, translated_text, source_lang, target_lang) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (h, text, result, source, target),
                )
            return result

        except Exception as e:
            err_msg = str(e)
            if "RequestLimitExceeded" in err_msg and attempt < 2:
                time.sleep(1.5)
                continue
            logger.warning(f"Translation failed: {e}")
            return text


def translate_title(title):
    """翻译新闻标题（英→中），太长则截断"""
    if not title:
        return title
    # Already Chinese, skip
    chinese_chars = sum(1 for c in title if '\u4e00' <= c <= '\u9fff')
    if chinese_chars > len(title) * 0.3:
        return title
    return translate(title[:200])
