# Jarvis Brain

Voice-assistant backend, runs on a Raspberry Pi. Receives audio (or text) from the
[PC wake-word client](../voice-client) or the Telegram bot, understands it directly via Gemini
(no local speech-to-text step), runs a Gemini agent with tools (shell, Obsidian vault, web
search, phone push notifications), and replies with a spoken audio clip and/or text.

## Endpoints

- `GET /jarvis/health` — liveness check
- `POST /jarvis/text` — `{"text": "...", "session_id": "..."}` → `{"reply": "...", "conversation_ended": bool}` (debug, no audio)
- `POST /jarvis/command` — multipart form: `audio` file + `session_id` → WAV bytes back, with
  `X-Reply-Text` / `X-Language` / `X-Conversation-Ended` headers (URL-encoded where needed)
- `POST /jarvis/telegram-webhook` — Telegram calls this; see `app/telegram_bot.py`

All endpoints except `/health` and the webhook require header `X-Jarvis-Key: <JARVIS_API_KEY>`
(the webhook instead verifies Telegram's `X-Telegram-Bot-Api-Secret-Token` header).

## How it understands and talks

- **Understanding**: raw audio is sent straight to Gemini as multimodal input
  (`agent.ask_audio()`) — no separate Whisper/STT pass. Much faster, and the agent can react to
  tone/what was actually said even when a transcript would've been ambiguous.
- **Speaking**: `app/tts.py`'s `synthesize()` tries, in order: Microsoft Edge TTS (free,
  unlimited, no API key — `synthesize_edge()`, German "Conrad" voice deepened with an EQ +
  de-ess + mild pitch-shift chain via ffmpeg) → Gemini's own TTS (`synthesize_gemini()`, better
  quality but the free tier caps out around 10 requests/day per model) → local Piper
  (`synthesize_piper()`, unlimited fallback, needs the binary + voice models downloaded
  separately, see below).
- **Tools** (`app/tools/`): `run_shell_command` (full, unrestricted shell access **including
  root** — deliberate, see "Root access" below), `obsidian_write_note`/`read_note`/`list_notes`/`search`
  (the vault at `./vault` is Jarvis's persistent long-term memory), `web_search`
  (DuckDuckGo, no key), `notify_phone` (push to the phone via the Telegram bot),
  `end_conversation` (lets the agent signal a multi-turn session is over).

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in GEMINI_API_KEY, JARVIS_API_KEY, etc. -- see below
```

**Not in this repo (download separately, all free):**
- Piper binary + voices → `models/piper/piper`, `models/voices/*.onnx` (used only as the final
  TTS fallback) — [rhasspy/piper releases](https://github.com/rhasspy/piper/releases) +
  [piper-voices on HuggingFace](https://huggingface.co/rhasspy/piper-voices)
- `ffmpeg` with `librubberband` + `deesser` filters compiled in (used for the Edge TTS voice
  processing chain) — Debian's `apt install ffmpeg` has these on trixie/13+

### Root access

`run_shell_command`'s docstring tells the model `sudo` is available without a password, so if
you want that to be true you have to grant it explicitly:

```bash
echo "$USER ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/010-jarvis-nopasswd
sudo chmod 440 /etc/sudoers.d/010-jarvis-nopasswd
sudo visudo -c   # must print "parsed OK"
```

Revoke any time with `sudo rm /etc/sudoers.d/010-jarvis-nopasswd` — no code changes needed.

**Think about this one before you run it.** The agent reads *untrusted* input (emails, web
search results, Telegram messages), so content written by someone else can end up in its
context. Untrusted input plus root is a real prompt-injection path to a wrecked machine. Every
command is logged to `logs/commands.log`, but that's forensics, not prevention. Skipping this
step is a perfectly reasonable choice — the agent still works, it just can't do things that
need root.

**Config (`.env`):**
- `GEMINI_API_KEY` — https://aistudio.google.com/apikey
- `JARVIS_API_KEY` — any long random string, shared with the PC client's `.env`
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_WEBHOOK_SECRET` / `TELEGRAM_ALLOWED_CHAT_ID` — get a bot
  token from [@BotFather](https://t.me/BotFather), generate any random string for the webhook
  secret, then call `telegram_bot.register_webhook("https://your-domain/jarvis/telegram-webhook")`
  once. **`TELEGRAM_ALLOWED_CHAT_ID` gates who the bot will respond to** — Jarvis has full shell
  access, so this must be set or every message is logged-and-ignored (never processed). Leave it
  empty, message your bot once, grep `logs/jarvis.log` for the logged `chat_id`, then set it.

## Run

```bash
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8420
```

Or via systemd — see `jarvis-brain.service` (adjust the paths for your install location).

## Logs

- `logs/jarvis.log` — app log (requests, replies, tool calls)
- `logs/commands.log` — audit log of every shell command the agent ran via `run_shell_command`
