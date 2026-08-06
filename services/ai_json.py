"""Robust helpers for parsing JSON returned by chat models."""
import os
import json
import re


class AIResponseError(ValueError):
    """Raised when a model response cannot be safely consumed."""


class AIResponseTruncated(AIResponseError):
    """Raised when the provider stopped before returning a complete answer."""


def ai_thinking_options(model=None, base_url=None):
    """Return DeepSeek thinking-mode options for structured, short outputs.

    Thinking mode is useful for difficult analysis, but it is counterproductive
    for small JSON payloads: the output budget can be consumed before the final
    ``content`` field is produced.  Only add the provider-specific option for
    DeepSeek requests so OpenAI-compatible providers are not affected.
    """
    provider_text = f"{model or ''} {base_url or ''}".lower()
    if "deepseek" not in provider_text:
        return {}

    mode = os.getenv("AI_THINKING_MODE", "disabled").strip().lower()
    if mode in {"disabled", "disable", "off", "false", "none"}:
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    if mode in {"enabled", "enable", "on", "true"}:
        return {"extra_body": {"thinking": {"type": "enabled"}}}
    return {}


def _usage_summary(response):
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    fields = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "reasoning_tokens",
    )
    result = {}
    for field in fields:
        value = getattr(usage, field, None)
        if value is not None:
            result[field] = value
    return result


def extract_response_content(response):
    """Extract final text and metadata, rejecting incomplete responses early."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise AIResponseError("AI response has no choices")

    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
            else:
                parts.append(str(getattr(part, "text", "") or ""))
        content = "".join(parts)

    usage = _usage_summary(response)
    metadata = {
        "finish_reason": finish_reason,
        "usage": usage,
        "content_chars": len(str(content or "")),
        "reasoning_chars": len(str(getattr(message, "reasoning_content", "") or "")),
    }

    if finish_reason == "length":
        raise AIResponseTruncated(f"AI response truncated: {metadata}")
    if finish_reason and finish_reason not in {"stop"}:
        raise AIResponseError(f"AI response stopped with {finish_reason}: {metadata}")
    if not content or not str(content).strip():
        raise AIResponseError(f"empty AI response: {metadata}")
    return str(content), metadata


def parse_ai_json(text):
    """Parse JSON from model text, tolerating code fences and surrounding prose."""
    if not text:
        raise ValueError("empty AI response")

    raw = str(text).strip()
    candidates = [raw]

    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        candidates.insert(0, fence.group(1).strip())

    obj = _extract_balanced(raw, "{", "}")
    if obj:
        candidates.append(obj)

    arr = _extract_balanced(raw, "[", "]")
    if arr:
        candidates.append(arr)

    last_error = None
    for candidate in candidates:
        candidate = _normalize_jsonish(candidate)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue

    raise last_error or ValueError("AI response is not JSON")


def _normalize_jsonish(text):
    out = str(text).strip()
    out = out.replace("\ufeff", "")
    out = re.sub(r",\s*([}\]])", r"\1", out)
    return out


def _extract_balanced(text, open_char, close_char):
    start = text.find(open_char)
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None
