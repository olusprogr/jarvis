"""Text-to-speech. Three backends, tried in this order by synthesize():
- synthesize_edge(): Microsoft Edge's free TTS (via the `edge-tts` package, no API key, no quota
  -- unofficial use of the Edge "Read Aloud" service, but widely used and reliable in practice).
  PRIMARY as of 2026-09-12: Conrad voice + an EQ/de-ess/mild-pitch chain the user approved after
  Gemini's raw pitch-shifted/rubberband-shifted voices sounded artifacted. See EDGE_FILTER_CHAIN.
- synthesize_gemini(): Gemini's cloud TTS (Algenib voice) -- good quality but both free-tier
  models cap out at 10 requests/*day* each (hit during testing 2026-09-12), so it's a fallback.
- synthesize_piper(): local Piper binary, fast, unlimited, always available -- last resort.
"""
import asyncio
import logging
import subprocess
import tempfile
import wave
from io import BytesIO
from pathlib import Path

import edge_tts
from google import genai
from google.genai import types

from . import config

log = logging.getLogger("jarvis.tts")

# Voice + ffmpeg filter chain the user A/B-tested and approved 2026-09-12:
# - equalizer boosts at 110Hz (chest/fundamental) and 3000Hz (clarity/intelligibility)
# - equalizer cut at 350Hz (removes the "muddy/boxy" mid-range some deepened voices get)
# - deesser tames sibilance that gets harsher when the rest of the voice is deepened
# - a mild rubberband pitch shift (0.94, i.e. ~-6%) on top of edge-tts's own --pitch=-15Hz
#   -- much gentler than the 0.85-0.88 factors that sounded robotic on the Piper voices earlier,
#   the EQ chain is doing most of the "deeper" work here, not the pitch shift itself.
EDGE_VOICE = {"de": "de-DE-ConradNeural", "en": "en-US-ChristopherNeural"}
EDGE_PITCH = "-15Hz"
EDGE_FILTER_CHAIN = (
    "equalizer=f=110:t=q:w=1:g=4,"
    "equalizer=f=350:t=q:w=1.2:g=-4,"
    "equalizer=f=3000:t=q:w=1:g=1.5,"
    "deesser=i=0.3,"
    "rubberband=pitch=0.94"
)

# Tried in order until one succeeds -- each model has its own separate free-tier quota bucket,
# so falling through to the next one on a 429 gets more mileage out of the free tier before
# finally dropping to local Piper. gemini-3.1-flash-tts-preview is cheapest/lowest-latency per
# Google's pricing docs but its free tier caps out fast (10 requests/*day*, hit during testing
# 2026-09-12); gemini-2.5-flash-preview-tts is the fallback with a separate quota.
GEMINI_TTS_MODELS = ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]
GEMINI_TTS_VOICE = "Algenib"  # "gravelly" per Google's voice list -- deepest of the male voices tried
GEMINI_TTS_SAMPLE_RATE = 24000  # matches the raw PCM Gemini TTS returns (audio/L16;rate=24000)

# Gemini TTS is "controllable" -- a natural-language style instruction prepended to the text
# measurably deepens/slows the delivery (user-confirmed A/B test 2026-09-12). Kept separate from
# the actual reply text sent to the agent so the instruction never ends up in chat history.
STYLE_INSTRUCTION = {
    "de": "Sprich mit einer sehr tiefen, männlichen Stimme in normalem, zügigem Tempo: ",
    "en": "Speak in a very deep male voice at a normal, brisk pace: ",
}

_tts_client: genai.Client | None = None


def _get_tts_client() -> genai.Client:
    global _tts_client
    if _tts_client is None:
        _tts_client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _tts_client


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def synthesize_edge(text: str, language: str = "de") -> bytes:
    """Microsoft Edge TTS + the EQ/de-ess/pitch chain above. Free, no API key, no daily cap."""
    voice = EDGE_VOICE.get(language, EDGE_VOICE["de"])
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
        mp3_path = Path(tmp_mp3.name)
    wav_path = mp3_path.with_suffix(".wav")
    try:
        async def _generate():
            communicate = edge_tts.Communicate(text, voice, pitch=EDGE_PITCH)
            await communicate.save(str(mp3_path))

        asyncio.run(_generate())

        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(mp3_path), "-af", EDGE_FILTER_CHAIN, str(wav_path)],
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            log.error("ffmpeg EQ chain failed: %s", proc.stderr.decode(errors="replace"))
            raise RuntimeError("Edge TTS post-processing failed")
        return wav_path.read_bytes()
    finally:
        mp3_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


def synthesize_gemini(text: str, language: str = "de") -> bytes:
    """Gemini native TTS -- multilingual, voice (Algenib) is independent of language, but the
    style instruction is picked by language so it doesn't get spoken/leak into the output.
    Tries each model in GEMINI_TTS_MODELS in order, since each has its own quota bucket."""
    client = _get_tts_client()
    style = STYLE_INSTRUCTION.get(language, STYLE_INSTRUCTION["de"])
    last_error: Exception | None = None
    for model in GEMINI_TTS_MODELS:
        try:
            response = client.models.generate_content(
                model=model,
                contents=style + text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=GEMINI_TTS_VOICE)
                        )
                    ),
                ),
            )
            part = response.candidates[0].content.parts[0]
            return _pcm_to_wav(part.inline_data.data, GEMINI_TTS_SAMPLE_RATE)
        except Exception as e:
            log.warning("Gemini TTS model %s failed (%s), trying next...", model, e)
            last_error = e
    raise last_error


def synthesize_piper(text: str, language: str = "en") -> bytes:
    """Runs Piper synchronously, returns WAV bytes. Kept as a fast local fallback."""
    voice = config.VOICE_DE if language.startswith("de") else config.VOICE_EN
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        out_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            [str(config.PIPER_BIN), "-m", str(voice), "-f", str(out_path)],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            log.error("Piper failed: %s", proc.stderr.decode(errors="replace"))
            raise RuntimeError("TTS synthesis failed")
        return out_path.read_bytes()
    finally:
        out_path.unlink(missing_ok=True)


def synthesize(text: str, language: str = "de") -> bytes:
    """Active TTS entry point used by main.py. Edge (free, unlimited, approved voice) first,
    then Gemini (better quality but daily-capped), then local Piper as the always-works floor."""
    try:
        return synthesize_edge(text, language)
    except Exception:
        log.exception("Edge TTS failed, trying Gemini...")
    try:
        return synthesize_gemini(text, language)
    except Exception:
        log.exception("Gemini TTS also failed, falling back to local Piper")
        return synthesize_piper(text, language)
