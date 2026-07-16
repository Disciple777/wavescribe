# WaveScribe 🎤

**AI-powered dictation for Windows** — speak, transcribe, and insert text into any application with global hotkeys.

Record your microphone, get real-time AI transcription via OpenAI Whisper (cloud API or local model), and paste the result into any focused text field. Works in any app — Notepad, browsers, IDEs, Word, Slack, etc.

---

## ✨ Features

- **🎤 Dual transcription engine**: OpenAI Whisper API (cloud) or local `openai-whisper` model (offline-capable)
- **⌨️ Global hotkeys**: Ctrl+Shift+R to start recording, Ctrl+Shift+S to stop & transcribe — works from any app
- **📝 Smart formatting**: ~40 spoken punctuation commands (`"comma" → ","`, `"open paren" → "("`), auto-capitalization, numbers-as-digits conversion, redundant punctuation cleanup
- **📋 Clipboard paste**: Inserts text via Ctrl+V (clipboard-based, so Unicode/special chars work perfectly)
- **🎯 Custom hotkey binding**: Capture any key combination through the UI
- **🔊 Sound effects**: Rising/descending tones on start/stop recording
- **💡 Floating overlay**: Compact always-on-top status indicator during recording/transcribing
- **🖥️ System tray**: Minimizes to tray with Show/Quit menu
- **💾 Config persistence**: Settings saved to `%APPDATA%\WaveScribe\config.json` — survives reinstalls
- **📦 Bundled fonts**: Poppins & Montserrat loaded at runtime (not installed system-wide)
- **🏗️ One-command build**: PyInstaller + Inno Setup installer pipeline

---

## 📸 Screenshot

```
┌──────────────────────────────────────────┐
│ ● WaveScribe                       Idle  │
├──────────────────────────────────────────┤
│ ┌──────────────────────────────────────┐ │
│ │ ● Idle                               │ │
│ │ Mode: [Cloud] │ [Local]               │ │
│ │                                      │ │
│ │ Ctrl+Shift+R   Start Recording       │ │
│ │ Ctrl+Shift+S   Stop & Transcribe     │ │
│ │                                      │ │
│ │ [🎤  Record]       [⏹  Stop]       │ │
│ └──────────────────────────────────────┘ │
│ ┌──────────────────────────────────────┐ │
│ │ Transcription                     [✕]│ │
│ │                                      │ │
│ │ Your transcription will appear       │ │
│ │ here...                              │ │
│ │                                      │ │
│ │ [📝 Insert into Window]              │ │
│ └──────────────────────────────────────┘ │
│ ┌──────────────────────────────────────┐ │
│ │ Settings                             │ │
│ │ Shortcuts                     ...    │ │
│ │ Model Size     [tiny] [base] [small] │ │
│ │ Local Model                     ...  │ │
│ │ OpenAI API Key                 [Save]│ │
│ │ Smart Formatting               ...   │ │
│ └──────────────────────────────────────┘ │
│              WaveScribe v0.1.0           │
└──────────────────────────────────────────┘
```

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.12+**
- **Windows** (primary target; Linux/Mac have partial support)
- **Microphone** connected and working

### Quick Start (Development)

```bash
# 1. Clone and enter the project
cd wavescribe

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run WaveScribe
python main.py
```

### Configuration

On first run, WaveScribe creates a config file at:

- **Windows**: `%APPDATA%\WaveScribe\config.json`
- **Linux/Mac**: `~/.config/wavescribe/config.json`

You can set:
- **OpenAI API key** (required for Cloud mode)
- **Model size** (tiny / base / small)
- **Mode** (Cloud ↔ Local)
- **Hotkeys** (start & stop)
- **Formatting toggles** (punctuation via speech, auto-capitalize, numbers as digits)

---

## 🧱 Architecture

```
┌────────────────────────────────────────────────────┐
│                 GUI Layer                            │
│  CustomTkinter Window (main thread)                  │
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

### Data Flow

```
1. Ctrl+Shift+R pressed   → Hotkey thread → queue "start_recording"
2. GUI polls queue         → calls audio_capture.start()
3. Audio buffers mic      → sounddevice callback (audio thread)
4. Ctrl+Shift+S pressed   → Hotkey thread → queue "stop_recording"
5. GUI polls queue         → calls audio_capture.stop() → WAV bytes
6. Worker thread spawns   → calls transcription API or local model
7. Text returned          → formatted (punctuation, caps, numbers)
8. GUI displays text      → user clicks "Insert" → clipboard paste
```

### Project Structure

```
wavescribe/
├── main.py                    # Entry point — wires everything together
├── requirements.txt           # Python dependencies
├── config.json                # User settings (loaded from %APPDATA% at runtime)
├── WaveScribe-Plan.md         # Original planning document
│
├── app/
│   ├── gui.py                 # CustomTkinter main window (all UI)
│   ├── state.py               # Thread-safe AppState, enums, event queue
│   ├── audio_capture.py       # sounddevice microphone → WAV bytes
│   ├── transcriber.py         # Cloud (OpenAI API) + Local (openai-whisper)
│   ├── formatter.py           # 40+ punctuation replacements, spacing, caps
│   ├── typer.py               # Clipboard-based text insertion (Ctrl+V)
│   ├── hotkeys.py             # Global hotkey registration & management
│   ├── overlay.py             # Floating status indicator (always-on-top)
│   ├── sounds.py              # Synthesized start/stop sound effects
│   ├── tray.py                # System tray icon + menu
│   ├── theme.py               # Colors, bundled fonts (Poppins/Montserrat)
│   └── config.py              # JSON config persistence + migration
│
├── tests/
│   ├── test_formatter.py      # 30+ unit tests for formatting pipeline
│   └── test_audio_capture.py  # Init & error handling tests
│
├── assets/
│   ├── icon.png               # App icon
│   ├── icon.ico               # Windows icon (for installer)
│   ├── Poppins-Regular.ttf    # Bundled font
│   ├── Poppins-Bold.ttf       # Bundled font
│   ├── Montserrat-Regular.ttf # Bundled font
│   └── Montserrat-Bold.ttf    # Bundled font
│
├── build_windows.py           # Full build script (model pre-download → exe → installer)
├── build_installer.bat        # Batch file to run Inno Setup
├── WaveScribe.spec            # PyInstaller spec (generated)
├── installer.iss              # Inno Setup installer config
│
├── dist/                      # PyInstaller output
└── installer/                 # Setup exe + portable ZIP
```

---

## 🧪 Running Tests

```bash
pytest tests/ -v
```

Tests cover:
- **Formatter**: All ~40 punctuation mappings, spacing cleanup, auto-capitalization, redundant punctuation collapse, number conversion, edge cases
- **Audio capture**: Initialization, error handling

---

## 📦 Building for Distribution

WaveScribe uses PyInstaller to bundle everything into a standalone executable, with optional Inno Setup installer.

### Prerequisites

- Install [Inno Setup](https://jrsoftware.org/isdl.php) (for installer, optional)

### Build Commands

```bash
# Activate venv
.venv\Scripts\activate

# Basic build (executable only)
python build_windows.py

# Build + portable ZIP
python build_windows.py --zip

# Build + Inno Setup installer
python build_windows.py --installer
```

The build script:
1. Pre-downloads the Whisper "base" model and bundles it
2. Generates a PyInstaller `.spec` with all hidden imports and data files
3. Cleans previous build artifacts
4. Runs PyInstaller to produce `dist/WaveScribe/`
5. Optionally creates a portable ZIP or Inno Setup installer

### Output

| Artifact | Location | Size |
|----------|----------|------|
| Executable folder | `dist/WaveScribe/` | ~150-200 MB |
| Portable ZIP | `installer/WaveScribe-Portable.zip` | ~60-80 MB |
| Installer | `installer/WaveScribe-Setup-v0.1.0.exe` | ~50-70 MB |

---

## 🧰 Technology Stack

| Component | Library | Purpose |
|-----------|---------|---------|
| **GUI** | `customtkinter` | Modern dark-themed native widgets |
| **Audio Capture** | `sounddevice` | PortAudio bindings, mic input |
| **WAV Conversion** | `scipy` | NumPy audio → WAV bytes |
| **Cloud Transcription** | `openai` | OpenAI Whisper API client |
| **Local Transcription** | `openai-whisper` | Local Whisper model (tiny/base/small) |
| **Text Insertion** | `pyperclip` + `pyautogui` | Clipboard copy & Ctrl+V paste |
| **Global Hotkeys** | `keyboard` | System-wide keyboard shortcuts |
| **System Tray** | `pystray` + `Pillow` | Tray icon with context menu |
| **Fonts** | Bundled `.ttf` | Poppins & Montserrat (private GDI load) |
| **Sound** | `winsound` (stdlib) | Synthesized start/stop tones |
| **Testing** | `pytest` | Unit test framework |
| **Packaging** | `PyInstaller` | Standalone .exe bundler |
| **Installer** | `Inno Setup` | Windows installer (.exe) |

---

## 🔮 Roadmap

- [x] Microphone audio capture → WAV
- [x] Cloud transcription (OpenAI Whisper API)
- [x] Local transcription (openai-whisper)
- [x] Smart formatting (punctuation, caps, numbers)
- [x] Global hotkeys
- [x] Custom hotkey binding
- [x] System tray
- [x] Floating status overlay
- [x] Sound effects
- [x] Config persistence
- [x] PyInstaller packaging
- [x] Inno Setup installer
- [ ] Audio level meter during recording
- [ ] Coding mode (spoken symbols → code syntax)
- [ ] Auto-start with Windows
- [ ] Multiple language support
- [ ] Transcription history

---

## 📄 License

MIT
