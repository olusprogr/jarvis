# Jarvis Voice Client

Windows background app: listens for "Hey Jarvis" on the mic, then holds a multi-turn spoken
conversation with the [Jarvis Brain](../brain) backend — no need to repeat the wake word between
turns. Shows Windows toast notifications for "your turn" / "Jarvis speaking", and ends the
conversation automatically after a few seconds of silence or when asked to.

## How it works

- **Wake word**: [openWakeWord](https://github.com/dscripka/openWakeWord)'s pretrained
  `hey_jarvis_v0.1` model — no custom training needed, it already ships with this exact phrase.
  The three small ONNX model files it needs are bundled in `models/`.
- **Recording**: `record_utterance()` requires `SPEECH_START_FRAMES_NEEDED` consecutive loud
  frames (a noise gate) before treating something as speech, then records until
  `SILENCE_SECONDS_TO_STOP` of trailing silence. `CONVERSATION_SILENCE_TIMEOUT` bounds how long
  it'll wait for the user to start talking on each turn before ending the conversation.
- **Playback**: the mic stream is explicitly paused (`stream.stop()`/`stream.start()`) while
  Jarvis's own audio (greeting or reply) plays, otherwise speaker bleed into the mic gets
  mistaken for the next thing the user said.
- **Greetings**: `models/greeting-*.wav` are pre-generated short acknowledgement lines ("Bereit,
  Master.", "Sag an.", ...) played instantly on wake-word trigger, before the network round trip
  to the backend even starts.
- **Notifications**: `notify()` shows a Windows balloon-tip via a hidden PowerShell subprocess
  (not a bundled toast library — those pull in WinRT/COM bindings that crash PyInstaller's
  dependency analysis when building the standalone .exe). Duration is matched to what it's
  announcing (the real reply-audio length, or the listening-window timeout), not a fixed value.

## Setup (dev)

```powershell
python -m venv venv
./venv/Scripts/pip install -r requirements.txt
Copy-Item .env.example .env   # fill in JARVIS_URL + JARVIS_API_KEY (same key as the brain's .env)
./venv/Scripts/python jarvis_client.py
```

## Build + install as a real background app

```powershell
./venv/Scripts/pyinstaller --onefile --noconsole --name JarvisClient jarvis_client.py
./install.ps1
```

`install.ps1` copies the built exe + `.env` + `models/` into `%LOCALAPPDATA%\JarvisClient`,
registers a Startup-folder autostart shortcut + Start Menu entry, and a proper Add/Remove
Programs entry (uninstall via `uninstall.ps1.template`, copied in as `uninstall.ps1` at install
time). Re-run `install.ps1` after any rebuild to update the installed copy — it's safe to run
repeatedly.

**Note**: after building, `%LOCALAPPDATA%\JarvisClient\JarvisClient.exe` briefly appears as two
processes (a PyInstaller onefile bootloader + the actual app) — that's normal, not a bug.
