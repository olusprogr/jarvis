# Jarvis Brain

Voice-assistant backend, runs on a Raspberry Pi. Receives audio (or text) from the
[PC wake-word client](../voice-client) or the Telegram bot, understands it directly via Gemini
(no local speech-to-text step), runs a Gemini agent with tools, and replies with a spoken audio
clip and/or text.

## Endpoints

- `GET /jarvis/health` — liveness check
- `POST /jarvis/text` — `{"text": "...", "session_id": "..."}` → `{"reply": "...", "conversation_ended": bool}` (debug, no audio)
- `POST /jarvis/command` — multipart form: `audio` file + `session_id` → full WAV bytes back,
  with `X-Reply-Text` / `X-Language` / `X-Conversation-Ended` headers (URL-encoded where needed)
- `POST /jarvis/command-stream` — same input, but streams the reply back sentence-by-sentence
  (see wire format in `app/main.py`) so playback of sentence 1 can start while Gemini is still
  generating the rest. This is what the PC client actually uses; `/command` stays as the
  simpler non-streaming variant.
- `POST /jarvis/telegram-webhook` — Telegram calls this; see `app/telegram_bot.py`
- `GET /jarvis/pc-actions`, `POST /jarvis/pc-result` — polled/posted by the PC client's
  `pc_agent.py` for the `run_on_pc` tool; only meaningful if `app/tools/pc_control.py` is
  deployed (see "PC control" below)

All endpoints except `/health` and the webhook require header `X-Jarvis-Key: <JARVIS_API_KEY>`
(the webhook instead verifies Telegram's `X-Telegram-Bot-Api-Secret-Token` header).

## How it understands and talks

- **Understanding**: raw audio is sent straight to Gemini as multimodal input
  (`agent.ask_audio()` / `ask_audio_streaming()`) — no separate Whisper/STT pass. Faster, and
  the agent can react to tone/what was actually said even when a transcript would've been
  ambiguous.
- **Speaking**: `app/tts.py`'s `synthesize()` tries, in order:
  1. **ElevenLabs** (`synthesize_elevenlabs()`) — best quality, the only paid backend, skipped
     entirely unless `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID` are set.
  2. **Microsoft Edge TTS** (`synthesize_edge()`) — free, unlimited, no API key. German "Conrad"
     voice deepened with an EQ + de-ess + mild pitch-shift chain via ffmpeg.
  3. **Gemini's own TTS** (`synthesize_gemini()`) — good quality, but the free tier caps out
     around 10 requests/day *per model*, so two different Gemini TTS models are tried in turn
     before falling through.
  4. **Local Piper** (`synthesize_piper()`) — unlimited, works offline, needs the binary + voice
     models downloaded separately (see below). The floor that can never fail for quota reasons.
- **Tools** (`app/tools/`):
  - `run_shell_command` — full, unrestricted shell access, **including root** if configured
    (deliberate, see "Root access" below)
  - `run_on_pc` — runs a command on the user's Windows PC, not the Pi (see "PC control" below)
  - `obsidian_write_note` / `read_note` / `list_notes` / `search` — the vault at `./vault` is
    Jarvis's persistent long-term memory
  - `web_search` — DuckDuckGo results (titles + snippets only), no API key
  - `fetch_url` — actually reads a page's or RSS/Atom feed's content; pair with `web_search`
    when the answer depends on what a page *says*, not just that it exists
  - `list_unread_emails` / `read_email` / `search_emails` / `send_email` — IMAP/SMTP via a
    Gmail App Password (see `.env` below)
  - `notify_phone` — sends a Telegram message to the user as a phone push
  - `end_conversation` — lets the agent signal a multi-turn voice session is over

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
- `lxml` (in `requirements.txt`) — needed by `fetch_url`'s RSS/Atom parsing

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
pages, Telegram messages), so content written by someone else can end up in its context.
Untrusted input plus root is a real prompt-injection path to a wrecked machine. Every command is
logged to `logs/commands.log`, but that's forensics, not prevention. Skipping this step is a
perfectly reasonable choice — the agent still works, it just can't do things that need root.

### PC control

`app/tools/pc_control.py` lets the agent run commands on the user's Windows PC (opening an app
or website, controlling playback, ...) via `run_on_pc`. The Pi can't reach the PC directly, so
the direction is inverted: commands are queued here, the PC's `pc_agent.py` polls
`/jarvis/pc-actions` for them, runs them in PowerShell, and posts the result to
`/jarvis/pc-result` — `run_on_pc` then blocks briefly so the agent can report real output
instead of "I queued something".

This is **not deployed by default** — `pc_control.py` isn't imported anywhere unless you wire it
in yourself (import in `agent.py`, add to `TOOLS`, add the two endpoints to `main.py`; see git
history for the exact diff, or the [voice-client README](../voice-client/README.md#pc-control)
for the PC side). Same trade-off as root, one machine further: the agent reads untrusted content
and, with this enabled, can act on a second machine because of it. Decide consciously.

**Config (`.env`):**
- `GEMINI_API_KEY` — https://aistudio.google.com/apikey
- `JARVIS_API_KEY` — any long random string, shared with the PC client's `.env`
- `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` — optional, best-quality TTS. Key from
  elevenlabs.io → Profile → API Keys (a `text_to_speech`-only key is enough); voice ID from
  Voices → pick one → the ID in its detail panel. Leave both empty to skip it.
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_WEBHOOK_SECRET` / `TELEGRAM_ALLOWED_CHAT_ID` — get a bot
  token from [@BotFather](https://t.me/BotFather), generate any random string for the webhook
  secret, then call `telegram_bot.register_webhook("https://your-domain/jarvis/telegram-webhook")`
  once. **`TELEGRAM_ALLOWED_CHAT_ID` gates who the bot will respond to** — Jarvis has full shell
  access, so this must be set or every message is logged-and-ignored (never processed). Leave it
  empty, message your bot once, grep `logs/jarvis.log` for the logged `chat_id`, then set it.
- `EMAIL_ADDRESS` / `EMAIL_APP_PASSWORD` — a Gmail "App Password"
  (myaccount.google.com/apppasswords, needs 2-Step Verification turned on first), not OAuth.
  Override `EMAIL_IMAP_HOST` / `EMAIL_SMTP_HOST` for a non-Gmail provider.

## Run

```bash
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8420
```

Or via systemd — see `jarvis-brain.service` (adjust the paths for your install location).

## Logs

- `logs/jarvis.log` — app log (requests, replies, tool calls)
- `logs/commands.log` — audit log of every shell command the agent ran via `run_shell_command`
