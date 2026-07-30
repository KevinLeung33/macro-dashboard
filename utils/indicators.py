"""指标Z-score计算"""
import pandas as pd
import numpy as np
from db.repository import query_series, query_latest_values


def prepare_series(df):
    out = df.copy()
    if out.empty:
        return out
    out["date"] = pd.to_datetime(out["date"])
    return out.sort_values("date")


def latest_value(df):
    return df["value"].iloc[-1] if not df.empty and len(df) > 0 else None


def scale_series(df, divisor):
    out = df.copy()
    if not out.empty:
        out["value"] = out["value"] / divisor
    return out


def yoy_series(df, periods=12):
    out = prepare_series(df)
    if out.empty:
        return out
    out["value"] = out["value"].pct_change(periods) * 100
    return out.dropna(subset=["value"])[["date", "value"]]


def mom_annualized_series(df):
    out = prepare_series(df)
    if out.empty:
        return out
    out["value"] = ((out["value"] / out["value"].shift(1)) ** 12 - 1) * 100
    return out.dropna(subset=["value"])[["date", "value"]]


def compute_zscores(window_years=5):
    """计算所有FRED指标当前值的历史Z-score"""
    results = []
    latest = query_latest_values("fred")
    if latest.empty:
        return results

    for _, row in latest.iterrows():
        sid = row["series_id"]
        cur_val = row["value"]
        name = row.get("display_name", sid)
        
        df = query_series("fred", sid)
        if df.empty or len(df) < 24:
            continue
        
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        
        cutoff = df["date"].max() - pd.DateOffset(years=window_years)
        hist = df[df["date"] >= cutoff]["value"]
        
        if len(hist) < 12 or hist.std() < 1e-9:
            continue
        
        mean = hist.mean()
        std = hist.std()
        z = (cur_val - mean) / std
        
        # Determine if higher or lower is "extreme"
        # For yields/rates/VIX/HY/inflation → higher = extreme (positive z = hot)
        # For employment/spread/GDP → lower = extreme (negative z = bad)
        higher_is_extreme_sids = [
            "FEDFUNDS", "DGS10", "DGS2", "DGS3MO", "DFII10",
            "VIXCLS", "BAMLH0A0HYM2", "NFCI", "UNRATE",
            "CPIAUCSL", "PCEPILFE", "T10YIE", "T5YIE",
            "DCOILWTICO", "DHHNGSP", "PCOPPUSDM",
            "JTSJOL", "JTSQUR", "AHETPI",
        ]
        higher_bad = sid in higher_is_extreme_sids
        
        if higher_bad:
            # Positive z = elevated = potentially bad (hot inflation, high VIX, etc.)
            if z > 2:
                level = "🔴"
            elif z > 1:
                level = "🟡"
            elif z < -2:
                level = "🔵"
            elif z < -1:
                level = "⚪"
            else:
                level = "🟢"
        else:
            # Lower = bad (low spread, low GDP, low participation)
            if z < -2:
                level = "🔴"
            elif z < -1:
                level = "🟡"
            elif z > 2:
                level = "🔵"
            elif z > 1:
                level = "⚪"
            else:
                level = "🟢"
        
        results.append({
            "display_name": name,
            "series_id": sid,
            "current": cur_val,
            "mean": mean,
            "z_score": z,
            "level": level,
            "percentile": (hist < cur_val).mean() * 100,
        })
    
    return sorted(results, key=lambda x: abs(x["z_score"]), reverse=True)
