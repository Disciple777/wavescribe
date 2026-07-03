"""Global hotkey management for WaveScribe.

Uses the keyboard library to register system-wide shortcuts.
Supports dynamic hotkey changes via the HotkeyManager class.
Hotkey events are put into a queue.Queue for the GUI to process.
"""

import queue
from typing import Optional

import keyboard


class HotkeyManager:
    """Manages global hotkeys with support for dynamic updates.

    Usage:
        mgr = HotkeyManager(event_queue)
        mgr.set_hotkeys("ctrl+shift+r", "ctrl+shift+s")
        # ... later, change hotkeys dynamically:
        mgr.set_hotkeys("ctrl+shift+t", "ctrl+shift+y")

    The keyboard listener thread stays alive via run() / keyboard.wait().
    """

    def __init__(self, event_queue: queue.Queue) -> None:
        self._event_queue = event_queue
        self._start_handler: Optional[keyboard.HotKey] = None
        self._stop_handler: Optional[keyboard.HotKey] = None

        # Clear any stuck modifiers from previous sessions
        self._release_all_modifiers()

    # ── Public API ──

    def set_hotkeys(self, start_hotkey: str, stop_hotkey: str) -> None:
        """Set (or update) the start and stop hotkeys.

        Removes any previously registered hotkeys before registering
        the new ones. Safe to call multiple times.
        """
        self._remove_old_hotkeys()

        self._start_handler = keyboard.add_hotkey(
            start_hotkey,
            lambda: self._event_queue.put("start_recording"),
            suppress=False,
        )
        self._stop_handler = keyboard.add_hotkey(
            stop_hotkey,
            lambda: self._event_queue.put("stop_recording"),
            suppress=False,
        )

    def run(self) -> None:
        """Block forever, keeping the keyboard listener alive.

        Call this in a daemon thread.
        """
        keyboard.wait()

    # ── Internal helpers ──

    def _remove_old_hotkeys(self) -> None:
        """Safely remove any previously registered hotkeys."""
        if self._start_handler is not None:
            try:
                keyboard.remove_hotkey(self._start_handler)
            except Exception:
                pass
            self._start_handler = None

        if self._stop_handler is not None:
            try:
                keyboard.remove_hotkey(self._stop_handler)
            except Exception:
                pass
            self._stop_handler = None

    @staticmethod
    def _release_all_modifiers() -> None:
        """Force-release all common modifier keys to clear stuck states.

        The keyboard library can sometimes leave modifiers in a "pressed"
        state. This restores normal keyboard behavior.
        """
        for key in ("ctrl", "alt", "shift", "windows"):
            try:
                keyboard.release(key)
            except Exception:
                pass
