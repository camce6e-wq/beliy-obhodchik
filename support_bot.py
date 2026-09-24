#!/usr/bin/env python3
"""
Отдельный Telegram-бот поддержки «БелыйОбходчик».

Зачем: в боте и на сайте везде указан @beliy_obhodchik_support_bot, но без
отдельного бота эта ссылка никуда не ведёт. Этот бот закрывает поддержку:
пользователь пишет ему напрямую — типовые вопросы отвечает сам, сложные
пересылает владельцам (ADMIN_USER_IDS). Ответ владельца на пересланное
сообщение (reply) доставляется пользователю.

Как создать бота (один раз, вручную):
  1. В Telegram откройте @BotFather -> /newbot
  2. Название: «БелыйОбходчик — поддержка», юзернейм: beliy_obhodchik_support_bot
  3. BotFather выдаст токен вида 123456:ABC...
  4. Впишите его в .env как:
       SUPPORT_BOT_TOKEN=123456:ABC...
  5. Запуск:  python support_bot.py   (или run_support_bot.bat / deploy)

Автоответы переиспользуют те же правила, что и основной бот (SUPPORT_RULES),
поэтому бот и поддержка отвечают одинаково.
"""
import os
import sys
import time
import logging

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

try:
    import telebot
    from telebot import types  # noqa: F401
except ImportError:
    print("Установите библиотеку: pip install pyTelegramBotAPI")
    exit(1)

# Общие правила автоответа (единый источник с основным ботом).
from telegram_bot import AutoConfigBot  # noqa: E402

SUPPORT_RULES = AutoConfigBot.SUPPORT_RULES
SUPPORT_ANSWERS = AutoConfigBot.SUPPORT_ANSWERS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _load_dotenv(path: str = ".env") -> None:
    """Мини-загрузчик .env без внешних зависимостей (не перезаписывает env)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(("#", ";")) or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except (FileNotFoundError, PermissionError):
        pass
    except OSError as e:
        print(f"Не удалось прочитать {path}: {e}", file=sys.stderr)


_load_dotenv()

SUPPORT_BOT_TOKEN = os.environ.get('SUPPORT_BOT_TOKEN', "")
# Владельцы поддержки: ADMIN_USER_IDS переиспользуется из .env основного бота.
ADMIN_USER_IDS = [int(x) for x in os.environ.get('ADMIN_USER_IDS', '').split(',') if x.strip()]
COMMAND_COOLDOWN = float(os.environ.get('COMMAND_COOLDOWN', '2'))
MAIN_BOT_USERNAME = os.environ.get('MAIN_BOT_USERNAME', 'beliy_obhodchik_bot')


def auto_answer(text: str):
    """Тот же автоответчик, что и в основном боте: None, если вопрос не понят."""
    tl = (text or "").lower()
    t = tl
    if any(name in t for name in ("giga", "ultra", "hopper", "omni", "keenetic omni", "keenetic giga")):
        return (
            "🎛 *Keenetic — отличный выбор!*\n"
            "Ваша модель Keenetic полностью поддержана: основной бот подготовит "
            "готовый архив для загрузки в роутер «в один клик».\n\n"
            "Как получить: заведите сервер -> /vps в основном боте, затем /buy."
        )
    if "archer" in t or "tp-link" in t:
        return (
            "🌐 *TP-Link Archer — рабочий вариант.*\n"
            "Встроенного VPN-клиента достаточно: основной бот выдаст VLESS-ключи и "
            "инструкцию импорта именно под вашу модель. Начните с /buy в основном боте."
        )
    if "redmi" in t or "xiaomi" in t:
        return (
            "📶 *Xiaomi/Redmi — вариант для тех, кто не боится настройки.*\n"
            "Основной бот выдаст ключи и короткую инструкцию. Для «один клик и забыл» "
            "лучше Keenetic — подобрать можно в основном боте: /router"
        )
    for keywords, topic in SUPPORT_RULES:
        if any(kw in tl for kw in keywords):
            return SUPPORT_ANSWERS[topic]
    return None


class SupportBot:
    """Бот поддержки: автоответ + пересылка владельцу + ответ-реплай."""

    def __init__(self, token: str, admin_ids=None):
        self.bot = telebot.TeleBot(token)
        self.admin_ids = list(admin_ids) if admin_ids else list(ADMIN_USER_IDS)
        # forward.message_id у владельца -> (user_id, chat_id)
        self.forwards = {}
        self.last_cmd = {}
        self._spam_notified = {}
        self.register_handlers()

    # ===== Защита =====

    def _is_private(self, chat_type) -> bool:
        return chat_type == 'private'

    def _guard_message(self, message) -> bool:
        chat_type = getattr(message.chat, 'type', None)
        if not self._is_private(chat_type):
            try:
                self.bot.send_message(
                    message.chat.id,
                    "ℹ️ Поддержка работает только в личных сообщениях.\n"
                    "Напишите мне: @beliy_obhodchik_support_bot",
                )
            except Exception:
                pass
            return False
        return True

    def _antiflood(self, user_id: int, cooldown: float) -> bool:
        if user_id in self.admin_ids:
            return False
        now = time.monotonic()
        last = self.last_cmd.get(user_id)
        if last is not None and (now - last) < cooldown:
            return True
        self.last_cmd[user_id] = now
        if len(self.last_cmd) > 20000:
            cutoff = now - 3600
            self.last_cmd = {k: v for k, v in self.last_cmd.items() if v > cutoff}
        return False

    def _notify_slow(self, chat_id: int, user_id: int):
        now = time.monotonic()
        if now - self._spam_notified.get(user_id, 0) > 15:
            self._spam_notified[user_id] = now
            try:
                self.bot.send_message(chat_id, "⏳ Не так быстро. Подождите пару секунд и повторите.")
            except Exception:
                pass

    # ===== Реле ответов владельца =====

    def _relay_admin_reply(self, message) -> bool:
        """Если владелец ответил на пересланное сообщение — доставляем ответ юзеру."""
        reply = getattr(message, 'reply_to_message', None)
        if not reply or message.from_user.id not in self.admin_ids:
            return False
        ticket = self.forwards.get(reply.message_id)
        if not ticket:
            return False
        user_id, chat_id = ticket
        try:
            self.bot.send_message(chat_id, f"📩 *Ответ поддержки:*\n{message.text or ''}")
            self.bot.send_message(message.chat.id, f"✉️ Ответ отправлен пользователю {user_id}.")
            return True
        except Exception as e:
            logger.error("Не удалось доставить ответ пользователю %s: %s", user_id, e)
            self.bot.send_message(message.chat.id, f"❌ Не удалось доставить ответ (id {user_id}).")
            return True

    def _forward_to_admins(self, message) -> bool:
        """Пересылает вопрос владельцам. True, если уведомили хоть одного."""
        if not self.admin_ids:
            return False
        forwarded = False
        for admin_id in self.admin_ids:
            try:
                fwd = self.bot.forward_message(
                    admin_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )
                self.forwards[fwd.message_id] = (message.from_user.id, message.chat.id)
                forwarded = True
            except Exception as e:
                logger.error("Не удалось переслать вопрос владельцу %s: %s", admin_id, e)
        return forwarded

    # ===== Хендлеры =====

    def register_handlers(self):
        @self.bot.message_handler(commands=['start', 'help', 'menu'])
        def welcome(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            self.bot.send_message(
                message.chat.id,
                "🎧 *Поддержка «БелыйОбходчик»*\n\n"
                "Опишите проблему своими словами — на типовые вопросы отвечаю сразу, "
                "сложные передаю человеку и отвечаю сюда.\n\n"
                "Примеры:\n"
                "• «Как оплатить?»\n"
                "• «Перестал работать Ютуб»\n"
                "• «Где взять IP сервера?»\n\n"
                f"Покупка и настройка заказа — в основном боте: @{MAIN_BOT_USERNAME}",
                parse_mode='Markdown',
            )

        @self.bot.message_handler(func=lambda m: True,
                                  content_types=['text', 'photo', 'document', 'video', 'audio', 'voice', 'sticker'])
        def handle_message(message):
            if not self._guard_message(message):
                return
            # Ответ владельца на пересланное сообщение — приоритет
            if self._relay_admin_reply(message):
                return
            user_id = message.from_user.id
            # Сообщения владельцев вне реплики не обрабатываем
            if user_id in self.admin_ids:
                return
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return

            text = message.text or ""
            answer = auto_answer(text)
            if answer:
                self.bot.send_message(message.chat.id, answer, parse_mode='Markdown')
                return

            # Непонятный вопрос -> человеку
            sent = self._forward_to_admins(message)
            if sent:
                self.bot.send_message(
                    message.chat.id,
                    "🧑‍💻 Вопрос сложный — я передал его человеку. Ответ придёт сюда.",
                )
            else:
                self.bot.send_message(
                    message.chat.id,
                    "😕 Я пока учусь. Напишите, пожалуйста, позже или в основной бот: "
                    f"@{MAIN_BOT_USERNAME}",
                )

    # ===== Запуск =====

    def start(self):
        logger.info("Запускаю бота поддержки (владельцы: %s)", self.admin_ids)
        while True:
            try:
                self.bot.infinity_polling(
                    none_stop=True,
                    interval=2,
                    timeout=20,
                    long_polling_timeout=20,
                )
            except KeyboardInterrupt:
                logger.info("Бот поддержки остановлен пользователем.")
                return
            except Exception as e:
                logger.error("Поллинг поддержки завершился с ошибкой, перезапуск через 10 сек: %s", e)
                time.sleep(10)


def main():
    if not SUPPORT_BOT_TOKEN:
        print("=" * 60)
        print("НЕ ЗАДАН SUPPORT_BOT_TOKEN")
        print("=" * 60)
        print("Создайте бота поддержки в @BotFather:")
        print("  1. Откройте @BotFather -> /newbot")
        print("  2. Название: «БелыйОбходчик — поддержка»")
        print("  3. Юзернейм: beliy_obhodchik_support_bot")
        print("  4. Скопируйте токен в .env:")
        print("       SUPPORT_BOT_TOKEN=123456:ABC...")
        print("Затем запустите снова.")
        sys.exit(1)
    bot = SupportBot(SUPPORT_BOT_TOKEN)
    bot.start()


if __name__ == '__main__':
    main()