"""Central config, loaded from .env (see .env.example)."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
JARVIS_API_KEY = os.getenv("JARVIS_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

VAULT_DIR = Path(os.getenv("VAULT_DIR", str(BASE_DIR / "vault"))).resolve()
MODELS_DIR = BASE_DIR / "models"
PIPER_BIN = MODELS_DIR / "piper" / "piper"
VOICE_DE = MODELS_DIR / "voices" / "de_DE-thorsten-high.onnx"
VOICE_EN = MODELS_DIR / "voices" / "en_US-lessac-high.onnx"

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_DOWNLOAD_ROOT = str(MODELS_DIR / "whisper")

LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
VAULT_DIR.mkdir(parents=True, exist_ok=True)

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8420"))

# Telegram bot (app/telegram_bot.py) -- two-way chat/voice with Jarvis from the phone, and the
# delivery channel for phone push notifications (app/tools/phone_notify.py).
# TELEGRAM_ALLOWED_CHAT_ID gates who the bot will actually respond to: Jarvis has full shell
# access, so an unrestricted public bot would be a serious security hole. Leave empty until
# the user's own chat_id is known (webhook logs it), then lock it down to just that id.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
TELEGRAM_ALLOWED_CHAT_ID = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "")

# ElevenLabs TTS (app/tts.py) -- best-sounding backend, and the only paid one. Leave the key
# empty to skip it entirely; the free Edge/Gemini/Piper chain still works.
# Voice IDs come from elevenlabs.io -> Voices (pick one, "ID" in its detail panel).
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")

# Email (app/tools/email_tool.py) -- plain IMAP/SMTP with a Gmail "App Password"
# (myaccount.google.com/apppasswords), not OAuth -- no Cloud Console app registration needed.
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")
EMAIL_IMAP_HOST = os.getenv("EMAIL_IMAP_HOST", "imap.gmail.com")
EMAIL_IMAP_PORT = int(os.getenv("EMAIL_IMAP_PORT", "993"))
EMAIL_SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "465"))
