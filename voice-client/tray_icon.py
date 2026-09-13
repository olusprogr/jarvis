"""System tray icon: permanent visual proof the background client is actually running --
the status window only appears during a live conversation, so between conversations there was
previously no way to tell "is Jarvis even on" without checking Task Manager.

Draws its own icon (a simple "J" badge) instead of shipping an .ico asset, and runs pystray's
loop in a daemon thread like the other UI pieces here (status_window.py) so it never blocks the
wake-word/audio loop.
"""
import logging
import os
import threading

from PIL import Image, ImageDraw

log = logging.getLogger("jarvis.tray")

_icon = None  # module-level so quit_app() (menu callback) can stop it


def _build_image() -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, size - 2, size - 2), fill=(74, 158, 255, 255))  # matches the status window's blue
    draw.text((size / 2, size / 2), "J", fill=(255, 255, 255, 255), anchor="mm",
              font=_font(size))
    return img


def _font(size: int):
    try:
        from PIL import ImageFont
        return ImageFont.truetype("segoeuib.ttf", int(size * 0.55))
    except Exception:
        from PIL import ImageFont
        return ImageFont.load_default()


def _open_logs(icon, item) -> None:
    try:
        import config
        os.startfile(config.LOG_DIR)  # noqa: S606 -- opening a local folder the user owns
    except Exception:
        log.exception("Log-Ordner konnte nicht geoeffnet werden")


def _quit_app(icon, item) -> None:
    log.info("Beenden ueber Tray-Icon angefordert.")
    icon.stop()
    os._exit(0)  # background app with no normal event-loop exit path -- hard-stop is deliberate


def start() -> None:
    global _icon
    import pystray

    _icon = pystray.Icon(
        "jarvis",
        _build_image(),
        "Jarvis läuft",
        menu=pystray.Menu(
            pystray.MenuItem("Jarvis läuft", None, enabled=False),
            pystray.MenuItem("Logs öffnen", _open_logs),
            pystray.MenuItem("Beenden", _quit_app),
        ),
    )
    threading.Thread(target=_icon.run, daemon=True, name="tray-icon").start()
    log.info("Tray-Icon aktiv.")
