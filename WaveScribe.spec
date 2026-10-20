# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for WaveScribe — AI-powered dictation for Windows.
"""

import sys
from pathlib import Path

# ── Block 1: Analysis ──────────────────────────────────────────────
a = Analysis(
    ["main.py"],
    pathex=[r"C:\Users\CPU\Documents\Programas\codex\wavescribe"],
    binaries=[],
    datas=[
        (r"C:\Users\CPU\Documents\Programas\codex\wavescribe\assets", "assets"),
        (r"C:\Users\CPU\Documents\Programas\codex\wavescribe\app\web", "app/web"),
        (r"C:\Users\CPU\Documents\Programas\codex\wavescribe\.venv\Lib\site-packages\whisper\assets", "whisper/assets"),
        (r"C:\Users\CPU/.cache/whisper\base.pt", "whisper_models/base.pt"),
    ],
    hiddenimports=[
        # Core app modules
        "app",
        "app.audio_capture",
        "app.autostart",
        "app.config",
        "app.core",
        "app.edge_tts",
        "app.formatter",
        "app.history",
        "app.hotkeys",
        "app.listening",
        "app.selection",
        "app.sessions",
        "app.sounds",
        "app.state",
        "app.system_tts",
        "app.transcriber",
        "app.tray",
        "app.tts_engine",
        "app.tts_errors",
        "app.typer",
        "app.voices",
        "app.webui",
        # HTML UI host (Edge WebView2 via pythonnet / WinForms)
        "webview",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr",
        "clr_loader",
        "bottle",
        "proxy_tools",
        # Sound device (CFFI backend)
        "sounddevice",
        "_sounddevice_data",
        # System tray
        "pystray",
        "PIL",
        "PIL._imaging",
        "PIL._imagingtk",
        # Global hotkeys
        "keyboard",
        # Audio processing
        "numpy",
        # Config persistence
        "json",
        # Threading / queue
        "threading",
        "queue",
        # Font registration (Windows GDI)
        "ctypes",
        "ctypes.wintypes",
        # Sound playback (Windows)
        "winsound",
        # Temp directory
        "tempfile",
        # HTTP / API
        "httpx",
        "httpcore",
        "openai",
        # Edge neural TTS (online) — optional package, needs its async HTTP stack
        "edge_tts",
        "aiohttp",
        "tabulate",
        # Windows system libraries
        # (pystray/keyboard use ctypes on Windows — pywin32 is NOT a dependency,
        #  so hidden imports for pywintypes/win32* would just log 'not found' errors)
        # Windows auto-start (registry)
        "winreg",
        # PyAutoGUI / typing
        "pyperclip",
        "pyautogui",
        "pytweening",
        "pygetwindow",
        "pymsgbox",
        "pyscreeze",
        # pystray dependency
        "six",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Test and dev
        "pytest",
        # Build tools
        "setuptools",
        "pip",
    ],
    noarchive=False,
    optimize=2,
)

# ── Block 2: PYMEDATA ─────────────────────────────────────────────
pyz = PYZ(a.pure)

# ── Block 3: EXE (wrapper loader) ─────────────────────────────────
# exclude_binaries=True: binaries/datas go only into COLLECT (_internal/),
# so the exe runs in place instead of self-extracting a full copy to %TEMP%.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WaveScribe",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # No console window — GUI only
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=r"C:\Users\CPU\Documents\Programas\codex\wavescribe\assets\icon.ico",
)

# ── Block 4: COLLECT (one-folder bundle) ───────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="WaveScribe",
)
