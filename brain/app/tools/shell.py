"""Full shell access for the agent, on explicit user request (no sandboxing).
Every command is logged to logs/commands.log with timestamp + output for audit.
"""
import datetime
import subprocess
from .. import config

LOG_FILE = config.LOGS_DIR / "commands.log"


def _log(command: str, output: str, returncode: int) -> None:
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"\n[{stamp}] rc={returncode} $ {command}\n{output}\n")


def run_shell_command(command: str, timeout_seconds: int = 60) -> str:
    """Execute a shell command on the Raspberry Pi (full system access, runs as user 'olus').
    Use this for anything the user could do themselves on their machine: check system status,
    manage files, control services (sudo available via 'sudo -S' is NOT pre-authenticated,
    prefer commands that don't need sudo), install things, run scripts, query processes, etc.
    Long-running or destructive commands: briefly say out loud what you are about to do first.

    Args:
        command: the shell command to run (bash).
        timeout_seconds: max seconds to wait before killing it (default 60).
    """
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout_seconds,
            executable="/bin/bash",
        )
        output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
        output = output[-6000:]  # cap size going back to the model
        _log(command, output, proc.returncode)
        return f"(exit {proc.returncode})\n{output}".strip()
    except subprocess.TimeoutExpired:
        _log(command, "TIMEOUT", -1)
        return f"Befehl nach {timeout_seconds}s abgebrochen (Timeout)."
    except Exception as e:
        _log(command, f"ERROR: {e}", -1)
        return f"Fehler beim Ausführen: {e}"
