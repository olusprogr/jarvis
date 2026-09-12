import asyncio
import logging
import urllib.parse

from fastapi import BackgroundTasks, FastAPI, Depends, Header, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import Response, JSONResponse
from langdetect import detect, LangDetectException

from . import config, tts, agent, telegram_bot
from .auth import require_api_key

DEFAULT_LANGUAGE = "de"  # used only if reply-language detection fails outright


def detect_reply_language(text: str, fallback: str = DEFAULT_LANGUAGE) -> str:
    """Detects the language of the reply text itself (what will actually be
    spoken), falling back for very short or ambiguous replies where
    detection isn't reliable."""
    if len(text.strip()) < 3:
        return fallback
    try:
        return detect(text)
    except LangDetectException:
        return fallback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOGS_DIR / "jarvis.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("jarvis.main")

app = FastAPI(title="Jarvis Brain")


@app.get("/jarvis/health")
def health():
    return {"status": "ok"}


@app.post("/jarvis/text")
def text_command(payload: dict, _auth=Depends(require_api_key)):
    """Debug/testing endpoint: text in, text out (no STT/TTS)."""
    text = payload.get("text", "")
    session_id = payload.get("session_id", "default")
    reply, ended = agent.ask(session_id, text)
    return {"reply": reply, "conversation_ended": ended}


@app.post("/jarvis/command")
async def voice_command(
    audio: UploadFile = File(...),
    session_id: str = Form(default="default"),
    _auth=Depends(require_api_key),
):
    """Full voice pipeline: audio in -> Gemini (understands speech directly, no local
    STT step) -> TTS -> audio out. Returns WAV bytes; reply is in the X-Reply-Text
    header (URL-encoded, since HTTP headers must be latin-1/ASCII-safe)."""
    audio_bytes = await audio.read()
    mime_type = "audio/wav" if (audio.filename or "").endswith(".wav") else "audio/webm"
    reply, ended = agent.ask_audio(session_id, audio_bytes, mime_type=mime_type)

    tts_language = detect_reply_language(reply)
    # tts.synthesize() blocks (network calls, subprocess, and edge-tts's own asyncio.run()
    # internally -- which would crash if called directly here, since this route already runs
    # inside FastAPI's event loop). Run it in a worker thread instead.
    wav_bytes = await asyncio.to_thread(tts.synthesize, reply, tts_language)

    headers = {
        "X-Reply-Text": urllib.parse.quote(reply),
        "X-Language": tts_language,
        "X-Conversation-Ended": "true" if ended else "false",
    }
    return Response(content=wav_bytes, media_type="audio/wav", headers=headers)


@app.post("/jarvis/telegram-webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    """Telegram POSTs here on every incoming message. We verify the secret token Telegram
    echoes back (set via telegram_bot.register_webhook), respond 200 immediately, and process
    the actual message in the background -- Telegram expects a fast response and will retry
    (duplicate-sending) if the webhook is slow, and our agent call can take several seconds."""
    if not config.TELEGRAM_WEBHOOK_SECRET or x_telegram_bot_api_secret_token != config.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(403, "Invalid webhook secret")
    update = await request.json()
    background_tasks.add_task(telegram_bot.handle_update, update)
    return {"ok": True}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    log.exception("Unhandled error")
    return JSONResponse(status_code=500, content={"error": str(exc)})
