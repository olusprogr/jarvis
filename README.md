# Jarvis

Personal voice assistant. Say "Hey Jarvis" on a PC (or message a Telegram bot), and it
understands you (Gemini, full audio understanding — no separate speech-to-text step), acts on it
with full shell access + persistent Obsidian-vault memory + web search + phone push
notifications, and talks back.

## Architecture

```
[PC: wake-word client] --audio--> [Pi: Jarvis Brain (FastAPI)] --audio understanding + agent--> Gemini
        ^                                    |
        |__________ spoken reply ____________|

[Phone: Telegram bot] <--text/voice--> [Pi: Jarvis Brain] (same backend, same agent brain)
```

- **[brain/](brain)** — the backend, runs on a Raspberry Pi. FastAPI service, Gemini-powered
  agent with tools, text-to-speech (Edge TTS primary, Gemini TTS / local Piper as fallbacks),
  Telegram bot integration.
- **[voice-client/](voice-client)** — Windows background app for the PC. Wake-word detection +
  multi-turn conversation loop + toast notifications.

Both talk to the same backend, share the same agent/tools/memory (the Obsidian vault),
independent of which one you're using at the moment.

## Why full shell access?

The agent's `run_shell_command` tool is deliberately unrestricted — an explicit choice, not a
default — so Jarvis can do "anything you could do yourself" on the Pi. On the machine this was
built for it also has passwordless `sudo`, i.e. root. Every command is logged to
`brain/logs/commands.log`.

If you deploy this yourself, make that call consciously. The agent reads untrusted input
(emails, web search results, Telegram messages), so someone else's text can reach its context —
combined with root, that's a real prompt-injection path to a wrecked machine. Granting root is
opt-in and documented in [brain/README.md](brain/README.md#root-access); leaving it out still
gives you a fully working assistant.

## Setup

See [brain/README.md](brain/README.md) and [voice-client/README.md](voice-client/README.md) for
the two halves. Both need a **free** [Gemini API key](https://aistudio.google.com/apikey); the
Telegram integration (chat, voice messages, and phone push notifications) is free too —
nothing here requires a paid service.
