"""Lets the agent drive a real, visible browser window on the user's PC -- navigate, click,
fill forms, read pages. Goes through the same inverted-polling channel as pc_control.py's
run_on_pc, but the PC side (pc_agent.py) keeps a persistent Playwright browser/page alive between
calls instead of spawning a fresh one-shot process each time, so a click after a navigate acts on
the page that navigate actually loaded, not a blank new tab.

Use this instead of run_on_pc for anything that needs to click through an actual website (forms,
multi-step flows, logging in) and instead of fetch_url for pages that need interaction first --
fetch_url is read-only and can't click anything.
"""
from . import pc_control


def browser_open(url: str) -> str:
    """Opens `url` in a visible browser window on the user's PC (launches one if none is open
    yet, otherwise navigates the existing tab). Returns the resulting page title and URL."""
    return pc_control.run_browser_action("goto", description=f"Oeffne {url}", url=url)


def browser_click(text: str = "", selector: str = "") -> str:
    """Clicks an element on the currently open page. Prefer `text` -- the visible label of the
    button or link (e.g. "Anmelden", "Akzeptieren", "Weiter") -- since that's what you can
    actually see described to you; pass `selector` (a CSS selector) only if you already know the
    exact one. Returns the resulting page title/URL, or an error if nothing matched."""
    return pc_control.run_browser_action(
        "click", description=f"Klicke {text or selector}", text=text, selector=selector,
    )


def browser_fill(value: str, text: str = "", selector: str = "") -> str:
    """Types `value` into an input field on the currently open page, identified by its visible
    label or placeholder text (`text`) or a CSS `selector`. Does not submit the form -- follow up
    with browser_press("Enter") or browser_click on the submit button."""
    return pc_control.run_browser_action(
        "fill", description=f"Fuelle Feld {text or selector}", value=value, text=text, selector=selector,
    )


def browser_press(key: str) -> str:
    """Sends a keyboard key to the currently open page, e.g. "Enter", "Escape", "Tab",
    "PageDown"."""
    return pc_control.run_browser_action("press", description=f"Taste {key}", key=key)


def browser_read() -> str:
    """Returns the visible text of the currently open page (capped), so you can see what's
    actually there before deciding what to click or fill next."""
    return pc_control.run_browser_action("read", description="Lese Seite")


def browser_close() -> str:
    """Closes the browser window on the user's PC. Call this when a browsing task is done so it
    doesn't linger open on screen."""
    return pc_control.run_browser_action("close", description="Schliesse Browser")
