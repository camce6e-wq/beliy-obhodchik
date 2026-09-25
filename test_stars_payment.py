#!/usr/bin/env python3
"""Офлайн-тест оплаты звёздами Telegram (XTR): инвойс -> pre_checkout -> доставка."""
import os
import sys
import tempfile
import sqlite3
from types import SimpleNamespace

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import telegram_bot as tb

TMP = tempfile.mkdtemp(prefix="startest_")
DB = os.path.join(TMP, "payments.db")
REAL = tb.PaymentSystem
tb.PaymentSystem = lambda: REAL(db_path=DB)

bot = tb.AutoConfigBot(tb.TELEGRAM_BOT_TOKEN)

sent_invoices = []
sent_docs = []
sent_msgs = []
sent_markups = []


def fake_send_invoice(chat_id, title, description, payload, provider_token, currency, prices, **kw):
    sent_invoices.append({
        "chat_id": chat_id, "title": title, "payload": payload,
        "provider_token": provider_token, "currency": currency,
        "amount": prices[0].amount,
    })
    return SimpleNamespace(message_id=1)


def fake_send_document(chat_id, doc, **kw):
    sent_docs.append(kw.get("caption") or "")
    return SimpleNamespace(message_id=1)


def fake_send_message(chat_id, text, **kw):
    sent_msgs.append(text)
    sent_markups.append(kw.get("reply_markup"))
    return SimpleNamespace(message_id=1)


bot.bot.send_invoice = fake_send_invoice
bot.bot.send_document = fake_send_document
bot.bot.send_message = fake_send_message


def find_handler(list_name, key=None, value=None):
    handlers = getattr(bot.bot, list_name)
    if key is None:
        return handlers[-1]['function']
    for h in handlers:
        if key in h and h[key] == value:
            return h['function']
    return None


# 1) Пользователь нажимает "Оплатить звёздами"
USER = 333
CHAT = 444
pid = bot.payment_system.create_payment(USER, "star_tester")
bot.user_data[USER] = {
    "payment_id": pid,
    "server_ip": "95.217.1.1",
    "sni_hostname": "api.notion.com",
}
call = SimpleNamespace(
    from_user=SimpleNamespace(id=USER),
    message=SimpleNamespace(chat=SimpleNamespace(id=CHAT, type='private')),
)
cb_handler = find_handler("callback_query_handlers")
call.data = "pay_stars"
bot.last_cmd.clear()
cb_handler(call)

assert len(sent_invoices) == 1, "send_invoice не вызван"
inv = sent_invoices[0]
assert inv["currency"] == "XTR", "валюта должна быть XTR"
assert inv["provider_token"] == "", "для звёзд provider_token пустой"
assert inv["payload"] == pid, "payload = payment_id"
assert inv["amount"] == tb.STARS_PRICE, "цена в звёздах не совпадает"

print(f"OK 1/3: инвойс XTR на {inv['amount']} звёзд создан")

# 2) Pre-checkout -> подтверждаем
pc_handler = find_handler("pre_checkout_query_handlers")
answers = []


def fake_answer(qid, ok=True, **kw):
    answers.append(ok)


bot.bot.answer_pre_checkout_query = fake_answer
pc_query = SimpleNamespace(id="pcq_1")
pc_handler(pc_query)
assert answers == [True], "pre_checkout не подтверждён"
print("OK 2/3: pre_checkout подтверждён")

# 3) Успешная оплата -> доставка конфига
sp_handler = find_handler("message_handlers")
# убедимся, что это хендлер successful_payment
filters = [h for h in bot.bot.message_handlers if h.get("filters", {}).get("content_types") == ["successful_payment"]]
assert filters, "хендлер successful_payment не найден"
sp_handler = filters[0]["function"]

msg = SimpleNamespace(
    from_user=SimpleNamespace(id=USER),
    chat=SimpleNamespace(id=CHAT, type='private'),
    successful_payment=SimpleNamespace(invoice_payload=pid, total_amount=tb.STARS_PRICE),
)
sp_handler(msg)

# Ключей ещё нет -> вместо документа показываются кнопки выбора установки
def _has_install_buttons():
    return any(
        m and any(
            b.to_dict().get("callback_data") in ("install_ssh", "install_self")
            for row2 in m.keyboard
            for b in row2
        )
        for m in sent_markups
    )

assert len(sent_docs) == 0, "документ не должен отправляться до установки ключей"
assert _has_install_buttons(), "не показаны кнопки выбора установки VPS"
con = sqlite3.connect(DB)
conf = con.execute("SELECT status FROM payments WHERE payment_id=?", (pid,)).fetchone()
con.close()
assert conf and conf[0] == "paid", "оплата не подтверждена"

# Установка выполнена -> доставка реального конфига
bot.payment_system.save_vps_setup(
    user_id=USER,
    server_ip="95.217.1.1",
    uuid="99999999-8888-7777-6666-555555555555",
    public_key="starpub==",
    short_id="aaaabbbb",
    sni_hostname="api.notion.com",
    install_method="ssh",
)
ok = bot.generate_and_send_config(USER, CHAT, data={
    "payment_id": pid,
    "user_id": USER,
    "chat_id": CHAT,
    "server_ip": "95.217.1.1",
    "sni_hostname": "api.notion.com",
})
assert ok, "не удалось сгенерировать конфиг после установки ключей"
assert len(sent_docs) == 1, "конфиг не отправлен после установки"


def _has_menu_buttons():
    return any(
        m and any(
            b.to_dict().get("callback_data") == "cmd_support"
            for row2 in m.keyboard
            for b in row2
        )
        for m in sent_markups
    )


assert _has_menu_buttons(), "после доставки конфига не показано стартовое меню"
print("OK 3/3: оплата звёздами подтверждена, кнопки установки показаны, после ключей конфиг доставлен + меню")
print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ")