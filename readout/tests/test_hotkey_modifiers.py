"""Tests for hotkey modifier decoding (readout/gui.py).

Regression for the "phantom Alt" bug: on many Windows systems tkinter
reports the Alt bit (Mod1, 0x0008) in ``event.state`` for *every* key
event, even when Alt is not pressed. ``decode_hotkey_modifiers`` therefore
takes alt/win from the keyboard library's physical key state instead of
the tkinter bitmask.
"""

from readout.gui import decode_hotkey_modifiers

# tkinter event.state bits: shift = 0x1, ctrl = 0x4, alt(phantom) = 0x8
CTRL_SHIFT_STATE = 0x0005


def test_phantom_alt_bit_is_ignored():
    """Ctrl+Shift+7 must NOT become Ctrl+Shift+Alt+7 when Alt isn't held."""
    mods = decode_hotkey_modifiers(CTRL_SHIFT_STATE, alt_down=False)
    assert mods == ["ctrl", "shift"]


def test_real_alt_is_included():
    mods = decode_hotkey_modifiers(CTRL_SHIFT_STATE, alt_down=True)
    assert mods == ["ctrl", "shift", "alt"]


def test_ctrl_and_shift_come_from_state_bits():
    assert decode_hotkey_modifiers(0x0004, alt_down=False) == ["ctrl"]
    assert decode_hotkey_modifiers(0x0001, alt_down=False) == ["shift"]
    assert decode_hotkey_modifiers(0x0000) == []


def test_win_key_comes_from_physical_state():
    assert decode_hotkey_modifiers(0x0004, win_down=True) == ["ctrl", "win"]
    assert decode_hotkey_modifiers(0x0005, alt_down=False, win_down=False) == ["ctrl", "shift"]


def test_pure_phantom_state_yields_no_modifiers():
    """A bare key whose state only has the phantom Alt bit is modifier-free."""
    assert decode_hotkey_modifiers(0x0008, alt_down=False) == []
