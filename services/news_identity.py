"""Stable identities for RSS articles and event-clustering evidence.

The raw URL is useful provenance, but it is not a reliable identity: RSS feeds
often add tracking parameters, and the same article can be exposed through a
canonical URL and a redirected URL.  These helpers are deliberately
deterministic and do not call an external service, so they are safe to use in
the ingestion and scheduled-clustering paths.
"""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "mc_cid", "mc_eid",
    "ocid", "output", "ref", "referrer", "source", "src",
}
_TRACKING_QUERY_PREFIXES = ("utm_", "mc_")


def canonicalize_url(value) -> str:
    """Return a conservative canonical form of a public article URL.

    Only known tracking parameters and fragments are removed.  Editorial query
    parameters are retained, avoiding accidental conflation of distinct pages.
    """
    raw = html.unescape(str(value or "").strip())
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw.split("#", 1)[0]
    if not parsed.scheme or not parsed.netloc:
        return raw.split("#", 1)[0]

    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_KEYS
        and not key.lower().startswith(_TRACKING_QUERY_PREFIXES)
    ]
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        path,
        urlencode(sorted(query)),
        "",
    ))


def normalize_title(value) -> str:
    """Normalize a headline without stripping its economically meaningful words."""
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_fingerprint(value) -> str:
    """A compact stable hash for exact/near-exact headline identity checks."""
    normalized = normalize_title(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32] if normalized else ""


def article_hash(url, title) -> str:
    """Hash the canonical URL when present, otherwise use the title identity."""
    canonical_url = canonicalize_url(url)
    identity = f"url:{canonical_url}" if canonical_url else f"title:{title_fingerprint(title)}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
