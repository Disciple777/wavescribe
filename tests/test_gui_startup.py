"""Regression test: the GUI must construct and update without crashing.

This guards against startup crashes like the v1.3 release bug:
``AttributeError: '_tkinter.tkapp' object has no attribute '_settings_card'``
raised from ``App.__init__`` -> ``_update_ui``.

Requires a display; skipped gracefully on headless machines.
"""
import pytest

ctk = pytest.importorskip("customtkinter")

from app.state import AppState, create_event_queue, AppMode  # noqa: E402
from app.audio_capture import AudioCapture  # noqa: E402
from app.hotkeys import HotkeyManager  # noqa: E402
from app.gui import App  # noqa: E402


def _display_available() -> bool:
    """Check whether a Tk display is available before building the GUI."""
    import tkinter as tk

    try:
        root = tk.Tk()
        root.destroy()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _display_available(), reason="no display available"
)


def test_gui_constructs_and_updates() -> None:
    """App() must build every card and survive repeated _update_ui calls."""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    state = AppState()
    event_queue = create_event_queue()
    audio = AudioCapture()
    hotkey_mgr = HotkeyManager(event_queue)

    app = App(state, audio, event_queue, hotkey_mgr)

    try:
        # Exercise the full UI update path a few times to catch widget-order bugs
        for _ in range(3):
            app._update_ui()
            app.update_idletasks()

        # Toggle history off/on — exercises the _settings_card ordering path
        state.set_history_enabled(False)
        app._update_ui()
        state.set_history_enabled(True)
        app._update_ui()

        # Toggle mode — exercises the local-language visibility path
        state.set_mode(AppMode.LOCAL)
        app._update_ui()
        state.set_mode(AppMode.CLOUD)
        app._update_ui()
    finally:
        app.destroy()
