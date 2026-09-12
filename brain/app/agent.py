"""Jarvis agent: Gemini with function-calling tools, one chat session per session_id
(in-memory only — resets on service restart). This is the 'brain'."""
import contextvars
import logging
import re
from google import genai
from google.genai import types

from . import config
from .tools.shell import run_shell_command
from .tools.obsidian import obsidian_write_note, obsidian_read_note, obsidian_list_notes, obsidian_search
from .tools.websearch import web_search, fetch_url
from .tools.phone_notify import notify_phone
from .tools.email_tool import list_unread_emails, read_email, search_emails, send_email

log = logging.getLogger("jarvis.agent")

# Set right before each chat.send_message() call so the end_conversation tool below knows which
# session it was invoked for (a plain global would race if two requests ever overlapped).
_current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_session_id", default="")
_end_flags: dict[str, bool] = {}


def end_conversation() -> str:
    """Call this exactly when the user wants to end the whole conversation session -- they say
    goodbye, "Chat beenden", "das wars", "danke, das ist alles", "Jarvis, Schluss für jetzt" or
    similar, or a multi-turn exchange has reached a clear, natural conclusion. Do NOT call this
    just because you finished answering a single question -- only when the user is done talking
    to you for now and the voice client should stop listening until the wake word again.
    """
    sid = _current_session_id.get()
    _end_flags[sid] = True
    return "Ok, Konversation wird beendet."

SYSTEM_INSTRUCTION = """\
Du bist Jarvis, persönlicher Assistent auf dem Raspberry Pi des Nutzers.

Ton: ruhig, kompetent, leicht trocken. Souverän statt gezwungen lässig, kein Anbiedern, keine \
Ausrufezeichen-Begeisterung. Du erledigst Dinge, statt sie vorzuschlagen.

- Antworte kurz und gesprochen, wie am Telefon (wird per TTS vorgelesen). Kein Markdown, keine \
Aufzählungen, keine Codeblöcke.
- Sprich die Sprache des Nutzers (Deutsch oder Englisch).
- Du hast vollen Shell-Zugriff inkl. sudo/root (run_shell_command) und sollst ihn frei nutzen: \
recherchieren, programmieren, konfigurieren, installieren, ändern, löschen. Bei zerstörerischen \
oder schwer umkehrbaren Aktionen kurz ansagen, was du tust, bevor du es tust.
- Vault (obsidian_*) ist dein Langzeitgedächtnis: wichtige Fakten, Projekte und Ergebnisse dort \
festhalten, und nachschauen statt zu fragen, was du wissen könntest.
- web_search findet Seiten, fetch_url liest sie aus. Für Nachrichten, aktuelle Zahlen oder \
Artikelinhalte reicht die Trefferliste nie — immer mit fetch_url nachladen, bei News am besten \
direkt den RSS-Feed (z.B. tagesschau.de/xml/rss2). notify_phone für Nachrichten aufs Handy.
- E-Mail-Zugriff (list_unread_emails/read_email/search_emails/send_email). Vor send_email kurz \
rückfragen, außer der Nutzer hat den Inhalt schon diktiert.
- Laufende Konversation: nach deiner Antwort wird automatisch weiter zugehört, ohne erneutes \
"Hey Jarvis". Antworte einfach weiter.
- end_conversation() NUR bei ausdrücklicher Verabschiedung ("tschüss", "das wars", "Chat \
beenden"). NIEMALS weil eine Frage beantwortet ist oder du etwas nicht verstanden hast — dann \
nachfragen. Im Zweifel weiterreden; nach ein paar Sekunden Stille beendet das System selbst.
"""

TOOLS = [
    run_shell_command,
    obsidian_write_note,
    obsidian_read_note,
    obsidian_list_notes,
    obsidian_search,
    web_search,
    fetch_url,
    notify_phone,
    list_unread_emails,
    read_email,
    search_emails,
    send_email,
    end_conversation,
]

_client: genai.Client | None = None
_sessions: dict[str, "genai.chats.Chat"] = {}


def get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def get_session(session_id: str):
    if session_id not in _sessions:
        client = get_client()
        _sessions[session_id] = client.chats.create(
            model=config.GEMINI_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=TOOLS,
                temperature=0.4,
            ),
        )
    return _sessions[session_id]


def ask(session_id: str, user_text: str) -> tuple[str, bool]:
    """Returns (reply_text, conversation_ended)."""
    chat = get_session(session_id)
    _end_flags[session_id] = False
    token = _current_session_id.set(session_id)
    try:
        log.info("[%s] User: %s", session_id, user_text)
        response = chat.send_message(user_text)
    finally:
        _current_session_id.reset(token)
    reply = (response.text or "").strip()
    log.info("[%s] Jarvis: %s", session_id, reply)
    return reply or "Kannst du das nochmal sagen?", _end_flags.get(session_id, False)


def ask_audio(session_id: str, audio_bytes: bytes, mime_type: str = "audio/wav") -> tuple[str, bool]:
    """Sends the raw audio straight to Gemini (it understands speech natively) instead of
    running a separate local Whisper transcription step first -- cuts a whole slow pipeline
    stage (was 3-10s on the Pi's CPU) down to one Gemini call that does STT+reasoning together.
    Returns (reply_text, conversation_ended)."""
    chat = get_session(session_id)
    _end_flags[session_id] = False
    token = _current_session_id.set(session_id)
    try:
        log.info("[%s] User (audio, %d bytes)", session_id, len(audio_bytes))
        response = chat.send_message([
            types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
        ])
    finally:
        _current_session_id.reset(token)
    reply = (response.text or "").strip()
    log.info("[%s] Jarvis: %s", session_id, reply)
    return reply or "Kannst du das nochmal sagen?", _end_flags.get(session_id, False)


# Sentence-ish boundaries: split on . ! ? : ; and newlines, keeping the punctuation. Used to
# chop the streamed reply into speakable pieces so TTS can start on sentence 1 while Gemini is
# still generating sentence 2 -- the whole point of streaming here is perceived latency.
_SENTENCE_END = re.compile(r"(?<=[.!?:;])\s+|\n+")


def ask_audio_streaming(session_id: str, audio_bytes: bytes, mime_type: str = "audio/wav"):
    """Like ask_audio(), but a generator yielding (sentence_text, is_final, ended) as Gemini
    produces them. `ended` is only meaningful on the final yield (the end_conversation tool may
    not have been called yet when earlier sentences are emitted)."""
    chat = get_session(session_id)
    _end_flags[session_id] = False
    token = _current_session_id.set(session_id)
    buffer = ""
    full_reply = ""
    try:
        log.info("[%s] User (audio, %d bytes, streaming)", session_id, len(audio_bytes))
        for chunk in chat.send_message_stream([
            types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
        ]):
            # chunk.text is None on non-text parts (e.g. the function_call chunks that
            # automatic function calling emits) -- skip those, AFC handles them itself.
            if not chunk.text:
                continue
            buffer += chunk.text
            # Emit every complete sentence currently in the buffer, keep the trailing partial.
            while True:
                match = _SENTENCE_END.search(buffer)
                if not match:
                    break
                sentence, buffer = buffer[: match.end()].strip(), buffer[match.end():]
                if sentence:
                    full_reply += sentence + " "
                    yield sentence, False, False
    finally:
        _current_session_id.reset(token)

    tail = buffer.strip()
    if tail:
        full_reply += tail
    full_reply = full_reply.strip()
    ended = _end_flags.get(session_id, False)

    if not full_reply:
        # Empty reply means the model had nothing to say -- in practice this happens when the
        # recording was a clipped fragment or noise. Treat it as "didn't catch that", never as
        # a reason to end: an empty goodbye leaves the user staring at a dead conversation.
        log.info("[%s] Leere Antwort (ended=%s), frage nach statt zu beenden.", session_id, ended)
        yield "Kannst du das nochmal sagen?", True, False
        return

    log.info("[%s] Jarvis: %s", session_id, full_reply)
    # Final yield carries the trailing partial sentence (if any) plus the real `ended` flag.
    yield tail or "", True, ended
