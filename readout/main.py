"""Readout — select any text anywhere, press Ctrl+Alt+Space, and listen.

Entry point that wires together the config, state, TTS engines, global
hotkey, settings window and system tray.
"""

import os
import sys
import threading

# Add the project root to sys.path so `readout` is importable when this
# file is run directly (python readout/main.py) and when bundled.
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from readout.autostart import apply_autostart_setting, is_autostart_enabled  # noqa: E402
from readout.core.config import load_config  # noqa: E402
from readout.core.engine import EngineManager  # noqa: E402
from readout.core.hotkeys import ReadoutHotkeys  # noqa: E402
from readout.core.state import ReadoutState, create_event_queue  # noqa: E402
from readout.gui import App  # noqa: E402
from readout.tray import create_tray  # noqa: E402


def main() -> None:
    """Start Readout."""
    config = load_config()

    state = ReadoutState()
    event_queue = create_event_queue()
    engine = EngineManager(config.get("backend", "system"))

    # ── Global hotkey listeners (daemon thread) ──
    hotkeys = ReadoutHotkeys(event_queue)
    hotkeys.set_hotkeys(
        config.get("hotkey", "ctrl+alt+space"),
        config.get("swap_voice_hotkey", "ctrl+alt+v"),
    )
    threading.Thread(target=hotkeys.run, daemon=True, name="readout-hotkeys").start()

    # ── Keep the Run key in sync with the saved setting ──
    desired_autostart = config.get("autostart", False)
    if is_autostart_enabled() != desired_autostart:
        apply_autostart_setting(desired_autostart)

    # ── Settings window (blocking mainloop) ──
    app = App(state, event_queue, engine, hotkeys)

    # ── System tray (daemon thread) ──
    tray = create_tray(event_queue)
    threading.Thread(target=tray.run, daemon=True, name="readout-tray").start()

    app.mainloop()


if __name__ == "__main__":
    main()
