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
        (r"C:\Users\CPU\Documents\Programas\codex\wavescribe\.venv\Lib\site-packages\customtkinter\assets", "customtkinter/assets"),
        (r"C:\Users\CPU\Documents\Programas\codex\wavescribe\.venv\Lib\site-packages\whisper\assets", "whisper/assets"),
        (r"C:\Users\CPU/.cache/whisper\base.pt", "whisper_models/base.pt"),
    ],
    hiddenimports=[
        # Core app modules
        "app",
        "app.audio_capture",
        "app.config",
        "app.formatter",
        "app.gui",
        "app.hotkeys",
        "app.overlay",
        "app.sounds",
        "app.state",
        "app.theme",
        "app.transcriber",
        "app.tray",
        "app.typer",
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
        # Tkinter extras
        "tkinter",
        "tkinter.font",
        "tkinter.filedialog",
        # Windows system libraries for keyboard/pystray
        "pywintypes",
        "win32api",
        "win32con",
        "win32file",
        "win32gui",
        "win32process",
        # PyAutoGUI / typing
        "pyperclip",
        "pyautogui",
        "pytweening",
        "PyGetWindow",
        "PyMsgBox",
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
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
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
