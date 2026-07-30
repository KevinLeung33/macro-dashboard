"""Conversions for values crossing the pandas-to-sqlite boundary."""
import pandas as pd


def sqlite_date(value):
    """Return a date-like value as ISO text accepted by sqlite3."""
    if value is None:
        return None
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        return None
