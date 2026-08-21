"""Global hotkey management for Readout.

Uses the same ``keyboard`` library pattern as WaveScribe's app/hotkeys.py:
a single system-wide toggle hotkey whose events are delivered through a
thread-safe queue that the GUI polls.

The hotkey-capture feature also needs the *physical* key that the user
pressed. tkinter reports keysyms ("slash" for Shift+7 on a Spanish layout)
which don't match the ``keyboard`` library's names, so a lightweight
additional hook records the most recent non-modifier key-down name —
layout independent and always valid for ``keyboard.add_hotkey``.
"""

import queue
import time
from typing import Optional

import keyboard


class ReadoutHotkeys:
    """Registers the global toggle hotkey and reports events via a queue."""

    # Modifier / system keys never become the *main* key of a captured combo.
    _MODIFIER_NAMES = frozenset({
        "ctrl", "alt", "shift", "win", "windows", "menu",
        "left ctrl", "right ctrl", "left shift", "right shift",
        "left alt", "right alt", "left windows", "right windows",
        "esc",
    })

    def __init__(self, event_queue: queue.Queue) -> None:
        self._event_queue = event_queue
        self._handler = None
        self._swap_handler = None
        # Most recent non-modifier key-down observed by the global hook
        # (physical key name + timestamp). Used by the GUI's hotkey capture.
        self.last_key_name: str = ""
        self.last_key_time: float = 0.0
        self._hook = None
        self._start_key_hook()

    def _start_key_hook(self) -> None:
        """Hook all key-downs so capture can read the physical key pressed."""
        try:
            self._hook = keyboard.hook(self._on_key_event, suppress=False)
        except Exception:
            self._hook = None  # capture falls back to the tkinter keysym

    def _on_key_event(self, event) -> None:
        if event.event_type == "down" and event.name not in self._MODIFIER_NAMES:
            self.last_key_name = event.name
            self.last_key_time = time.time()

    def set_hotkey(self, combo: str) -> None:
        """Register only the read hotkey (keeps existing callers working)."""
        self.set_hotkeys(read_combo=combo)

    def set_hotkeys(self, read_combo: str = "", swap_combo: str = "") -> None:
        """Register the read and voice-swap hotkeys.

        Both are system-wide: the read hotkey emits ``toggle_read`` and the
        swap hotkey emits ``cycle_voice`` through the event queue. Empty
        combos are simply not registered.
        """
        self._remove()
        read_combo = (read_combo or "").strip()
        swap_combo = (swap_combo or "").strip()
        if read_combo:
            self._handler = keyboard.add_hotkey(
                read_combo,
                lambda: self._event_queue.put("toggle_read"),
                suppress=False,
            )
        if swap_combo:
            self._swap_handler = keyboard.add_hotkey(
                swap_combo,
                lambda: self._event_queue.put("cycle_voice"),
                suppress=False,
            )

    def run(self) -> None:
        """Block forever, keeping the keyboard listener alive (daemon thread)."""
        keyboard.wait()

    def _remove(self) -> None:
        for attr in ("_handler", "_swap_handler"):
            handler = getattr(self, attr, None)
            if handler is not None:
                try:
                    keyboard.remove_hotkey(handler)
                except Exception:
                    pass
                setattr(self, attr, None)
