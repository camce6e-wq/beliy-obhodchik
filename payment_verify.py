#!/usr/bin/env python3
"""Учёт бесплатных выдач доступа для БелыйОбходчик.
Только stdlib. Принимает POST /api/test-grant и пишет запись status='test'.
Оплаты больше нет: весь доступ бесплатный.
"""
import json
import os
import re
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler

TARIFFS = {"vps", "dpi", "test"}

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "payments.db")
GRANT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


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
        if self.path == "/api/test-grant":
            self.handle_test_grant()
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

    def handle_test_grant(self):
        body = self._read_body()
        grant_id = body.get("grant_id", "")
        tariff = body.get("tariff", "")
        if not GRANT_ID_RE.fullmatch(grant_id or "") or tariff not in TARIFFS:
            return self._json({"ok": False, "error": "bad request"}, 400)
        try:
            con = sqlite3.connect(DB_PATH, timeout=10)
            try:
                con.execute(
                    "CREATE TABLE IF NOT EXISTS payments(payment_id TEXT PRIMARY KEY,"
                    " user_id INTEGER, username TEXT, amount INTEGER,"
                    " currency TEXT DEFAULT 'RUB', status TEXT DEFAULT 'pending',"
                    " source TEXT DEFAULT 'crypto', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
                    " paid_at TIMESTAMP, metadata TEXT)"
                )
                con.execute(
                    "INSERT OR IGNORE INTO payments"
                    "(payment_id, amount, currency, status, source, metadata)"
                    " VALUES(?, 0, 'TEST', 'test', 'test-grant', ?)",
                    (grant_id, json.dumps({"tariff": tariff})),
                )
                con.commit()
            finally:
                con.close()
        except Exception:
            return self._json({"ok": False, "error": "db error"}, 500)
        self._json({"ok": True})

    def log_message(self, format, *args):
        pass


def main():
    server = HTTPServer(("127.0.0.1", 8080), Handler)
    print("Test-grant service on 127.0.0.1:8080", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
