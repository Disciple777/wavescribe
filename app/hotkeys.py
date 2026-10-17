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

    Supports two sets of hotkeys:
      - Dictation: start_recording / stop_recording
      - TTS Read Out: toggle_read / cycle_voice
    """

    def __init__(self, event_queue: queue.Queue) -> None:
        self._event_queue = event_queue
        self._start_handler: Optional[keyboard.HotKey] = None
        self._stop_handler: Optional[keyboard.HotKey] = None
        self._tts_read_handler: Optional[keyboard.HotKey] = None
        self._tts_swap_handler: Optional[keyboard.HotKey] = None

        # Clear any stuck modifiers from previous sessions
        self._release_all_modifiers()

    # ── Public API ──

    def set_hotkeys(self, start_hotkey: str, stop_hotkey: str) -> None:
        """Set (or update) the dictation start and stop hotkeys."""
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

    def set_tts_hotkeys(self, read_combo: str = "", swap_combo: str = "") -> None:
        """Set (or update) the TTS read and voice-swap hotkeys."""
        self._remove_tts_hotkeys()
        read_combo = (read_combo or "").strip()
        swap_combo = (swap_combo or "").strip()
        if read_combo:
            self._tts_read_handler = keyboard.add_hotkey(
                read_combo,
                lambda: self._event_queue.put("toggle_read"),
                suppress=False,
            )
        if swap_combo:
            self._tts_swap_handler = keyboard.add_hotkey(
                swap_combo,
                lambda: self._event_queue.put("cycle_voice"),
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

    def _remove_tts_hotkeys(self) -> None:
        """Safely remove any previously registered TTS hotkeys."""
        if self._tts_read_handler is not None:
            try:
                keyboard.remove_hotkey(self._tts_read_handler)
            except Exception:
                pass
            self._tts_read_handler = None

        if self._tts_swap_handler is not None:
            try:
                keyboard.remove_hotkey(self._tts_swap_handler)
            except Exception:
                pass
            self._tts_swap_handler = None

    @staticmethod
    def _release_all_modifiers() -> None:
        """Force-release all common modifier keys to clear stuck states."""
        for key in ("ctrl", "alt", "shift", "windows"):
            try:
                keyboard.release(key)
            except Exception:
                pass
