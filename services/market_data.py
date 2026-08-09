"""Provider fallback layer for market series."""
from db.repository import query_series


MARKET_PROVIDER_CHAIN = {
    "DX-Y.NYB": [
        ("yfinance", "DX-Y.NYB", "DXY"),
        ("fred", "DEXUSEU", "USD/EUR proxy"),
    ],
    "MSTR": [("yfinance", "MSTR", "MSTR"), ("alpha_vantage", "MSTR", "MSTR")],
    "NVDA": [("yfinance", "NVDA", "NVDA"), ("alpha_vantage", "NVDA", "NVDA")],
    "MU": [("yfinance", "MU", "MU"), ("alpha_vantage", "MU", "MU")],
    "^GSPC": [("fred", "SP500", "SP500"), ("yfinance", "^GSPC", "SP500")],
    "^IXIC": [("fred", "NASDAQCOM", "NASDAQ"), ("yfinance", "^IXIC", "NASDAQ")],
    "^DJI": [("fred", "DJIA", "DJIA"), ("yfinance", "^DJI", "DJIA")],
    "^RUT": [("yfinance", "^RUT", "Russell 2000")],
    "^VIX": [("fred", "VIXCLS", "VIX"), ("yfinance", "^VIX", "VIX")],
    "GC=F": [("yfinance", "GC=F", "Gold futures")],
    "CL=F": [("yfinance", "CL=F", "WTI futures")],
    "QQQ": [("yfinance", "QQQ", "QQQ")],
    "MAGS": [("yfinance", "MAGS", "MAGS")],
    "TLT": [("yfinance", "TLT", "TLT")],
    "HYG": [("yfinance", "HYG", "HYG")],
    "LQD": [("yfinance", "LQD", "LQD")],
    "USDJPY=X": [("yfinance", "USDJPY=X", "USDJPY")],
    "USDCNH=X": [("yfinance", "USDCNH=X", "USDCNH")],
    "000300.SS": [("yfinance", "000300.SS", "CSI300")],
    "ETH-USD": [("binance_spot", "ETH-USD", "ETH"), ("yfinance", "ETH-USD", "ETH")],
    "BTC-USD": [("fred", "CBBTCUSD", "BTC"), ("binance_spot", "BTC-USD", "BTC")],
    "HSTECH": [("akshare_hk_index", "HSTECH", "Hang Seng TECH Index")],
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
