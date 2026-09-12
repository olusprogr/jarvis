"""Jarvis wake-word client. Runs in the background, listens for 'Hey Jarvis', then holds a
multi-turn conversation (no need to repeat the wake word between turns) until the user says
they're done or goes quiet for CONVERSATION_SILENCE_TIMEOUT seconds.
"""
import io
import logging
import random
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from collections import deque

# Under pythonw.exe (no console) sys.stdout/stderr are None -- guard against that.
if sys.stdout is not None and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr is not None and sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf
from openwakeword.model import Model

import config

_handlers = [logging.FileHandler(config.LOG_DIR / "client.log", encoding="utf-8")]
if sys.stderr is not None:  # console handler only makes sense with python.exe, not pythonw.exe
    _handlers.append(logging.StreamHandler())
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("jarvis.client")

GREETINGS = sorted(config.GREETINGS_DIR.glob("greeting-*.wav"))


def notify(title: str, message: str, duration_ms: int = 4000) -> None:
    """Shows a Windows notification bottom-right via a hidden PowerShell helper,
    without blocking the caller. Uses a plain WinForms balloon tip rather than a
    packaged library (e.g. win11toast) -- those pull in WinRT/COM bindings that
    crash PyInstaller's dependency analysis when bundled into a frozen exe.

    duration_ms should match how long the thing it's announcing actually takes (e.g. the
    reply audio's real playback length, or the listening window's timeout) -- a fixed
    duration regardless of context is what caused the popups to drift out of sync with
    what Jarvis was actually doing."""
    def _show():
        try:
            safe_title = title.replace("'", "''")
            safe_message = (message or "").replace("'", "''")[:500]
            # Windows clamps the visible balloon to a few seconds regardless of what we ask for,
            # but the process (and thus the tray icon) needs to stay alive for the full duration
            # or the balloon gets torn down early -- sleep matches duration_ms, not a fixed value.
            sleep_seconds = max(1.0, duration_ms / 1000)
            script = (
                "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                "$n = New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                "$n.Visible = $true; "
                f"$n.ShowBalloonTip({duration_ms}, '{safe_title}', '{safe_message}', "
                "[System.Windows.Forms.ToolTipIcon]::Info); "
                f"Start-Sleep -Seconds {sleep_seconds}; "
                "$n.Dispose()"
            )
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=sleep_seconds + 10,
            )
        except Exception:
            log.exception("Benachrichtigung fehlgeschlagen")
    threading.Thread(target=_show, daemon=True).start()


def play_audio(stream: sd.InputStream, data: np.ndarray, sr: int) -> None:
    """Pauses the mic stream while playing, then resumes -- otherwise the mic picks up Jarvis's
    own voice through speaker bleed, which gets read as a buffered 'utterance' the instant the
    next record_utterance() starts, causing a spurious extra turn (the 'Du bist dran' spam /
    duplicated recordings the user reported)."""
    stream.stop()
    try:
        sd.play(data, sr)
        sd.wait()
    finally:
        stream.start()


def play_greeting(stream: sd.InputStream) -> None:
    """Plays one of the pre-generated 'Ich höre Sie, Master.'-style clips instantly --
    no network round-trip, so there's zero delay between the wake word and *some* response."""
    if not GREETINGS:
        return
    try:
        data, sr = sf.read(str(random.choice(GREETINGS)), dtype="float32")
        play_audio(stream, data, sr)
    except Exception:
        log.exception("Begrüßung konnte nicht abgespielt werden")


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))


def record_utterance(stream: sd.InputStream, start_timeout_seconds: float | None = None):
    """Waits (up to start_timeout_seconds, if given) for speech to begin, then records until
    trailing silence or MAX_RECORD_SECONDS. Returns int16 mono samples, or None if
    start_timeout_seconds elapsed with no speech at all (caller should end the conversation).

    Requires SPEECH_START_FRAMES_NEEDED *consecutive* loud frames before committing to "speech
    started" (a noise gate/debounce) -- a single loud frame used to be enough, so a cough, a
    door, a chair creak, or the TV in the background would immediately get treated as the start
    of an utterance. A small pre-roll buffer keeps the frames leading up to the trigger so the
    actual first syllable isn't clipped once speech is confirmed."""
    silence_frames_needed = int(config.SILENCE_SECONDS_TO_STOP * config.SAMPLE_RATE / config.FRAME_SIZE)
    min_frames = int(config.MIN_RECORD_SECONDS * config.SAMPLE_RATE / config.FRAME_SIZE)
    max_frames = int(config.MAX_RECORD_SECONDS * config.SAMPLE_RATE / config.FRAME_SIZE)
    start_timeout_frames = (
        int(start_timeout_seconds * config.SAMPLE_RATE / config.FRAME_SIZE)
        if start_timeout_seconds is not None else None
    )
    confirm_frames_needed = config.SPEECH_START_FRAMES_NEEDED

    frames = []
    preroll = deque(maxlen=confirm_frames_needed)
    consecutive_silence = 0
    consecutive_loud = 0
    speech_started = False
    frames_read = 0

    while frames_read < max_frames:
        data, _ = stream.read(config.FRAME_SIZE)
        frame = data[:, 0]
        frames_read += 1
        loud = rms(frame) >= config.SILENCE_RMS_THRESHOLD

        if not speech_started:
            preroll.append(frame.copy())
            consecutive_loud = consecutive_loud + 1 if loud else 0
            if consecutive_loud >= confirm_frames_needed:
                speech_started = True
                frames.extend(preroll)  # keep the onset, not just the frames after confirmation
            elif start_timeout_frames is not None and frames_read >= start_timeout_frames:
                return None  # nobody spoke in time -- end the conversation
            continue

        frames.append(frame.copy())
        consecutive_silence = 0 if loud else consecutive_silence + 1
        if len(frames) >= min_frames and consecutive_silence >= silence_frames_needed:
            break

    if not speech_started:
        return None
    return np.concatenate(frames)


def send_to_jarvis(stream: sd.InputStream, audio: np.ndarray, session_id: str) -> bool | None:
    """Sends one utterance, plays back the reply. Returns True if Jarvis ended the
    conversation, False to keep it going, or None if the request itself failed."""
    buf = io.BytesIO()
    sf.write(buf, audio, config.SAMPLE_RATE, format="WAV", subtype="PCM_16")
    buf.seek(0)

    log.info("Sende %.1fs Audio an %s ...", len(audio) / config.SAMPLE_RATE, config.JARVIS_URL)
    try:
        resp = requests.post(
            f"{config.JARVIS_URL}/command",
            headers={"X-Jarvis-Key": config.JARVIS_API_KEY},
            files={"audio": ("clip.wav", buf, "audio/wav")},
            data={"session_id": session_id},
            timeout=90,
        )
    except requests.RequestException as e:
        log.error("Anfrage an Jarvis fehlgeschlagen: %s", e)
        notify("Jarvis", f"Konnte den Pi nicht erreichen: {e}")
        return None

    if resp.status_code != 200:
        log.error("Jarvis antwortete mit %s: %s", resp.status_code, resp.text[:500])
        notify("Jarvis", f"Fehler vom Server ({resp.status_code})")
        return None

    reply_text = urllib.parse.unquote(resp.headers.get("X-Reply-Text", ""))
    ended = resp.headers.get("X-Conversation-Ended", "false") == "true"
    log.info("Jarvis: %s (ended=%s)", reply_text, ended)

    reply_audio, sr = sf.read(io.BytesIO(resp.content), dtype="float32")
    playback_ms = int(len(reply_audio) / sr * 1000)
    notify("Jarvis spricht", reply_text[:250] or "(keine Antwort erhalten)", duration_ms=playback_ms)
    play_audio(stream, reply_audio, sr)
    return ended


def run_conversation(stream: sd.InputStream) -> None:
    """One full 'Hey Jarvis' session: greeting, then turn after turn until Jarvis calls
    end_conversation(), the request fails, or the user goes quiet for too long."""
    session_id = str(uuid.uuid4())
    play_greeting(stream)

    while True:
        notify("Jarvis hört zu", "Du bist dran ...", duration_ms=int(config.CONVERSATION_SILENCE_TIMEOUT * 1000))
        utterance = record_utterance(stream, start_timeout_seconds=config.CONVERSATION_SILENCE_TIMEOUT)
        if utterance is None:
            log.info("Keine Antwort innerhalb %.0fs -- Chat beendet.", config.CONVERSATION_SILENCE_TIMEOUT)
            notify("Jarvis", "Chat beendet.")
            return

        ended = send_to_jarvis(stream, utterance, session_id)
        if ended is None:  # request failed -- don't loop forever on a broken connection
            notify("Jarvis", "Chat beendet (Fehler).")
            return
        if ended:
            notify("Jarvis", "Chat beendet.")
            return
        # else: loop back around for the next turn, still listening


def main() -> None:
    log.info("Lade Wake-Word-Modell '%s' ...", config.WAKE_WORD_MODEL)
    oww = Model(
        wakeword_models=[str(config.WAKE_WORD_MODEL_PATH)],
        melspec_model_path=str(config.MELSPEC_MODEL_PATH),
        embedding_model_path=str(config.EMBEDDING_MODEL_PATH),
        inference_framework="onnx",
    )
    log.info("Jarvis-Client läuft. Höre auf 'Hey Jarvis' ... (%d Begrüßungen geladen)", len(GREETINGS))

    log.info("Mic-Device: %s", config.MIC_DEVICE if config.MIC_DEVICE is not None else "(System-Standard)")
    with sd.InputStream(
        samplerate=config.SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=config.FRAME_SIZE,
        device=config.MIC_DEVICE,
    ) as stream:
        while True:
            data, _ = stream.read(config.FRAME_SIZE)
            frame = data[:, 0]
            predictions = oww.predict(frame)
            score = predictions.get(config.WAKE_WORD_MODEL, 0.0)

            if score > config.WAKE_WORD_THRESHOLD:
                log.info("Wake word erkannt (score=%.2f).", score)
                oww.reset()
                run_conversation(stream)
                log.info("Höre wieder auf 'Hey Jarvis' ...")


if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            break
        except Exception:
            log.exception("Client abgestürzt, starte in 5s neu ...")
            time.sleep(5)
