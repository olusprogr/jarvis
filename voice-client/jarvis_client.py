"""Jarvis wake-word client. Runs in the background, listens for 'Hey Jarvis', then holds a
multi-turn conversation (no need to repeat the wake word between turns) until the user says
they're done or goes quiet for CONVERSATION_SILENCE_TIMEOUT seconds.
"""
import io
import json
import logging
import random
import sys
import time
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
import pc_agent

import config
from status_window import StatusWindow

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
GOODBYES = sorted(config.GREETINGS_DIR.glob("goodbye-*.wav"))

# Live status overlay -- shows what Jarvis is doing right now + a mic level meter, so it's
# always clear whose turn it is and whether the mic is actually picking the user up.
status = StatusWindow()

# Reused across requests so the TCP connection to the Pi stays warm (keep-alive) instead of
# a fresh handshake every single turn -- small but free latency win on every exchange.
_http = requests.Session()


def play_audio(stream: sd.InputStream, data: np.ndarray, sr: int) -> None:
    """Pauses the mic stream while playing, then resumes -- otherwise the mic picks up Jarvis's
    own voice through speaker bleed, which gets read as a buffered 'utterance' the instant the
    next record_utterance() starts, causing a spurious extra turn (the 'Du bist dran' spam /
    duplicated recordings the user reported earlier)."""
    stream.stop()
    try:
        sd.play(data, sr)
        sd.wait()
    finally:
        stream.start()
        # The device needs a moment to actually start delivering real audio again -- the first
        # reads right after stream.start() can be silence/garbage while it re-syncs. Without this,
        # the *next* record_utterance() call would start reading during that dead window and
        # could miss the user's first syllable entirely (silence -> no speech detected -> no
        # reply at all, which is what "spreche ich und es kommt keine Nachricht" looked like).
        try:
            stream.read(config.FRAME_SIZE * 2)
        except Exception:
            pass


def play_greeting(stream: sd.InputStream) -> None:
    """Plays one of the pre-generated 'Bereit, Master.'-style clips instantly --
    no network round-trip, so there's zero delay between the wake word and *some* response."""
    if not GREETINGS:
        return
    try:
        data, sr = sf.read(str(random.choice(GREETINGS)), dtype="float32")
        play_audio(stream, data, sr)
    except Exception:
        log.exception("Begrüßung konnte nicht abgespielt werden")


def play_goodbye(stream: sd.InputStream) -> None:
    """Says 'Chat beendet' out loud before the conversation actually closes, so the end of a
    conversation is audible rather than just a silent timeout the user has to infer."""
    if not GOODBYES:
        return
    try:
        data, sr = sf.read(str(random.choice(GOODBYES)), dtype="float32")
        play_audio(stream, data, sr)
    except Exception:
        log.exception("Verabschiedung konnte nicht abgespielt werden")


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))


class NoiseFloor:
    """Tracks the room's ambient noise level and derives the speech threshold from it.

    A single hardcoded RMS cutoff only ever fits one room and one mic: too high and quiet
    speech is missed, too low and a fan or a TV registers as speech. So the floor is learned
    continuously -- but *only* from frames that are already quiet relative to the current floor
    (`NOISE_FLOOR_QUIET_GATE`), otherwise the user's own speech would drag the floor up and
    progressively deafen the gate. Clamped between MIN/MAX so a silent room can't make it
    hypersensitive and a loud one can't make it deaf.
    """

    def __init__(self):
        self.floor = config.MIN_SPEECH_RMS / config.SPEECH_RATIO

    def update(self, level: float) -> None:
        if level < self.floor * config.NOISE_FLOOR_QUIET_GATE:
            a = config.NOISE_FLOOR_ALPHA
            self.floor = max(a * self.floor + (1.0 - a) * level, 1e-3)

    @property
    def threshold(self) -> float:
        if config.SILENCE_RMS_THRESHOLD:  # explicitly pinned in .env -- honour it, don't adapt
            return config.SILENCE_RMS_THRESHOLD
        return min(max(self.floor * config.SPEECH_RATIO, config.MIN_SPEECH_RMS),
                   config.MAX_SPEECH_RMS)


noise = NoiseFloor()


def probe_mic(device, blocksize: int) -> float | None:
    """Opens a device briefly and returns its peak RMS, or None if it can't be opened."""
    try:
        with sd.InputStream(device=device, samplerate=config.SAMPLE_RATE, channels=1,
                            dtype="int16", blocksize=blocksize) as s:
            peak = 0.0
            deadline = time.monotonic() + config.MIC_PROBE_SECONDS
            while time.monotonic() < deadline:
                data, _ = s.read(blocksize)
                peak = max(peak, rms(data[:, 0]))
            return peak
    except Exception:
        return None


def choose_mic(blocksize: int):
    """Picks an input device that actually delivers audio.

    Order: the configured MIC_DEVICE (warn loudly if it probes silent, but respect the choice),
    then the OS default, then -- if that's silent/unopenable and MIC_AUTO_FALLBACK is on -- the
    loudest of everything else. Without this a silent default mic looks exactly like "Jarvis
    ignores me", with nothing in the logs to explain it (which is precisely what happened here
    once already, and needed manual device probing to diagnose)."""
    if config.MIC_DEVICE is not None:
        peak = probe_mic(config.MIC_DEVICE, blocksize)
        if peak is None:
            log.warning("Mic %r laesst sich nicht oeffnen -- versuche es trotzdem.", config.MIC_DEVICE)
        elif peak < config.MIC_SILENT_RMS:
            log.warning("Mic %r wirkt stumm (peak=%.1f). Pruefe Windows-Pegel oder setze MIC_DEVICE um.",
                        config.MIC_DEVICE, peak)
        else:
            log.info("Mic %r ok (peak=%.1f).", config.MIC_DEVICE, peak)
        return config.MIC_DEVICE

    default = sd.default.device[0] if sd.default.device else None
    if default is not None and default >= 0:
        peak = probe_mic(default, blocksize)
        if peak is not None and peak >= config.MIC_SILENT_RMS:
            log.info("Standard-Mic [%d] %s (peak=%.1f).", default,
                     sd.query_devices(default)["name"], peak)
            return default
        log.warning("Standard-Mic [%s] stumm oder nicht nutzbar (peak=%s).", default,
                    f"{peak:.1f}" if peak is not None else "nicht oeffenbar")

    if not config.MIC_AUTO_FALLBACK:
        return default

    best, best_peak = None, -1.0
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] < 1 or idx == default:
            continue
        peak = probe_mic(idx, blocksize)
        if peak is not None and peak > best_peak:
            best, best_peak = idx, peak

    if best is not None and best_peak >= config.MIC_SILENT_RMS:
        log.info("Mic automatisch gewaehlt: [%d] %s (peak=%.1f).", best,
                 sd.query_devices(best)["name"], best_peak)
        return best

    log.warning("Kein aktives Mic gefunden -- nutze Standard.")
    return default


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

    seconds_per_frame = config.FRAME_SIZE / config.SAMPLE_RATE

    while frames_read < max_frames:
        data, _ = stream.read(config.FRAME_SIZE)
        frame = data[:, 0]
        frames_read += 1
        level = rms(frame)
        threshold = noise.threshold
        loud = level >= threshold
        if not loud:
            noise.update(level)  # keep learning the room, but never from the user's own speech
        # Scale against ~4x the speech threshold so normal speech fills a good chunk of the bar
        # without instantly pinning it at full.
        status.set_level(level / (threshold * 4))

        if not speech_started:
            preroll.append(frame.copy())
            consecutive_loud = consecutive_loud + 1 if loud else 0
            if consecutive_loud >= confirm_frames_needed:
                speech_started = True
                status.set_state("recording")
                status.set_countdown(None)
                frames.extend(preroll)  # keep the onset, not just the frames after confirmation
            elif start_timeout_frames is not None:
                if frames_read >= start_timeout_frames:
                    return None  # nobody spoke in time -- end the conversation
                status.set_countdown((start_timeout_frames - frames_read) * seconds_per_frame)
            continue

        frames.append(frame.copy())
        consecutive_silence = 0 if loud else consecutive_silence + 1
        if len(frames) >= min_frames and consecutive_silence >= silence_frames_needed:
            break

    if not speech_started:
        return None
    return np.concatenate(frames)


def _iter_frames(resp):
    """Parses the streaming wire format from /jarvis/command-stream: repeated frames of
    [1 byte type][4 bytes big-endian length][payload]. Yields (type_byte, payload)."""
    buf = b""
    for piece in resp.iter_content(8192):
        buf += piece
        while len(buf) >= 5:
            kind = buf[0:1]
            length = int.from_bytes(buf[1:5], "big")
            if len(buf) < 5 + length:
                break
            yield kind, buf[5:5 + length]
            buf = buf[5 + length:]


def send_to_jarvis(stream: sd.InputStream, audio: np.ndarray, session_id: str) -> bool | None:
    """Sends one utterance, plays the reply as it streams in sentence by sentence. Returns True
    if Jarvis ended the conversation, False to keep going, None if the request failed.

    Uses the streaming endpoint so playback of sentence 1 can start while the backend is still
    generating/synthesizing the rest -- the mic stays paused across the whole sequence, not
    per-sentence, so it never picks up Jarvis's own audio between chunks."""
    buf = io.BytesIO()
    sf.write(buf, audio, config.SAMPLE_RATE, format="WAV", subtype="PCM_16")
    buf.seek(0)

    log.info("Sende %.1fs Audio an %s ...", len(audio) / config.SAMPLE_RATE, config.JARVIS_URL)
    status.set_state("thinking")
    try:
        resp = _http.post(
            f"{config.JARVIS_URL}/command-stream",
            headers={"X-Jarvis-Key": config.JARVIS_API_KEY},
            files={"audio": ("clip.wav", buf, "audio/wav")},
            data={"session_id": session_id},
            timeout=90,
            stream=True,
        )
    except requests.RequestException as e:
        log.error("Anfrage an Jarvis fehlgeschlagen: %s", e)
        return None

    if resp.status_code != 200:
        log.error("Jarvis antwortete mit %s: %s", resp.status_code, resp.text[:500])
        return None

    ended = False
    mic_paused = False
    try:
        for kind, payload in _iter_frames(resp):
            if kind == b"S":
                # Sentence text arrives just before its audio -- show it live in the status
                # window so the current sentence is readable while it's being spoken.
                text = json.loads(payload.decode("utf-8")).get("text", "")
                if text:
                    status.set_state("speaking", text)
            elif kind == b"A":
                if not mic_paused:
                    stream.stop()
                    mic_paused = True
                chunk_audio, sr = sf.read(io.BytesIO(payload), dtype="float32")
                sd.play(chunk_audio, sr)
                sd.wait()
            elif kind == b"M":
                meta = json.loads(payload.decode("utf-8"))
                ended = bool(meta.get("ended"))
                log.info("Jarvis: %s (ended=%s)", meta.get("reply", ""), ended)
    except Exception:
        log.exception("Fehler beim Empfangen der Antwort")
        return None
    finally:
        if mic_paused:
            stream.start()
            try:
                stream.read(config.FRAME_SIZE * 2)  # discard re-sync garbage, see play_audio()
            except Exception:
                pass

    return ended


def run_conversation(stream: sd.InputStream) -> None:
    """One full 'Hey Jarvis' session: greeting, then turn after turn until Jarvis calls
    end_conversation(), the request fails, or the user goes quiet for too long."""
    session_id = str(uuid.uuid4())
    status.show("greeting")
    try:
        play_greeting(stream)

        while True:
            status.set_state("waiting")
            utterance = record_utterance(stream, start_timeout_seconds=config.CONVERSATION_SILENCE_TIMEOUT)
            if utterance is None:
                log.info("Keine Antwort innerhalb %.0fs -- Chat beendet.", config.CONVERSATION_SILENCE_TIMEOUT)
                status.set_state("ended")
                play_goodbye(stream)
                return

            ended = send_to_jarvis(stream, utterance, session_id)
            if ended is None:  # request failed -- don't loop forever on a broken connection
                status.set_state("ended", "Verbindungsfehler")
                time.sleep(1.5)
                return
            if ended:
                log.info("Jarvis hat das Gespräch beendet.")
                status.set_state("ended")
                time.sleep(1.2)
                return
            # else: loop back around for the next turn, still listening
    finally:
        status.hide()


def main() -> None:
    log.info("Lade Wake-Word-Modell '%s' ...", config.WAKE_WORD_MODEL)
    oww = Model(
        wakeword_models=[str(config.WAKE_WORD_MODEL_PATH)],
        melspec_model_path=str(config.MELSPEC_MODEL_PATH),
        embedding_model_path=str(config.EMBEDDING_MODEL_PATH),
        inference_framework="onnx",
    )
    pc_agent.start()
    log.info("Jarvis-Client läuft. Höre auf 'Hey Jarvis' ... (%d Begrüßungen geladen)", len(GREETINGS))

    device = choose_mic(config.FRAME_SIZE)
    log.info("Mic-Device: %s | Start-Schwelle: %.0f", device, noise.threshold)
    with sd.InputStream(
        samplerate=config.SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=config.FRAME_SIZE,
        device=device,
    ) as stream:
        while True:
            data, _ = stream.read(config.FRAME_SIZE)
            frame = data[:, 0]
            # Idle listening is the best time to learn the room's noise floor -- it's quiet by
            # definition here, so the threshold is already tuned when a conversation starts.
            noise.update(rms(frame))
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
