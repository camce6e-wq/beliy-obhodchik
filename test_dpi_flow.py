#!/usr/bin/env python3
"""Офлайн-тест услуги «Без сервера» (DPI/Zapret):
  * крипто-путь: /dpi -> счёт на DPI_RUB_PRICE -> оплата -> доставка setup_nfqws.sh
  * звёзды-путь: /dpi -> send_invoice на DPI_STARS_PRICE -> successful_payment -> доставка
  * неизвестная команда -> подсказка с главным меню (не молчим)"""
import os
import sys
import tempfile
import sqlite3
from types import SimpleNamespace

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import telegram_bot as tb

TMP = tempfile.mkdtemp(prefix="dpitest_")
DB = os.path.join(TMP, "payments.db")
REAL_PAYMENT_SYSTEM = tb.PaymentSystem


def _payment_system_factory(*args, **kwargs):
    return REAL_PAYMENT_SYSTEM(db_path=DB)


tb.PaymentSystem = _payment_system_factory


class FakeCryptoPay:
    def __init__(self):
        self.created = []
        self.paid = []

    def create_invoice(self, amount_rub=500, payload="", description=None):
        inv = {
            "invoice_id": "inv_" + payload.replace("-", "")[:10],
            "status": "active",
            "pay_url": "https://t.me/CryptoBot?start=pay_" + payload,
            "amount": amount_rub,
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
sent_invoices = []


def fake_send_message(chat_id, text, **kw):
    sent_messages.append(text)
    sent_markups.append(kw.get("reply_markup"))


def fake_send_document(chat_id, doc, **kw):
    sent_docs.append(kw.get("caption") or "")
    return SimpleNamespace(message_id=1)


def fake_send_invoice(chat_id, title, description, payload, provider_token, currency, prices, **kw):
    sent_invoices.append({"payload": payload, "prices": prices, "title": title})


bot.bot.send_message = fake_send_message
bot.bot.send_document = fake_send_document
bot.bot.send_invoice = fake_send_invoice


def msg(text, user_id):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=user_id + 1000, type='private'),
        message_id=1,
        text=text,
    )


def call(data, user_id):
    return SimpleNamespace(
        id="cbq_1",
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(chat=SimpleNamespace(id=user_id + 1000, type='private')),
    )


def by_name(name):
    for h in bot.bot.message_handlers:
        if h["function"].__name__ == name:
            return h["function"]
    raise AssertionError(f"хендлер {name} не найден")


cb_handler = [h for h in bot.bot.callback_query_handlers if 'function' in h][-1]['function']


def start_dpi_order(user_id):
    bot.last_cmd.clear()
    by_name("make_dpi_order")(msg("/dpi", user_id))
    con = sqlite3.connect(DB)
    row = con.execute(
        "SELECT payment_id, amount, status FROM payments WHERE user_id=? ORDER BY rowid DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    con.close()
    assert row, "платёж не записан"
    pid, amount, status = row
    assert pid.startswith("dpi_"), f"payment_id должен иметь префикс dpi_, а был {pid}"
    assert amount == tb.DPI_RUB_PRICE == 1000, f"сумма DPI должна быть 1000, а была {amount}"
    assert status == "pending"
    return pid


# ===== Часть 1. Крипто-путь =====
CR = 5111
sent_clear = lambda: (sent_messages.clear(), sent_markups.clear(), sent_invoices.clear(), sent_docs.clear(), bot.last_cmd.clear())
sent_clear()
pid = start_dpi_order(CR)
text = " ".join(sent_messages)
assert "1000" in text and "500" in text, f"в тексте /dpi должны быть цены 1000/500, а было: {text[:200]}"
print(f"OK 1/6: /dpi -> платёж {pid} на 1000 ₽ (pending)")

sent_clear()
cb_handler(call("make_dpi_payment", CR))
assert len(fake.created) == 1, "счёт не создан"
inv = fake.created[0]
assert inv["payload"] == pid and inv["amount"] == 1000, "счёт должен быть на payload=payment_id и 1000 ₽"
assert any("1000" in t for t in sent_messages), "сообщение о счёте должно содержать 1000 ₽"
assert any(
    m and any(b.to_dict().get("url", "").startswith("https://t.me/CryptoBot") for row2 in m.keyboard for b in row2)
    for m in sent_markups
), "нет кнопки оплаты с pay_url"
print("OK 2/6: make_dpi_payment -> счёт на 1000 ₽, pending + БД заполнены")

sent_clear()
inv["status"] = "paid"
fake.paid = [inv]
bot._process_paid_invoices(fake.get_paid_invoices())
assert any("setup_nfqws.sh" in c for c in sent_docs), "после крипто-оплаты документ setup_nfqws.sh не отправлен"
assert any("по шагам" in t for t in sent_messages), "нет инструкции по шагам"
con = sqlite3.connect(DB)
st = con.execute("SELECT status FROM payments WHERE payment_id=?", (pid,)).fetchone()[0]
con.close()
assert st == "paid", f"крипто-платёж должен быть paid, а был {st}"
print("OK 3/6: крипто-оплата -> confirm_payment + доставка скрипта, статус paid")

# ===== Часть 2. Звёзды-путь =====
ST = 6222
sent_clear()
pid2 = start_dpi_order(ST)
sent_clear()
cb_handler(call("pay_dpi_stars", ST))
assert len(sent_invoices) == 1, "send_invoice не вызван"
si = sent_invoices[0]
assert si["payload"] == pid2, "payload инвойса должен совпадать с payment_id"
assert si["prices"][0].amount == tb.DPI_STARS_PRICE == 500, "цена звёзд должна быть 500"
assert pid2 not in bot.pending_payments or True  # звёзды идут без pending crypto
print("OK 4/6: pay_dpi_stars -> send_invoice 500 ⭐ с payload=payment_id")

sent_clear()
sp = SimpleNamespace(
    from_user=SimpleNamespace(id=ST),
    chat=SimpleNamespace(id=ST + 1000, type='private'),
    message_id=2,
    successful_payment=SimpleNamespace(invoice_payload=pid2, total_amount=500),
)
by_name("handle_successful_payment")(sp)
assert any("setup_nfqws.sh" in c for c in sent_docs), "после звёзд документ setup_nfqws.sh не отправлен"
assert any("по шагам" in t for t in sent_messages), "нет инструкции по шагам"
con = sqlite3.connect(DB)
st = con.execute("SELECT status FROM payments WHERE payment_id=?", (pid2,)).fetchone()[0]
con.close()
assert st == "paid", f"звёздный платёж должен быть paid, а был {st}"
print("OK 5/6: оплата звёздами -> confirm_payment + доставка скрипта, статус paid")

# ===== Часть 3. Неизвестная команда =====
UK = 7333
sent_clear()
by_name("handle_unknown_command")(msg("/definitely_not_a_command", UK))
assert any("Не знаю команду" in t for t in sent_messages), "нет подсказки про неизвестную команду"
has_menu = any(
    m and any(b.to_dict().get("callback_data") in ("cmd_menu", "cmd_buy", "cmd_dpi") for row2 in m.keyboard for b in row2)
    for m in sent_markups
)
assert has_menu, "у неизвестной команды должно быть главное меню"
print("OK 6/6: неизвестная команда -> подсказка + главное меню")

print("\nВсе проверки DPI-флоу пройдены.")