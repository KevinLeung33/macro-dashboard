import re
import requests
from datetime import datetime

from config.settings import TIC_DATA_URL
from db.repository import upsert_tic_holdings, log_fetch


def fetch_and_store_tic():
    try:
        resp = requests.get(TIC_DATA_URL, timeout=30)
        resp.raise_for_status()
        text = resp.text

        lines = text.strip().split("\n")
        header_line = None
        data_start = 0
        for i, line in enumerate(lines):
            if "Country" in line:
                header_line = i
                continue
            if header_line is not None and line.startswith("Grand Total"):
                data_start = i
                break

        if header_line is None:
            log_fetch("tic", "", "error", error_message="Cannot find header")
            return

        headers = lines[header_line].split()
        date_columns = [h for h in headers if h.startswith("202") and len(h) >= 6]

        records = []
        for line in lines[header_line + 1:]:
            if not line.strip():
                continue
            parts = line.split()
            if not parts:
                continue

            if parts[0] == "Grand" or parts[0] == "Of" or parts[0] == "Notes:" or parts[0] == "Estimated":
                break

            if len(parts) < 3:
                continue

            country_parts = []
            idx = 0
            for p in parts:
                try:
                    float(p)
                    break
                except ValueError:
                    country_parts.append(p)
                    idx += 1

            country = " ".join(country_parts).rstrip(",")
            if not country:
                continue

            values = []
            for p in parts[idx:]:
                try:
                    values.append(float(p))
                except ValueError:
                    continue

            for j, date_col in enumerate(date_columns):
                if j < len(values):
                    records.append({
                        "date": f"{date_col[:4]}-{date_col[4:6]}-01",
                        "country": country,
                        "holdings_billions": values[j],
                        "category": "total",
                    })

        if records:
            upsert_tic_holdings(records)
            log_fetch("tic", "all", "success", len(records))
    except Exception as e:
        log_fetch("tic", "all", "error", error_message=str(e))
