import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "state.json"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ALLOWED_GROUP_IDS = {
    int(x.strip()) for x in os.getenv("ALLOWED_GROUP_IDS", "").split(",") if x.strip()
}
DEFAULT_RECIPIENTS = {
    int(x.strip()) for x in os.getenv("DEFAULT_RECIPIENT_USER_IDS", "").split(",") if x.strip()
}
KEYWORDS = [x.strip().lower() for x in os.getenv("KEYWORDS", "").split(",") if x.strip()]
MATCH_MODE = os.getenv("MATCH_MODE", "contains").strip().lower()
CASE_SENSITIVE = os.getenv("CASE_SENSITIVE", "false").strip().lower() == "true"
ALERT_TEMPLATE = os.getenv(
    "ALERT_TEMPLATE",
    "Keyword hit in {chat_title} by {sender}: {link}\n\nMatched: {matched}\n\nText: {text}",
)


def load_state() -> Dict[str, Any]:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"users": {}, "recipients": []}


STATE = load_state()



def save_state() -> None:
    DATA_FILE.write_text(json.dumps(STATE, ensure_ascii=False, indent=2), encoding="utf-8")



def remember_user(user_id: int, chat_id: int, username: Optional[str], full_name: str) -> None:
    STATE.setdefault("users", {})[str(user_id)] = {
        "chat_id": chat_id,
        "username": username,
        "full_name": full_name,
    }
    save_state()



def add_recipient(user_id: int) -> None:
    rec = set(STATE.get("recipients", []))
    rec.add(user_id)
    STATE["recipients"] = sorted(rec)
    save_state()



def remove_recipient(user_id: int) -> None:
    rec = set(STATE.get("recipients", []))
    rec.discard(user_id)
    STATE["recipients"] = sorted(rec)
    save_state()



def get_all_recipient_user_ids() -> list[int]:
    return sorted(set(STATE.get("recipients", [])) | DEFAULT_RECIPIENTS)



def normalize(text: str) -> str:
    return text if CASE_SENSITIVE else text.lower()



def find_matches(text: str) -> list[str]:
    haystack = normalize(text)
    found = []
    for kw in KEYWORDS:
        needle = kw if CASE_SENSITIVE else kw.lower()
        if MATCH_MODE == "word":
            words = {w.strip(".,!?;:()[]{}<>\"'`").lower() for w in haystack.split()}
            if needle in words:
                found.append(kw)
        else:
            if needle in haystack:
                found.append(kw)
    return found



def build_message_link(chat_id: int, username: Optional[str], message_id: int) -> str:
    if username:
        return f"https://t.me/{username}/{message_id}"
    internal = str(chat_id)
    if internal.startswith("-100"):
        internal = internal[4:]
    elif internal.startswith("-"):
        internal = internal[1:]
    return f"https://t.me/c/{internal}/{message_id}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return
    remember_user(user.id, chat.id, user.username, user.full_name)
    text = (
        "Hi. I can send keyword alerts from the target group to your DM.\n\n"
        "Commands:\n"
        "/subscribe - receive alerts\n"
        "/unsubscribe - stop alerts\n"
        "/status - show your status\n"
    )
    await update.message.reply_text(text)


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return
    remember_user(user.id, chat.id, user.username, user.full_name)
    add_recipient(user.id)
    await update.message.reply_text("You are subscribed to keyword alerts.")


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return
    remember_user(user.id, chat.id, user.username, user.full_name)
    remove_recipient(user.id)
    await update.message.reply_text("You are unsubscribed from keyword alerts.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    subscribed = user.id in get_all_recipient_user_ids()
    known = str(user.id) in STATE.get("users", {})
    await update.message.reply_text(
        f"Known to bot: {'yes' if known else 'no'}\nSubscribed: {'yes' if subscribed else 'no'}\nKeywords: {', '.join(KEYWORDS) or '(none)'}"
    )


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    link = build_message_link(chat.id, getattr(chat, "username", None), message.message_id)
    sender = user.full_name if user else "Unknown sender"
    body = ALERT_TEMPLATE.format(
        chat_title=chat.title or "Unnamed chat",
        sender=sender,
        link=link,
        matched=", ".join(matched),
        text=text[:1000],
    )

    recipients = get_all_recipient_user_ids()
    if not recipients:
        logger.info("Match found but no recipients configured")
        return

    for recipient_user_id in recipients:
        rec = STATE.get("users", {}).get(str(recipient_user_id))
        if not rec:
            logger.warning("Recipient %s has not started the bot yet", recipient_user_id)
            continue
        try:
            await context.bot.send_message(chat_id=rec["chat_id"], text=body, disable_web_page_preview=True)
        except Exception as exc:
            logger.exception("Failed to notify %s: %s", recipient_user_id, exc)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is required")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & (filters.TEXT | filters.Caption(True)), handle_group_message))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
