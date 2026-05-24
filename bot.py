import asyncio
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


#логирование

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

#конфиг

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "state.json"

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()

ALLOWED_GROUP_IDS: frozenset[int] = frozenset(
    int(x.strip()) for x in os.getenv("ALLOWED_GROUP_IDS", "").split(",") if x.strip()
)
DEFAULT_RECIPIENTS: frozenset[int] = frozenset(
    int(x.strip())
    for x in os.getenv("DEFAULT_RECIPIENT_USER_IDS", "").split(",")
    if x.strip()
)

MATCH_MODE: str = os.getenv("MATCH_MODE", "contains").strip().lower()
CASE_SENSITIVE: bool = os.getenv("CASE_SENSITIVE", "false").strip().lower() == "true"
ALERT_TEMPLATE: str = os.getenv(
    "ALERT_TEMPLATE",
    "Keyword hit in {chat_title} by {sender}: {link}\n\nMatched: {matched}\n\nText: {text}",
)


#компиляция ключевиков в один regex при запуске

_raw_keywords: list[str] = [
    x.strip() for x in os.getenv("KEYWORDS", "").split(",") if x.strip()
]
# Сохраняем исходный регистр для вывода; юзаем (?i) если регистр не важен.
KEYWORDS: list[str] = _raw_keywords

_flags = 0 if CASE_SENSITIVE else re.IGNORECASE

if MATCH_MODE == "word":
    _patterns = [re.compile(rf"\b{re.escape(kw)}\b", _flags) for kw in KEYWORDS]
else:
    _patterns = [re.compile(re.escape(kw), _flags) for kw in KEYWORDS]


def find_matches(text: str) -> list[str]:
    """Возвращает список найденных в *text* ключевиков (в исходном регистре)."""
    #один regex-поиск на ключевик, без нормализации строк
    #и пересборки сетов внутри цикла
    return [kw for kw, pat in zip(KEYWORDS, _patterns) if pat.search(text)]



#управление состоянием

class StateManager:

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Failed to load state, starting fresh: %s", exc)
        return {"users": {}, "recipients": []}

    def save(self) -> None:
        """Пишем во временный файл, затем атомарно подменяем основной."""
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, prefix=".state_tmp_"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except OSError:
            os.unlink(tmp_path)
            raise

    #реестр пользователей

    def remember_user(
        self, user_id: int, chat_id: int, username: Optional[str], full_name: str
    ) -> None:
        self._data.setdefault("users", {})[str(user_id)] = {
            "chat_id": chat_id,
            "username": username,
            "full_name": full_name,
        }
        self.save()

    def get_user_chat_id(self, user_id: int) -> Optional[int]:
        rec = self._data.get("users", {}).get(str(user_id))
        return rec["chat_id"] if rec else None

    def is_known(self, user_id: int) -> bool:
        return str(user_id) in self._data.get("users", {})

    #список получателей

    def add_recipient(self, user_id: int) -> None:
        rec: set[int] = set(self._data.get("recipients", []))
        rec.add(user_id)
        self._data["recipients"] = sorted(rec)
        self.save()

    def remove_recipient(self, user_id: int) -> None:
        rec: set[int] = set(self._data.get("recipients", []))
        rec.discard(user_id)
        self._data["recipients"] = sorted(rec)
        self.save()

    def all_recipient_ids(self) -> list[int]:
        return sorted(set(self._data.get("recipients", [])) | DEFAULT_RECIPIENTS)

    def is_subscribed(self, user_id: int) -> bool:
        return user_id in self.all_recipient_ids()


state = StateManager(DATA_FILE)

#хелперы

def build_message_link(
    chat_id: int, username: Optional[str], message_id: int
) -> str:
    if username:
        return f"https://t.me/{username}/{message_id}"
    internal = str(chat_id)
    if internal.startswith("-100"):
        internal = internal[4:]
    elif internal.startswith("-"):
        internal = internal[1:]
    return f"https://t.me/c/{internal}/{message_id}"


#общий хелпер для start/subscribe/unsubscribe, чтобы
#не копипастить вызов remember_user в каждом хендлере
def _register_user(update: Update) -> bool:
    """Сохраняет/обновляет запись юзера. Возвращает False, если юзера/чата нет."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return False
    state.remember_user(user.id, chat.id, user.username, user.full_name)
    return True

#хендлеры команд

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _register_user(update):
        return
    await update.message.reply_text(
        "Hi. I can send keyword alerts from the target group to your DM.\n\n"
        "Commands:\n"
        "/subscribe — receive alerts\n"
        "/unsubscribe — stop alerts\n"
        "/status — show your status\n"
    )


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _register_user(update):
        return
    state.add_recipient(update.effective_user.id)
    await update.message.reply_text("You are subscribed to keyword alerts.")


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _register_user(update):
        return
    state.remove_recipient(update.effective_user.id)
    await update.message.reply_text("You are unsubscribed from keyword alerts.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    await update.message.reply_text(
        f"Known to bot: {'yes' if state.is_known(user.id) else 'no'}\n"
        f"Subscribed: {'yes' if state.is_subscribed(user.id) else 'no'}\n"
        f"Keywords: {', '.join(KEYWORDS) or '(none)'}"
    )


#хендлер сообщений из групп

async def handle_group_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat:
        return

    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if ALLOWED_GROUP_IDS and chat.id not in ALLOWED_GROUP_IDS:
        return

    text = message.text or message.caption or ""
    if not text:
        return

    matched = find_matches(text)
    if not matched:
        return

    link = build_message_link(
        chat.id, getattr(chat, "username", None), message.message_id
    )
    body = ALERT_TEMPLATE.format(
        chat_title=chat.title or "Unnamed chat",
        sender=user.full_name if user else "Unknown sender",
        link=link,
        matched=", ".join(matched),
        text=text[:1000],
    )

    recipients = state.all_recipient_ids()
    if not recipients:
        logger.info("Match found but no recipients configured")
        return

    #рассылаем все уведомления асинхронно (конкурентно), а не ждем каждое в цикле.
    #при большом числе получателей это работает намного быстрее
    async def _notify(recipient_id: int) -> None:
        chat_id = state.get_user_chat_id(recipient_id)
        if chat_id is None:
            logger.warning("Recipient %s has not started the bot yet", recipient_id)
            return
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=body, disable_web_page_preview=True
            )
        except Exception as exc:
            logger.exception("Failed to notify %s: %s", recipient_id, exc)

    await asyncio.gather(*(_notify(rid) for rid in recipients))


#хендлер ошибок

async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    logger.exception("Unhandled error: %s", context.error)

if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is required")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (filters.TEXT | filters.Caption(True)),
            handle_group_message,
        )
    )
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)