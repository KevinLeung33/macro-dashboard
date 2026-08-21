"""Read-only OKX bill-history probe.

This intentionally does not write SQLite, change configuration, or call any
order endpoint. It is used before enabling production funding/interest sync.
"""
import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from services.okx_readonly import probe_okx_account_bills


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="", help="optional profile, e.g. carry")
    parser.add_argument("--days", type=int, default=100)
    args = parser.parse_args()
    load_dotenv(dotenv_path=Path.cwd() / ".env")
    result = probe_okx_account_bills(profile=args.profile or None, days=args.days)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
