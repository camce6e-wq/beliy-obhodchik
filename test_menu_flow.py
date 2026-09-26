#!/usr/bin/env python3
"""Офлайн-тест кнопочного меню: /start -> inline-кнопки, cmd_* диспетчер,
отмена и «пустой экран» -> возврат к приветствию."""
import sys
from types import SimpleNamespace

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import telegram_bot as tb

# Тест не должен зависеть от локального .env (там может быть ADMIN_USER_IDS)
tb.ADMIN_USER_IDS = []

bot = tb.AutoConfigBot(tb.TELEGRAM_BOT_TOKEN)

sent = []


def fake_send_message(chat_id, text, **kw):
    sent.append({"chat_id": chat_id, "text": text, "markup": kw.get("reply_markup")})
    return SimpleNamespace(message_id=1)


def fake_answer_callback_query(callback_query_id):
    pass


class FakeBot:
    def __init__(self, real):
        self.real = real

    def send_message(self, chat_id, text, **kw):
        return fake_send_message(chat_id, text, **kw)

    def answer_callback_query(self, qid):
        fake_answer_callback_query(qid)

    def __getattr__(self, name):
        return getattr(self.real, name)


bot.bot = FakeBot(bot.bot)

USER = 777
CHAT = 888


def msg(text="/start"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=USER),
        chat=SimpleNamespace(id=CHAT, type='private'),
        message_id=1,
        text=text,
    )


def call(data):
    return SimpleNamespace(
        id="cbq_1",
        data=data,
        from_user=SimpleNamespace(id=USER),
        message=SimpleNamespace(chat=SimpleNamespace(id=CHAT, type='private')),
    )


# 1) /start показывает приветствие с inline-кнопками главного меню
sent.clear()
bot.last_cmd.clear()
start_handler = None
for h in bot.bot.message_handlers:
    cmds = h.get("filters", {}).get("commands")
    if isinstance(cmds, list) and "start" in cmds:
        start_handler = h["function"]
        break
assert start_handler, "хендлер /start не найден"
start_handler(msg("/start"))
assert len(sent) == 1, "должно быть одно приветствие"
m = sent[0]
assert "БелыйОбходчик" in m["text"]
assert "/buy" not in m["text"], "в приветствии не должно быть ссылок на команды"
kb = m["markup"]
assert kb is not None, "приветствие должно быть с inline-кнопками"
assert kb.to_dict().get("inline_keyboard"), "это должны быть inline-кнопки"
print(f"OK 1/5: /start -> приветствие + {sum(len(r) for r in kb.to_dict()['inline_keyboard'])} inline-кнопок")

# 2) cmd_* диспетчер: «Купить настройку» -> инструкция с ценами -> ввод IP -> кнопки оплаты
sent.clear()
bot.last_cmd.clear()
cb_handler = [h for h in bot.bot.callback_query_handlers if 'function' in h][-1]['function']
cb_handler(call("cmd_buy"))
assert sent, "cmd_buy должен что-то отправить"
text = " ".join(s["text"] for s in sent)
assert "1500" in text and "750" in text, "цены VPS 1500₽/750⭐ должны быть в тексте"
print("OK 2/6: cmd_buy -> инструкция с ценами 1500₽/750⭐")

sent.clear()
bot.last_cmd.clear()
process_ip = None
for h in bot.bot.message_handlers:
    if h["function"].__name__ == "process_server_ip":
        process_ip = h["function"]
        break
assert process_ip, "хендлер ввода IP не найден"
process_ip(msg("95.217.1.1"))
assert sent, "после ввода IP должны быть кнопки оплаты"
if not any(s["markup"] for s in sent):
    print("SENT:", [s["text"][:80] for s in sent])
assert any(s["markup"] for s in sent), "должны быть кнопки оплаты"
print("OK 3/6: ввод IP -> кнопки оплаты")

# 4) Отмена заказа -> сразу возврат к приветствию
sent.clear()
bot.last_cmd.clear()
cb_handler(call("cancel_purchase"))
assert len(sent) == 1, "после отмены должно быть одно сообщение"
assert "БелыйОбходчик" in sent[0]["text"], "после отмены показывается приветствие"
assert "Отменён" not in sent[0]["text"], "не должно быть текста про отмену"
print("OK 4/6: отмена -> приветствие")

# 5) cmd_menu -> приветствие (кнопка «В главное меню» в /router)
sent.clear()
bot.last_cmd.clear()
cb_handler(call("cmd_menu"))
assert len(sent) == 1 and "БелыйОбходчик" in sent[0]["text"]
print("OK 5/6: cmd_menu -> приветствие")

# 6) Все кнопки меню ведут на реальные обработчики (не тупики)
menus = tb.AutoConfigBot._main_menu_inline_keyboard(bot)
rows = menus.to_dict()["inline_keyboard"]
labels = [b["callback_data"] for row in rows for b in row if "callback_data" in b]
webapp_buttons = [b["web_app"]["url"] for row in rows for b in row if "web_app" in b]
expected = {"cmd_buy", "cmd_router", "cmd_dpi", "cmd_vps", "cmd_guide",
            "cmd_faq", "cmd_myorders", "cmd_support"}
assert set(labels) == expected, f"набор кнопок = {set(labels)}"
assert len(webapp_buttons) == 1 and "/webapp/" in webapp_buttons[0], \
    f"в меню должна быть кнопка Mini App: {webapp_buttons}"
for cb in sorted(expected):
    sent.clear()
    bot.last_cmd.clear()
    try:
        cb_handler(call(cb))
    except Exception as e:
        print(f"  ERROR {cb}: {type(e).__name__}: {e}")
        raise
    assert sent, f"кнопка {cb} должна показывать экран (не тупик)"
print(f"OK 6/6: все {len(expected)} кнопок меню открывают экраны")

# 7) Все статические callback_data бота не падают и не оставляют без ответа
bot.user_data[USER] = {
    "payment_id": tb.PaymentSystem().create_payment(USER, "menu_tester"),
    "server_ip": "95.217.1.1",
    "sni_hostname": "api.notion.com",
}
used = [b["callback_data"] for row in rows for b in row if "callback_data" in b]
known = {"cancel_purchase", "create_new", "install_self", "install_ssh",
         "make_dpi_payment", "make_payment", "pay_dpi_stars", "pay_stars",
         "retry_ssh_install", "update_existing"}
for cb in sorted(known):
    sent.clear()
    bot.last_cmd.clear()
    try:
        cb_handler(call(cb))
    except Exception as e:
        print(f"  ERROR {cb}: {type(e).__name__}: {e}")
        raise
    assert sent, f"callback {cb} должен на что-то ответить (не пустота)"
print(f"OK 7/7: все {len(known)} callback_data обрабатываются без падений")

# 8) Динамические колбэки router_* (все категории) и update_<order> (реальный заказ)
router_keys = list(tb.ROUTER_CATEGORIES.keys())
assert router_keys, "нет категорий роутеров"
for key in router_keys:
    sent.clear()
    bot.last_cmd.clear()
    cb_handler(call(f"router_{key}"))
    assert sent, f"router_{key} должен показывать подборку"
    assert any(s["markup"] for s in sent), f"router_{key} должен давать путь назад (кнопки)"
print(f"OK 8/9: все {len(router_keys)} категорий роутеров показывают подборку с меню")

sent.clear()
bot.last_cmd.clear()
pid = tb.PaymentSystem().create_payment(USER, "update_tester")
bot.user_data[USER] = {"payment_id": pid}
order_id = bot.payment_system.create_order(
    payment_id=pid, user_id=USER,
    config_path="", server_ip="95.217.1.1", uuid="u", sni_hostname="api.notion.com",
)
cb_handler(call(f"update_{order_id}"))
assert sent, "update_<order> должен отвечать"
print("OK 9/10: update_<order> с реальным заказом обрабатывается")

# 10) /status — хендлер состояния системы (упоминается в /guide и INSTRUCTIONS.md)
status_handler = None
for h in bot.bot.message_handlers:
    cmds = h.get("filters", {}).get("commands")
    if isinstance(cmds, list) and "status" in cmds:
        status_handler = h["function"]
        break
assert status_handler, "хендлер /status не найден"
sent.clear()
bot.last_cmd.clear()
status_handler(msg("/status"))
assert sent, "/status должен отвечать"
assert "Статус системы" in " ".join(s["text"] for s in sent)
assert any(s["markup"] for s in sent), "/status должен давать путь назад (меню)"
print("OK 10/10: /status -> состояние системы с меню")

print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ")