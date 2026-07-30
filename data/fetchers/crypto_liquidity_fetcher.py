"""Crypto-native liquidity data with DefiLlama primary and CoinGecko fallback."""
from datetime import datetime
import time

import requests

from db.repository import log_fetch, query_series, upsert_series_meta, upsert_time_series
from data.incremental import filter_new_records, observation_start


DEFILLAMA_STABLE_URL = "https://stablecoins.llama.fi"
COINGECKO_URL = "https://api.coingecko.com/api/v3"
KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"
COINBASE_EXCHANGE_URL = "https://api.exchange.coinbase.com"
HEADERS = {"User-Agent": "macro-dashboard/1.0"}

SERIES_META = {
    "STABLE_TOTAL_MCAP": {
        "display_name": "稳定币总市值",
        "unit": "USD",
        "frequency": "daily",
        "category": "crypto_liquidity",
        "yaxis_label": "美元",
    },
    "USDT_MCAP": {
        "display_name": "USDT市值",
        "unit": "USD",
        "frequency": "daily",
        "category": "crypto_liquidity",
        "yaxis_label": "美元",
    },
    "USDC_MCAP": {
        "display_name": "USDC市值",
        "unit": "USD",
        "frequency": "daily",
        "category": "crypto_liquidity",
        "yaxis_label": "美元",
    },
    "STABLE_MAJOR_MCAP": {
        "display_name": "USDT+USDC市值",
        "unit": "USD",
        "frequency": "daily",
        "category": "crypto_liquidity",
        "yaxis_label": "美元",
    },
    "USDT_SHARE": {
        "display_name": "USDT在主流稳定币中的占比",
        "unit": "%",
        "frequency": "daily",
        "category": "crypto_liquidity",
        "yaxis_label": "%",
    },
    "USDC_SHARE": {
        "display_name": "USDC在主流稳定币中的占比",
        "unit": "%",
        "frequency": "daily",
        "category": "crypto_liquidity",
        "yaxis_label": "%",
    },
    "ETHBTC": {
        "display_name": "ETH/BTC",
        "unit": "ratio",
        "frequency": "daily",
        "category": "crypto_liquidity",
        "yaxis_label": "比值",
    },
}

COINGECKO_MARKET_CAP_COINS = {
    "USDT_MCAP": "tether",
    "USDC_MCAP": "usd-coin",
}

STABLE_SYMBOLS = {
    "USDT_MCAP": "USDT",
    "USDC_MCAP": "USDC",
}


def _request_json(url, params=None, retries=2, delay=3):
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
            if resp.status_code == 429 and attempt < retries:
                time.sleep(delay * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(delay * (attempt + 1))
    raise last_error


def _date_from_ts(value):
    ts = int(value)
    if ts > 10_000_000_000:
        ts = ts / 1000
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")


def _nested_usd_value(row):
    for key in ("totalCirculatingUSD", "totalCirculating", "totalBridgedToUSD"):
        val = row.get(key)
        if isinstance(val, dict):
            for subkey in ("peggedUSD", "usd", "USD"):
                if val.get(subkey) is not None:
                    return float(val[subkey])
        elif isinstance(val, (int, float)):
            return float(val)
    return None


def fetch_stable_total_defillama():
    data = _request_json(f"{DEFILLAMA_STABLE_URL}/stablecoincharts/all")
    records = []
    rows = data if isinstance(data, list) else data.get("peggedAssets", [])
    for row in rows:
        if not isinstance(row, dict) or row.get("date") is None:
            continue
        value = _nested_usd_value(row)
        if value is None:
            continue
        records.append({"date": _date_from_ts(row["date"]), "value": value})
    return _dedupe_records(records)


def _stablecoin_ids_defillama():
    data = _request_json(f"{DEFILLAMA_STABLE_URL}/stablecoins", params={"includePrices": "true"})
    assets = data.get("peggedAssets", data if isinstance(data, list) else [])
    ids = {}
    for item in assets:
        symbol = str(item.get("symbol") or "").upper()
        if symbol in ("USDT", "USDC") and item.get("id") is not None:
            ids[symbol] = str(item["id"])
    return ids


def fetch_stablecoin_market_cap_defillama(symbol):
    ids = _stablecoin_ids_defillama()
    stable_id = ids.get(symbol.upper())
    if not stable_id:
        raise ValueError(f"DefiLlama stablecoin id not found for {symbol}")
    data = _request_json(f"{DEFILLAMA_STABLE_URL}/stablecoin/{stable_id}")
    records = []
    for row in data.get("tokens", []):
        if not isinstance(row, dict) or row.get("date") is None:
            continue
        circulating = row.get("circulating") or {}
        value = circulating.get("peggedUSD") if isinstance(circulating, dict) else None
        if value is None:
            continue
        records.append({"date": _date_from_ts(row["date"]), "value": float(value)})
    return _dedupe_records(records)


def fetch_market_cap_coingecko(coin_id, days=1825):
    data = _request_json(
        f"{COINGECKO_URL}/coins/{coin_id}/market_chart",
        params={"vs_currency": "usd", "days": str(days)},
    )
    records = [
        {"date": _date_from_ts(item[0]), "value": float(item[1])}
        for item in data.get("market_caps", [])
        if len(item) >= 2 and item[1] is not None
    ]
    return _dedupe_records(records)


def fetch_price_coingecko(coin_id, days=1825):
    data = _request_json(
        f"{COINGECKO_URL}/coins/{coin_id}/market_chart",
        params={"vs_currency": "usd", "days": str(days)},
    )
    records = [
        {"date": _date_from_ts(item[0]), "value": float(item[1])}
        for item in data.get("prices", [])
        if len(item) >= 2 and item[1] is not None
    ]
    return _dedupe_records(records)


def fetch_ethbtc_kraken():
    data = _request_json(KRAKEN_URL, params={"pair": "ETHXBT", "interval": 1440})
    if data.get("error"):
        raise ValueError("; ".join(data["error"]))
    result = data.get("result") or {}
    rows = []
    for key, value in result.items():
        if key == "last":
            continue
        rows = value
        break
    records = [
        {"date": _date_from_ts(item[0]), "value": float(item[4])}
        for item in rows
        if len(item) >= 5 and item[4] is not None
    ]
    return _dedupe_records(records)


def fetch_ethbtc_coinbase():
    data = _request_json(
        f"{COINBASE_EXCHANGE_URL}/products/ETH-BTC/candles",
        params={"granularity": 86400},
    )
    records = [
        {"date": _date_from_ts(item[0]), "value": float(item[4])}
        for item in data
        if len(item) >= 5 and item[4] is not None
    ]
    return _dedupe_records(records)


def _dedupe_records(records):
    by_date = {}
    for record in records:
        by_date[record["date"]] = record
    return [by_date[d] for d in sorted(by_date)]


def _ratio_records(numerator, denominator):
    den = {r["date"]: r["value"] for r in denominator if r.get("value")}
    out = []
    for row in numerator:
        base = den.get(row["date"])
        if base:
            out.append({"date": row["date"], "value": row["value"] / base})
    return out


def _local_price_records(source, series_id):
    df = query_series(source, series_id)
    if df.empty:
        return []
    return [
        {"date": str(row["date"])[:10], "value": float(row["value"])}
        for _, row in df.dropna(subset=["value"]).iterrows()
    ]


def _sum_records(*series):
    common_dates = set.intersection(*[{r["date"] for r in rows} for rows in series if rows])
    by_series = [{r["date"]: r["value"] for r in rows} for rows in series]
    return [
        {"date": date, "value": sum(rows[date] for rows in by_series)}
        for date in sorted(common_dates)
    ]


def _store(series_id, records, source_detail="", incremental=True):
    if not records:
        log_fetch("crypto_liquidity", series_id, "error", error_message="Empty response")
        return 0
    records = filter_new_records("crypto_liquidity", series_id, records, overlap_days=3) if incremental else records
    if not records:
        log_fetch("crypto_liquidity", series_id, "success", 0)
        return 0
    upsert_time_series("crypto_liquidity", series_id, records)
    meta = dict(SERIES_META[series_id])
    if source_detail:
        meta["display_name"] = f"{meta['display_name']} ({source_detail})"
    upsert_series_meta("crypto_liquidity", series_id, meta)
    log_fetch("crypto_liquidity", series_id, "success", len(records))
    return len(records)


def fetch_and_store_crypto_liquidity(delay=2.0, incremental=True):
    """Fetch stablecoin liquidity and ETH/BTC; tolerate partial source outages."""
    results = {}
    market_caps = {}

    try:
        records = fetch_stable_total_defillama()
        results["STABLE_TOTAL_MCAP"] = _store("STABLE_TOTAL_MCAP", records, "DefiLlama", incremental=incremental)
        print(f"  稳定币总市值: {results['STABLE_TOTAL_MCAP']} records")
    except Exception as exc:
        log_fetch("crypto_liquidity", "STABLE_TOTAL_MCAP", "error", error_message=str(exc))
        print(f"  稳定币总市值: FAILED - {exc}")

    for series_id, coin_id in COINGECKO_MARKET_CAP_COINS.items():
        try:
            time.sleep(delay)
            records = fetch_stablecoin_market_cap_defillama(STABLE_SYMBOLS[series_id])
            source_detail = "DefiLlama"
        except Exception as exc:
            print(f"  {SERIES_META[series_id]['display_name']}: DefiLlama fallback needed - {exc}")
            try:
                time.sleep(delay)
                days = 30 if incremental and observation_start("crypto_liquidity", series_id, overlap_days=3) else 365
                records = fetch_market_cap_coingecko(coin_id, days=days)
                source_detail = "CoinGecko fallback"
            except Exception as fallback_exc:
                log_fetch("crypto_liquidity", series_id, "error", error_message=str(fallback_exc))
                print(f"  {SERIES_META[series_id]['display_name']}: FAILED - {fallback_exc}")
                continue

        try:
            market_caps[series_id] = records
            results[series_id] = _store(series_id, records, source_detail, incremental=incremental)
            print(f"  {SERIES_META[series_id]['display_name']}: {results[series_id]} records")
        except Exception as exc:
            log_fetch("crypto_liquidity", series_id, "error", error_message=str(exc))
            print(f"  {SERIES_META[series_id]['display_name']}: FAILED - {exc}")

    if market_caps.get("USDT_MCAP") and market_caps.get("USDC_MCAP"):
        records = _sum_records(market_caps["USDT_MCAP"], market_caps["USDC_MCAP"])
        results["STABLE_MAJOR_MCAP"] = _store("STABLE_MAJOR_MCAP", records, "USDT+USDC proxy", incremental=incremental)
        print(f"  USDT+USDC市值: {results['STABLE_MAJOR_MCAP']} records")
        results["USDT_SHARE"] = _store(
            "USDT_SHARE",
            [{"date": row["date"], "value": row["value"] * 100}
             for row in _ratio_records(market_caps["USDT_MCAP"], records)],
            "derived from USDT+USDC",
            incremental=incremental,
        )
        results["USDC_SHARE"] = _store(
            "USDC_SHARE",
            [{"date": row["date"], "value": row["value"] * 100}
             for row in _ratio_records(market_caps["USDC_MCAP"], records)],
            "derived from USDT+USDC",
            incremental=incremental,
        )

    records = []
    source_detail = ""
    for source_name, fetcher in (
        ("Kraken", fetch_ethbtc_kraken),
        ("Coinbase", fetch_ethbtc_coinbase),
    ):
        try:
            time.sleep(delay)
            records = fetcher()
            source_detail = source_name
            if records:
                break
        except Exception as exc:
            print(f"  ETH/BTC: {source_name} fallback needed - {exc}")

    if not records:
        try:
            time.sleep(delay)
            days = 30 if incremental and observation_start("crypto_liquidity", "ETHBTC", overlap_days=3) else 365
            eth = fetch_price_coingecko("ethereum", days=days)
            time.sleep(delay)
            btc = fetch_price_coingecko("bitcoin", days=days)
            records = _ratio_records(eth, btc)
            source_detail = "CoinGecko fallback"
            if not records:
                raise ValueError("Empty CoinGecko ETH/BTC")
        except Exception as exc:
            print(f"  ETH/BTC: CoinGecko fallback needed - {exc}")
            try:
                eth = _local_price_records("yfinance", "ETH-USD")
                btc = _local_price_records("fred", "CBBTCUSD") or _local_price_records("yfinance", "BTC-USD")
                records = _ratio_records(eth, btc)
                source_detail = "local price proxy"
            except Exception as fallback_exc:
                records = []
                source_detail = ""
                exc = fallback_exc

    try:
        results["ETHBTC"] = _store("ETHBTC", records, source_detail, incremental=incremental)
        print(f"  ETH/BTC: {results['ETHBTC']} records")
    except Exception as exc:
        log_fetch("crypto_liquidity", "ETHBTC", "error", error_message=str(exc))
        print(f"  ETH/BTC: FAILED - {exc}")

    return results
