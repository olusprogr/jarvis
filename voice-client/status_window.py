"""Live status overlay for the conversation: a small frameless always-on-top window in the
bottom-right corner that says what Jarvis is doing right now (listening / recording / thinking /
speaking), counts down the listening window, and shows a live mic level so the user can actually
see whether they're being picked up.

Windows toast notifications were tried first and are the wrong tool here -- they're transient,
arrive with a delay, and stack up instead of updating in place.

Threading: Tk insists on owning its thread, so the window runs its own mainloop in a daemon
thread and polls a plain shared dict (guarded by a lock) via `after()`. Callers just mutate
state through the thread-safe setters below and never touch Tk objects directly.
"""
import logging
import queue
import threading
import tkinter as tk

log = logging.getLogger("jarvis.status")

# state name -> (label text, accent colour)
STATES = {
    "greeting": ("Verbunden", "#4a9eff"),
    "waiting": ("Du bist dran - sprich", "#3ddc84"),
    "recording": ("Ich höre zu ...", "#3ddc84"),
    "thinking": ("Denke nach ...", "#ffb340"),
    "speaking": ("Jarvis spricht", "#4a9eff"),
    "ended": ("Chat beendet", "#8a8a8a"),
}

WIDTH, HEIGHT = 330, 104
MARGIN = 24  # distance from the screen's bottom-right corner


class StatusWindow:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = "greeting"
        self._detail = ""
        self._level = 0.0        # 0..1 mic level
        self._countdown = None   # seconds remaining, or None to hide
        self._visible = False
        self._commands = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # --- thread-safe API used by the client -------------------------------------------------

    def show(self, state: str = "greeting") -> None:
        with self._lock:
            self._state = state
            self._detail = ""
            self._level = 0.0
            self._countdown = None
        self._commands.put("show")

    def hide(self) -> None:
        self._commands.put("hide")

    def set_state(self, state: str, detail: str = "") -> None:
        with self._lock:
            self._state = state
            self._detail = detail
            if state != "waiting":
                self._countdown = None
            if state not in ("waiting", "recording"):
                self._level = 0.0

    def set_level(self, level: float) -> None:
        with self._lock:
            self._level = max(0.0, min(1.0, level))

    def set_countdown(self, seconds_left: float | None) -> None:
        with self._lock:
            self._countdown = seconds_left

    # --- Tk side ----------------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._root = tk.Tk()
            self._root.overrideredirect(True)       # no title bar / borders
            self._root.attributes("-topmost", True)
            self._root.configure(bg="#15171c")
            try:
                self._root.attributes("-alpha", 0.96)
            except Exception:
                pass  # transparency is optional

            screen_w = self._root.winfo_screenwidth()
            screen_h = self._root.winfo_screenheight()
            x = screen_w - WIDTH - MARGIN
            y = screen_h - HEIGHT - MARGIN - 48  # leave room for the taskbar
            self._root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

            self._canvas = tk.Canvas(
                self._root, width=WIDTH, height=HEIGHT,
                bg="#15171c", highlightthickness=0,
            )
            self._canvas.pack()
            self._root.withdraw()

            self._tick()
            self._root.mainloop()
        except Exception:
            log.exception("Status-Fenster konnte nicht gestartet werden")

    def _tick(self) -> None:
        try:
            while True:
                try:
                    cmd = self._commands.get_nowait()
                except queue.Empty:
                    break
                if cmd == "show":
                    self._root.deiconify()
                    self._root.attributes("-topmost", True)
                    self._visible = True
                elif cmd == "hide":
                    self._root.withdraw()
                    self._visible = False

            if self._visible:
                self._draw()
        except Exception:
            log.exception("Fehler beim Zeichnen des Status-Fensters")
        finally:
            self._root.after(60, self._tick)  # ~16 fps, smooth enough for a level meter

    def _draw(self) -> None:
        with self._lock:
            state, detail, level, countdown = self._state, self._detail, self._level, self._countdown

        label, accent = STATES.get(state, (state, "#8a8a8a"))
        c = self._canvas
        c.delete("all")

        # rounded-ish border accent down the left edge
        c.create_rectangle(0, 0, 4, HEIGHT, fill=accent, outline="")

        c.create_text(20, 22, anchor="w", text="JARVIS", fill="#5c6270",
                      font=("Segoe UI", 8, "bold"))
        c.create_text(20, 46, anchor="w", text=label, fill=accent,
                      font=("Segoe UI", 13, "bold"))

        if countdown is not None:
            c.create_text(WIDTH - 20, 46, anchor="e", text=f"{countdown:.0f}s",
                          fill="#5c6270", font=("Segoe UI", 11))

        if state in ("waiting", "recording"):
            # live mic level meter -- answers "is it actually hearing me right now"
            bar_x, bar_y, bar_w, bar_h = 20, 70, WIDTH - 40, 8
            c.create_rectangle(bar_x, bar_y, bar_x + bar_w, bar_y + bar_h,
                               fill="#22252c", outline="")
            filled = int(bar_w * level)
            if filled > 0:
                c.create_rectangle(bar_x, bar_y, bar_x + filled, bar_y + bar_h,
                                   fill=accent, outline="")
        elif detail:
            shown = detail if len(detail) <= 46 else detail[:43] + "..."
            c.create_text(20, 74, anchor="w", text=shown, fill="#9aa0ad",
                          font=("Segoe UI", 9))
