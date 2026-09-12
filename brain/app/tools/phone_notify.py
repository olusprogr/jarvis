"""Push notifications to the user's phone -- delivered as a Telegram message to their own chat.

Previously used ntfy.sh; dropped in favour of Telegram since the bot was already set up and
working, so this is one integration/credential/app fewer to keep alive. A Telegram message
produces a normal phone push notification, and has the side benefit that the user can reply to
it right there and continue the conversation.
"""
import logging

from .. import config

log = logging.getLogger("jarvis.notify")


def notify_phone(message: str, title: str = "Jarvis") -> str:
    """Sends a push notification to the user's phone (via Telegram). Use this whenever the user
    asks to be notified, reached, "called", or pinged on their phone -- or proactively, when
    something important happened that the user should know about even while away from the
    Pi/PC.

    Args:
        message: the notification text/body to send.
        title: short notification title (default "Jarvis").
    """
    if not config.TELEGRAM_ALLOWED_CHAT_ID:
        return "Kein Telegram-Chat konfiguriert, Benachrichtigung nicht möglich."
    try:
        # Imported lazily: telegram_bot imports agent, and agent imports this module -- a
        # top-level import here would be a circular import at startup.
        from ..telegram_bot import send_message

        text = f"*{title}*\n{message}" if title and title != "Jarvis" else message
        send_message(config.TELEGRAM_ALLOWED_CHAT_ID, text)
        return "Benachrichtigung aufs Handy geschickt."
    except Exception as e:
        log.exception("notify_phone fehlgeschlagen")
        return f"Benachrichtigung fehlgeschlagen: {e}"
