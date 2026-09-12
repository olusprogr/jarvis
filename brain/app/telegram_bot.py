"""Telegram bot: two-way chat with Jarvis from the phone (text and voice messages both ways).
Uses the raw Bot API via requests -- no framework needed for our modest needs (receive webhook,
send text back). Reuses the same agent.py brain/session pattern as the voice pipeline.

Security: Jarvis has full shell access to the Pi. TELEGRAM_ALLOWED_CHAT_ID must be set or every
incoming message is logged-and-ignored -- never processed by the agent for an unconfigured or
mismatched chat_id. See config.py for the bootstrapping note.
"""
import logging

import requests

from . import agent, config, tts

log = logging.getLogger("jarvis.telegram")

API_BASE = "https://api.telegram.org/bot{token}"


def _api(method: str, **params) -> dict:
    url = f"{API_BASE.format(token=config.TELEGRAM_BOT_TOKEN)}/{method}"
    resp = requests.post(url, json=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_message(chat_id: int | str, text: str) -> None:
    try:
        _api("sendMessage", chat_id=chat_id, text=text)
    except Exception:
        log.exception("Telegram sendMessage fehlgeschlagen")


def _download_file(file_id: str) -> bytes:
    info = _api("getFile", file_id=file_id)
    file_path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{file_path}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.content


def handle_update(update: dict) -> None:
    """Processes one Telegram Update (called from the webhook route in main.py)."""
    message = update.get("message") or update.get("edited_message")
    if not message:
        return  # not a message we care about (e.g. a reaction, channel post, etc.)

    chat_id = message["chat"]["id"]
    session_id = f"telegram-{chat_id}"

    if not config.TELEGRAM_ALLOWED_CHAT_ID:
        log.warning(
            "TELEGRAM_ALLOWED_CHAT_ID ist nicht gesetzt -- Nachricht von chat_id=%s ignoriert. "
            "Trag diese chat_id in .env ein, um sie freizuschalten.", chat_id,
        )
        return
    if str(chat_id) != str(config.TELEGRAM_ALLOWED_CHAT_ID):
        log.warning("Nachricht von nicht-erlaubter chat_id=%s ignoriert.", chat_id)
        return

    try:
        if "voice" in message:
            audio_bytes = _download_file(message["voice"]["file_id"])
            reply, _ended = agent.ask_audio(session_id, audio_bytes, mime_type="audio/ogg")
        elif "text" in message:
            reply, _ended = agent.ask(session_id, message["text"])
        else:
            send_message(chat_id, "Kann nur Text oder Sprachnachrichten verarbeiten.")
            return
        send_message(chat_id, reply)
    except Exception:
        log.exception("Fehler bei der Verarbeitung der Telegram-Nachricht")
        send_message(chat_id, "Da ist was schiefgelaufen.")


def register_webhook(webhook_url: str) -> dict:
    """One-time setup call: tells Telegram where to POST incoming updates."""
    return _api(
        "setWebhook",
        url=webhook_url,
        secret_token=config.TELEGRAM_WEBHOOK_SECRET,
        allowed_updates=["message"],
    )
