"""Tests for readout/core/hotkeys.py — registration of both global hotkeys.

The ``keyboard`` library needs a real keyboard hook on Windows, so we
monkeypatch ``add_hotkey`` / ``remove_hotkey`` and verify the combos and
events are wired correctly.
"""

import queue

from readout.core.hotkeys import ReadoutHotkeys


def _patched_hotkeys(monkeypatch) -> tuple:
    """Build a ReadoutHotkeys with fake keyboard registration; return
    (instance, registered, removed)."""
    registered = []
    removed = []

    class _FakeHandler:
        def __init__(self, combo):
            self.combo = combo

    monkeypatch.setattr(
        "readout.core.hotkeys.keyboard.add_hotkey",
        lambda combo, callback, **kw: registered.append(combo) or _FakeHandler(combo),
    )
    monkeypatch.setattr(
        "readout.core.hotkeys.keyboard.remove_hotkey",
        lambda handler: removed.append(handler.combo),
    )
    monkeypatch.setattr(
        "readout.core.hotkeys.keyboard.hook", lambda *a, **k: None
    )
    return ReadoutHotkeys(queue.Queue()), registered, removed


def test_set_hotkeys_registers_both(monkeypatch):
    hotkeys, registered, _ = _patched_hotkeys(monkeypatch)
    hotkeys.set_hotkeys("ctrl+alt+space", "ctrl+alt+v")
    assert registered == ["ctrl+alt+space", "ctrl+alt+v"]


def test_set_hotkeys_skips_empty_combos(monkeypatch):
    hotkeys, registered, _ = _patched_hotkeys(monkeypatch)
    hotkeys.set_hotkeys("ctrl+alt+space", "")
    assert registered == ["ctrl+alt+space"]
    hotkeys.set_hotkeys("", "")
    assert registered == ["ctrl+alt+space"]


def test_swap_event_is_cycle_voice(monkeypatch):
    """The swap hotkey callback must emit ``cycle_voice`` (not toggle_read)."""
    import readout.core.hotkeys as hk

    callbacks = {}

    monkeypatch.setattr(
        "readout.core.hotkeys.keyboard.add_hotkey",
        lambda combo, callback, **kw: callbacks.__setitem__(combo, callback),
    )
    monkeypatch.setattr(
        "readout.core.hotkeys.keyboard.remove_hotkey", lambda handler: None
    )
    monkeypatch.setattr(
        "readout.core.hotkeys.keyboard.hook", lambda *a, **k: None
    )

    q = queue.Queue()
    hotkeys = ReadoutHotkeys(q)
    hotkeys.set_hotkeys("ctrl+alt+space", "ctrl+alt+v")
    callbacks["ctrl+alt+v"]()
    assert q.get_nowait() == "cycle_voice"
    callbacks["ctrl+alt+space"]()
    assert q.get_nowait() == "toggle_read"


def test_set_hotkey_alias_registers_read_only(monkeypatch):
    hotkeys, registered, _ = _patched_hotkeys(monkeypatch)
    hotkeys.set_hotkey("ctrl+shift+p")
    assert registered == ["ctrl+shift+p"]


def test_remove_clears_both_handlers(monkeypatch):
    hotkeys, _, removed = _patched_hotkeys(monkeypatch)
    hotkeys.set_hotkeys("ctrl+alt+space", "ctrl+alt+v")
    hotkeys._remove()
    assert removed == ["ctrl+alt+space", "ctrl+alt+v"]
    assert hotkeys._handler is None
    assert hotkeys._swap_handler is None
