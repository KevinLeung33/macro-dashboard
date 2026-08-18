"""Optional read-only OKX WebSocket -> Redis realtime cache.

This module has no order, cancel, amend, transfer, or withdrawal method.  It
is deliberately disabled unless ``OKX_WS_ENABLED=true`` and ``REDIS_URL`` is
configured.  REST remains the source of truth and reconciliation path.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("okx_realtime")


def _enabled():
    return os.getenv("OKX_WS_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def _inst_ids():
    raw = os.getenv("OKX_WS_INST_IDS", "BTC-USDT-SWAP,ETH-USDT-SWAP")
    return [item.strip() for item in raw.split(",") if item.strip()]


class OKXRealtimeService:
    """Two reconnecting read-only sockets: public market and private account."""

    def __init__(self):
        self.enabled = _enabled()
        self.redis_url = os.getenv("REDIS_URL", "").strip()
        self.ttl = max(30, int(os.getenv("OKX_WS_CACHE_TTL_SECONDS", "120")))
        self.url_public = os.getenv(
            "OKX_WS_PUBLIC_URL", "wss://ws.okx.com:8443/ws/v5/public"
        )
        self.url_private = os.getenv(
            "OKX_WS_PRIVATE_URL", "wss://ws.okx.com:8443/ws/v5/private"
        )
        self._stop = threading.Event()
        self._threads = []
        self._redis = None
        self._status = {
            "enabled": self.enabled,
            "running": False,
            "redis": False,
            "public": "stopped",
            "private": "stopped",
            "last_message_at": None,
            "last_error": "",
        }
        self._status_lock = threading.Lock()

    def _get_redis(self):
        if self._redis is not None:
            return self._redis
        if not self.redis_url:
            raise RuntimeError("REDIS_URL is not configured")
        import redis

        client = redis.Redis.from_url(self.redis_url, decode_responses=True)
        client.ping()
        self._redis = client
        self._set_status(redis=True)
        return client

    def _set_status(self, **changes):
        with self._status_lock:
            self._status.update(changes)
            payload = dict(self._status)
        try:
            client = self._redis
            if client is not None:
                client.setex("okx:realtime:status", self.ttl, json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    def status(self):
        with self._status_lock:
            return dict(self._status)

    def _cache(self, key, value):
        client = self._get_redis()
        payload = {
            "received_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "value": value,
        }
        client.setex(key, self.ttl, json.dumps(payload, ensure_ascii=False, default=str))
        self._set_status(last_message_at=payload["received_at"])

    def _login_args(self):
        import base64
        import hashlib
        import hmac

        api_key = os.getenv("OKX_API_KEY", "").strip()
        secret = os.getenv("OKX_API_SECRET", "").strip()
        passphrase = os.getenv("OKX_API_PASSPHRASE", "").strip()
        if not all((api_key, secret, passphrase)):
            raise RuntimeError("OKX read-only credentials are incomplete")
        timestamp = str(int(time.time()))
        sign = base64.b64encode(
            hmac.new(secret.encode(), (timestamp + "GET" + "/users/self/verify").encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "op": "login",
            "args": [{
                "apiKey": api_key,
                "passphrase": passphrase,
                "timestamp": timestamp,
                "sign": sign,
            }],
        }

    def _public_subscribe(self):
        args = []
        for inst_id in _inst_ids():
            args.extend([
                {"channel": "tickers", "instId": inst_id},
                {"channel": "mark-price", "instId": inst_id},
            ])
        return {"op": "subscribe", "args": args}

    def _private_subscribe(self):
        inst_type = os.getenv("OKX_INST_TYPE", "SWAP")
        return {
            "op": "subscribe",
            "args": [
                {"channel": "account"},
                {"channel": "orders", "instType": inst_type},
                {"channel": "positions", "instType": inst_type},
            ],
        }

    def _run_socket(self, kind, url, private=False):
        import websocket

        backoff = 2
        while not self._stop.is_set():
            ws = None
            try:
                self._set_status(**{kind: "connecting"})
                ws = websocket.create_connection(url, timeout=20, enable_multithread=True)
                ws.settimeout(20)
                if private:
                    ws.send(json.dumps(self._login_args()))
                    login = json.loads(ws.recv())
                    if login.get("event") != "login" or str(login.get("code", "0")) != "0":
                        raise RuntimeError(f"OKX WebSocket login failed: {login}")
                ws.send(json.dumps(self._private_subscribe() if private else self._public_subscribe()))
                self._set_status(**{kind: "connected", "last_error": ""})
                backoff = 2
                while not self._stop.is_set():
                    try:
                        raw = ws.recv()
                    except Exception as exc:
                        if "timed out" in str(exc).lower():
                            ws.send("ping")
                            continue
                        raise
                    if raw in (None, "pong"):
                        continue
                    if raw == "ping":
                        ws.send("pong")
                        continue
                    message = json.loads(raw)
                    if message.get("event") in {"subscribe", "login"}:
                        continue
                    if message.get("event") == "error":
                        raise RuntimeError(str(message))
                    channel = ((message.get("arg") or {}).get("channel") or "unknown")
                    suffix = ((message.get("arg") or {}).get("instId") or channel).replace("/", "_")
                    scope = "private" if private else "public"
                    self._cache(f"okx:realtime:{scope}:{channel}:{suffix}", message)
            except Exception as exc:
                self._set_status(**{kind: "error", "last_error": str(exc)[:500]})
                logger.warning("OKX %s WebSocket disconnected: %s", kind, exc)
                if not self._stop.wait(backoff):
                    backoff = min(backoff * 2, 60)
            finally:
                try:
                    if ws is not None:
                        ws.close()
                except Exception:
                    pass
        self._set_status(**{kind: "stopped"})

    def start(self):
        if not self.enabled:
            logger.info("OKX WebSocket disabled; REST sync remains active")
            return False
        if self._threads:
            return True
        self._get_redis()
        self._stop.clear()
        for kind, url, private in (
            ("public", self.url_public, False),
            ("private", self.url_private, True),
        ):
            thread = threading.Thread(
                target=self._run_socket,
                args=(kind, url, private),
                name=f"okx-ws-{kind}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        self._set_status(running=True)
        return True

    def stop(self):
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=3)
        self._threads = []
        self._set_status(running=False)


def read_realtime_status():
    """Read Redis status for diagnostics without requiring the WS service."""
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return {"enabled": False, "redis": False, "error": "REDIS_URL is not configured"}
    try:
        import redis

        client = redis.Redis.from_url(url, decode_responses=True)
        raw = client.get("okx:realtime:status")
        return json.loads(raw) if raw else {"enabled": True, "redis": True, "status": "no heartbeat"}
    except Exception as exc:
        return {"enabled": True, "redis": False, "error": str(exc)[:500]}
