"""Last-resort brain: answer through the local OmniRoute gateway when every Gemini model in
agent.GEMINI_MODEL_CHAIN is exhausted (typically the free-tier daily quota, which took Jarvis
down on every channel at once on 2026-09-13).

Two things make this a genuinely different code path rather than "one more model in the chain":

1. **No audio.** Gemini gets the raw WAV and does speech-understanding + reasoning in one call.
   OmniRoute's free keyless pool is text-only (verified: the model replies that it cannot process
   audio), so this path transcribes locally with the dormant faster-whisper in app/stt.py first.
   That costs ~2-6s, which is the right trade for a fallback: slower beats silent.
2. **No automatic function calling.** google-genai runs the tool loop itself from plain Python
   callables. The OpenAI-compatible API needs JSON-schema declarations and a hand-rolled dispatch
   loop, both below. Without it the model would *claim* to have run shell commands or sent mail
   without anything actually happening -- worse than admitting it is degraded.

OFF BY DEFAULT. Set OMNIROUTE_ENABLED=true only after curating which providers the gateway may
route to: this path sends whatever the user said (mail contents, calendar, vault notes) to
whichever provider OmniRoute picks, and free tiers commonly train on their inputs.
"""
import inspect
import json
import logging
import tempfile
import typing

import requests

from . import config

log = logging.getLogger("jarvis.omniroute")

# Cap the tool loop: each iteration is a full model round-trip, and a model that keeps re-calling
# tools without concluding would otherwise hang the voice pipeline indefinitely.
MAX_TOOL_ROUNDS = 5
REQUEST_TIMEOUT = 120

_PY_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def _json_type(annotation) -> str:
    """Maps a Python annotation to a JSON-schema type, defaulting to string -- an over-broad
    'string' just means the model passes text where we wanted an int, which the tool itself
    coerces, whereas guessing wrong in the other direction makes the schema unusable."""
    if annotation is inspect.Parameter.empty:
        return "string"
    origin = typing.get_origin(annotation)
    if origin is not None:  # Optional[int], list[str], ... -- use the first concrete arg
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        annotation = args[0] if args else str
    return _PY_TO_JSON.get(annotation, "string")


def build_tool_schemas(tools: list) -> list[dict]:
    """Derives OpenAI function schemas from the same plain Python callables agent.TOOLS holds, so
    the two paths can never drift apart the way two hand-maintained tool lists would."""
    schemas = []
    for fn in tools:
        sig = inspect.signature(fn)
        properties, required = {}, []
        for name, param in sig.parameters.items():
            properties[name] = {"type": _json_type(param.annotation)}
            if param.default is inspect.Parameter.empty:
                required.append(name)
        schemas.append({
            "type": "function",
            "function": {
                "name": fn.__name__,
                "description": (inspect.getdoc(fn) or "")[:1024],
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        })
    return schemas


def transcribe_audio(audio_bytes: bytes, suffix: str = ".wav") -> str:
    """Local speech-to-text via faster-whisper. app/stt.py has been dormant since audio started
    going straight to Gemini; it stays installed precisely for this case -- it needs no network
    and no quota, so it still works when everything external is failing."""
    from . import stt  # imported lazily: loads a ~500MB model, pointless on the normal path
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    text, _language = stt.transcribe(path)
    return text.strip()


def _gemini_history_to_openai(history) -> list[dict]:
    """Best-effort carry-over of the Gemini session's context. Only plain text parts survive --
    tool calls/results are dropped rather than translated, since the two APIs disagree on their
    shape and a malformed history would break the request outright. Losing some context is
    recoverable; failing the last-resort call is not."""
    messages = []
    for entry in history or []:
        role = "assistant" if getattr(entry, "role", "") == "model" else "user"
        text = " ".join(
            part.text for part in (getattr(entry, "parts", None) or []) if getattr(part, "text", None)
        ).strip()
        if text:
            messages.append({"role": role, "content": text})
    return messages[-20:]  # recent turns only -- free-tier context windows are often small


def _post(messages: list[dict], tool_schemas: list[dict]) -> dict:
    """One gateway round-trip, retried once. The keyless free pool is observably flaky -- an
    identical request failed and then succeeded seconds later during testing -- and this is the
    last line of defence before Jarvis goes silent, so one cheap retry is worth it."""
    payload = {"model": config.OMNIROUTE_MODEL, "messages": messages}
    if tool_schemas:
        payload["tools"] = tool_schemas
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            resp = requests.post(
                f"{config.OMNIROUTE_URL}/chat/completions", json=payload, timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_error = e
            log.warning("OmniRoute-Anfrage fehlgeschlagen (Versuch %d/2): %s", attempt + 1, e)
    raise last_error


def ask(system_instruction: str, tools: list, user_text: str, history=None) -> str:
    """Runs one full exchange against OmniRoute, including the tool-dispatch loop. Returns the
    reply text. Raises on transport/HTTP failure so the caller can fall through to its own
    last-resort message rather than speaking an empty string."""
    tools_by_name = {fn.__name__: fn for fn in tools}
    tool_schemas = build_tool_schemas(tools)

    messages = [{"role": "system", "content": system_instruction}]
    messages.extend(_gemini_history_to_openai(history))
    messages.append({"role": "user", "content": user_text})

    for _ in range(MAX_TOOL_ROUNDS):
        data = _post(messages, tool_schemas)
        choice = data["choices"][0]
        message = choice["message"]
        calls = message.get("tool_calls") or []

        if not calls:
            log.info("OmniRoute-Antwort ueber Modell %s", data.get("model", "?"))
            return (message.get("content") or "").strip()

        # Echo the assistant's tool_calls back before the results -- the API requires every
        # tool-result message to reference a tool_call it can see in the preceding turn.
        messages.append({
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": calls,
        })
        for call in calls:
            name = call.get("function", {}).get("name", "")
            raw_args = call.get("function", {}).get("arguments") or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
            fn = tools_by_name.get(name)
            if fn is None:
                result = f"Unbekanntes Tool: {name}"
            else:
                try:
                    log.info("OmniRoute Tool-Aufruf: %s(%s)", name, args)
                    result = str(fn(**args))
                except Exception as e:  # a failing tool must not kill the whole fallback
                    log.exception("Tool %s fehlgeschlagen", name)
                    result = f"Fehler: {e}"
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": result[:4000],
            })

    log.warning("OmniRoute: Tool-Schleife nach %d Runden abgebrochen", MAX_TOOL_ROUNDS)
    return "Ich komme hier gerade nicht weiter."


def ask_audio(system_instruction: str, tools: list, audio_bytes: bytes,
              mime_type: str = "audio/wav", history=None) -> str:
    """Audio entry point: transcribe locally, then the same text path as ask()."""
    suffix = ".ogg" if "ogg" in mime_type else ".wav"
    text = transcribe_audio(audio_bytes, suffix=suffix)
    log.info("OmniRoute-Fallback, lokal transkribiert: %r", text)
    if not text:
        return "Kannst du das nochmal sagen?"
    return ask(system_instruction, tools, text, history=history)
