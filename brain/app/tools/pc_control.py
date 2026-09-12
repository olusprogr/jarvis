"""Run commands on the user's Windows PC from the Pi.

The Pi can't reach the PC directly, so the flow is inverted: commands are queued here, the PC's
voice client polls for them, runs them, and posts the result back. run_on_pc() then blocks
briefly so a quick command still feels synchronous and the agent can report the real output.
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
    now = time.time()
    with _lock:
        _expire_old(now)
        batch = [{"id": c["id"], "command": c["command"]} for c in _pending]
        _pending.clear()
    return batch


def submit_result(command_id: str, output: str, exit_code: int) -> None:
    with _lock:
        _results[command_id] = {"output": output, "exit_code": exit_code}
        event = _events.get(command_id)
    if event:
        event.set()


def run_on_pc(command: str, description: str = "") -> str:
    """Run a PowerShell command on the user's Windows PC (not the Pi) and return its output.

    Use this for anything that must happen on the PC itself: opening a website or application,
    controlling playback, locking the screen, reading or writing files on the PC. The PC's
    voice client must be running. Examples: `Start-Process 'https://youtube.com'` to open a URL,
    `Start-Process spotify` to launch an app, `Stop-Process -Name notepad` to close one.

    Args:
        command: the PowerShell command to run on the PC.
        description: short summary of what it does (for the audit log).
    """
    command_id = str(uuid.uuid4())
    event = threading.Event()
    with _lock:
        _pending.append({"id": command_id, "command": command, "queued_at": time.time()})
        _events[command_id] = event
    log.info("PC-Befehl eingereiht (%s): %s", description or "-", command)

    if not event.wait(timeout=RESULT_WAIT_SECONDS):
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
