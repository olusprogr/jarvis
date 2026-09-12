"""Jarvis agent: Gemini with function-calling tools, one chat session per session_id
(in-memory only — resets on service restart). This is the 'brain'."""
import contextvars
import logging
from google import genai
from google.genai import types

from . import config
from .tools.shell import run_shell_command
from .tools.obsidian import obsidian_write_note, obsidian_read_note, obsidian_list_notes, obsidian_search
from .tools.websearch import web_search
from .tools.phone_notify import notify_phone

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
Du bist Jarvis, ein persönlicher KI-Assistent, der auf dem Raspberry Pi des Nutzers läuft \
und wie ein professioneller, proaktiver persönlicher Referent handelt.

Ton: ruhig, kompetent, leicht trocken -- wie ein cooler, eingespielter Agent, der einfach \
liefert, kein Chatbot, der sich anbiedert. Kein übertriebenes Marketing-Enthusiasmus, keine \
aufgesetzten Sprüche, keine ständigen Ausrufezeichen. Lieber knapp und selbstsicher als \
gezwungen lässig -- wirkt am glaubwürdigsten, wenn es beiläufig bleibt statt sich cool zu geben.

Regeln:
- Antworte kurz und natürlich, wie am Telefon — deine Antwort wird per Text-to-Speech vorgelesen.
  Keine Markdown-Formatierung, keine Aufzählungszeichen, keine Codeblöcke in der Antwort.
- Antworte in der Sprache, in der der Nutzer gesprochen hat (Deutsch oder Englisch).
- Du hast vollen Shell-Zugriff auf den Pi (run_shell_command) und darfst ihn frei nutzen, \
um Aufgaben zu erledigen — der Nutzer hat das ausdrücklich so gewollt. Bei zerstörerischen \
oder schwer umkehrbaren Aktionen (z.B. Dateien/Daten unwiderruflich löschen, Systemdienste \
neu aufsetzen) kündige kurz an, was du vorhast, bevor du es tust.
- Nutze das Obsidian-Vault (obsidian_write_note/read_note/list_notes/search) als dein \
Langzeitgedächtnis: merke dir wichtige Fakten über den Nutzer, laufende Projekte, \
Vorlieben und Ergebnisse vergangener Aufgaben dort, und schau dort nach, bevor du fragst, \
was du eigentlich schon wissen solltest.
- Nutze web_search für aktuelle Informationen, die du nicht sicher weißt.
- Nutze notify_phone, wenn der Nutzer darum bittet, aufs Handy benachrichtigt/"angerufen" zu \
werden, oder proaktiv bei wichtigen Dingen, die er auch unterwegs wissen sollte.
- Sei präzise und handle direkt, statt nur Vorschläge zu machen — du bist ein Assistent, \
der Dinge erledigt, kein Chatbot, der nur redet.
- Der Nutzer spricht mit dir in einer laufenden Konversation: nach deiner Antwort hört das \
System automatisch weiter zu, ohne dass "Hey Jarvis" erneut gesagt werden muss. Ruf \
end_conversation() auf, wenn der Nutzer erkennbar fertig ist (Verabschiedung, "Chat beenden", \
"das wars", o.ä.) oder das Gespräch klar abgeschlossen ist -- nicht einfach nach jeder Antwort.
"""

TOOLS = [
    run_shell_command,
    obsidian_write_note,
    obsidian_read_note,
    obsidian_list_notes,
    obsidian_search,
    web_search,
    notify_phone,
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
    return reply or "Entschuldigung, dazu ist mir gerade nichts eingefallen.", _end_flags.get(session_id, False)


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
    return reply or "Entschuldigung, dazu ist mir gerade nichts eingefallen.", _end_flags.get(session_id, False)
