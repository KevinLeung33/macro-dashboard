"""Provider fallback layer for market series."""
from db.repository import query_series


MARKET_PROVIDER_CHAIN = {
    "DX-Y.NYB": [
        ("stooq", "DX-Y.NYB", "DXY"),
        ("yfinance", "DX-Y.NYB", "DXY"),
        ("fred", "DEXUSEU", "USD/EUR proxy"),
    ],
    "MSTR": [("alpha_vantage", "MSTR", "MSTR"), ("stooq", "MSTR", "MSTR"), ("yfinance", "MSTR", "MSTR")],
    "NVDA": [("alpha_vantage", "NVDA", "NVDA"), ("stooq", "NVDA", "NVDA"), ("yfinance", "NVDA", "NVDA")],
    "MU": [("alpha_vantage", "MU", "MU"), ("stooq", "MU", "MU"), ("yfinance", "MU", "MU")],
    "^GSPC": [("fred", "SP500", "SP500"), ("stooq", "^GSPC", "SP500"), ("yfinance", "^GSPC", "SP500")],
    "^IXIC": [("fred", "NASDAQCOM", "NASDAQ"), ("stooq", "^IXIC", "NASDAQ"), ("yfinance", "^IXIC", "NASDAQ")],
    "^DJI": [("fred", "DJIA", "DJIA"), ("stooq", "^DJI", "DJIA"), ("yfinance", "^DJI", "DJIA")],
    "^RUT": [("stooq", "^RUT", "Russell 2000"), ("yfinance", "^RUT", "Russell 2000")],
    "^VIX": [("fred", "VIXCLS", "VIX"), ("stooq", "^VIX", "VIX"), ("yfinance", "^VIX", "VIX")],
    "GC=F": [("stooq", "GC=F", "Gold futures"), ("yfinance", "GC=F", "Gold futures")],
    "CL=F": [("stooq", "CL=F", "WTI futures"), ("yfinance", "CL=F", "WTI futures")],
    "BTC-USD": [("fred", "CBBTCUSD", "BTC"), ("yfinance", "BTC-USD", "BTC")],
}


def query_market_series(logical_id, start_date=None, end_date=None):
    """Return the first available provider series plus provider metadata."""
    chain = MARKET_PROVIDER_CHAIN.get(logical_id, [("yfinance", logical_id, logical_id)])
    attempted = []
    for source, series_id, label in chain:
        df = query_series(source, series_id, start_date=start_date, end_date=end_date)
        attempted.append(f"{source}:{series_id}")
        if not df.empty:
            df.attrs["provider"] = source
            df.attrs["series_id"] = series_id
            df.attrs["logical_id"] = logical_id
            df.attrs["label"] = label
            df.attrs["attempted"] = attempted
            return df, {
                "provider": source,
                "series_id": series_id,
                "logical_id": logical_id,
                "label": label,
                "attempted": attempted,
                "is_proxy": source == "fred" and series_id != logical_id and logical_id == "DX-Y.NYB",
            }

    import pandas as pd

    empty = pd.DataFrame(columns=["date", "value"])
    empty.attrs["logical_id"] = logical_id
    empty.attrs["attempted"] = attempted
    return empty, {
        "provider": None,
        "series_id": logical_id,
        "logical_id": logical_id,
        "label": logical_id,
        "attempted": attempted,
        "is_proxy": False,
    }


def query_market_series_only(logical_id, start_date=None, end_date=None):
    df, _meta = query_market_series(logical_id, start_date=start_date, end_date=end_date)
    return df
