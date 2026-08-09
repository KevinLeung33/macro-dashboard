"""Small, dependency-free configuration helpers for AI shadow trading."""
import os


def env_float(name, default, minimum=None, maximum=None):
    try:
        value = float(os.getenv(name, default))
    except (TypeError, ValueError):
        value = float(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_int(name, default, minimum=None, maximum=None):
    try:
        value = int(float(os.getenv(name, default)))
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def shadow_constraints():
    """Centralise virtual-account limits used by generation and simulation."""
    max_risk_pct = env_float("AI_SHADOW_MAX_RISK_PCT", "0.01", 0.0001, 0.10)
    analysis_timeframe = os.getenv("AI_SHADOW_ANALYSIS_TIMEFRAME", "1H").strip() or "1H"
    if analysis_timeframe not in {"5m", "15m", "1H", "4H", "1D"}:
        analysis_timeframe = "1H"
    return {
        "virtual_equity_usd": env_float("AI_SHADOW_ACCOUNT_EQUITY_USD", "10000", 100, 10_000_000),
        "max_risk_pct": max_risk_pct,
        "min_risk_pct": env_float("AI_SHADOW_MIN_RISK_PCT", "0.0025", 0.0001, max_risk_pct),
        "max_notional_usd": env_float("AI_SHADOW_MAX_NOTIONAL_USD", "2500", 10, 100_000_000),
        "min_risk_reward": env_float("AI_SHADOW_MIN_RR", "1.5", 0.1, 20),
        "default_expiry_hours": env_int("AI_SHADOW_DEFAULT_EXPIRY_HOURS", "24", 1, 24 * 30),
        "default_time_stop_hours": env_int("AI_SHADOW_DEFAULT_TIME_STOP_HOURS", "72", 1, 24 * 90),
        "max_expiry_hours": env_int("AI_SHADOW_MAX_EXPIRY_HOURS", "720", 1, 24 * 90),
        "max_time_stop_hours": env_int("AI_SHADOW_MAX_TIME_STOP_HOURS", "2160", 1, 24 * 180),
        "fee_bps": env_float("AI_SHADOW_FEE_BPS", "5", 0, 500),
        "slippage_bps": env_float("AI_SHADOW_SLIPPAGE_BPS", "2", 0, 500),
        "analysis_timeframe": analysis_timeframe,
    }
