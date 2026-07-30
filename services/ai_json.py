"""Robust helpers for parsing JSON returned by chat models."""
import json
import re


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
