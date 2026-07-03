"""Text insertion into any focused window via clipboard paste.

Uses the system clipboard to paste text, which handles Unicode
characters (ñ, á, é, í, ó, ú, ü, ¿, ¡, etc.) correctly.
"""

import time

import pyautogui
import pyperclip

# ── Global guard to prevent duplicate paste ──
# Tracks the last text that was pasted. If the same text is requested
# again (e.g. via a double-call from auto-insert + something else),
# the second call is silently ignored. This is in addition to the
# ``_auto_inserted`` guard in the GUI layer.
_last_pasted_text: str = ""


def type_text(text: str) -> None:
    """Type text into the currently focused window.

    Copies the text to the clipboard and simulates Ctrl+V to paste it.
    The transcription text is left on the clipboard afterward so you
    can paste it again elsewhere (e.g. Ctrl+V in another field).

    This approach supports any Unicode characters (Spanish tildes, ñ,
    special symbols, etc.) unlike pyautogui.typewrite which only works
    with ASCII characters that have direct key mappings.

    Args:
        text: The text to type.

    Note:
        The user must have a text field focused before calling this function.
    """
    global _last_pasted_text

    if not text:
        return

    # If this exact text was already pasted, skip (prevents double-paste
    # even if the guard in the GUI layer is bypassed).
    if text == _last_pasted_text:
        return
    _last_pasted_text = text

    # Give the user a brief moment to focus the target window
    # (if called right after a button click, the GUI window is focused)
    time.sleep(0.3)

    try:
        # Copy our text to clipboard and Ctrl+V to paste
        pyperclip.copy(text)
        # Small wait for clipboard to settle
        time.sleep(0.05)

        # Simulate Ctrl+V to paste
        pyautogui.keyDown("ctrl")
        time.sleep(0.02)
        pyautogui.keyDown("v")
        time.sleep(0.02)
        pyautogui.keyUp("v")
        time.sleep(0.02)
        pyautogui.keyUp("ctrl")
    except Exception:
        pass  # clipboard unavailable or paste failed — silently skip
