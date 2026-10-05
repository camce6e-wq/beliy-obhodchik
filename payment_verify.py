#!/usr/bin/env python3
"""Сервер верификации оплаты USDT (TRC-20) для БелыйОбходчик.
Только stdlib. Проверяет входящие переводы через TronGrid API.
"""
import json
import math
import secrets
import threading
import time
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

WALLET = "TCUJondgJ3PA321H8JJapP1dHht1dL6bfu"
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
WINDOW_MINUTES = 60
LOCK_TTL = 15 * 60
CLEANUP_INTERVAL = 60

TARIFFS = {"vps": 1500, "dpi": 1000}

locks = {}
locks_lock = threading.Lock()
rate_cache = {"value": 85.0, "ts": 0}
rate_lock = threading.Lock()


def get_rate():
    with rate_lock:
        if time.time() - rate_cache["ts"] < 300:
            return rate_cache["value"]
    try:
        req = urllib.request.Request(
            "https://open.er-api.com/v6/latest/USD",
            headers={"User-Agent": "BeliyObhodchik-Verify/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        rate = float(data["rates"]["RUB"])
        with rate_lock:
            rate_cache["value"] = rate
            rate_cache["ts"] = time.time()
        return rate
    except Exception:
        with rate_lock:
            return rate_cache["value"]


def find_transfer(amount):
    since = int((time.time() - WINDOW_MINUTES * 60) * 1000)
    url = (
        f"https://api.trongrid.io/v1/accounts/{WALLET}/transactions/trc20"
        f"?only_to=true&limit=50&order_by=block_timestamp,desc&start_time={since}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BeliyObhodchik-Verify/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    rows = data.get("data", [])
    for row in rows:
        if row.get("type") != "Transfer":
            continue
        ts = row.get("block_timestamp")
        if not ts or ts < since:
            continue
        info = row.get("token_info", {})
        contract = (info.get("address") or "").lower()
        is_usdt = contract == USDT_CONTRACT.lower() or (
            not contract and str(info.get("symbol", "")).upper() == "USDT"
        )
        if not is_usdt:
            continue
        try:
            value = float(row["value"]) / (10 ** int(info.get("decimals", 6)))
        except (ValueError, KeyError):
            continue
        if abs(value - amount) < 0.02:
            return row["transaction_id"]
    return None


def cleanup_locks():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        now = time.time()
        with locks_lock:
            expired = [k for k, v in locks.items() if v["expires_at"] < now]
            for k in expired:
                del locks[k]


class Handler(BaseHTTPRequestHandler):
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path == "/api/lock":
            self.handle_lock()
        elif self.path == "/api/verify":
            self.handle_verify()
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > 1024:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    def handle_lock(self):
        body = self._read_body()
        tariff = body.get("tariff")
        if tariff not in TARIFFS:
            return self._json({"ok": False, "error": "bad tariff"}, 400)
        rub = TARIFFS[tariff]
        rate = get_rate()
        amount = math.ceil(rub / rate * 100) / 100
        lock_id = secrets.token_hex(16)
        with locks_lock:
            locks[lock_id] = {
                "tariff": tariff,
                "amount": amount,
                "rate": rate,
                "expires_at": time.time() + LOCK_TTL,
            }
        self._json({
            "ok": True,
            "lock_id": lock_id,
            "amount": amount,
            "rate": rate,
            "expires_at": int(time.time() + LOCK_TTL),
        })

    def handle_verify(self):
        body = self._read_body()
        lock_id = body.get("lock_id", "")
        with locks_lock:
            lock = locks.get(lock_id)
        if not lock:
            return self._json({"ok": False, "error": "lock not found"}, 404)
        if time.time() > lock["expires_at"]:
            return self._json({"ok": False, "error": "lock expired"}, 410)
        txid = find_transfer(lock["amount"])
        if txid:
            with locks_lock:
                locks.pop(lock_id, None)
            self._json({"ok": True, "txid": txid, "tariff": lock["tariff"]})
        else:
            self._json({"ok": False})

    def log_message(self, format, *args):
        pass


def main():
    t = threading.Thread(target=cleanup_locks, daemon=True)
    t.start()
    server = HTTPServer(("127.0.0.1", 8080), Handler)
    print("Payment verify service on 127.0.0.1:8080", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
