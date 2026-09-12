"""Config for the PC-side wake-word client. Copy .env.example to .env and adjust."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# When bundled by PyInstaller (--onefile), __file__ lives in a throwaway temp
# extraction dir that changes every launch. Anchor everything to the .exe's own
# folder instead so .env/logs/models persist next to wherever it's installed.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

# LAN endpoint by default (low latency, works when at home on the same network).
# Falls back to the public HTTPS URL if JARVIS_URL is overridden in .env
# (e.g. JARVIS_URL=https://olusprogr.dynv6.net/jarvis for away-from-home use).
JARVIS_URL = os.getenv("JARVIS_URL", "http://192.168.178.211:8420/jarvis")
JARVIS_API_KEY = os.getenv("JARVIS_API_KEY", "")

WAKE_WORD_MODEL = os.getenv("WAKE_WORD_MODEL", "hey_jarvis_v0.1")
WAKE_WORD_THRESHOLD = float(os.getenv("WAKE_WORD_THRESHOLD", "0.5"))

# openWakeWord model files, bundled next to the exe under models/ (see install.ps1)
# instead of relying on the pip package's own resources/ dir, which doesn't exist
# in a frozen build.
MODELS_DIR = BASE_DIR / "models"
WAKE_WORD_MODEL_PATH = MODELS_DIR / f"{WAKE_WORD_MODEL}.onnx"
MELSPEC_MODEL_PATH = MODELS_DIR / "melspectrogram.onnx"
EMBEDDING_MODEL_PATH = MODELS_DIR / "embedding_model.onnx"

MIC_DEVICE = os.getenv("MIC_DEVICE") or None  # index (int) or a name substring (str); unset = OS default
if MIC_DEVICE is not None:
    try:
        MIC_DEVICE = int(MIC_DEVICE)
    except ValueError:
        pass  # keep as string -- sounddevice matches device names by substring

SAMPLE_RATE = 16000
FRAME_SIZE = 1280  # 80ms at 16kHz, openWakeWord's expected chunk size

# Recording (after wake word triggers, and for each turn within a conversation)
MAX_RECORD_SECONDS = float(os.getenv("MAX_RECORD_SECONDS", "15"))
# 0.7 was too aggressive -- it cut the user off mid-sentence, sending 1-2s fragments that the
# agent couldn't make sense of (and then ended the conversation on). 1.0 is the compromise.
SILENCE_SECONDS_TO_STOP = float(os.getenv("SILENCE_SECONDS_TO_STOP", "1.0"))
MIN_RECORD_SECONDS = float(os.getenv("MIN_RECORD_SECONDS", "0.6"))
# Noise gate: this many *consecutive* loud frames (at 80ms/frame, 3 = ~240ms) are required
# before a sound counts as "speech started" -- filters out coughs/door clunks/brief background
# noise that used to trigger a recording off a single loud frame.
SPEECH_START_FRAMES_NEEDED = int(os.getenv("SPEECH_START_FRAMES_NEEDED", "3"))

# --- Adaptive speech threshold -----------------------------------------------------------
# Replaces a single hardcoded RMS cutoff (was 300), which only ever suited one room and one mic:
# too high and quiet speech is missed, too low and a fan or TV counts as speech. Instead the
# ambient noise floor is measured continuously and the speech threshold is derived from it.
SILENCE_RMS_THRESHOLD = float(os.getenv("SILENCE_RMS_THRESHOLD", "0")) or None  # set to pin it
NOISE_FLOOR_ALPHA = float(os.getenv("NOISE_FLOOR_ALPHA", "0.99"))   # closer to 1 = adapts slower
NOISE_FLOOR_QUIET_GATE = float(os.getenv("NOISE_FLOOR_QUIET_GATE", "2.2"))  # only learn below floor*this
SPEECH_RATIO = float(os.getenv("SPEECH_RATIO", "3.5"))              # speech = this much above the floor
MIN_SPEECH_RMS = float(os.getenv("MIN_SPEECH_RMS", "180"))          # absolute floor: never hypersensitive
MAX_SPEECH_RMS = float(os.getenv("MAX_SPEECH_RMS", "2500"))         # absolute ceiling: never go deaf

# --- Microphone probing ------------------------------------------------------------------
# On startup each candidate device is opened briefly and its peak level measured, so a silent
# or unopenable default mic gets detected and skipped instead of silently hearing nothing.
MIC_PROBE_SECONDS = float(os.getenv("MIC_PROBE_SECONDS", "0.4"))
MIC_SILENT_RMS = float(os.getenv("MIC_SILENT_RMS", "8"))  # peak below this = effectively dead
MIC_AUTO_FALLBACK = os.getenv("MIC_AUTO_FALLBACK", "1") != "0"  # scan for a working mic if needed

# Multi-turn conversation: after the wake word (or after Jarvis finishes speaking), how long to
# wait for the user to start talking before ending the conversation automatically.
CONVERSATION_SILENCE_TIMEOUT = float(os.getenv("CONVERSATION_SILENCE_TIMEOUT", "5.0"))

GREETINGS_DIR = MODELS_DIR  # pre-generated "Ich höre Sie, Master." etc, greeting-*.wav

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
