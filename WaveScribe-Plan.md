# WaveScribe Plan

## 🎯 What is WaveScribe?

A Windows desktop app that listens to microphone input, transcribes it using AI (OpenAI Whisper API or any local model), and types the transcription directly into any focused text field — like having a real-time dictation assistant that works in any application.

---

## ✅ Decisions Made

These are locked in based on your priorities:

| Decision | Choice | Why |
|----------|--------|-----|
| **#1 Priority** | **Small disk footprint** | You're escaping a 12.3GB Tauri toolchain |
| **Language** | **Python** | Smallest dev footprint (~200MB), fastest dev cycle, richest ecosystem |
| **GUI Framework** | **CustomTkinter** | Modern dark theme built-in, native Windows look, no webview dependency |
| **Audio Capture** | **sounddevice** (PortAudio) | Simple, reliable, pure Python bindings |
| **GUI Feel** | **Modern custom dark theme** | CustomTkinter's default dark mode is gorgeous out of the box |
| **Transcription** | **OpenAI Whisper API (cloud)** | Start with cloud, add local later as a nice-to-have |

### Why Python Won Over the Alternatives

| Factor | Python | Go + Wails | C# .NET |
|--------|--------|------------|---------|
| **Dev footprint** | **~200MB** ✅ | ~500MB | ~600MB-1GB |
| **Compile time** | **None** ✅ — instant feedback | Seconds | Tens of seconds |
| **Dark theme GUI** | **Built-in** (CustomTkinter) ✅ | Need React/Node setup | Need to build from scratch |
| **Final exe size** | 30-50MB (PyInstaller) | 5-15MB | 50-150MB |
| **Total from zero to working** | **~30 min** ✅ | ~1-2 hours | ~2-4 hours |
| **Windows integration** | Good (via libs) | Good (via WebView2) | Best (native) |

---

## 🐍 Python Technology Stack

| Component | Package | What It Does |
|-----------|---------|--------------|
| **GUI** | `customtkinter` | Modern themed tkinter widgets, dark mode by default |
| **Audio Capture** | `sounddevice` | PortAudio bindings, captures mic input to numpy arrays |
| **WAV Conversion** | `scipy` | Write numpy audio arrays to WAV format in memory |
| **OpenAI API** | `openai` | Official SDK for Whisper transcription API |
| **Text Typing** | `pyautogui` | Simulate keystrokes into the focused window |
| **Global Hotkeys** | `keyboard` | Register system-wide shortcuts (Ctrl+Shift+R/S) |
| **System Tray** | `pystray` + `Pillow` | System tray icon with context menu |
| **Testing** | `pytest` | Run unit tests |
| **Packaging** | `pyinstaller` | Bundle everything into a single `.exe` |

**Estimated total dev environment size:** ~200-250MB (Python 3.12 + virtual environment + all packages)

---

## 📋 Core Features (MVP)

### Must-Have

1. **Microphone Audio Capture** — Start/stop recording from default mic via sounddevice
2. **AI Transcription** — Send WAV to OpenAI Whisper API, get text back
3. **Smart Formatting** — ~40 spoken punctuation mappings, auto-capitalization, spacing cleanup
4. **Text Insertion** — Type the transcription into any focused window via pyautogui
5. **Global Hotkeys** — Ctrl+Shift+R (start), Ctrl+Shift+S (stop & transcribe)
6. **Desktop GUI Window** — Recording status, buttons, transcription display, settings
7. **System Tray** — Background process, Show/Hide, Quit

### Nice-to-Have (Future)

- [ ] Audio level meter during recording (CustomTkinter progress bar)
- [ ] Customizable hotkeys
- [ ] Coding mode (spoken symbols → code syntax)
- [ ] Auto-start with Windows (registry entry)
- [ ] Local Whisper model (offline via `whisper-openai` or `whisper.cpp`)
- [ ] Multiple language support
- [ ] Transcription history

---

## 🧱 Architecture

```
┌────────────────────────────────────────────────────┐
│                 GUI Layer                           │
│  CustomTkinter Window (main thread)                 │
│  ┌─────────┐ ┌──────────────┐ ┌────────────────┐   │
│  │ Status  │ │ Transcription│ │ Settings Panel  │   │
│  │ +Buttons│ │   Display    │ │ (mode, API key, │   │
│  │         │ │              │ │  model, toggle) │   │
│  └─────────┘ └──────────────┘ └────────────────┘   │
├────────────────────────────────────────────────────┤
│               Control Layer                         │
│  ┌──────────────┐  ┌──────────────┐                │
│  │ StateMachine │  │ Event Bus    │                │
│  │ (thread-safe)│  │ (queues)     │                │
│  └──────────────┘  └──────────────┘                │
├──────────┬──────────┬──────────┬───────────────────┤
│ Audio    │ OpenAI   │ Text     │ System Tray       │
│ Capture  │ Transcrib│ Insertion│ (daemon thread)   │
│ (thread) │ er (proc)│ (direct) │                   │
├──────────┴──────────┴──────────┴───────────────────┤
│              Global Hotkeys (daemon thread)          │
│              keyboard.add_hotkey()                   │
└────────────────────────────────────────────────────┘
```

### Threading Model

Python is single-threaded (GIL), but this app uses **threading for I/O**:

| Thread | Runs | Purpose |
|--------|------|---------|
| **Main** | CustomTkinter GUI | UI updates, button clicks |
| **Hotkey thread** | keyboard listener | Background hotkey detection |
| **Audio thread** | sounddevice callback | Real-time audio capture |
| **Worker thread** | Transcription API call | Non-blocking HTTP request |

State changes from non-GUI threads are communicated to the GUI via **queue.Queue** and polled with `window.after()` timers.

### Data Flow

```
1. Ctrl+Shift+R pressed       → Hotkey thread → puts "start" in queue
2. GUI polls queue            → calls audio_capture.start()
3. Audio captures to buffer   → sounddevice callback (audio thread)
4. Ctrl+Shift+S pressed       → Hotkey thread → puts "stop" in queue
5. GUI polls queue            → calls audio_capture.stop() → returns WAV bytes
6. GUI shows "Transcribing..."→ spawns worker thread → calls OpenAI API
7. Worker returns text        → calls formatter.format() → updates state
8. GUI polls state change     → displays transcription
9. User clicks "Insert"       → pyautogui.typewrite() types into focused window
```

---

## 📁 Project Structure (Python)

```
wavescribe/
├── main.py                        # Entry point: wire everything together
├── requirements.txt               # pip freeze output
├── build.spec                     # PyInstaller spec (for packaging)
├── WaveScribe-Plan.md             # ← This file
│
├── app/
│   ├── __init__.py
│   ├── gui.py                     # CustomTkinter window, all UI widgets
│   ├── state.py                   # AppState dataclass + thread-safe access
│   ├── audio_capture.py           # sounddevice recording to WAV bytes
│   ├── transcriber.py             # OpenAI Whisper API client
│   ├── formatter.py               # Punctuation formatting (ported from Rust)
│   ├── typer.py                   # pyautogui text insertion
│   ├── hotkeys.py                 # keyboard library global shortcuts
│   └── tray.py                    # pystray system tray icon
│
├── tests/
│   ├── __init__.py
│   ├── test_formatter.py          # Ported from Rust: 4+ unit tests
│   └── test_audio_capture.py      # Basic audio pipeline tests
│
└── assets/
    └── icon.png                   # App icon (can use the existing one)
```

---

## 📋 Implementation Steps (in order)

### Step 1: Project Scaffold
**Goal:** Create the directory structure, virtual environment, and install all dependencies.

- Create directories: `app/`, `tests/`, `assets/`
- Copy existing `icon.png` from Tauri project (at `src-tauri/icons/icon.png`)
- Create and activate virtual environment
- Install dependencies:
  ```
  pip install customtkinter sounddevice scipy openai pyautogui keyboard pystray Pillow pytest
  ```
- Freeze to `requirements.txt`
- **Verify:** `pip list` shows all packages installed

**Disk footprint at this point:** ~200MB (Python + venv + packages)

---

### Step 2: Shared State (`app/state.py`)
**Goal:** Thread-safe state management that all components can read/write.

- Create `AppState` dataclass with:
  - `status`: `"idle"` | `"recording"` | `"transcribing"` | `"error"`
  - `mode`: `"local"` | `"cloud"`
  - `api_key`: `str`
  - `model_size`: `"tiny"` | `"base"` | `"small"`
  - `auto_punctuate`: `bool`
  - `last_transcription`: `str`
  - `error_message`: `str`
- Use `threading.Lock` for thread-safe getters/setters
- Use `queue.Queue` for event-driven communication between threads

**Port from Rust:** `Arc<Mutex<T>>` → `threading.Lock` + plain class

---

### Step 3: Smart Formatting (`app/formatter.py`)
**Goal:** Port the Rust formatting logic 1:1 to Python. This is pure text processing — no dependencies, no threads.

- Port all ~40 punctuation mapping replacements (case-insensitive)
- Port spacing cleanup (remove space before `,.?!:;)`, after `({[`)
- Port auto-capitalization (first letter + after `.?!`)
- Write at least the 4 existing Rust tests in `tests/test_formatter.py`

**Port from Rust:** This is the most straightforward port. Rust's string methods → Python string methods:
- `to_lowercase()` → `.lower()`
- `contains()` → `in`
- `replace()` → `.replace()`
- `chars()` → iterate over string
- `to_ascii_uppercase()` → `.upper()`

**Verify:** `pytest tests/test_formatter.py -v` passes all tests

---

### Step 4: Audio Capture (`app/audio_capture.py`)
**Goal:** Capture microphone input and return WAV bytes.

- Use `sounddevice.InputStream` with a callback
- Collect raw float32 samples into a list of numpy arrays
- `start()` — begins capturing
- `stop()` — stops, concatenates, converts to 16-bit PCM WAV via `scipy.io.wavfile.write()` (in-memory via `io.BytesIO`)
- Return the WAV bytes

**Key code pattern:**
```python
import sounddevice as sd
import numpy as np
from scipy.io import wavfile
import io

class AudioCapture:
    def __init__(self, samplerate=16000):
        self.samplerate = samplerate
        self.buffer = []
        self.stream = None

    def start(self):
        self.buffer = []
        self.stream = sd.InputStream(
            samplerate=self.samplerate, channels=1,
            callback=self._callback
        )
        self.stream.start()

    def _callback(self, indata, frames, time, status):
        if status:
            print(f"Audio error: {status}")
        self.buffer.append(indata.copy())

    def stop(self):
        self.stream.stop()
        self.stream.close()
        audio = np.concatenate(self.buffer, axis=0)
        buf = io.BytesIO()
        wavfile.write(buf, self.samplerate,
                      (audio * 32767).astype(np.int16))
        return buf.getvalue()
```

---

### Step 5: Transcription Engine (`app/transcriber.py`)
**Goal:** Send WAV bytes to OpenAI Whisper API and return transcribed text.

- Use `openai.OpenAI()` client
- `transcribe_cloud(wav_bytes, api_key)` → sends multipart form, returns text
- Handle errors: timeout, auth failure, network issues
- For now, cloud-only. Local mode shows a "coming soon" message.

**Key code pattern:**
```python
from openai import OpenAI
import io

def transcribe_cloud(wav_bytes: bytes, api_key: str) -> str:
    client = OpenAI(api_key=api_key)
    with io.BytesIO(wav_bytes) as f:
        f.name = "audio.wav"
        transcript = client.audio.transcriptions.create(
            model="whisper-1", file=f, response_format="text"
        )
    return transcript.strip()
```

---

### Step 6: Text Insertion (`app/typer.py`)
**Goal:** Type text into any focused window via keyboard simulation.

- Use `pyautogui.typewrite()` for text
- Handle multi-line text with `pyautogui.keyDown()` / `keyUp()` for Enter
- Add a small delay between characters to ensure reliability

**Important:** pyautogui is simple but has a limitation — it can only type ASCII characters reliably. For anything beyond that, `pyperclip` + `keyboard.write()` is an alternative path.

---

### Step 7: Global Hotkeys (`app/hotkeys.py`)
**Goal:** Register Ctrl+Shift+R and Ctrl+Shift+S system-wide.

- Use `keyboard.add_hotkey()` to register both shortcuts
- Each hotkey callback puts a message into the `queue.Queue` for the GUI to process
- Run the keyboard listener in a daemon thread

```python
import keyboard
import queue

def register_hotkeys(q: queue.Queue):
    keyboard.add_hotkey("ctrl+shift+r", lambda: q.put("start_recording"))
    keyboard.add_hotkey("ctrl+shift+s", lambda: q.put("stop_recording"))
    keyboard.wait()  # Blocks forever
```

**Note:** `keyboard` may require admin privileges on some Windows configurations. If so, we can fall back to `pynput` which works without admin but has slightly different API.

---

### Step 8: System Tray (`app/tray.py`)
**Goal:** System tray icon with Show/Quit menu.

- Use `pystray.Icon` with a `PIL.Image` icon
- Menu items: "Show WaveScribe" and "Quit"
- Run in a daemon thread

```python
import pystray
from PIL import Image

def create_tray(show_callback, quit_callback):
    icon = Image.open("assets/icon.png")
    menu = pystray.Menu(
        pystray.MenuItem("Show WaveScribe", show_callback),
        pystray.MenuItem("Quit", quit_callback)
    )
    return pystray.Icon("wavescribe", icon, "WaveScribe", menu)
```

---

### Step 9: GUI (`app/gui.py`)
**Goal:** The main CustomTkinter window with all controls and displays.

**Window setup:**
- 600×700, centered on screen
- Dark theme by default (`customtkinter.set_appearance_mode("dark")`)
- Color theme: `"blue"` (default CustomTkinter accent)

**UI Layout (top to bottom):**

```
┌──────────────────────────────────────────┐
│ [●] WaveScribe                  [Status]  │  ← Header with colored status dot
├──────────────────────────────────────────┤
│ Recording Ctrl+Shift+R                   │
│ Stop     Ctrl+Shift+S                    │  ← Status + shortcut display card
│ Mode: [Local] ↔ [Cloud]                  │
│ [🎤 Record] [⏹ Stop]                     │  ← Control buttons
├──────────────────────────────────────────┤
│ Last Transcription                       │
│ ┌──────────────────────────────────────┐ │
│ │ Transcribed text appears here...     │ │  ← Read-only textbox
│ │                                      │ │
│ └──────────────────────────────────────┘ │
│ [📝 Insert into Window]                 │  ← Action button
├──────────────────────────────────────────┤
│ Settings                                 │
│ ┌──────────────────────────────────────┐ │
│ │ Model Size: [tiny] [base] [small]   │ │  ← Segmented buttons
│ │ API Key: [••••••••••••] [Save]      │ │  ← Password entry + button
│ │ Smart Formatting: [========O  ]     │ │  ← Switch/toggle
│ └──────────────────────────────────────┘ │
├──────────────────────────────────────────┤
│           WaveScribe v0.1.0              │  ← Footer
└──────────────────────────────────────────┘
```

**CustomTkinter widgets to use:**
- `CTk` — Main window
- `CTkFrame` — Card containers (with border)
- `CTkLabel` — Text labels, status text
- `CTkButton` — Record/Stop/Insert/Save buttons (fg_color changes for green/red)
- `CTkSegmentedButton` — Model size selector, mode toggle
- `CTkTextbox` — Transcription display (read-only)
- `CTkEntry` — API key input (password mode: `show="*"`)
- `CTkSwitch` — Smart formatting toggle
- `CTkProgressBar` — Future: audio level meter

**CustomTkinter dark theme colors (default):**
- Background: `"#212121"`
- Card frames: `"#2b2b2b"`
- Accent: `"#1f6aa5"` (blue)
- Text: `"#ffffff"` / `"#a0a0a0"`

**State polling:** Use `window.after(100)` to poll the queue every 100ms for hotkey events and update the UI accordingly.

---

### Step 10: Entry Point (`main.py`)
**Goal:** Wire everything together and start the app.

```python
import threading
import queue
from app.gui import App
from app.state import AppState
from app.audio_capture import AudioCapture
from app.hotkeys import register_hotkeys
from app.tray import create_tray

def main():
    q = queue.Queue()
    state = AppState()
    audio = AudioCapture()
    
    # Start hotkey listener in daemon thread
    hotkey_thread = threading.Thread(
        target=register_hotkeys, args=(q,), daemon=True
    )
    hotkey_thread.start()
    
    # Create and run GUI (blocks main thread)
    app = App(state, audio, q)
    app.mainloop()

if __name__ == "__main__":
    main()
```

---

### Step 11: Unit Tests (`tests/`)
**Goal:** Ensure correctness of the core logic.

- `test_formatter.py` — Port the 4 Rust tests + add edge cases:
  - Punctuation commands → symbols
  - Case-insensitive matching ("Open Bracket" → "{")
  - Auto-capitalization after `. ? !`
  - Bullet points and spacing cleanup
  - Empty string, no-op input handling
- `test_audio_capture.py` — Basic tests (requires a mic or mock):
  - Buffer starts empty after init
  - Stop returns bytes

**Verify:** `pytest tests/ -v` passes all tests

---

### Step 12: Package with PyInstaller
**Goal:** Build a single `.exe` file for distribution.

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --icon=assets/icon.png --name=WaveScribe main.py
```

**Output:** `dist/WaveScribe.exe` (~30-50MB)

**Optimization tips:**
- Use `--exclude-module` to strip unused packages (e.g., matplotlib, tkinter's less-used modules)
- Add hidden imports for CustomTkinter if needed
- Test on a clean Windows machine to verify no missing DLLs

---

## 🔄 Comparison: Old vs New

| Aspect | Tauri/Rust (Current) | Python + CustomTkinter (Target) |
|--------|---------------------|--------------------------------|
| **Dev environment** | **12.3 GB** 😱 | **~200-250 MB** ✅ |
| **Final exe** | ~10-15 MB | ~30-50 MB |
| **Dev iteration** | ~30-60s (compile + bundle) | **Instant** (no compile) ✅ |
| **GUI tech** | React in WebView | **Native** (tkinter framework) ✅ |
| **Frontend knowledge** | HTML/CSS/JS/React | Python only |
| **Audio pipeline** | Browser API → base64 → Rust decode → WAV | **Direct** (sounddevice → numpy → WAV) ✅ |
| **Hotkeys** | Tauri plugin (native) | keyboard library (may need admin) |
| **Packaging** | Complex (NSIS/msi bundler) | One PyInstaller command |
| **Testing** | Rust tests + npm test | pytest (all in Python) |

---

## 🎨 Dark Theme Mockup

CustomTkinter's default dark theme looks like this (conceptually):

```
 ┌──────────────────────────────────────────┐
 │ ● WaveScribe                    Recording │
 │──────────────────────────────────────────│
 │ ┌──────────────────────────────────────┐ │
 │ │ Status: 🔴 Recording...              │ │
 │ │ Mode: [Local] │ [Cloud]              │ │
 │ │                                      │ │
 │ │  Ctrl+Shift+R  Start                 │ │
 │ │  Ctrl+Shift+S  Stop & Transcribe     │ │
 │ │                                      │ │
 │ │  [🎤 Record]   [⏹ Stop]             │ │
 │ └──────────────────────────────────────┘ │
 │ ┌──────────────────────────────────────┐ │
 │ │ Last Transcription                   │ │
 │ │                                      │ │
 │ │ "Hello world, this is a test of      │ │
 │ │  the WaveScribe transcription        │ │
 │ │  system period New paragraph It      │ │
 │ │  works great exclamation mark"       │ │
 │ │                                      │ │
 │ │  [📝 Insert into Window]             │ │
 │ └──────────────────────────────────────┘ │
 │ ┌──────────────────────────────────────┐ │
 │ │ Settings                             │ │
 │ │                                      │ │
 │ │ Model Size                           │ │
 │ │  [ tiny ] [ base ] [ small ]         │ │
 │ │                                      │ │
 │ │ OpenAI API Key (Cloud Mode)          │ │
 │ │  [•••••••••••••••••] [Save]         │ │
 │ │                                      │ │
 │ │ Smart Formatting  [=========O ]     │ │
 │ └──────────────────────────────────────┘ │
 │              WaveScribe v0.1.0           │
 └──────────────────────────────────────────┘
```

---

## 📅 Estimated Timeline

| Step | Time | Can Parallelize? |
|------|------|-----------------|
| 1. Scaffold | 10 min | — |
| 2. State | 15 min | — |
| 3. Formatter | 20 min | ✅ With Step 2 |
| 4. Audio Capture | 30 min | ✅ With Step 3 |
| 5. Transcriber | 15 min | ✅ With Step 3, 4 |
| 6. Typer | 10 min | ✅ With Step 4, 5 |
| 7. Hotkeys | 20 min | ✅ With Step 4, 5 |
| 8. System Tray | 15 min | ✅ With Step 4, 5 |
| 9. GUI | **2-3 hrs** | After Steps 2-8 |
| 10. Entry Point | 30 min | After Step 9 |
| 11. Tests | 30 min | ✅ With Step 3 |
| 12. Package | 30 min | After Step 10 |
| **Total** | **~5 hrs** | — |

---

## 🚀 Getting Started

When you're ready to begin:

```bash
# 1. Create the project directory
mkdir wavescribe-python
cd wavescribe-python

# 2. Set up virtual environment
python -m venv .venv
.venv\Scripts\activate

# 3. Install everything
pip install customtkinter sounddevice scipy openai pyautogui keyboard pystray Pillow pytest pyinstaller
pip freeze > requirements.txt

# 4. Create the directory structure
mkdir app tests assets

# 5. Start with Step 1 in the plan!
```
