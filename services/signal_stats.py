"""Analytics for composite signal review performance."""
import pandas as pd

from db.repository import query_composite_signal_reviews


HORIZONS = {
    "return_1d": "1D",
    "return_3d": "3D",
    "return_7d": "7D",
}


def _clean_reviews(limit=2000):
    rows = query_composite_signal_reviews(limit=limit)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    for col in HORIZONS:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def signal_effectiveness(limit=2000, min_samples=2):
    """Return signal-level and signal-asset-level performance tables."""
    df = _clean_reviews(limit=limit)
    if df.empty:
        return {"by_signal": [], "by_signal_asset": [], "top_signal": None, "sample_count": 0}

    by_signal = _aggregate(df, ["signal_name"], min_samples=min_samples)
    by_signal_asset = _aggregate(df, ["signal_name", "asset"], min_samples=min_samples)

    ranked = [row for row in by_signal if row.get("valid_7d", 0) >= min_samples]
    ranked = sorted(ranked, key=lambda x: abs(x.get("avg_7d") or 0), reverse=True)

    return {
        "by_signal": by_signal,
        "by_signal_asset": by_signal_asset,
        "top_signal": ranked[0] if ranked else None,
        "sample_count": int(len(df)),
    }


def _aggregate(df, group_cols, min_samples=2):
    rows = []
    for keys, sub in df.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        base = {col: keys[i] for i, col in enumerate(group_cols)}
        base["review_count"] = int(len(sub))
        for col, label in HORIZONS.items():
            valid = sub[col].dropna()
            base[f"valid_{label.lower()}"] = int(len(valid))
            if len(valid) >= min_samples:
                base[f"avg_{label.lower()}"] = float(valid.mean())
                base[f"median_{label.lower()}"] = float(valid.median())
                base[f"positive_rate_{label.lower()}"] = float((valid > 0).mean() * 100)
            else:
                base[f"avg_{label.lower()}"] = None
                base[f"median_{label.lower()}"] = None
                base[f"positive_rate_{label.lower()}"] = None
        rows.append(base)

    def sort_key(row):
        avg = row.get("avg_7d")
        return (row.get("valid_7d", 0), abs(avg or 0), row.get("review_count", 0))

    return sorted(rows, key=sort_key, reverse=True)


def cockpit_signal_stats():
    stats = signal_effectiveness(min_samples=1)
    rows = stats.get("by_signal", [])
    complete = [r for r in rows if r.get("valid_7d", 0)]
    if not complete:
        return {"summary": "暂无足够复盘样本", "top": None, "count": stats.get("sample_count", 0)}
    top = sorted(complete, key=lambda x: abs(x.get("avg_7d") or 0), reverse=True)[0]
    avg = top.get("avg_7d")
    rate = top.get("positive_rate_7d")
    return {
        "summary": f"{top['signal_name']} 7D均值 {avg:+.2f}% · 上涨占比 {rate:.0f}%",
        "top": top,
        "count": stats.get("sample_count", 0),
    }
