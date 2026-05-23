# Telegram keyword alert bot

A small Python bot that watches a Telegram group or supergroup and sends a DM to subscribed users when a keyword appears.

## Features

- Watches group and supergroup messages.
- Matches keywords by substring or whole word.
- Sends a direct message with a clickable link to the matched message.
- Supports multiple recipients.
- Stores recipient chat IDs in `state.json` after they run `/start`.

## Important Telegram limitations

- The bot can only DM a user after that user starts the bot in private chat.
- To see normal group messages, disable Privacy Mode in BotFather or make the bot an admin in the group.
- Private supergroup message links use the `https://t.me/c/.../...` format.

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

## Commands

- `/start` — register your DM chat with the bot.
- `/subscribe` — opt into alerts.
- `/unsubscribe` — opt out.
- `/status` — see current status.
