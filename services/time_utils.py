"""Application time helpers with one explicit timezone setting."""
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "Asia/Shanghai"


def timezone_name():
    return os.getenv("SCHEDULER_TIMEZONE", DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE


def app_timezone():
    name = timezone_name()
    # These zones have no DST and can work even when Windows has no tzdata package.
    if name == "UTC":
        return timezone.utc
    if name == "Asia/Shanghai":
        return timezone(timedelta(hours=8), name="CST")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"Invalid SCHEDULER_TIMEZONE={name!r}; use an IANA timezone such as Asia/Shanghai"
        ) from exc


def app_now():
    return datetime.now(app_timezone())
