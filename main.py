"""WaveScribe — AI-powered dictation for Windows.

Entry point that wires together all modules and starts the application.
"""

import os
import sys
import threading

# Add project root to path (for development / pyinstaller compat)
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import customtkinter as ctk

from app.state import AppState, create_event_queue, AppStatus
from app.audio_capture import AudioCapture
from app.hotkeys import HotkeyManager
from app.config import load_config
from app.tray import create_tray
from app.gui import App


def main() -> None:
    """Start WaveScribe application."""

    # ── Set theme ──
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    # ── Initialize state & components ──
    state = AppState()
    event_queue = create_event_queue()
    audio = AudioCapture()

    # ── Load saved config ──
    config = load_config()

    # ── Start hotkey listener (daemon thread) ──
    hotkey_mgr = HotkeyManager(event_queue)
    hotkey_mgr.set_hotkeys(
        config.get("hotkey_start", "ctrl+shift+r"),
        config.get("hotkey_stop", "ctrl+shift+s"),
    )

    hotkey_thread = threading.Thread(
        target=hotkey_mgr.run,
        daemon=True,
        name="hotkey-listener",
    )
    hotkey_thread.start()

    # ── Create GUI (pass hotkey_mgr for dynamic updates) ──
    app = App(state, audio, event_queue, hotkey_mgr)

    # ── System tray ──
    icon_path = os.path.join(project_root, "assets", "icon.png")
    tray = create_tray(
        show_callback=app.show_window,
        quit_callback=app.destroy,
        icon_path=icon_path,
    )

    # Run tray in a daemon thread
    tray_thread = threading.Thread(
        target=tray.run,
        daemon=True,
        name="system-tray",
    )
    tray_thread.start()

    # ── Run GUI (blocks main thread) ──
    app.mainloop()


if __name__ == "__main__":
    main()
