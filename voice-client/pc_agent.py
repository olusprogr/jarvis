"""Executes commands Jarvis wants run on this PC.

The Pi can't reach this machine directly, so this polls the backend for queued actions, runs
them, and posts the output back. Runs as a daemon thread alongside the wake-word loop, so PC
commands work whether they came from the mic, Telegram, or anywhere else.

Two kinds of action: plain PowerShell one-liners (the original mechanism -- opening apps/URLs,
media keys, ...), and structured browser actions (kind="browser") that drive a *persistent*
Playwright browser/page kept alive in this module between polls, so a click after a navigate
acts on the page that navigate actually loaded rather than a fresh blank tab each time. Both
kinds are always handled from this same thread (_loop's), so no locking is needed around the
Playwright objects -- Playwright's sync API isn't safe to touch from multiple threads anyway.

Note this means the Pi-side agent can execute arbitrary commands (and now arbitrary browser
navigation/clicks) here -- same trust boundary as the shell access it already has on the Pi,
extended to this machine.
"""
import logging
import subprocess
import threading
import time

import requests

import config

log = logging.getLogger("jarvis.pcagent")

POLL_SECONDS = 1.5
COMMAND_TIMEOUT = 60
IDLE_BACKOFF_SECONDS = 15  # after repeated failures, stop hammering a dead backend

_pw = None       # playwright sync-api driver, started lazily on first browser action
_browser = None  # the one Chromium instance, kept open across polls
_page = None     # the one tab we drive; browser_open() navigates it, doesn't open new tabs


def _run(command: str) -> tuple[str, int]:
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            timeout=COMMAND_TIMEOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
        return output[-4000:], proc.returncode
    except subprocess.TimeoutExpired:
        return f"Befehl nach {COMMAND_TIMEOUT}s abgebrochen.", -1
    except Exception as e:
        return f"Fehler: {e}", -1


def _ensure_page():
    """Returns the live page, (re)launching the browser/page if this is the first call or a
    previous one got closed/crashed."""
    global _pw, _browser, _page
    if _page is not None:
        try:
            _ = _page.url  # cheap liveness probe -- throws if the page/browser died underneath us
            return _page
        except Exception:
            _page = None
    from playwright.sync_api import sync_playwright  # imported lazily: optional dependency,
    # only needed once a browser action is actually used (see requirements.txt + README)
    if _pw is None:
        _pw = sync_playwright().start()
    if _browser is None or not _browser.is_connected():
        _browser = _pw.chromium.launch(headless=False)
    _page = _browser.new_page()
    return _page


def _locate(page, text: str, selector: str):
    """General-purpose locator for clicks: prefer an exact CSS selector if given, otherwise
    match by visible text (what the model actually "sees" described to it)."""
    if selector:
        return page.locator(selector).first
    return page.get_by_text(text, exact=False).first


def _locate_field(page, text: str, selector: str):
    """Locator for fill(): inputs rarely have visible inner text, so try the ways a field is
    normally identified (placeholder, associated label, accessible role) before falling back to
    plain text matching."""
    if selector:
        return page.locator(selector).first
    for attempt in (
        lambda: page.get_by_placeholder(text, exact=False),
        lambda: page.get_by_label(text, exact=False),
        lambda: page.get_by_role("textbox", name=text),
    ):
        try:
            loc = attempt()
            if loc.count() > 0:
                return loc.first
        except Exception:
            continue
    return page.get_by_text(text, exact=False).first


def _run_browser(op: str, args: dict) -> tuple[str, int]:
    global _pw, _browser, _page
    try:
        if op == "close":
            if _browser is not None:
                _browser.close()
            if _pw is not None:
                _pw.stop()
            _pw = _browser = _page = None
            return "Browser geschlossen.", 0

        page = _ensure_page()

        if op == "goto":
            page.goto(args.get("url", ""), wait_until="domcontentloaded", timeout=20000)
            return f"Geoeffnet: {page.title()} ({page.url})", 0

        if op == "click":
            _locate(page, args.get("text", ""), args.get("selector", "")).click(timeout=10000)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            return f"Geklickt. Jetzt: {page.title()} ({page.url})", 0

        if op == "fill":
            _locate_field(page, args.get("text", ""), args.get("selector", "")).fill(
                args.get("value", ""), timeout=10000,
            )
            return "Feld ausgefuellt.", 0

        if op == "press":
            page.keyboard.press(args.get("key", "Enter"))
            return f"Taste {args.get('key')} gesendet.", 0

        if op == "read":
            return page.inner_text("body")[:4000], 0

        return f"Unbekannte Browser-Aktion: {op}", 1
    except ImportError:
        return "Playwright nicht installiert. pip install playwright && playwright install chromium", 1
    except Exception as e:
        return f"Fehler: {e}", 1


def _loop() -> None:
    session = requests.Session()
    headers = {"X-Jarvis-Key": config.JARVIS_API_KEY}
    consecutive_failures = 0

    while True:
        try:
            resp = session.get(f"{config.JARVIS_URL}/pc-actions", headers=headers, timeout=10)
            resp.raise_for_status()
            actions = resp.json().get("actions", [])
            consecutive_failures = 0

            for action in actions:
                if action.get("kind") == "browser":
                    op = action.get("op", "")
                    args = action.get("args") or {}
                    log.info("Browser-Aktion von Jarvis: %s %s", op, args)
                    output, code = _run_browser(op, args)
                    log.info("Browser-Aktion fertig (Code %s)", code)
                else:
                    command = action.get("command", "")
                    log.info("PC-Befehl von Jarvis: %s", command)
                    output, code = _run(command)
                    log.info("PC-Befehl fertig (Code %s)", code)
                try:
                    session.post(
                        f"{config.JARVIS_URL}/pc-result",
                        headers=headers,
                        json={"id": action.get("id"), "output": output, "exit_code": code},
                        timeout=10,
                    )
                except requests.RequestException:
                    log.warning("Ergebnis konnte nicht zurueckgemeldet werden.")
        except requests.RequestException:
            consecutive_failures += 1
            if consecutive_failures == 3:
                log.warning("Backend nicht erreichbar -- PC-Abfrage laeuft langsamer weiter.")
        except Exception:
            log.exception("Fehler in der PC-Befehlsschleife")

        time.sleep(IDLE_BACKOFF_SECONDS if consecutive_failures >= 3 else POLL_SECONDS)


def start() -> None:
    threading.Thread(target=_loop, daemon=True, name="pc-agent").start()
    log.info("PC-Befehlskanal aktiv.")
