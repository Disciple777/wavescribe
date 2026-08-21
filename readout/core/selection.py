"""Grab the currently selected text from *any* Windows application.

Approach
--------
The only portable way to read a text selection from an arbitrary app is to
make the OS copy it for us:

  1. Save the user's clipboard.
  2. Put a unique random *sentinel* string on the clipboard.
  3. Simulate a copy shortcut, trying several in order:

       - Ctrl+C  — works in every normal app; in Windows Terminal it
         copies when there *is* a selection (and is passed through to
         the shell as an interrupt when there isn't).
       - Ctrl+Insert — classic terminal copy binding (still a Windows
         Terminal default).
       - Ctrl+Shift+C — Windows Terminal's canonical copy shortcut.

  4. Poll the clipboard until the sentinel is replaced (or a timeout).
  5. Always restore the user's original clipboard, even on error.

Legacy consoles
---------------
The classic cmd/PowerShell console (conhost) has **no** keyboard copy
shortcut — Ctrl+C is always an interrupt.  With QuickEdit mode on, a mouse
drag copies the selection to the clipboard when you release it.  When such
a console is focused we therefore skip the sentinel trick entirely (it
would clobber the auto-copied text) and simply read the clipboard.

All clipboard/keyboard access goes through module-level helpers so the
logic is unit-testable with mocks.
"""

import html
import random
import re
import string
import time
from typing import List, Optional

import keyboard
import pyautogui
import pyperclip

try:
    import ctypes
except ImportError:  # pragma: no cover - non-Windows
    ctypes = None

# Copy shortcuts tried in order.  Terminals/consoles treat Ctrl+C as
# "interrupt" (and full-screen TUIs may capture the mouse so no selection
# exists at all), hence the fallbacks.  Each extra attempt is harmless in
# apps that don't bind the shortcut.
_COPY_SHORTCUTS = ("ctrl+c", "ctrl+insert", "ctrl+shift+c")

# Window classes that cannot copy text via any keyboard shortcut:
# conhost (cmd/PowerShell) and ConEmu.
_LEGACY_TERMINAL_CLASS_MARKERS = ("consolewindowclass", "virtualconsoleclass")

# ══════════════════════════════════════════════════════════════════
#  Text sanitizing + Win32 clipboard-format reading
# ══════════════════════════════════════════════════════════════════
# A selection that includes an image (e.g. a Discord sticker/emoticon)
# lands on the clipboard as image data with no plain-text format at all.
# pyperclip then yields "", so we fall back to enumerating the actual
# clipboard formats and extracting whatever readable text exists (ANSI,
# HTML, RTF) and strip binary junk from the result.

# Standard clipboard format ids (Winuser.h).
_CF_TEXT = 1
_CF_BITMAP = 2
_CF_TIFF = 6
_CF_OEMTEXT = 7
_CF_DIB = 8
_CF_PALETTE = 9
_CF_UNICODETEXT = 13
_CF_ENHMETAFILE = 14
_CF_DIBV5 = 17
_CF_DSPBITMAP = 0x0082
_CF_DSPMETAFILEPICT = 0x0083
_CF_DSPENHMETAFILE = 0x008E

# Formats that identify the clipboard as holding an image (no text).
_IMAGE_STANDARD_FORMATS = frozenset({
    _CF_BITMAP, _CF_TIFF, _CF_DIB, _CF_PALETTE, _CF_ENHMETAFILE, _CF_DIBV5,
    _CF_DSPBITMAP, _CF_DSPMETAFILEPICT, _CF_DSPENHMETAFILE,
})
_IMAGE_FORMAT_MARKERS = (
    "png", "jpeg", "jpg", "jfif", "gif", "webp", "tiff", "bitmap",
    "dib", "emf", "wmf", "image/", "system.drawing.bitmap",
)

# Registered-format names (>= 0xC000) that carry readable text.
_TEXT_FORMAT_MARKERS = (
    "html format", "text/html", "text/plain", "text/rtf",
    "rich text format", "application/rtf",
)

if ctypes is not None:  # pragma: no cover - Windows only
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    _user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    _user32.OpenClipboard.restype = ctypes.c_int
    _user32.CloseClipboard.argtypes = []
    _user32.CloseClipboard.restype = ctypes.c_int
    _user32.EnumClipboardFormats.argtypes = [ctypes.c_uint]
    _user32.EnumClipboardFormats.restype = ctypes.c_uint
    _user32.GetClipboardData.argtypes = [ctypes.c_uint]
    _user32.GetClipboardData.restype = ctypes.c_void_p
    _user32.GetClipboardFormatNameW.argtypes = [
        ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int
    ]
    _user32.GetClipboardFormatNameW.restype = ctypes.c_int

    _kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalSize.restype = ctypes.c_size_t
    _kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalUnlock.restype = ctypes.c_int
else:
    _user32 = _kernel32 = None


def sanitize_text(text: str) -> str:
    """Strip binary/control junk from grabbed text while keeping real text.

    Removes NUL bytes, C0/C1 control characters, byte-order marks and the
    Unicode replacement character (which appears when image bytes get
    decoded as text). Tabs/newlines and emoji are kept — an emoticon that
    copied as text should still be spoken.
    """
    if not text:
        return ""
    cleaned = []
    for ch in text:
        if ch in "\t\n\r":
            cleaned.append(ch)
        elif ch == "\ufeff" or ch == "\ufffd":
            continue  # BOM / binary-decode marker
        elif ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F:
            continue  # control characters / C1 range
        else:
            cleaned.append(ch)
    return "".join(cleaned).strip()


def _enum_clipboard_formats() -> List[int]:
    """List the clipboard's current format ids (clipboard must be open)."""
    formats = []
    fmt = 0
    while True:
        fmt = _user32.EnumClipboardFormats(fmt)
        if fmt == 0:
            break
        formats.append(fmt)
    return formats


def _format_name(fmt: int) -> str:
    """Registered name of a custom clipboard format ("" for standard ids)."""
    buf = ctypes.create_unicode_buffer(256)
    n = _user32.GetClipboardFormatNameW(fmt, buf, 256)
    return buf.value if n else ""


def _read_format(fmt: int) -> bytes:
    """Raw bytes of a clipboard format (clipboard must be open)."""
    handle = _user32.GetClipboardData(fmt)
    if not handle:
        return b""
    size = _kernel32.GlobalSize(handle)
    if size <= 0 or size > 64 * 1024 * 1024:
        return b""
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        return b""
    try:
        buf = ctypes.create_string_buffer(size)
        ctypes.memmove(buf, ptr, size)
        return buf.raw
    finally:
        _kernel32.GlobalUnlock(handle)


def _extract_html_text(raw: bytes) -> str:
    """Strip the CF_HTML wrapper/tags and return the visible text."""
    text = raw.decode("utf-8", errors="replace")
    # CF_HTML starts with a header like "Version:0.9\nStartHTML:71\n...".
    start = end = None
    for line in text.splitlines()[:12]:
        low = line.lower()
        if low.startswith("starthtml:"):
            try:
                start = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif low.startswith("endhtml:"):
            try:
                end = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    if start is not None and end is not None:
        if not (0 <= start < end <= len(text)):
            # Malformed CF_HTML — the header lines would be read aloud as
            # literal text. Better to report no text than speak that junk.
            return ""
        text = text[start:end]
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def _extract_rtf_text(raw: bytes) -> str:
    """Best-effort plain text from an RTF stream (control words removed)."""
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", text)   # \'hh escapes
    text = re.sub(r"\\[a-zA-Z]+\-?\d* ?", "", text)  # \controlword
    text = text.replace("{", "").replace("}", "")
    return " ".join(text.split())


def _decode_format(fmt: int, raw: bytes, name: str = "") -> str:
    """Decode raw clipboard bytes for ``fmt`` into text ("" if none)."""
    if fmt == _CF_UNICODETEXT:
        # Strip the trailing NUL terminator as full U+0000 code units.
        # (Splitting on b"\x00\x00" is unsafe: a char < 0x100 followed by a
        # NUL creates a false "00 00" pair across the boundary.)
        data = raw
        while data.endswith(b"\x00\x00"):
            data = data[:-2]
        if len(data) % 2:  # defensive: drop a stray leftover byte
            data = data[:-1]
        return data.decode("utf-16-le", errors="replace").strip()
    if fmt in (_CF_TEXT, _CF_OEMTEXT):
        return raw.split(b"\x00")[0].decode("mbcs", errors="replace").strip()
    low = name.lower()
    if "html" in low:
        return _extract_html_text(raw)
    if "rtf" in low:
        return _extract_rtf_text(raw)
    return ""


def _read_clipboard_ctypes() -> str:
    """Extract text from the clipboard via Win32 formats (ctypes fallback).

    Used when the clipboard has no CF_UNICODETEXT — a selection that
    included an image may still be stored as HTML or RTF. Never raises.
    """
    if ctypes is None:
        return ""
    if not _user32.OpenClipboard(None):
        return ""
    try:
        formats = _enum_clipboard_formats()
        # Explicit text formats first, in preference order.
        for fmt in (_CF_UNICODETEXT, _CF_TEXT, _CF_OEMTEXT):
            if fmt in formats:
                decoded = _decode_format(fmt, _read_format(fmt))
                if decoded:
                    return decoded
        # Then registered formats with a text-ish name (HTML / RTF).
        for fmt in formats:
            if fmt >= 0xC000:
                name = _format_name(fmt).lower()
                if any(m in name for m in _TEXT_FORMAT_MARKERS):
                    decoded = _decode_format(fmt, _read_format(fmt), name)
                    if decoded:
                        return decoded
        return ""
    finally:
        _user32.CloseClipboard()


def clipboard_has_image() -> bool:
    """True when the clipboard currently holds an image (no text).

    Used after a copy attempt to distinguish "nothing was copied" from
    "an image (e.g. a Discord sticker) was copied".
    """
    if ctypes is None:
        return False
    if not _user32.OpenClipboard(None):
        return False
    try:
        for fmt in _enum_clipboard_formats():
            if fmt in _IMAGE_STANDARD_FORMATS:
                return True
            if fmt >= 0xC000:
                name = _format_name(fmt).lower()
                if any(m in name for m in _IMAGE_FORMAT_MARKERS):
                    return True
        return False
    finally:
        _user32.CloseClipboard()


def _make_sentinel() -> str:
    """Return a random marker string that will never collide with real text."""
    alphabet = string.ascii_letters + string.digits
    return "READOUT_COPY_" + "".join(random.choices(alphabet, k=14))


def release_modifiers() -> None:
    """Force-release modifier keys that are still physically held down.

    The toggle hotkey (e.g. Ctrl+Alt+Space) fires while Ctrl/Alt are held,
    so a simulated Ctrl+C would otherwise arrive as Ctrl+Alt+C. Releasing
    the modifiers first makes the copy land reliably in every app.
    """
    for key in ("ctrl", "alt", "shift", "windows"):
        try:
            keyboard.release(key)
        except Exception:
            pass


def _simulate_copy(shortcut: str) -> None:
    """Press the given copy shortcut (e.g. ``ctrl+c``, ``ctrl+insert``).

    ``shortcut`` is a ``+``-joined combo like ``ctrl+shift+c``; the last
    part is the main key and everything before it is held as modifiers.
    """
    parts = shortcut.split("+")
    mods, key = parts[:-1], parts[-1]
    for mod in mods:
        pyautogui.keyDown(mod)
    time.sleep(0.02)
    pyautogui.keyDown(key)
    time.sleep(0.02)
    pyautogui.keyUp(key)
    time.sleep(0.02)
    for mod in reversed(mods):
        pyautogui.keyUp(mod)
    time.sleep(0.02)


def is_legacy_terminal_focused() -> bool:
    """True when the foreground window is a legacy console (conhost/ConEmu).

    Legacy consoles have no keyboard copy shortcut, so the sentinel trick
    can't work there — Readout instead reads the clipboard, which QuickEdit
    mode fills on mouse-release.
    """
    if ctypes is None:  # pragma: no cover - non-Windows
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        cls = buf.value.lower()
        return any(m in cls for m in _LEGACY_TERMINAL_CLASS_MARKERS)
    except Exception:
        return False


def _grab_from_terminal_clipboard(max_text_length: int = 8000) -> str:
    """Return text already copied to the clipboard by a legacy console.

    Never writes the clipboard or simulates keys here: writing a sentinel
    would destroy the QuickEdit auto-copied selection, and sending Ctrl+C
    would interrupt the CLI app running in the console.
    """
    text = sanitize_text(_read_clipboard())
    if not text:
        return ""
    return text[:max_text_length]


def _read_clipboard() -> str:
    """Read the clipboard as text; "" if unavailable or not text.

    Tries the plain-text format first (CF_UNICODETEXT via pyperclip), then
    falls back to enumerating the clipboard's formats and extracting text
    from ANSI, HTML or RTF.  Apps like Discord/browsers can put a selection
    that includes an image on the clipboard *without* a plain-text format —
    the fallback still finds the readable text.
    """
    try:
        text = pyperclip.paste()
    except Exception:
        text = ""
    text = sanitize_text(text or "")
    if text:
        return text
    return sanitize_text(_read_clipboard_ctypes())


def _write_clipboard(text: str) -> bool:
    """Write text to the clipboard. Returns False if the clipboard is locked."""
    try:
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def grab_selected_text(
    timeout: float = 1.0,
    poll_interval: float = 0.05,
    max_text_length: int = 8000,
    in_legacy_terminal: Optional[bool] = None,
    info: Optional[dict] = None,
) -> str:
    """Copy the active selection and return it as a string.

    Returns "" when nothing is selected, the clipboard is unavailable, or
    the focused app doesn't support programmatic copying. The user's
    clipboard is always restored, no matter what happens.

    ``in_legacy_terminal`` lets callers pass the (already checked) foreground
    state so the grab and the caller's messaging always agree on which path
    ran; when None it is detected here.

    ``info`` (optional): a dict the caller passes in; on return it may carry
    ``info["image_only"] = True`` when a copy *did* happen but the selection
    contained no readable text (e.g. a sticker/emoticon image), so the UI can
    tell the two "no text" cases apart.
    """
    # Legacy consoles can't copy via keyboard and QuickEdit already put the
    # selection on the clipboard — read it without clobbering anything.
    if in_legacy_terminal is None:
        in_legacy_terminal = is_legacy_terminal_focused()
    if in_legacy_terminal:
        return _grab_from_terminal_clipboard(max_text_length)

    original = _read_clipboard()
    sentinel = _make_sentinel()

    if not _write_clipboard(sentinel):
        return ""

    text = ""
    image_copy = False
    try:
        release_modifiers()
        time.sleep(0.05)

        try:
            # Try each copy shortcut, stopping as soon as one produced text.
            # A short poll after each catches apps that update the clipboard
            # a few milliseconds after the keystroke.
            for shortcut in _COPY_SHORTCUTS:
                _simulate_copy(shortcut)
                time.sleep(0.1)
                current = _read_clipboard()
                if current and current != sentinel:
                    text = current
                    break

            if not text:
                # Some apps copy asynchronously with a longer delay — give
                # them the full polling window before giving up.
                deadline = time.time() + timeout
                while time.time() < deadline:
                    current = _read_clipboard()
                    if current and current != sentinel:
                        text = current
                        break
                    time.sleep(poll_interval)
        except Exception:
            # Copy simulation failed (e.g. keyboard blocked) — treat as no selection.
            text = ""

        # A copy may still have happened even though no text came back — e.g.
        # a sticker/emoticon image replaced our sentinel. Detect that BEFORE
        # the user's clipboard is restored below.
        if not text and clipboard_has_image():
            image_copy = True
    finally:
        _write_clipboard(original)

    text = sanitize_text(text or "")
    if not text:
        if info is not None and image_copy:
            info["image_only"] = True
        return ""
    return text[:max_text_length]
