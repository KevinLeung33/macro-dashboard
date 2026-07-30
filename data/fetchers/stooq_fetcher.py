"""Stooq market data fetcher.

Stores data with source='stooq' and the existing logical series_id
used by the dashboard, such as DX-Y.NYB or MSTR.
"""
from datetime import datetime
import time

import pandas as pd
import requests

from db.repository import log_fetch, upsert_series_meta, upsert_time_series


STOOQ_URL = "https://stooq.com/q/d/l/"
HEADERS = {"User-Agent": "Mozilla/5.0"}


STOOQ_SYMBOLS = {
    "^GSPC": {"stooq": "^spx", "display_name": "🇺🇸 标普500", "category": "us_equities", "yaxis_label": "点"},
    "^IXIC": {"stooq": "^ndq", "display_name": "🇺🇸 纳斯达克100", "category": "us_equities", "yaxis_label": "点"},
    "^DJI": {"stooq": "^dji", "display_name": "🇺🇸 道琼斯工业", "category": "us_equities", "yaxis_label": "点"},
    "^RUT": {"stooq": "^rut", "display_name": "🇺🇸 罗素2000", "category": "us_equities", "yaxis_label": "点"},
    "^VIX": {"stooq": "^vix", "display_name": "😱 VIX恐慌指数", "category": "volatility", "yaxis_label": "点"},
    "DX-Y.NYB": {"stooq": "dx.f", "display_name": "💱 美元指数DXY", "category": "fx", "yaxis_label": "点"},
    "GC=F": {"stooq": "gc.f", "display_name": "🥇 黄金期货", "category": "commodities", "yaxis_label": "美元/盎司"},
    "CL=F": {"stooq": "cl.f", "display_name": "🛢️ WTI原油期货", "category": "commodities", "yaxis_label": "美元/桶"},
    "MSTR": {"stooq": "mstr.us", "display_name": "🇺🇸 Strategy(MSTR)", "category": "crypto_equity", "yaxis_label": "美元"},
    "MU": {"stooq": "mu.us", "display_name": "🇺🇸 美光科技", "category": "semiconductors", "yaxis_label": "美元"},
    "NVDA": {"stooq": "nvda.us", "display_name": "🇺🇸 英伟达", "category": "semiconductors", "yaxis_label": "美元"},
    "^N225": {"stooq": "^nkx", "display_name": "🇯🇵 日经225", "category": "international", "yaxis_label": "点"},
    "^HSI": {"stooq": "^hsi", "display_name": "🇭🇰 恒生指数", "category": "international", "yaxis_label": "点"},
    "^GDAXI": {"stooq": "^dax", "display_name": "🇩🇪 德国DAX", "category": "international", "yaxis_label": "点"},
    "^FTSE": {"stooq": "^ukx", "display_name": "🇬🇧 英国富时100", "category": "international", "yaxis_label": "点"},
}


def _date_days_ago(years):
    return datetime(datetime.now().year - years, 1, 1).strftime("%Y%m%d")


def _fetch_stooq_csv(stooq_symbol, years=5):
    params = {
        "s": stooq_symbol,
        "i": "d",
        "d1": _date_days_ago(years),
        "d2": datetime.now().strftime("%Y%m%d"),
    }
    resp = requests.get(
        STOOQ_URL,
        params=params,
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    if not resp.text.strip() or "No data" in resp.text[:100]:
        return pd.DataFrame()
    from io import StringIO

    df = pd.read_csv(StringIO(resp.text))
    if df.empty or "Date" not in df.columns or "Close" not in df.columns:
        return pd.DataFrame()
    df = df.dropna(subset=["Date", "Close"])
    return df


def fetch_and_store_stooq_market(years=5, delay=0.5, incremental=True):
    from data.incremental import filter_new_records, observation_start

    ok, failed = 0, 0
    for logical_id, meta in STOOQ_SYMBOLS.items():
        name = meta.get("display_name", logical_id)
        try:
            start = observation_start("stooq", logical_id, overlap_days=3) if incremental else None
            if start:
                params_start = start.replace("-", "")
                params_end = datetime.now().strftime("%Y%m%d")
                resp = requests.get(
                    STOOQ_URL,
                    params={"s": meta["stooq"], "i": "d", "d1": params_start, "d2": params_end},
                    headers=HEADERS,
                    timeout=30,
                )
                resp.raise_for_status()
                from io import StringIO

                df = pd.read_csv(StringIO(resp.text)) if resp.text.strip() else pd.DataFrame()
            else:
                df = _fetch_stooq_csv(meta["stooq"], years=years)
            if df.empty:
                log_fetch("stooq", logical_id, "error", error_message="Empty")
                failed += 1
                continue

            records = [
                {"date": str(row["Date"]), "value": float(row["Close"])}
                for _, row in df.iterrows()
            ]
            records = filter_new_records("stooq", logical_id, records, overlap_days=3) if incremental else records
            upsert_time_series("stooq", logical_id, records)
            upsert_series_meta("stooq", logical_id, {
                "display_name": name,
                "unit": meta.get("unit", ""),
                "frequency": "daily",
                "category": meta.get("category", ""),
                "yaxis_label": meta.get("yaxis_label", ""),
            })
            log_fetch("stooq", logical_id, "success", len(records))
            ok += 1
            time.sleep(delay)
        except Exception as e:
            log_fetch("stooq", logical_id, "error", error_message=str(e))
            failed += 1
            print(f"  {name}: FAILED - {e}")

    if failed:
        print(f"  stooq: {ok} ok, {failed} failed")
    else:
        print(f"  stooq: {ok} symbols loaded")
