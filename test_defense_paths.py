#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Защитные пути, не покрытые test_security_and_p2.py:
  1. generate_and_send_config без установленных ключей VPS: конфиг НЕ выдаётся,
     заказ не создаётся, возвращается False + приглашение установить ключи
  2. IDOR: update_<order_id> отклоняет чужого пользователя (без документов/установки)
  3. _reply_from_owner: ответ владельца доставляется и тикет удаляется; не-админ игнорируется
  4. _md_escape экранирует спецсимволы Markdown (backtick, _, *, скобки, ! и др.)
  5. _parse_vps_output: мусор и невалидные данные отклоняются; валидный блок парсится
  6. /stats молчит для не-админа
  7. _save_setup_and_deliver с пустыми данными не сохраняет и не шлёт конфиг"""
import os
import sys
import tempfile
from types import SimpleNamespace

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import telegram_bot as tb

_ORIG_PAYMENT_SYSTEM = tb.PaymentSystem
tb.ADMIN_USER_IDS = [777]


class _Ctx:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="def_")
        self.db = os.path.join(self.tmp, "payments.db")
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
            id="cbq_def_1",
            data=data,
            from_user=SimpleNamespace(id=user),
            message=SimpleNamespace(chat=SimpleNamespace(id=user + 1000, type='private')),
        )


def _make_bot(ctx):
    bot = tb.AutoConfigBot(tb.TELEGRAM_BOT_TOKEN)
    bot.sent_msgs = []
    bot.sent_markups = []
    bot.sent_docs = []
    bot.sent_forwards = []

    def fake_send_message(chat_id, text, **kw):
        bot.sent_msgs.append(text)
        bot.sent_markups.append(kw.get("reply_markup"))
        return SimpleNamespace(message_id=1)

    def fake_send_document(chat_id, doc, **kw):
        bot.sent_docs.append(kw.get("caption") or "")
        return SimpleNamespace(message_id=1)

    def fake_forward_message(admin_id, from_chat_id, message_id):
        bot.sent_forwards.append(message_id)
        return SimpleNamespace(message_id=555)

    bot.bot.send_message = fake_send_message
    bot.bot.send_document = fake_send_document
    bot.bot.forward_message = fake_forward_message
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


# ============ 1. Без ключей VPS конфиг не выдаётся ============
ctx = _Ctx()
bot = _make_bot(ctx)
USER, CHAT = 777, 1777
bot.user_data[USER] = {
    "payment_id": "pay_guard", "user_id": USER, "chat_id": CHAT,
    "server_ip": "95.217.1.1", "sni_hostname": "api.notion.com",
}
bot.last_cmd.clear()
ok = bot.generate_and_send_config(USER, CHAT)
assert not ok, "без установленных ключей VPS конфиг не должен отдаваться"
assert len(bot.sent_docs) == 0, "документ не должен уходить без настоящих ключей"
assert any("требует установки Xray" in m for m in bot.sent_msgs), \
    f"нет приглашения установить ключи: {bot.sent_msgs[:200]}"
assert bot.payment_system.find_paid_order("pay_guard") is None, \
    "заказ не должен создаваться на несуществующих ключах"
print("OK 1/7: без ключей VPS конфиг не выдаётся (False, нет документа, нет заказа)")

# ============ 2. IDOR: чужой не трогает чужой заказ ============
ctx = _Ctx()
bot = _make_bot(ctx)
A, B = 777, 888
order_id = bot.payment_system.create_order(
    payment_id="pay_a", user_id=A, config_path="/tmp/conf_a.zip",
    server_ip="95.217.1.1", uuid="11111111-2222-3333-4444-555555555555",
    sni_hostname="api.notion.com",
)
bot.payment_system.mark_order_delivered(order_id)
cb = _handler(bot, "callback_query_handlers")
bot.last_cmd.clear()
cb(ctx.call(f"update_{order_id}", B))
assert bot.payment_system.get_user_orders(B) == [], "у чужака не должно появиться заказов"
assert len(bot.sent_docs) == 0, "чужой пользователь не должен получить конфиг"
assert any("Здравствуйте" in m for m in bot.sent_msgs), f"нет welcome: {bot.sent_msgs[:200]}"
assert not any(
    m and any(b.to_dict().get("callback_data") in ("install_ssh", "install_self")
              for row2 in m.keyboard for b in row2)
    for m in bot.sent_markups
), "чужой не должен видеть кнопки установки чужого заказа"
print("OK 2/7: update_ чужого заказа отклонён (IDOR) — без документа и без кнопок установки")

# ============ 3. Ответ владельца: доставка + удаление тикета ============
ctx = _Ctx()
bot = _make_bot(ctx)
USER, CHAT = 555, 1555
q = ctx.msg("Помогите с настройкой", user=USER, chat=CHAT)
ok = bot._forward_to_owner(q)
assert ok and bot.sent_forwards == [q.message_id], "вопрос не переслан владельцу"
assert bot.support_forwards.get(555) == (USER, CHAT), "тикет не зарегистрирован"

reply = SimpleNamespace(
    from_user=SimpleNamespace(id=777),  # владелец
    chat=SimpleNamespace(id=777 + 2000, type='private'),
    message_id=10,
    text="Попробуйте перезагрузить роутер",
    reply_to_message=SimpleNamespace(message_id=555),
)
bot.sent_msgs.clear()
ok = bot._reply_from_owner(reply)
assert ok, "ответ владельца не ушёл"
assert any("Ответ поддержки" in m and "перезагрузить роутер" in m for m in bot.sent_msgs), \
    f"пользователь не получил ответ: {bot.sent_msgs}"
assert 555 not in bot.support_forwards, "отработанный тикет должен удаляться"
assert any("Ответ отправлен пользователю 555" in m for m in bot.sent_msgs), "владельцу нет подтверждения"

# Не-админ пытается ответить — игнор, тикет остаётся
bot.support_forwards[666] = (USER, CHAT)
outsider = SimpleNamespace(
    from_user=SimpleNamespace(id=424242),
    chat=SimpleNamespace(id=100500, type='private'),
    message_id=11,
    text="обман",
    reply_to_message=SimpleNamespace(message_id=666),
)
ok = bot._reply_from_owner(outsider)
assert not ok, "не-админ не может отвечать как поддержка"
assert bot.support_forwards.get(666) == (USER, CHAT), "чужой ответ не должен удалять тикет"
print("OK 3/7: ответ владельца доставлен, тикет удалён; не-админ ответить не может")

# ============ 4. _md_escape ============
md = tb.AutoConfigBot._md_escape
assert md("a`b") == r"a\`b"
assert md("x_y*z") == r"x\_y\*z"
assert md("ip:95.217.1.1!") == r"ip:95\.217\.1\.1\!"
assert md("[brackets]") == r"\[brackets\]"
assert md("a-b+c=d") == r"a\-b\+c\=d"
assert md("backtick `cmd` done") == r"backtick \`cmd\` done"
print("OK 4/7: _md_escape экранирует спецсимволы Markdown (в т.ч. backtick)")

# ============ 5. _parse_vps_output ============
bot = _make_bot(_Ctx())
valid = (
    "что-то сверху\n"
    "===BELIY-OBHODCHIK-VLESS===\n"
    "SERVER_IP=8.8.8.8\n"
    "UUID=22222222-3333-4444-5555-666666666666\n"
    "PUBLIC_KEY=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789XX\n"
    "SHORT_ID=1a2b3c4d\n"
    "SNI_HOSTNAME=api.notion.com\n"
    "SNI_IP=1.2.3.4\n"
    "===BELIY-OBHODCHIK-VLESS-END===\n"
    "хвост\n"
)
p = bot._parse_vps_output(valid)
assert p and p["SERVER_IP"] == "8.8.8.8" and p["SNI_HOSTNAME"] == "api.notion.com", p
assert bot._parse_vps_output("") is None
assert bot._parse_vps_output("нет маркеров") is None
assert bot._parse_vps_output(valid.replace("8.8.8.8", "127.0.0.1")) is None, "loopback не должен пройти"
assert bot._parse_vps_output(valid.replace("8.8.8.8", "10.1.2.3")) is None, "private не должен пройти"
assert bot._parse_vps_output(valid.replace("8.8.8.8", "1.2.3")) is None, "не-IPv4 не должен пройти"
assert bot._parse_vps_output(valid.replace("api.notion.com", "x.com;id")) is None, "hostname с инъекцией"
assert bot._parse_vps_output(valid.replace("UUID=22222222-3333-4444-5555-666666666666", "UUID=не-ууид")) is None
assert bot._parse_vps_output(valid.replace("PUBLIC_KEY=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789XX", "PUBLIC_KEY=!!!")) is None
assert bot._parse_vps_output(valid.replace("SHORT_ID=1a2b3c4d", "SHORT_ID=zzzz")) is None, "short_id должен быть hex"
print("OK 5/7: _parse_vps_output отклоняет мусор, парсит валидный блок")

# ============ 6. /stats: не-админ не получает статистику ============
ctx = _Ctx()
bot = _make_bot(ctx)
stats_handler = _handler(bot, "message_handlers", "commands", ["stats"])
bot.sent_msgs.clear()
stats_handler(ctx.msg("/stats", user=989))
joined = " ".join(bot.sent_msgs)
assert "Пользователей" not in joined, f"не-админ увидел статистику: {joined[:200]}"
print("OK 6/7: /stats молчит для не-администратора")

# ============ 7. _save_setup_and_deliver без данных ============
ctx = _Ctx()
bot = _make_bot(ctx)
ok = bot._save_setup_and_deliver(777, 1777, None)
assert not ok and len(bot.sent_docs) == 0, "пустой вывод не должен сохраняться/отправляться"
assert bot.payment_system.get_vps_setup(777) is None, "ключи не должны сохраняться без данных"
print("OK 7/7: _save_setup_and_deliver с пустыми данными ничего не делает")

tb.PaymentSystem = _ORIG_PAYMENT_SYSTEM
print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ")