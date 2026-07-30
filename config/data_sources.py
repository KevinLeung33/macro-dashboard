"""Data source registry for update policy, fallback order and documentation."""

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
        "enabled": True,
        "priority": 40,
        "refresh": "daily",
        "incremental": True,
        "description": "Equity fallback for MSTR/NVDA/MU when API key is configured.",
    },
    "stooq": {
        "label": "Stooq",
        "enabled": True,
        "priority": 50,
        "refresh": "daily",
        "incremental": True,
        "description": "No-key market fallback. Some environments may receive anti-bot HTML.",
    },
    "yfinance": {
        "label": "yfinance",
        "enabled": True,
        "priority": 60,
        "refresh": "daily",
        "incremental": True,
        "description": "Market fallback through Yahoo Finance.",
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
        "description": "RSS and optional Alpha Vantage news, followed by AI analysis and clustering.",
    },
}


def source_config(source):
    return DATA_SOURCES.get(source, {})


def source_enabled(source, override=None):
    if override is not None:
        return bool(override)
    return bool(source_config(source).get("enabled", True))


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
