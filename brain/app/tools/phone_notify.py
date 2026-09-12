"""Push notifications to the user's phone via ntfy.sh (free, no account, no API key --
just POST to a topic URL and anyone subscribed to that topic on the ntfy app gets it)."""
import requests

from .. import config


def notify_phone(message: str, title: str = "Jarvis") -> str:
    """Sends a push notification to the user's phone. Use this whenever the user asks to be
    notified, reached, "called", or pinged on their phone -- or proactively, when something
    important happened that the user should know about even while away from the Pi/PC.

    Args:
        message: the notification text/body to send.
        title: short notification title (default "Jarvis").
    """
    try:
        resp = requests.post(
            f"https://ntfy.sh/{config.NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": "high",
                "Tags": "robot",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return "Benachrichtigung aufs Handy geschickt."
    except Exception as e:
        return f"Benachrichtigung fehlgeschlagen: {e}"
