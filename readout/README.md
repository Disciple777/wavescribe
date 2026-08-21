# 🔊 Readout

Select **any text in any application** (browser, editor, PDF, terminal…),
press your hotkey, and Readout reads it out loud. Press the hotkey again
to stop.

Built as a standalone project that reuses the patterns and `.venv` of its
parent codebase (WaveScribe).

## How it works

```
Select text anywhere → press Ctrl+Alt+Space
   ↓
 1. Your clipboard is saved
 2. A unique sentinel marker is placed on the clipboard
 3. A copy shortcut is simulated — Ctrl+C, then Ctrl+Insert, then Ctrl+Shift+C
 4. The clipboard is polled until the marker is replaced
 5. Your original clipboard is restored — always
 6. If text was found, it's spoken aloud (offline or neural voice)
 7. Press the hotkey again → stop speaking
```

The clipboard-sentinel trick means no app integrations or accessibility
APIs are needed — it works everywhere, instantly.

## Voice engines

| Engine | Quality | Offline | Requires |
|--------|---------|---------|----------|
| **Windows voices** (default) | Good (Windows built-ins; add "natural voices" in Windows Settings for better) | ✅ | nothing |
| **Edge neural** | Excellent (Microsoft neural voices, 100+ languages) | ❌ | internet + `pip install edge-tts` |

Switch engines in Settings → Voice. The engine list refreshes automatically.

## Getting started

```bat
cd <project root>
python -m venv .venv
.venv\Scripts\pip install -r readout\requirements.txt   :: installs edge-tts for neural voices
readout\run_readout.bat
```

Or run it directly:

```bat
.venv\Scripts\python.exe readout\main.py
```

## Features

- **Global toggle hotkey** — Ctrl+Alt+Space by default, capture your own from Settings
- **Works in any app** — browser, Word, VS Code, PDFs, terminals (Ctrl+Insert fallback)
- **Clipboard-safe** — your clipboard is restored after every read
- **Audio feedback cues** — a blip confirms the hotkey was heard, a rising tone confirms reading started, and a low buzz tells you nothing was selected
- **Two voice engines** — offline system voices + optional Edge neural voices
- **Voice rotation (main + secondary voices)** — keep an English voice as your main and a Spanish one as a secondary, then swap with one button, the tray item, or the **Ctrl+Alt+V** hotkey (cycle through the list, switching engines automatically)
- **Speed & volume controls** — 50–150% speed, 0–100 volume, persisted
- **System tray** — quick Read / Stop / Cycle voice / Show / Quit; closing the window hides to tray
- **Auto-start with Windows** — toggle in Settings (HKCU Run key)
- **Manual text box** — paste text and hear it without selecting anything

## Voice rotation

For multilingual users (or anyone who likes a couple of voices), Readout
keeps an ordered **voice list**: the first entry is your ⭐ **main voice** and
the rest are secondary. Press the swap button, the tray item, or the
**swap hotkey** (Ctrl+Alt+V by default, changeable in Settings) to cycle
through them — the engine switches automatically if a voice belongs to the
other engine.

```
⭐ main:        en-US-AriaNeural   (Edge)
   secondary 1: es-ES-ElviraNeural (Edge)
   secondary 2: (System default)   (Windows)

Ctrl+Alt+V  →  main → secondary 1 → secondary 2 → main → …
```

How to set it up:

1. Pick your **main** voice in Settings → Voice → ⭐ Main voice.
2. Pick a secondary in the **Voice list** section and press **＋ Add voice**
   (the rows list the current engine's voices; entries from the other
   engine show a read-only label with their engine badge — switch the
   Engine picker to edit them).
3. Press **🔁 Cycle voice**, the tray item, or the swap hotkey to rotate.

Choosing a new main voice demotes the old one to the first secondary (nothing
is lost), and the swap hotkey is captureable just like the read hotkey.

With ~200 Edge voices, use the **🔍 search box** in Settings → Voice to find
one fast (type "Spanish", "Aria", a language code…). The search filters
every voice dropdown live — and when searching, the add picker also shows
voices that are already in your rotation (e.g. your main voice), so you can
always tell where a voice lives.

## Troubleshooting: terminals

Reading a selection from a terminal is the trickiest case:

- **Classic cmd/PowerShell console (conhost):** it has no keyboard copy
  shortcut at all. If QuickEdit is on (default), a mouse drag copies the
  selection to the clipboard when you release it — Readout detects this
  window and reads what's on the clipboard. It never sends Ctrl+C there
  (which would interrupt the CLI you're running).
- **Full-screen CLI apps (TUIs)** like Vim, tmux, or interactive dashboards
  often *capture the mouse*, so dragging with the mouse does **not** create
  a real terminal selection — Readout correctly reports "no text selected".
  Hold **Shift while dragging** to force a normal selection.
- **Windows Terminal:** `Ctrl+C` only copies when there **is** a selection;
  otherwise it sends an interrupt to the shell. Readout tries `Ctrl+Insert`
  and `Ctrl+Shift+C` as fallbacks.
- Enabling **copyOnSelect** in Windows Terminal settings copies any
  selection instantly — then Readout always finds it.

Either way, a failed grab now shows a **persistent red error status** plus
an explanatory message (and a low buzz), so you always know why nothing
was read.

## Project layout

```
readout/
│   main.py                # entry point (hotkey thread + tray + window)
│   gui.py                 # settings window (CustomTkinter)
│   tray.py                # system tray icon + menu
│   autostart.py           # HKCU Run-key helpers
│   requirements.txt
├── core/
│   ├── selection.py       # grab selected text (clipboard sentinel trick)
│   ├── system_tts.py      # offline Windows voices (System.Speech)
│   ├── edge_tts.py        # Edge neural voices (edge-tts + MCI playback)
│   ├── engine.py          # engine manager / backend abstraction
│   ├── voices.py          # voice rotation helpers (main + secondary list)
│   ├── hotkeys.py         # global hotkey registration (keyboard lib)
│   ├── config.py          # %APPDATA%\Readout\config.json
│   ├── state.py           # thread-safe state + event queue
│   ├── errors.py          # shared exceptions
│   └── theme.py           # colours & bundled fonts
└── tests/                 # pytest suite (clipboard logic, engines, config)
```

## Roadmap

- [x] Global read-selection hotkey
- [x] Offline + Edge neural voices
- [x] Voice rotation (main + secondary voices + swap hotkey)
- [x] Tray icon + settings window
- [ ] Packaging (PyInstaller exe + Inno Setup installer)
- [ ] OpenAI / ElevenLabs voices (pluggable via `core/engine.py`)
- [ ] Per-language auto voice selection
