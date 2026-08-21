"""Local OKX funding-fee and borrowing-interest accounting."""
from datetime import datetime, timedelta, timezone

import pandas as pd

from db.repository import query_okx_account_bills


FUNDING_SUBTYPES = {"173", "174"}


def _records(account_label=None, hours=None, days=None):
    end = datetime.now(timezone.utc)
    begin = end - timedelta(hours=hours) if hours else end - timedelta(days=days or 90)
    rows = query_okx_account_bills(
        account_label=account_label,
        begin=begin.isoformat(),
        end=end.isoformat(),
    )
    data = []
    for row in rows:
        item = dict(row)
        item["funding_signed"] = 0.0
        item["interest_expense"] = 0.0
        subtype = str(item.get("bill_subtype") or "")
        bill_type = str(item.get("bill_type") or "")
        pnl = float(item.get("pnl") or 0)
        interest = float(item.get("interest") or 0)
        if bill_type == "8" and subtype in FUNDING_SUBTYPES:
            item["funding_signed"] = pnl
        elif bill_type == "7":
            item["interest_expense"] = abs(interest or float(item.get("amount") or 0))
        data.append(item)
    return data


def funding_interest_summary(account_label=None, hours=None, days=None):
    rows = _records(account_label=account_label, hours=hours, days=days)
    funding_income = sum(max(0.0, row["funding_signed"]) for row in rows)
    funding_expense = sum(max(0.0, -row["funding_signed"]) for row in rows)
    interest_expense = sum(row["interest_expense"] for row in rows)
    by_currency = {}
    for row in rows:
        currency = str(row.get("currency") or "UNKNOWN")
        item = by_currency.setdefault(currency, {"funding_income": 0.0, "funding_expense": 0.0, "interest_expense": 0.0})
        item["funding_income"] += max(0.0, row["funding_signed"])
        item["funding_expense"] += max(0.0, -row["funding_signed"])
        item["interest_expense"] += row["interest_expense"]
        item["funding_net"] = item["funding_income"] - item["funding_expense"]
        item["net_after_interest"] = item["funding_net"] - item["interest_expense"]
    return {
        "funding_income": funding_income,
        "funding_expense": funding_expense,
        "funding_net": funding_income - funding_expense,
        "interest_expense": interest_expense,
        "net_after_interest": funding_income - funding_expense - interest_expense,
        "rows": len(rows),
        "by_currency": by_currency,
    }


def funding_interest_windows(account_label=None):
    return {
        f"{days}d": funding_interest_summary(account_label=account_label, days=days)
        for days in (1, 3, 7, 30, 90)
    }


def cumulative_frame(account_label=None, days=90):
    rows = _records(account_label=account_label, days=days)
    if not rows:
        return pd.DataFrame(columns=["date", "funding_income", "interest_expense", "net"])
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["bill_ts"], utc=True).dt.floor("D")
    frame["currency"] = frame["currency"].fillna("UNKNOWN")
    frame["funding_income"] = frame["funding_signed"].clip(lower=0)
    frame["funding_expense"] = (-frame["funding_signed"]).clip(lower=0)
    frame["interest_expense"] = frame["interest_expense"]
    frame = frame.groupby(["currency", "date"], as_index=False)[
        ["funding_income", "funding_expense", "interest_expense"]
    ].sum()
    frame = frame.sort_values(["currency", "date"])
    frame["funding_income_cum"] = frame.groupby("currency")["funding_income"].cumsum()
    frame["interest_expense_cum"] = frame.groupby("currency")["interest_expense"].cumsum()
    frame["funding_expense_cum"] = frame.groupby("currency")["funding_expense"].cumsum()
    frame["net_cum"] = frame["funding_income_cum"] - frame["funding_expense_cum"] - frame["interest_expense_cum"]
    return frame


def format_settlement_card(summary, profiles=None):
    profiles = profiles or []
    lines = [
        "结算后过去 8 小时",
        "以下金额按币种分别统计，避免把 USDT、USDG 等直接相加：",
        f"本地账单记录：{summary['rows']} 条",
    ]
    for currency, item in sorted((summary.get("by_currency") or {}).items()):
        lines.extend([
            f"{currency}：资金费收入 {item['funding_income']:+.8f}",
            f"{currency}：资金费支出 {-item['funding_expense']:+.8f}",
            f"{currency}：利息支出 {-item['interest_expense']:+.8f}",
            f"{currency}：扣息后净额 {item['net_after_interest']:+.8f}",
        ])
    if profiles:
        lines.append("\n账户明细：")
        for label, item in profiles:
            lines.append(
                f"- {label}：资金费净额 {item['funding_net']:+.8f}，利息支出 {item['interest_expense']:.8f}"
            )
    return "\n".join(lines)
