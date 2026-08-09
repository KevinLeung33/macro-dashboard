"""OKX read-only account and market data adapter.

This module deliberately implements GET requests only. It is intended for an
OKX API key with Read permission and does not expose any order-placement or
withdrawal method.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from db.repository import (
    clear_trade_positions,
    upsert_trade_account_snapshot,
    upsert_trade_fill,
    upsert_trade_order,
    upsert_trade_position,
    refresh_trade_plan_order_links,
)

logger = logging.getLogger("okx_readonly")


class OKXReadOnlyError(RuntimeError):
    """Raised when an OKX read-only request or response cannot be used."""


def _float(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=100):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _iso_from_ms(value):
    parsed = _float(value)
    if parsed is None:
        return ""
    try:
        return datetime.fromtimestamp(parsed / 1000, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _safe_json(value):
    """Return a serialisable copy without ever including credentials."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {}


class OKXReadOnlyClient:
    """Small signed REST client with a deliberately GET-only surface."""

    def __init__(self, base_url=None, session=None):
        self.base_url = (base_url or os.getenv("OKX_API_BASE_URL", "https://www.okx.com")).rstrip("/")
        self.api_key = os.getenv("OKX_API_KEY", "").strip()
        self.api_secret = os.getenv("OKX_API_SECRET", "").strip()
        self.passphrase = os.getenv("OKX_API_PASSPHRASE", "").strip()
        self.demo = os.getenv("OKX_API_DEMO", "false").lower() in {"1", "true", "yes"}
        self.timeout = max(3, _int(os.getenv("OKX_API_TIMEOUT_SECONDS", "15"), 15))
        self.session = session or requests.Session()
        self._server_time_cache = None

    @property
    def configured(self):
        return bool(self.api_key and self.api_secret and self.passphrase)

    def _server_timestamp(self):
        now = time.time()
        if self._server_time_cache and now - self._server_time_cache[0] < 60:
            return self._server_time_cache[1]
        response = self.session.get(
            f"{self.base_url}/api/v5/public/time",
            headers={"User-Agent": "macro-dashboard/okx-readonly"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("code")) != "0" or not payload.get("data"):
            raise OKXReadOnlyError(f"OKX public time failed: {payload.get('msg') or payload}")
        timestamp_ms = _float(payload["data"][0].get("ts"))
        if timestamp_ms is None:
            raise OKXReadOnlyError("OKX public time did not contain ts")
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self._server_time_cache = (now, timestamp)
        return timestamp

    def _request(self, method, path, params=None, private=False):
        # Keep this guard explicit: adding a write endpoint here should be an
        # intentional code review decision, not an accidental side effect.
        if str(method).upper() != "GET":
            raise OKXReadOnlyError("OKX read-only client only permits GET requests")
        params = {key: value for key, value in (params or {}).items() if value not in (None, "")}
        query = urlencode(params, doseq=True)
        request_path = path + (f"?{query}" if query else "")
        headers = {"User-Agent": "macro-dashboard/okx-readonly"}
        if private:
            if not self.configured:
                raise OKXReadOnlyError("OKX read-only credentials are not configured")
            timestamp = self._server_timestamp()
            message = f"{timestamp}GET{request_path}"
            sign = base64.b64encode(
                hmac.new(self.api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
            ).decode("utf-8")
            headers.update(
                {
                    "OK-ACCESS-KEY": self.api_key,
                    "OK-ACCESS-SIGN": sign,
                    "OK-ACCESS-TIMESTAMP": timestamp,
                    "OK-ACCESS-PASSPHRASE": self.passphrase,
                }
            )
            if self.demo:
                headers["x-simulated-trading"] = "1"
        response = self.session.get(f"{self.base_url}{request_path}", headers=headers, timeout=self.timeout)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise OKXReadOnlyError(f"OKX returned non-JSON response: HTTP {response.status_code}") from exc
        if str(payload.get("code")) != "0":
            raise OKXReadOnlyError(f"OKX API error {payload.get('code')}: {payload.get('msg') or 'unknown error'}")
        return payload.get("data") or []

    def _private_get(self, path, params=None):
        return self._request("GET", path, params=params, private=True)

    def fetch_account_config(self):
        return self._private_get("/api/v5/account/config")

    def fetch_balance(self):
        return self._private_get("/api/v5/account/balance")

    def fetch_positions(self, inst_type=None):
        return self._private_get(
            "/api/v5/account/positions",
            {"instType": inst_type or os.getenv("OKX_INST_TYPE", "SWAP")},
        )

    def fetch_pending_orders(self, inst_type=None, limit=None):
        return self._private_get(
            "/api/v5/trade/orders-pending",
            {
                "instType": inst_type or os.getenv("OKX_INST_TYPE", "SWAP"),
                "limit": limit or _int(os.getenv("OKX_SYNC_LIMIT", "100")),
            },
        )

    def fetch_recent_order_history(self, inst_type=None, limit=None):
        return self._private_get(
            "/api/v5/trade/orders-history",
            {
                "instType": inst_type or os.getenv("OKX_INST_TYPE", "SWAP"),
                "limit": limit or _int(os.getenv("OKX_SYNC_LIMIT", "100")),
            },
        )

    def fetch_order_history_archive(self, inst_type=None, limit=None):
        return self._private_get(
            "/api/v5/trade/orders-history-archive",
            {
                "instType": inst_type or os.getenv("OKX_INST_TYPE", "SWAP"),
                "limit": limit or _int(os.getenv("OKX_SYNC_LIMIT", "100")),
            },
        )

    def fetch_order_history(self, inst_type=None, limit=None):
        """Backward-compatible alias for the three-month archive endpoint."""
        return self.fetch_order_history_archive(inst_type=inst_type, limit=limit)

    def fetch_fills(self, inst_type=None, limit=None):
        return self._private_get(
            "/api/v5/trade/fills-history",
            {
                "instType": inst_type or os.getenv("OKX_INST_TYPE", "SWAP"),
                "limit": limit or _int(os.getenv("OKX_SYNC_LIMIT", "100")),
            },
        )

    def fetch_candles(self, inst_id, bar="1H", limit=200):
        rows = self._request(
            "GET",
            "/api/v5/market/candles",
            {"instId": inst_id, "bar": bar, "limit": min(300, max(1, int(limit)))},
            private=False,
        )
        candles = []
        for row in rows:
            if len(row) < 6:
                continue
            candles.append(
                {
                    "timestamp": _iso_from_ms(row[0]),
                    "ts": _float(row[0]),
                    "open": _float(row[1]),
                    "high": _float(row[2]),
                    "low": _float(row[3]),
                    "close": _float(row[4]),
                    "volume": _float(row[5], 0),
                    "confirm": row[8] if len(row) > 8 else "",
                }
            )
        return list(reversed(candles))


def _account_mode(acct_level):
    return {
        "1": "simple",
        "2": "single_currency_margin",
        "3": "multi_currency_margin",
        "4": "portfolio_margin",
    }.get(str(acct_level or ""), f"unknown({acct_level or 'unset'})")


def _normalise_order(row, account_label):
    return {
        "venue": "OKX",
        "account_label": account_label,
        "order_id": row.get("ordId", ""),
        "client_order_id": row.get("clOrdId", ""),
        "symbol": row.get("instId", ""),
        "instrument_type": row.get("instType", "SWAP"),
        "side": row.get("side", ""),
        "position_side": row.get("posSide", ""),
        "order_type": row.get("ordType", ""),
        "status": row.get("state", ""),
        "price": _float(row.get("px")),
        "avg_price": _float(row.get("avgPx")),
        "quantity": _float(row.get("sz"), 0),
        "filled_quantity": _float(row.get("accFillSz"), 0),
        "fee": _float(row.get("fee"), 0),
        "fee_asset": row.get("feeCcy", ""),
        "realized_pnl": _float(row.get("fillPnl")),
        "leverage": _float(row.get("lever")),
        "reduce_only": str(row.get("reduceOnly", "")).lower() == "true",
        "placed_at": _iso_from_ms(row.get("cTime")),
        "updated_at": _iso_from_ms(row.get("uTime") or row.get("cTime")),
        "raw_json": _safe_json(row),
    }


def _normalise_fill(row, account_label):
    return {
        "venue": "OKX",
        "account_label": account_label,
        "fill_id": row.get("tradeId", ""),
        "order_id": row.get("ordId", ""),
        "symbol": row.get("instId", ""),
        "side": row.get("side", ""),
        "price": _float(row.get("fillPx")),
        "quantity": _float(row.get("fillSz"), 0),
        "fee": _float(row.get("fee"), 0),
        "fee_asset": row.get("feeCcy", ""),
        "realized_pnl": _float(row.get("fillPnl")),
        "executed_at": _iso_from_ms(row.get("ts")),
        "raw_json": _safe_json(row),
    }


def _normalise_position(row, account_label):
    return {
        "venue": "OKX",
        "account_label": account_label,
        "symbol": row.get("instId", ""),
        "instrument_type": row.get("instType", "SWAP"),
        "margin_mode": row.get("mgnMode", "cross"),
        "position_side": row.get("posSide", ""),
        "quantity": _float(row.get("pos"), 0),
        "entry_price": _float(row.get("avgPx")),
        "mark_price": _float(row.get("markPx")),
        "liquidation_price": _float(row.get("liqPx")),
        "leverage": _float(row.get("lever")),
        "unrealized_pnl": _float(row.get("upl")),
        "unrealized_pnl_ratio": _float(row.get("uplRatio")),
        "margin": _float(row.get("margin")),
        "notional": _float(row.get("notionalUsd") or row.get("notionalCcy")),
        "updated_at": _iso_from_ms(row.get("uTime")),
        "raw_json": _safe_json(row),
    }


def _sync_positions(client, account_label):
    position_rows = client.fetch_positions()
    positions = [
        _normalise_position(row, account_label)
        for row in position_rows
        if row.get("instId") and abs(_float(row.get("pos"), 0) or 0) > 0
    ]
    clear_trade_positions("OKX", account_label)
    for position in positions:
        upsert_trade_position(position)
    return positions


def _sync_orders_and_fills(client, account_label, include_archive=True):
    """Synchronise pending plus recent terminal orders before optional archive data.

    OKX keeps ordinary cancelled orders in the recent-history endpoint for a
    short window.  Reading it on every execution sync is what allows a cached
    ``live`` order to turn into ``canceled`` promptly instead of remaining in
    the dashboard's current-order view.
    """
    pending = client.fetch_pending_orders()
    recent_history = client.fetch_recent_order_history()
    archive_history = client.fetch_order_history_archive() if include_archive else []
    rows = pending + recent_history + archive_history
    orders = [_normalise_order(row, account_label) for row in rows if row.get("ordId")]
    unique_orders = {}
    for order in orders:
        unique_orders[(order["venue"], order["account_label"], order["order_id"])] = order
    orders = list(unique_orders.values())
    for order in orders:
        upsert_trade_order(order)

    fill_rows = client.fetch_fills()
    fills = [_normalise_fill(row, account_label) for row in fill_rows if row.get("tradeId")]
    for fill in fills:
        upsert_trade_fill(fill)
    plan_link_updates = refresh_trade_plan_order_links("OKX", account_label)
    return orders, fills, plan_link_updates


def sync_okx_trade_execution(client=None, include_archive=False):
    """Lightweight read-only execution sync used by the scheduler.

    It intentionally does not insert a new account-equity snapshot every
    minute.  It refreshes positions, pending/recent orders and fills so linked
    trade plans can capture order transitions and fill progress.
    """
    client = client or OKXReadOnlyClient()
    if not client.configured:
        raise OKXReadOnlyError("OKX_API_KEY/OKX_API_SECRET/OKX_API_PASSPHRASE 未完整配置")
    account_label = os.getenv("OKX_ACCOUNT_LABEL", "main").strip() or "main"
    positions = _sync_positions(client, account_label)
    orders, fills, plan_link_updates = _sync_orders_and_fills(
        client,
        account_label,
        include_archive=include_archive,
    )
    return {
        "venue": "OKX",
        "account_label": account_label,
        "positions": positions,
        "orders": orders,
        "fills": fills,
        "plan_link_updates": plan_link_updates,
        "counts": {
            "positions": len(positions),
            "orders": len(orders),
            "fills": len(fills),
            "plan_link_updates": plan_link_updates.get("changed", 0),
        },
    }


def sync_okx_readonly_account(client=None):
    """Synchronise one OKX read-only account snapshot and return a safe summary."""
    client = client or OKXReadOnlyClient()
    if not client.configured:
        raise OKXReadOnlyError("OKX_API_KEY/OKX_API_SECRET/OKX_API_PASSPHRASE 未完整配置")

    account_label = os.getenv("OKX_ACCOUNT_LABEL", "main").strip() or "main"
    config_rows = client.fetch_account_config()
    config = config_rows[0] if config_rows else {}
    acct_level = str(config.get("acctLv") or "")
    account_mode = _account_mode(acct_level)
    required_level = str(os.getenv("OKX_REQUIRED_ACCOUNT_LEVEL", "3"))
    warnings = []
    if required_level and acct_level != required_level:
        warnings.append(f"OKX 账户模式为 {account_mode}，不是要求的跨币种保证金")

    balance_rows = client.fetch_balance()
    balance = balance_rows[0] if balance_rows else {}
    observed_at = _iso_from_ms(balance.get("ts")) or datetime.now(timezone.utc).isoformat()
    snapshot = {
        "venue": "OKX",
        "account_label": account_label,
        "observed_at": observed_at,
        "equity": _float(balance.get("adjEq") or balance.get("totalEq")),
        "available_balance": _float(balance.get("availEq")),
        "unrealized_pnl": _float(balance.get("upl"), 0),
        "margin_ratio": _float(balance.get("mgnRatio")),
        "account_mode": account_mode,
        "margin_mode": "cross",
        "raw_json": {"config": _safe_json(config), "balance": _safe_json(balance)},
    }
    upsert_trade_account_snapshot(**snapshot)

    positions = _sync_positions(client, account_label)
    position_modes = {str(item.get("margin_mode") or "cross") for item in positions}
    if position_modes and position_modes != {"cross"}:
        warnings.append("检测到非全 cross 持仓，请逐笔检查 margin_mode")

    orders, fills, plan_link_updates = _sync_orders_and_fills(
        client,
        account_label,
        include_archive=True,
    )

    return {
        "venue": "OKX",
        "account_label": account_label,
        "account_mode": account_mode,
        "acct_level": acct_level,
        "required_account_level": required_level,
        "margin_mode": "cross",
        "observed_at": observed_at,
        "snapshot": snapshot,
        "positions": positions,
        "orders": orders,
        "fills": fills,
        "warnings": warnings,
        "plan_link_updates": plan_link_updates,
        "counts": {
            "positions": len(positions), "orders": len(orders), "fills": len(fills),
            "plan_link_updates": plan_link_updates.get("changed", 0),
        },
    }
