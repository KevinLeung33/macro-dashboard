"""Data source registry for update policy, fallback order and documentation."""
import os

DATA_SOURCES = {
    "fred": {
        "label": "FRED",
        "enabled": True,
        "priority": 10,
        "refresh": "daily",
        "incremental": True,
        "description": "US macro, market and credit time series.",
    },
    "akshare": {
        "label": "AKShare",
        "enabled": True,
        "priority": 20,
        "refresh": "daily",
        "incremental": "filter_after_fetch",
        "description": "China macro data. Fetchers may return full tables, then store only new rows.",
    },
    "akshare_hk_index": {
        "label": "AKShare Hong Kong index",
        "enabled": True,
        "priority": 21,
        "refresh": "daily",
        "incremental": "filter_after_fetch",
        "description": "Exact Hang Seng TECH Index daily history through AKShare's Sina adapter.",
    },
    "tic": {
        "label": "US Treasury TIC",
        "enabled": True,
        "priority": 30,
        "refresh": "monthly",
        "incremental": False,
        "description": "Foreign holdings of US Treasuries.",
    },
    "alpha_vantage": {
        "label": "Alpha Vantage",
        "enabled": False,
        "priority": 40,
        "refresh": "daily",
        "incremental": True,
        "description": "Optional equity fallback; disabled by default because free-tier limits are unsuitable for scheduled refreshes.",
    },
    "stooq": {
        "label": "Stooq",
        "enabled": False,
        "priority": 50,
        "refresh": "daily",
        "incremental": True,
        "description": "Optional no-key market fallback; disabled by default when the endpoint returns empty/anti-bot responses.",
    },
    "yfinance": {
        "label": "yfinance",
        "enabled": True,
        "priority": 60,
        "refresh": "daily",
        "incremental": True,
        "description": "Market fallback through Yahoo Finance.",
    },
    "binance_spot": {
        "label": "Binance spot",
        "enabled": True,
        "priority": 65,
        "refresh": "daily",
        "incremental": True,
        "description": "BTC/ETH daily spot candles from Binance public klines.",
    },
    "crypto_liquidity": {
        "label": "Crypto liquidity",
        "enabled": True,
        "priority": 70,
        "refresh": "daily",
        "incremental": "filter_after_fetch",
        "description": "DefiLlama stablecoins plus Kraken/Coinbase ETHBTC fallback.",
    },
    "crypto_market": {
        "label": "Crypto derivatives",
        "enabled": True,
        "priority": 75,
        "refresh": "hourly",
        "incremental": True,
        "description": "Binance public funding rate and open interest history.",
    },
    "crypto_flows": {
        "label": "Crypto fund flows",
        "enabled": True,
        "optional": True,
        "required_env_any": ["BTC_ETF_FLOWS_URL", "BTC_EXCHANGE_NETFLOW_URL"],
        "priority": 76,
        "refresh": "daily",
        "incremental": True,
        "description": "Optional configured CSV/JSON adapters for BTC ETF and exchange netflow.",
    },
    "news": {
        "label": "News",
        "enabled": True,
        "priority": 80,
        "refresh": "hourly",
        "incremental": True,
        "description": "Official/media RSS (including BLS, SEC, EIA and Wu Blockchain) plus optional Alpha Vantage; RSS is refreshed faster and AI analysis runs separately.",
    },
}


def source_config(source):
    return DATA_SOURCES.get(source, {})


def source_enabled(source, override=None):
    if not bool(source_config(source).get("enabled", True)):
        return False
    if override is not None:
        return bool(override)
    return True


def source_is_configured(source):
    """Whether an enabled optional source has the configuration it needs.

    Optional adapters may deliberately run alongside a core pipeline without a
    provider URL.  They should be shown as unavailable, not treated as a data
    outage by the health monitor.
    """
    config = source_config(source)
    required_any = config.get("required_env_any") or []
    if not required_any:
        return True
    return any(os.getenv(name, "").strip() for name in required_any)


def source_summary_rows():
    rows = []
    for source, cfg in sorted(DATA_SOURCES.items(), key=lambda item: item[1].get("priority", 999)):
        rows.append({
            "source": source,
            "label": cfg.get("label", source),
            "refresh": cfg.get("refresh", ""),
            "incremental": cfg.get("incremental", False),
            "description": cfg.get("description", ""),
        })
    return rows
