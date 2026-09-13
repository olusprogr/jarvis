"""Run actions on the user's Windows PC from the Pi.

The Pi can't reach the PC directly, so the flow is inverted: actions are queued here, the PC's
voice client polls for them, runs them, and posts the result back. The queue/wait functions here
are used by two kinds of action: plain PowerShell commands (run_on_pc, unchanged behaviour) and
structured browser actions (browser_tool.py, drives a persistent Playwright session on the PC).
Payloads are passed through generically so this file doesn't need to know about browser-specific
fields -- the PC client's pc_agent.py decides what to do based on the "kind" field.
"""
import logging
import threading
import time
import uuid

log = logging.getLogger("jarvis.pc")

PENDING_TTL_SECONDS = 120
RESULT_WAIT_SECONDS = 25

_lock = threading.Lock()
_pending: list[dict] = []
_results: dict[str, dict] = {}
_events: dict[str, threading.Event] = {}


def _expire_old(now: float) -> None:
    global _pending
    _pending = [c for c in _pending if now - c["queued_at"] < PENDING_TTL_SECONDS]


def take_pending() -> list[dict]:
    """Returns each queued action's full payload (minus the internal queued_at timestamp) and
    clears the queue. Generic across action kinds -- the PC client's pc_agent.py looks at each
    action's own fields (kind/command/op/args) to decide how to run it."""
    now = time.time()
    with _lock:
        _expire_old(now)
        batch = [{k: v for k, v in c.items() if k != "queued_at"} for c in _pending]
        _pending.clear()
    return batch


def submit_result(command_id: str, output: str, exit_code: int) -> None:
    with _lock:
        _results[command_id] = {"output": output, "exit_code": exit_code}
        event = _events.get(command_id)
    if event:
        event.set()


def _queue_and_wait(payload: dict, wait_seconds: int = RESULT_WAIT_SECONDS) -> str:
    command_id = str(uuid.uuid4())
    event = threading.Event()
    with _lock:
        _pending.append({**payload, "id": command_id, "queued_at": time.time()})
        _events[command_id] = event

    if not event.wait(timeout=wait_seconds):
        with _lock:
            _events.pop(command_id, None)
        return "Kein Ergebnis vom PC erhalten. Laeuft der Jarvis-Client dort?"

    with _lock:
        result = _results.pop(command_id, {})
        _events.pop(command_id, None)
    output = (result.get("output") or "").strip()
    code = result.get("exit_code", 0)
    if code == 0:
        return output or "Erledigt (keine Ausgabe)."
    return f"Fehlgeschlagen (Code {code}):\n{output}"


def run_on_pc(command: str, description: str = "") -> str:
    """Run a PowerShell command on the user's Windows PC (not the Pi) and return its output.

    Use this for anything that must happen on the PC itself: opening a website or application,
    controlling playback, locking the screen, reading or writing files on the PC. The PC's
    voice client must be running. Examples: `Start-Process 'https://youtube.com'` to open a URL,
    `Start-Process spotify` to launch an app, `Stop-Process -Name notepad` to close one.

    For clicking through a website (not just opening it) use browser_open/browser_click/etc.
    instead -- this only runs one-shot commands, it can't see or interact with a page's content.

    Args:
        command: the PowerShell command to run on the PC.
        description: short summary of what it does (for the audit log).
    """
    log.info("PC-Befehl eingereiht (%s): %s", description or "-", command)
    return _queue_and_wait({"command": command})


def run_browser_action(op: str, description: str = "", **args) -> str:
    """Internal helper used by browser_tool.py -- queues a structured browser action for the
    PC's persistent Playwright session instead of a one-shot PowerShell command."""
    log.info("Browser-Aktion eingereiht (%s): %s %s", description or "-", op, args)
    return _queue_and_wait({"kind": "browser", "op": op, "args": args})
