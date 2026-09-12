# Jarvis Voice Client

Windows background app: listens for "Hey Jarvis" on the mic, then holds a multi-turn spoken
conversation with the [Jarvis Brain](../brain) backend — no need to repeat the wake word between
turns. A live status window shows whose turn it is and a mic level meter, and the conversation
ends automatically after a few seconds of silence or when asked to. Also runs commands on this
PC that the agent requests (open an app, a URL, ...) — see "PC control" below.

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
- **Status window**: `status_window.py` is a frameless, always-on-top Tk window bottom-right
  showing the current turn state (waiting/recording/thinking/speaking/ended), a live mic level
  meter, and a countdown for the listening window. Runs its own Tk mainloop in a daemon thread;
  only touch it through its thread-safe setters (`show`/`set_state`/`set_level`/`set_countdown`),
  never from another thread directly.
- **Noise floor**: `SILENCE_RMS_THRESHOLD` isn't a fixed cutoff — `NoiseFloor` learns the room's
  ambient level continuously (only from quiet frames, so speech never drags it up) and derives
  the speech threshold from that, clamped to `[MIN_SPEECH_RMS, MAX_SPEECH_RMS]`. Set
  `SILENCE_RMS_THRESHOLD` explicitly in `.env` to disable adaption and pin a fixed value instead.
- **Mic selection**: `choose_mic()` probes each candidate device briefly on startup (configured
  `MIC_DEVICE` → OS default → loudest of everything else) so a silent or unopenable default mic
  gets detected and logged instead of Jarvis just never hearing you.

## PC control

The agent's `run_on_pc` tool (Pi side) can run PowerShell commands on this machine — opening a
website or app, controlling playback, PC file operations. The Pi can't reach this PC directly
(no inbound port, and it isn't always on the same network), so the direction is inverted:
`pc_agent.py` polls the backend every ~1.5s for queued commands, runs them, and posts the result
back so the agent can report real output.

**This means the Pi-side agent can execute arbitrary commands on this PC.** Same trust boundary
as the shell access it already has on the Pi, extended one machine further — and the agent reads
untrusted content (emails, web pages, Telegram messages), so this is a real consideration, not a
formality. It's opt-in: `pc_agent.py` only does anything once its `start()` is called from
`jarvis_client.py`, and the corresponding `/jarvis/pc-actions` + `/jarvis/pc-result` endpoints
only exist on the Pi if `app/tools/pc_control.py` is deployed there (see
[brain/README.md](../brain/README.md#pc-control)). Leaving either side out gives you a fully
working voice assistant that simply can't touch this PC.

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
