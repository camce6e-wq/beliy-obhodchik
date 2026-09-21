#!/usr/bin/env python3
"""Офлайн-тест интеграции Crypto Bot: создание счёта -> оплата -> доставка конфига."""
import os
import sys
import hashlib
import tempfile
import sqlite3
from types import SimpleNamespace

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import telegram_bot as tb

TMP = tempfile.mkdtemp(prefix="paytest_")
DB = os.path.join(TMP, "payments.db")
REAL_PAYMENT_SYSTEM = tb.PaymentSystem


def _payment_system_factory():
    return REAL_PAYMENT_SYSTEM(db_path=DB)


tb.PaymentSystem = _payment_system_factory


class FakeCryptoPay:
    def __init__(self):
        self.created = []
        self.paid = []

    def create_invoice(self, amount_rub=500, payload="", description=None):
        inv = {
            "invoice_id": "inv_" + hashlib.md5(payload.encode()).hexdigest()[:8],
            "status": "active",
            "pay_url": "https://t.me/CryptoBot?start=pay_" + payload,
            "amount": 500,
            "payload": payload,
        }
        self.created.append(inv)
        return inv

    def get_paid_invoices(self):
        return [i for i in self.paid if i["status"] == "paid"]


fake = FakeCryptoPay()

bot = tb.AutoConfigBot(tb.TELEGRAM_BOT_TOKEN, crypto_pay_token="TEST_TOKEN")
bot.crypto_pay = fake

sent_messages = []
sent_docs = []
sent_markups = []


def fake_send_message(chat_id, text, **kw):
    sent_messages.append(text)
    sent_markups.append(kw.get("reply_markup"))


def fake_send_document(chat_id, doc, **kw):
    sent_docs.append(kw.get("caption") or "")
    return SimpleNamespace(message_id=1)


bot.bot.send_message = fake_send_message
bot.bot.send_document = fake_send_document

USER = 111
CHAT = 222

call = SimpleNamespace(
    from_user=SimpleNamespace(id=USER),
    message=SimpleNamespace(chat=SimpleNamespace(id=CHAT)),
)

# Берём реальный обработчик колбэков, зарегистрированный в register_handlers
handler = bot.bot.callback_query_handlers[0]['function']
assert handler is not None, "колбэк-хендлер не зарегистрирован"

pid = bot.payment_system.create_payment(USER, "tester")
bot.user_data[USER] = {
    "payment_id": pid,
    "server_ip": "45.88.101.5",
    "sni_hostname": "api.notion.com",
}
bot.user_states[USER] = "awaiting_payment"

# 1) Нажатие "Оплатить" -> счёт создан
call.data = "make_payment"
handler(call)

assert pid in bot.pending_payments, "счёт не добавлен в pending"
assert len(fake.created) == 1, "create_invoice не вызван"
inv = fake.created[0]

con = sqlite3.connect(DB)
row = con.execute("SELECT status, pay_url FROM invoices WHERE invoice_id=?", (inv["invoice_id"],)).fetchone()
con.close()
assert row and row[0] == "active", "счёт не записан в БД"
has_pay_button = any(
    m and any(
        b.to_dict().get("url", "").startswith("https://t.me/CryptoBot")
        for row2 in m.keyboard
        for b in row2
    )
    for m in sent_markups
)
assert has_pay_button, "не отправлена кнопка оплаты с pay_url"
assert bot.user_states[USER] == "awaiting_payment", "пользователь не должен быть завершён до оплаты"

print("OK 1/3: счёт создан, pay_url отправлен, pending + БД заполнены")

# 2) Пользователь оплатил -> фоновый луп находит оплату и доставляет конфиг
inv["status"] = "paid"
fake.paid = [inv]
bot._process_paid_invoices(fake.get_paid_invoices())

assert len(sent_docs) == 1, "конфиг не отправлен"
con = sqlite3.connect(DB)
conf = con.execute("SELECT status FROM payments WHERE payment_id=?", (pid,)).fetchone()
order = con.execute("SELECT order_id FROM orders WHERE payment_id=?", (pid,)).fetchone()
invst = con.execute("SELECT status FROM invoices WHERE invoice_id=?", (inv["invoice_id"],)).fetchone()
con.close()
assert conf and conf[0] == "paid", "оплата не подтверждена"
assert order, "заказ не создан"
assert invst and invst[0] == "paid", "счёт не помечен paid"
assert pid not in bot.pending_payments, "payment_id не убран из очереди"

print("OK 2/3: оплата подтверждена, заказ создан, конфиг доставлен, счёт paid")

# 2.5) Парсинг ответа getInvoices вида {"items": [...]}
class _RealCrypto(tb.CryptoPayClient):
    def _post(self, method, payload=None):
        return {"items": [
            {"invoice_id": "i1", "payload": pid, "status": "paid"},
        ]} if method == "getInvoices" else {}


real = _RealCrypto("x")
got = real.get_paid_invoices()
assert len(got) == 1 and got[0]["payload"] == pid, "формат getInvoices разобран неверно"
print("OK 2.5/3: getInvoices {\"items\": [...]} разбирается корректно")

# 3) Перезапуск: НЕоплаченный счёт восстанавливается из БД (оплаченные не восстанавливаются)
pid2 = bot.payment_system.create_payment(USER, "tester")
bot.user_data[USER] = {
    "payment_id": pid2,
    "server_ip": "45.88.101.5",
    "sni_hostname": "api.notion.com",
}
call.data = "make_payment"
handler(call)
assert pid2 in bot.pending_payments

bot2 = tb.AutoConfigBot(tb.TELEGRAM_BOT_TOKEN, crypto_pay_token="TEST_TOKEN")
bot2.crypto_pay = fake
assert pid2 in bot2.pending_payments, "после перезапуска ожидание оплаты потеряно"
assert pid not in bot2.pending_payments, "оплаченный счёт не должен быть в очереди"
print("OK 3/3: после перезапуска ожидающие счета восстановлены из БД")

print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ")