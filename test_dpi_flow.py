#!/usr/bin/env python3
"""Офлайн-тест бесплатной выдачи услуги «Без сервера» (DPI/Zapret):
* /dpi -> запись в учёте (source=free, amount=0, paid) -> сразу доставка setup_nfqws.sh
* повторный /dpi не ломает учёт и не дублирует доставку по тому же payment_id
* неизвестная команда -> подсказка с главным меню (не молчим)"""

import os
import sqlite3
import sys
import tempfile
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import telegram_bot as tb

TMP = tempfile.mkdtemp(prefix="dpitest_")
DB = os.path.join(TMP, "payments.db")
REAL_PAYMENT_SYSTEM = tb.PaymentSystem


def _payment_system_factory(*args, **kwargs):
    return REAL_PAYMENT_SYSTEM(db_path=DB)


tb.PaymentSystem = _payment_system_factory

bot = tb.AutoConfigBot(tb.TELEGRAM_BOT_TOKEN)

sent_messages = []
sent_docs = []
sent_markups = []


def fake_send_message(chat_id, text, **kw):
    sent_messages.append(text)
    sent_markups.append(kw.get("reply_markup"))
    return SimpleNamespace(message_id=1)


def fake_send_document(chat_id, doc, **kw):
    sent_docs.append(kw.get("caption") or "")
    return SimpleNamespace(message_id=1)


bot.bot.send_message = fake_send_message
bot.bot.send_document = fake_send_document


def msg(text, user_id):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=user_id + 1000, type="private"),
        message_id=1,
        text=text,
    )


def by_name(name):
    for h in bot.bot.message_handlers:
        if h["function"].__name__ == name:
            return h["function"]
    raise AssertionError(f"хендлер {name} не найден")


def sent_clear():
    sent_messages.clear()
    sent_markups.clear()
    sent_docs.clear()
    bot.last_cmd.clear()


def row_for(user_id):
    con = sqlite3.connect(DB)
    row = con.execute(
        "SELECT payment_id, amount, currency, status, source FROM payments"
        " WHERE user_id=? ORDER BY rowid DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    con.close()
    return row


# ===== Часть 1. /dpi: бесплатная выдача =====
U1 = 5111
sent_clear()
by_name("make_dpi_order")(msg("/dpi", U1))
row = row_for(U1)
assert row, "выдача не записана в учёт"
pid, amount, currency, status, source = row
assert pid.startswith("dpi_"), f"payment_id должен иметь префикс dpi_, а был {pid}"
assert amount == 0, f"сумма должна быть 0 (бесплатно), а была {amount}"
assert status == "paid", f"статус должен быть paid (выдано), а был {status}"
assert source == "free", f"source должен быть free, а был {source}"
print(f"OK 1/4: /dpi -> бесплатная выдача {pid} (amount=0, status=paid, source=free)")

# Скрипт ушёл сразу, без кнопок оплаты
assert any("setup_nfqws.sh" in c for c in sent_docs), "скрипт не отправлен сразу"
assert any("по шагам" in t for t in sent_messages), "нет инструкции по шагам"
text = " ".join(sent_messages)
assert "₽" not in text and "⭐" not in text, f"в тексте не должно быть цен: {text[:200]}"
assert not any(
    m
    and any(
        b.to_dict().get("callback_data", "").startswith(("make_dpi_payment", "pay_dpi_stars"))
        for row2 in m.keyboard
        for b in row2
    )
    for m in sent_markups
), "кнопок оплаты быть не должно"
print("OK 2/4: скрипт доставлен сразу, кнопок оплаты нет, цен в тексте нет")

# Заказ создан и отмечен доставленным (идемпотентность учёта)
con = sqlite3.connect(DB)
order = con.execute(
    "SELECT config_path, delivered_at FROM orders WHERE payment_id=?", (pid,)
).fetchone()
con.close()
assert order and order[0] == "setup_nfqws.sh", f"заказ не создан: {order}"
assert order[1] is not None, "заказ не отмечен доставленным"
print("OK 3/4: заказ создан и отмечен доставленным")

# Повторный вызов той же выдачи не плодит заказ
order_id = bot.payment_system.create_order(
    payment_id=pid, user_id=U1, config_path="setup_nfqws.sh",
    server_ip="", uuid="", sni_hostname="",
)
con = sqlite3.connect(DB)
n = con.execute("SELECT COUNT(*) FROM orders WHERE payment_id=?", (pid,)).fetchone()[0]
con.close()
assert n == 1, f"повторный create_order должен быть идемпотентным, заказов: {n}"
print("OK 4/4: повторная выдача по тому же payment_id не дублирует заказ")

# ===== Часть 2. Неизвестная команда =====
U2 = 7333
sent_clear()
by_name("handle_unknown_command")(msg("/definitely_not_a_command", U2))
assert any("Не знаю команду" in t for t in sent_messages), (
    "нет подсказки про неизвестную команду"
)
has_menu = any(
    m
    and any(
        b.to_dict().get("callback_data") in ("cmd_menu", "cmd_buy", "cmd_dpi")
        for row2 in m.keyboard
        for b in row2
    )
    for m in sent_markups
)
assert has_menu, "у неизвестной команды должно быть главное меню"
print("OK 5/5: неизвестная команда -> подсказка + главное меню")

print("\nВсе проверки бесплатной выдачи DPI пройдены.")