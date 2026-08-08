"""
全球宏观数据抓取器
使用 AKShare（仅宏观函数，不依赖 py_mini_racer）
"""
import time
from datetime import date, timedelta

import pandas as pd

from config.series_definitions import AKSHARE_SERIES
from data.incremental import filter_new_records
from db.repository import upsert_time_series, upsert_series_meta, log_fetch


def _try_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _date_str(d):
    if isinstance(d, pd.Timestamp):
        return d.strftime("%Y-%m-%d")
    s = str(d).strip()
    # "2008年01月份" or "2026年06月份"
    if "年" in s and "月" in s:
        import re
        m = re.match(r"(\d{4})\s*年\s*(\d{1,2})\s*月", s)
        if m:
            return f"{m.group(1)}-{m.group(2).zfill(2)}-15"
    # "2026-06" or "202604"
    if len(s) == 6 and s.isdigit():
        return f"{s[:4]}-{s[4:]}-01"
    # "2026-06-01" or similar
    if len(s) >= 10:
        return s[:10]
    # "2026-06" (7 chars, YYYY-MM)
    if "-" in s and len(s) == 7:
        return f"{s}-15"
    return s


def _post_process(df, pp):
    records = []
    def keyword_column(keywords, fallback=1):
        lowered = {str(col).lower(): col for col in df.columns}
        for key in keywords:
            for name, col in lowered.items():
                if key.lower() in name:
                    return col
        return df.columns[min(fallback, len(df.columns) - 1)]

    def rows_from_column(value_col):
        out = []
        for _, row in df.iterrows():
            value = _try_float(row[value_col])
            if value is not None:
                out.append({"date": _date_str(row[df.columns[0]]), "value": value})
        return out

    if pp == "pmi":
        for _, row in df.iterrows():
            v = _try_float(row[df.columns[1]])
            if v is not None:
                records.append({"date": _date_str(row[df.columns[0]]), "value": v})
    elif pp == "lpr":
        for _, row in df.iterrows():
            v = _try_float(row[df.columns[1]])
            if v is not None:
                records.append({"date": _date_str(row[df.columns[0]]), "value": v})
    elif pp == "second_col":
        for _, row in df.iterrows():
            v = _try_float(row[df.columns[1]])
            if v is not None:
                records.append({"date": _date_str(row[df.columns[0]]), "value": v})
    elif pp == "cpi":
        # macro_china_cpi_monthly: columns = [日期, 全国-当月, 全国-同比增长, ...]
        # Need 全国-同比增长 column (index 2)
        val_col = df.columns[2] if len(df.columns) > 2 else df.columns[1]
        for _, row in df.iterrows():
            v = _try_float(row[val_col])
            if v is not None:
                records.append({"date": _date_str(row[df.columns[0]]), "value": v})
    elif pp == "keyword_yoy":
        records = rows_from_column(keyword_column(["同比", "m2"]))
    elif pp == "keyword_stock":
        records = rows_from_column(keyword_column(["存量同比", "存量", "同比"]))
    elif pp == "keyword_dr007":
        records = rows_from_column(keyword_column(["dr007", "7天", "利率"]))
    elif pp == "event_current":
        date_col = keyword_column(["日期", "时间"], fallback=1)
        value_col = keyword_column(["今值", "现值"], fallback=2)
        for _, row in df.iterrows():
            value = _try_float(row[value_col])
            if value is not None:
                records.append({"date": _date_str(row[date_col]), "value": value})
    elif pp == "repo_fdr007":
        date_col = keyword_column(["date", "日期"], fallback=0)
        value_col = keyword_column(["fdr007"], fallback=1)
        for _, row in df.iterrows():
            value = _try_float(row[value_col])
            if value is not None:
                records.append({"date": _date_str(row[date_col]), "value": value})
    else:
        for _, row in df.iterrows():
            v = _try_float(row[df.columns[1]]) if len(df.columns) > 1 else None
            if v is not None:
                records.append({"date": _date_str(row[df.columns[0]]), "value": v})
    return records


def _candidate_fetch_kwargs(meta, candidate, post_process):
    """Build endpoint-specific arguments without coupling fallback schemas."""
    fetch_kwargs = dict(meta.get("fetch_kwargs", {}))
    fetch_kwargs.update(candidate.get("fetch_kwargs", {}))
    if post_process == "repo_fdr007":
        window_days = int(candidate.get("fetch_window_days", meta.get("fetch_window_days", 365)))
        end_date = date.today()
        fetch_kwargs.update({
            "start_date": (end_date - timedelta(days=window_days)).strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d"),
        })
    return fetch_kwargs


def _fetch_with_fallbacks(ak, meta):
    """Fetch one logical series, trying a compatible endpoint only if needed."""
    candidates = [{
        "fetch_func": meta["fetch_func"],
        "post_process": meta["post_process"],
    }]
    candidates.extend(meta.get("fallbacks", []))
    failures = []

    for index, candidate in enumerate(candidates):
        func_name = candidate.get("fetch_func")
        post_process = candidate.get("post_process", meta["post_process"])
        func = getattr(ak, func_name, None)
        if func is None:
            failures.append(f"{func_name} not found")
            continue
        try:
            df = func(**_candidate_fetch_kwargs(meta, candidate, post_process))
            if df is None or df.empty:
                raise ValueError("Empty")
            records = _post_process(df, post_process)
            if not records:
                raise ValueError(f"No usable records, cols={list(df.columns)[:4]}")
            return df, records, func_name, index > 0
        except Exception as exc:
            failures.append(f"{func_name}: {str(exc)[:180]}")

    raise RuntimeError("; ".join(failures) or "No usable fetch function")


def fetch_global_data(delay=2.0, incremental=True):
    try:
        import akshare as ak
    except ImportError:
        print("  AKShare not installed. Run: pip install akshare")
        return

    symbols = list(AKSHARE_SERIES.keys())
    for i, sid in enumerate(symbols):
        meta = AKSHARE_SERIES[sid]
        name = meta["display_name"]

        if i > 0:
            time.sleep(delay)

        try:
            _, raw_records, resolved_func, used_fallback = _fetch_with_fallbacks(ak, meta)
            records = (
                filter_new_records("akshare", sid, raw_records, overlap_days=35)
                if incremental else raw_records
            )
            if not records:
                # A valid monthly series can have no new observation since the
                # last refresh.  That is a successful fetch, not a source error.
                log_fetch("akshare", sid, "success", 0)
                print(f"  [{i+1}/{len(symbols)}] {name}: no new records ({resolved_func})")
                continue

            upsert_time_series("akshare", sid, records)
            upsert_series_meta("akshare", sid, {
                "display_name": name,
                "unit": meta.get("unit", ""),
                "frequency": meta.get("frequency", "monthly"),
                "category": meta["category"],
                "yaxis_label": meta.get("yaxis_label", ""),
            })
            log_fetch("akshare", sid, "success", len(records))
            fallback_note = f"; fallback={resolved_func}" if used_fallback else ""
            print(f"  [{i+1}/{len(symbols)}] {name}: {len(records)} recs, latest={records[-1]}{fallback_note}")
        except Exception as e:
            log_fetch("akshare", sid, "error", error_message=str(e))
            print(f"  [{i+1}/{len(symbols)}] {name}: FAILED - {e}")
