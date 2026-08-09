"""Deterministic, time-stamped context for a crypto trade plan.

The module gathers facts from the existing dashboard and public OKX candles.
It does not ask an LLM to invent market data, issue an order, or modify a
position. The returned object is stored with the plan so later reviews can use
only information that existed at planning time.
"""
import logging
import os
import re

from db.repository import query_analyzed_news
from services.asset_bias import build_asset_biases
from services.composite_signals import compute_composite_signals
from services.daily_context import get_data_health, get_market_moves
from services.news_clusterer import get_important_clusters
from services.okx_readonly import OKXReadOnlyClient
from services.time_utils import app_now

logger = logging.getLogger("trade_plan_context")
SNAPSHOT_VERSION = "trade-plan-context-v2"


def _asset_from_symbol(symbol):
    raw = str(symbol or "").upper().strip().replace("_", "-").replace("/", "-")
    if "-" in raw:
        return raw.split("-")[0] or "CRYPTO"
    value = re.sub(r"(?:SWAP|FUTURES|SPOT)$", "", re.sub(r"[^A-Z0-9]", "", raw))
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if value.endswith(quote) and len(value) > len(quote):
            return value[:-len(quote)]
    return value.split("SWAP")[0] or "CRYPTO"


def _okx_inst_id(symbol):
    raw = str(symbol or "").upper().strip().replace("_", "-").replace("/", "-")
    if not raw:
        return ""
    if raw.endswith(("-SWAP", "-FUTURES", "-SPOT")):
        return raw
    inst_type = os.getenv("OKX_INST_TYPE", "SWAP").upper().strip() or "SWAP"
    suffix = "-SWAP" if inst_type == "SWAP" else ""
    if "-" in raw:
        return raw + suffix
    for quote in ("USDT", "USDC", "USD"):
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[:-len(quote)]}-{quote}{suffix}"
    return raw


def _pct_change(values, bars):
    if len(values) <= bars or values[-bars - 1] in (None, 0):
        return None
    return round((values[-1] / values[-bars - 1] - 1) * 100, 4)


def _live_market_snapshot(symbol, analysis_timeframe):
    inst_id = _okx_inst_id(symbol)
    if not inst_id:
        return {"available": False, "reason": "交易对为空"}
    bar = analysis_timeframe if analysis_timeframe in {"5m", "15m", "1H", "4H", "1D"} else "1H"
    try:
        candles = OKXReadOnlyClient().fetch_candles(inst_id, bar=bar, limit=100)
        valid = [item for item in candles if item.get("close") is not None]
        if not valid:
            return {"available": False, "inst_id": inst_id, "bar": bar, "reason": "OKX 未返回有效 K 线"}
        closes = [float(item["close"]) for item in valid]
        window = valid[-min(24, len(valid)):]
        highs = [item.get("high") for item in window if item.get("high") is not None]
        lows = [item.get("low") for item in window if item.get("low") is not None]
        last = valid[-1]
        range_pct = None
        if highs and lows and last.get("close"):
            range_pct = round((max(highs) - min(lows)) / float(last["close"]) * 100, 4)
        return {
            "available": True,
            "provider": "OKX public market API",
            "inst_id": inst_id,
            "bar": bar,
            "captured_at": app_now().isoformat(),
            "last_candle": {
                key: last.get(key) for key in ("timestamp", "open", "high", "low", "close", "volume", "confirm")
            },
            "returns_pct": {
                "1_bar": _pct_change(closes, 1),
                "6_bars": _pct_change(closes, 6),
                "24_bars": _pct_change(closes, 24),
            },
            "recent_range_pct": range_pct,
            "candle_count": len(valid),
        }
    except Exception as exc:  # public data is optional; the plan remains recordable offline
        logger.warning("Could not fetch live OKX candles for %s: %s", inst_id, exc)
        return {
            "available": False,
            "provider": "OKX public market API",
            "inst_id": inst_id,
            "bar": bar,
            "reason": str(exc)[:300],
        }


def _compact_signal(signal):
    return {
        key: signal.get(key)
        for key in ("name", "category", "direction", "level", "score", "max_score", "summary", "assets", "watch_next")
    } | {
        "evidence": [
            {key: item.get(key) for key in ("label", "value", "score", "status", "detail")}
            for item in (signal.get("evidence") or [])[:8]
        ]
    }


def _compact_health(item):
    return {
        key: item.get(key)
        for key in ("source", "status", "age_hours", "latest_data_date", "latest_fetched_at", "last_error")
    }


def _compact_news(row):
    return {
        key: row.get(key)
        for key in (
            "published_at", "created_at", "source", "title", "summary_cn", "event_type",
            "assets_impacted", "direction", "severity", "confidence", "why_it_matters", "url",
        )
    }


def _compact_cluster(item):
    return {
        key: item.get(key)
        for key in (
            "title", "summary", "event_type", "assets_impacted", "direction", "severity",
            "confidence", "last_seen_at", "article_count", "primary_source",
        )
    }


def _compact_bias(item):
    if not item:
        return None
    return {
        key: item.get(key)
        for key in ("asset", "direction", "level", "score", "confidence", "focus", "window")
    } | {
        "drivers": [
            {
                key: driver.get(key)
                for key in ("name", "contribution", "summary")
            }
            for driver in (item.get("drivers") or [])[:4]
        ]
    }


def build_trade_plan_snapshot(plan):
    """Capture macro, news, data freshness and public-market facts for one plan.

    Exceptions from an optional upstream source are recorded in ``collection_errors``
    instead of preventing a user from saving the plan itself.
    """
    plan = dict(plan or {})
    symbol = str(plan.get("symbol") or "").strip().upper()
    asset = _asset_from_symbol(symbol)
    related_assets = list(dict.fromkeys([asset, "BTC"]))
    errors = []

    try:
        signals = compute_composite_signals(lookback=5)
        relevant_signals = [
            _compact_signal(signal) for signal in signals
            if asset in (signal.get("assets") or []) or "BTC" in (signal.get("assets") or [])
        ]
        biases = build_asset_biases(signals, limit=8)
        target_bias = next((item for item in biases if item.get("asset") == asset), None)
        crypto_proxy_bias = next((item for item in biases if item.get("asset") == "BTC"), None)
    except Exception as exc:
        logger.warning("Could not collect macro signals for trade plan: %s", exc)
        signals = []
        relevant_signals = []
        target_bias = None
        crypto_proxy_bias = None
        errors.append(f"宏观组合信号读取失败：{str(exc)[:240]}")

    try:
        market_moves = get_market_moves(lookback_points=5, limit=12)
    except Exception as exc:
        market_moves = []
        errors.append(f"市场指标读取失败：{str(exc)[:240]}")

    try:
        data_health = [_compact_health(item) for item in get_data_health()]
    except Exception as exc:
        data_health = []
        errors.append(f"数据新鲜度读取失败：{str(exc)[:240]}")

    try:
        news = [_compact_news(dict(row)) for row in query_analyzed_news(
            assets=related_assets, min_severity=2, limit=8, days=3,
        )]
    except Exception as exc:
        news = []
        errors.append(f"相关新闻读取失败：{str(exc)[:240]}")

    try:
        clusters = [
            _compact_cluster(item) for item in get_important_clusters(limit=12, min_severity=3)
            if any(token in str(item.get("assets_impacted") or "").upper() for token in related_assets)
        ][:6]
    except Exception as exc:
        clusters = []
        errors.append(f"新闻事件流读取失败：{str(exc)[:240]}")

    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "captured_at": app_now().isoformat(),
        "plan_identity": {
            "venue": plan.get("venue", ""),
            "symbol": symbol,
            "asset": asset,
            "side": plan.get("side", ""),
            "trade_type": plan.get("trade_type", ""),
            "expected_horizon": plan.get("expected_horizon", ""),
            "macro_horizon": plan.get("macro_horizon", ""),
            "analysis_timeframe": plan.get("analysis_timeframe", ""),
            "entry_order_type": plan.get("entry_order_type", ""),
            "entry_price": plan.get("entry_price"),
            "trigger_price": plan.get("trigger_price"),
            "planned_quantity": plan.get("planned_quantity"),
            "plan_status": plan.get("plan_status", ""),
            "order_id": plan.get("order_id", ""),
        },
        "live_market": _live_market_snapshot(symbol, plan.get("analysis_timeframe", "")),
        "macro": {
            "relevant_composite_signals": relevant_signals,
            "asset_bias": _compact_bias(target_bias or crypto_proxy_bias),
            "asset_bias_is_crypto_proxy": target_bias is None and crypto_proxy_bias is not None,
            "recent_market_moves": market_moves,
        },
        "news": {"articles": news, "important_clusters": clusters},
        "data_health": data_health,
        "collection_errors": errors,
    }
