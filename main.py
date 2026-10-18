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

from app.state import AppState, create_event_queue
from app.audio_capture import AudioCapture
from app.hotkeys import HotkeyManager
from app.config import load_config
from app.autostart import apply_autostart_setting, is_autostart_enabled
from app.tray import create_tray
from app.core import WaveScribeCore
from app.webui import WebUI
from app.tts_engine import EngineManager


def main() -> None:
    """Start WaveScribe application."""

    # ── Initialize state & components ──
    state = AppState()
    event_queue = create_event_queue()
    audio = AudioCapture()

    # ── Load saved config ──
    config = load_config()

    # ── Reconcile auto-start with the saved setting ──
    # Keeps the HKCU Run key in sync with the config: if the user enabled
    # "Start with Windows" (default), ensure the registry entry exists;
    # if they disabled it, make sure it's removed. Only writes to the
    # registry when the actual state differs from the desired state.
    desired_autostart = config.get("autostart", True)
    if is_autostart_enabled() != desired_autostart:
        apply_autostart_setting(desired_autostart)

    # ── Initialize TTS engine ──
    tts_engine = EngineManager(config.get("tts_backend", "system"))

    # ── Hotkey listener (daemon thread); the core registers the shortcuts ──
    hotkey_mgr = HotkeyManager(event_queue)
    hotkey_thread = threading.Thread(
        target=hotkey_mgr.run,
        daemon=True,
        name="hotkey-listener",
    )
    hotkey_thread.start()

    # ── Core (all app logic) + HTML view ──
    core = WaveScribeCore(state, audio, event_queue, hotkey_mgr, tts_engine, config=config)
    icon_path = os.path.join(project_root, "assets", "icon.png")
    ui = WebUI(core, icon_path=icon_path)
    core.start()

    # ── System tray ──
    tray = create_tray(
        show_callback=ui.show,
        quit_callback=ui.quit,
        icon_path=icon_path,
    )
    tray_thread = threading.Thread(
        target=tray.run,
        daemon=True,
        name="system-tray",
    )
    tray_thread.start()

    # ── Run UI (blocks main thread until quit) ──
    ui.run()

    try:
        tray.stop()
    except Exception:
        pass
    # Daemon threads (keyboard hook, audio) may hold the process open.
    os._exit(0)


if __name__ == "__main__":
    main()
