"""Speech-to-text via faster-whisper. Auto-detects DE/EN (and others)."""
import logging
from faster_whisper import WhisperModel
from . import config

log = logging.getLogger("jarvis.stt")

_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        log.info("Loading whisper model '%s' (%s)...", config.WHISPER_MODEL_SIZE, config.WHISPER_COMPUTE_TYPE)
        _model = WhisperModel(
            config.WHISPER_MODEL_SIZE,
            device="cpu",
            compute_type=config.WHISPER_COMPUTE_TYPE,
            download_root=config.WHISPER_DOWNLOAD_ROOT,
            cpu_threads=4,  # Pi 5 has 4 cores -- pin explicitly rather than guessing
        )
        log.info("Whisper model loaded.")
    return _model


def transcribe(audio_path: str) -> tuple[str, str]:
    """Returns (text, language_code). language_code is e.g. 'de' or 'en'."""
    model = get_model()
    # beam_size=5 (the faster-whisper default) roughly doubles inference time for
    # a small accuracy gain that doesn't matter much for short voice commands --
    # beam_size=2 is a much better latency/accuracy trade-off for a live assistant.
    segments, info = model.transcribe(audio_path, beam_size=2, vad_filter=True)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    language = info.language or "en"
    log.info("Transcribed (%s): %s", language, text)
    return text, language
