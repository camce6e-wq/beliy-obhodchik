#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Регресс по итогам аудита безопасности (P0/P1/P2):
  1. /stats считает выручку по источникам (crypto vs stars) — не заглушка
  2. Звёздный заказ переживает рестарт: invoice в БД -> повторная successful_payment
     не создаёт дублирующий заказ и не шлёт конфиг дважды
  3. SSH-инъекция: невалидный IP/hostname отклоняются до подключения, env-строки экранируются
  4. sni_manager: повторный add_donor не сбрасывает статистику; CDN определяется по hostname
  5. keenetic quick_generate: настоящая x25519 пара — ключ 32 байта в base64, уникален"""
import os
import sys
import tempfile
import sqlite3
import base64
import zipfile
import shlex
from types import SimpleNamespace

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import telegram_bot as tb
import sni_manager as sni
import keenetic_config_generator as kcg

_ORIG_PAYMENT_SYSTEM = tb.PaymentSystem

# Тесты не должны зависеть от локального .env.
# /stats и /status доступны только админам — тестовый пользователь и будет админом.
tb.ADMIN_USER_IDS = [777]


class _Ctx:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="sec_")
        self.db = os.path.join(self.tmp, "payments.db")
        # Принимаем любые аргументы и всегда используем свою БД: так можно
        # создавать несколько ботов в одном процессе без вложенных патчей.
        tb.PaymentSystem = lambda *a, **k: _ORIG_PAYMENT_SYSTEM(db_path=self.db)

    def msg(self, text="/start", user=777, chat=None, ctype='private'):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=user),
            chat=SimpleNamespace(id=chat if chat is not None else user + 1000, type=ctype),
            message_id=1,
            text=text,
        )

    def call(self, data, user=777):
        return SimpleNamespace(
            id="cbq_1",
            data=data,
            from_user=SimpleNamespace(id=user),
            message=SimpleNamespace(chat=SimpleNamespace(id=user + 1000, type='private')),
        )


def _make_bot(ctx):
    bot = tb.AutoConfigBot(tb.TELEGRAM_BOT_TOKEN)
    bot.sent_msgs = []
    bot.sent_markups = []
    bot.sent_invoices = []
    bot.sent_docs = []

    def fake_send_message(chat_id, text, **kw):
        bot.sent_msgs.append(text)
        bot.sent_markups.append(kw.get("reply_markup"))
        return SimpleNamespace(message_id=1)

    def fake_send_document(chat_id, doc, **kw):
        bot.sent_docs.append(kw.get("caption") or "")
        return SimpleNamespace(message_id=1)

    def fake_send_invoice(chat_id, title, description, payload, provider_token, currency, prices, **kw):
        bot.sent_invoices.append({"payload": payload, "amount": prices[0].amount})
        return SimpleNamespace(message_id=1)

    bot.bot.send_message = fake_send_message
    bot.bot.send_document = fake_send_document
    bot.bot.send_invoice = fake_send_invoice
    return bot


def _handler(bot, list_name, key=None, value=None):
    handlers = getattr(bot.bot, list_name)
    if key is None:
        return handlers[-1]['function']
    for h in handlers:
        val = h.get("filters", {}).get(key, h.get(key))
        if val == value:
            return h['function']
    raise AssertionError(f"хендлер {list_name}/{key}={value} не найден")


# ============ 1. /stats: честная выручка по источникам ============
ctx = _Ctx()
bot = _make_bot(ctx)

r1 = bot.payment_system.create_payment(111, "crypto_user", amount=tb.VPS_RUB_PRICE, source="crypto")
bot.payment_system.confirm_payment(r1)
r2 = bot.payment_system.create_payment(222, "stars_user", amount=tb.VPS_RUB_PRICE, source="crypto")
bot.payment_system.mark_payment_stars(r2, tb.STARS_PRICE)
bot.payment_system.confirm_payment(r2)
r3 = bot.payment_system.create_payment(333, "dpi_stars", amount=1, source="crypto")
bot.payment_system.mark_payment_stars(r3, tb.DPI_STARS_PRICE)
bot.payment_system.confirm_payment(r3)
r4 = bot.payment_system.create_payment(444, "pending_user", amount=tb.VPS_RUB_PRICE, source="crypto")  # не оплачен

stats_handler = _handler(bot, "message_handlers", "commands", ["stats"])
bot.last_cmd.clear()
stats_handler(ctx.msg("/stats"))
joined = " ".join(bot.sent_msgs)
assert f"Выручка (crypto): {tb.VPS_RUB_PRICE} ₽" in joined, f"crypto-выручка неверна: {joined[:300]}"
assert f"⭐ Выручка (звёзды): ~{tb.STARS_PRICE + tb.DPI_STARS_PRICE} ⭐" in joined, f"stars-выручка неверна: {joined[:300]}"
print(f"OK 1/5: /stats различает crypto={tb.VPS_RUB_PRICE}р и stars={tb.STARS_PRICE + tb.DPI_STARS_PRICE}⭐ (оплаченное не учитывается)")

# ============ 2. Звёздный заказ: переживает рестарт, без дублей ============
ctx2 = _Ctx()
bot = _make_bot(ctx2)
USER, CHAT = 555, 5555
pid = bot.payment_system.create_payment(USER, "star_restart")
bot.user_data[USER] = {"payment_id": pid, "server_ip": "95.217.1.1", "sni_hostname": "api.notion.com"}
cb = _handler(bot, "callback_query_handlers")
bot.last_cmd.clear()
cb(ctx2.call("pay_stars", USER))
assert len(bot.sent_invoices) == 1, "инвойс звёзд не создан"

sp = _handler(bot, "message_handlers", "content_types", ["successful_payment"])
bot.sent_msgs.clear(); bot.sent_markups.clear(); bot.sent_docs.clear(); bot.last_cmd.clear()
sm = SimpleNamespace(
    from_user=SimpleNamespace(id=USER),
    chat=SimpleNamespace(id=CHAT, type='private'),
    successful_payment=SimpleNamespace(invoice_payload=pid, total_amount=tb.STARS_PRICE),
)
sp(sm)
has_install = any(
    m and any(b.to_dict().get("callback_data") in ("install_ssh", "install_self") for row2 in m.keyboard for b in row2)
    for m in bot.sent_markups
)
assert has_install, "после оплаты звёздами не показаны кнопки установки"
assert len(bot.sent_docs) == 0, "документ не должен уходить до установки ключей"

# Ключи установлены -> доставка
bot.payment_system.save_vps_setup(
    user_id=USER, server_ip="95.217.1.1", uuid="11111111-2222-3333-4444-555555555555",
    public_key="realpub==", short_id="11112222", sni_hostname="api.notion.com",
    install_method="self",
)
ok = bot.generate_and_send_config(USER, CHAT, data={
    "payment_id": pid, "user_id": USER, "chat_id": CHAT,
    "server_ip": "95.217.1.1", "sni_hostname": "api.notion.com",
})
assert ok and len(bot.sent_docs) == 1, "конфиг не доставлен после установки ключей"

# РЕСТАРТ: новый бот, user_data пуст; счёт и заказ восстановлены из БД
bot2 = _make_bot(ctx2)
# Форсируем пустой user_data нового бота
assert pid not in bot2.user_data.get(USER, {}), "новый бот не должен помнить user_data"
sp2 = _handler(bot2, "message_handlers", "content_types", ["successful_payment"])
sm2 = SimpleNamespace(
    from_user=SimpleNamespace(id=USER),
    chat=SimpleNamespace(id=CHAT, type='private'),
    successful_payment=SimpleNamespace(invoice_payload=pid, total_amount=tb.STARS_PRICE),
)
bot2.last_cmd.clear()
sp2(sm2)
joined2 = "\n".join(bot2.sent_msgs)
assert "уже была принята" in joined2, f"после рестарта не сработала идемпотентность: {joined2[:300]}"
assert len(bot2.sent_docs) == 0, "после рестарта документ не должен приходить повторно"

con = sqlite3.connect(ctx2.db)
orders = con.execute("SELECT COUNT(*) FROM orders WHERE payment_id=?", (pid,)).fetchone()[0]
paid = con.execute("SELECT status FROM payments WHERE payment_id=?", (pid,)).fetchone()[0]
con.close()
assert orders == 1, "заказ должен быть ровно один (идемпотентность create_order)"
assert paid == "paid", "платёж должен остаться paid"
print("OK 2/5: звёздный заказ восстанавливается из БД после рестарта без дублей (1 заказ, 1 документ)")

# ============ 3. SSH-инъекция отклоняется до подключения + экранирование ============
ctx3 = _Ctx()
bot3 = _make_bot(ctx3)
USER3, CHAT3 = 666, 6666
for bad_ip, bad_host in [
    ("127.0.0.1", "api.notion.com"),
    ("10.0.0.5", "x; rm -rf /"),
    ("95.217.1.1", "notion.com;id"),
]:
    bot3.sent_msgs.clear(); bot3.last_cmd.clear()
    bot3._run_ssh_install(USER3, CHAT3, "pw", {"server_ip": bad_ip, "sni_hostname": bad_host})
    joined3 = "\n".join(bot3.sent_msgs)
    assert "Невалидный IP сервера или SNI-донор" in joined3, f"инъекция {bad_ip}/{bad_host} не отклонена: {joined3[:200]}"
    assert bot3.user_states.get(USER3) is None, "состояние должно быть сброшено"

env = tb.AutoConfigBot._ssh_env_line("api.notion.com", "45.88.101.5")
assert env == "export SNI_HOSTNAME=api.notion.com SNI_IP=45.88.101.5\n", f"env-строка неверна: {env!r}"
# Инъекция не выполнится: shlex должен разобрать подставленное значение как ОДИН токен
tokens = shlex.split(tb.AutoConfigBot._ssh_env_line("a;rm -rf /", "1.1.1.1;x"))
assert "SNI_HOSTNAME=a;rm -rf /" in tokens, f"хост должен быть одним токеном: {tokens}"
assert "SNI_IP=1.1.1.1;x" in tokens, f"IP должен быть одним токеном: {tokens}"
print("OK 3/5: SSH-инъекции отклонены, env-переменные экранируются через shlex.quote")

# ============ 4. sni_manager: статистика не сбрасывается, CDN по hostname ============
sni_db_path = os.path.join(ctx3.tmp, "sni_test.db")
sd = sni.SNIDatabase(db_path=sni_db_path)
donor_id = sd.add_donor("api.example-stable.com", "1.2.3.4", country_code="US", has_cdn=False)
for _ in range(5):
    sd.update_success_rate(donor_id, True, 42)
sd.update_success_rate(donor_id, False, 500)
con = sqlite3.connect(sni_db_path)
r = con.execute("SELECT success_rate, total_checks, failure_count FROM sni_donors WHERE id=?", (donor_id,)).fetchone()
con.close()
assert r is not None and r[1] == 6 and r[2] == 1, f"статистика не накоплена: {r}"

# Повторный add_donor того же hostname — метаданные обновляются, статистика сохраняется
sd.add_donor("api.example-stable.com", "5.6.7.8", country_code="DE", has_cdn=False)
con = sqlite3.connect(sni_db_path)
r2 = con.execute(
    "SELECT ip_address, country_code, success_rate, total_checks, failure_count FROM sni_donors WHERE id=?",
    (donor_id,),
).fetchone()
con.close()
assert r2[0] == "5.6.7.8" and r2[1] == "DE", f"метаданные не обновились: {r2}"
assert r2[2] == r[0] and r2[3] == 6 and r2[4] == 1, f"статистика сброшена после повторного add_donor: {r2}"

cdn_id = sd.add_donor("static.cloudfront.net", "9.9.9.9")  # has_cdn не передан — автоопределение
con = sqlite3.connect(sni_db_path)
cdn = con.execute("SELECT has_cdn FROM sni_donors WHERE id=?", (cdn_id,)).fetchone()[0]
con.close()
assert cdn == 1, "CDN-хост должен определяться по имени"
print("OK 4/5: add_donor сохраняет статистику (UPSERT), CDN определяется по hostname")

# ============ 5. keenetic: настоящая x25519 в quick_generate ============
ctx5 = _Ctx()
archive1, p1 = kcg.quick_generate("45.88.101.5", "api.notion.com")
archive2, p2 = kcg.quick_generate("45.88.101.5", "api.notion.com")
assert os.path.exists(archive1) and archive1.endswith(".zip")
assert p1["public_key"] != p2["public_key"], "ключи должны быть уникальными (RNG)"
for key in (p1["public_key"], p2["public_key"]):
    raw = base64.b64decode(key.encode("ascii"))
    assert len(raw) == 32, "публичный ключ должен быть ровно 32 байта (x25519)"
with zipfile.ZipFile(archive1) as zf:
    names = zf.namelist()
    assert "README.txt" in names and "vless_url.txt" in names, "zip без README/vless_url"
assert p1["uuid"] and len(p1["short_id"]) == 8
try:
    os.remove(archive1)
    os.remove(archive2)
except OSError:
    pass
print("OK 5/5: quick_generate даёт настоящую x25519-пару (32-байт ключ, уникальный)")

tb.PaymentSystem = _ORIG_PAYMENT_SYSTEM
print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ")