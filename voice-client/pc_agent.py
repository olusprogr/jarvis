"""Executes commands Jarvis wants run on this PC.

The Pi can't reach this machine directly, so this polls the backend for queued commands,
runs them in PowerShell, and posts the output back. Runs as a daemon thread alongside the
wake-word loop, so PC commands work whether they came from the mic, Telegram, or anywhere else.

Note this means the Pi-side agent can execute arbitrary commands here -- same trust boundary as
the shell access it already has on the Pi, extended to this machine.
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
