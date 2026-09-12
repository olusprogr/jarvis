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
SILENCE_SECONDS_TO_STOP = float(os.getenv("SILENCE_SECONDS_TO_STOP", "1.2"))
SILENCE_RMS_THRESHOLD = float(os.getenv("SILENCE_RMS_THRESHOLD", "300"))  # int16 RMS
MIN_RECORD_SECONDS = float(os.getenv("MIN_RECORD_SECONDS", "0.6"))
# Noise gate: this many *consecutive* loud frames (at 80ms/frame, 3 = ~240ms) are required
# before a sound counts as "speech started" -- filters out coughs/door clunks/brief background
# noise that used to trigger a recording off a single loud frame.
SPEECH_START_FRAMES_NEEDED = int(os.getenv("SPEECH_START_FRAMES_NEEDED", "3"))

# Multi-turn conversation: after the wake word (or after Jarvis finishes speaking), how long to
# wait for the user to start talking before ending the conversation automatically.
CONVERSATION_SILENCE_TIMEOUT = float(os.getenv("CONVERSATION_SILENCE_TIMEOUT", "3.0"))

GREETINGS_DIR = MODELS_DIR  # pre-generated "Ich höre Sie, Master." etc, greeting-*.wav

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
