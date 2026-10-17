"""Grab the currently selected text from *any* Windows application.

Approach
--------
The only portable way to read a text selection from an arbitrary app is to
make the OS copy it for us:

  1. Save the user's clipboard.
  2. Put a unique random *sentinel* string on the clipboard.
  3. Simulate a copy shortcut, trying several in order:

       - Ctrl+C  — works in every normal app; in Windows Terminal it
         copies when there *is* a selection (and is passed through to the
         shell as an interrupt when there isn't).
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

# Copy shortcuts tried in order.
_COPY_SHORTCUTS = ("ctrl+c", "ctrl+insert", "ctrl+shift+c")

# Window classes that cannot copy text via any keyboard shortcut:
_LEGACY_TERMINAL_CLASS_MARKERS = ("consolewindowclass", "virtualconsoleclass")

# ════════════════════════════════════════════════════════════════
#  Text sanitizing + Win32 clipboard-format reading
# ════════════════════════════════════════════════════════════════

_CF_TEXT = 1
_CF_UNICODETEXT = 13
_CF_OEMTEXT = 7

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
    """Strip binary/control junk from grabbed text while keeping real text."""
    if not text:
        return ""
    cleaned = []
    for ch in text:
        if ch in "\t\n\r":
            cleaned.append(ch)
        elif ch == "\ufeff" or ch == "\ufffd":
            continue
        elif ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F:
            continue
        else:
            cleaned.append(ch)
    return "".join(cleaned).strip()


def _enum_clipboard_formats() -> List[int]:
    formats = []
    fmt = 0
    while True:
        fmt = _user32.EnumClipboardFormats(fmt)
        if fmt == 0:
            break
        formats.append(fmt)
    return formats


def _format_name(fmt: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    n = _user32.GetClipboardFormatNameW(fmt, buf, 256)
    return buf.value if n else ""


def _read_format(fmt: int) -> bytes:
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
    text = raw.decode("utf-8", errors="replace")
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
            return ""
        text = text[start:end]
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def _extract_rtf_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", text)
    text = re.sub(r"\\[a-zA-Z]+\-?\d* ?", "", text)
    text = text.replace("{", "").replace("}", "")
    return " ".join(text.split())


def _decode_format(fmt: int, raw: bytes, name: str = "") -> str:
    if fmt == _CF_UNICODETEXT:
        data = raw
        while data.endswith(b"\x00\x00"):
            data = data[:-2]
        if len(data) % 2:
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
    if ctypes is None:
        return ""
    if not _user32.OpenClipboard(None):
        return ""
    try:
        formats = _enum_clipboard_formats()
        for fmt in (_CF_UNICODETEXT, _CF_TEXT, _CF_OEMTEXT):
            if fmt in formats:
                decoded = _decode_format(fmt, _read_format(fmt))
                if decoded:
                    return decoded
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
    if ctypes is None:
        return False
    if not _user32.OpenClipboard(None):
        return False
    try:
        _IMAGE_STANDARD_FORMATS = frozenset({2, 6, 8, 9, 14, 17})
        for fmt in _enum_clipboard_formats():
            if fmt in _IMAGE_STANDARD_FORMATS:
                return True
            if fmt >= 0xC000:
                name = _format_name(fmt).lower()
                _IMAGE_FORMAT_MARKERS = (
                    "png", "jpeg", "jpg", "jfif", "gif", "webp", "tiff", "bitmap",
                    "dib", "emf", "wmf", "image/", "system.drawing.bitmap",
                )
                if any(m in name for m in _IMAGE_FORMAT_MARKERS):
                    return True
        return False
    finally:
        _user32.CloseClipboard()


def _make_sentinel() -> str:
    alphabet = string.ascii_letters + string.digits
    return "WAVESCRIBE_COPY_" + "".join(random.choices(alphabet, k=14))


def release_modifiers() -> None:
    for key in ("ctrl", "alt", "shift", "windows"):
        try:
            keyboard.release(key)
        except Exception:
            pass


def _simulate_copy(shortcut: str) -> None:
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
    if ctypes is None:
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
    text = sanitize_text(_read_clipboard())
    if not text:
        return ""
    return text[:max_text_length]


def _read_clipboard() -> str:
    try:
        text = pyperclip.paste()
    except Exception:
        text = ""
    text = sanitize_text(text or "")
    if text:
        return text
    return sanitize_text(_read_clipboard_ctypes())


def _write_clipboard(text: str) -> bool:
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
    """
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
            for shortcut in _COPY_SHORTCUTS:
                _simulate_copy(shortcut)
                time.sleep(0.1)
                current = _read_clipboard()
                if current and current != sentinel:
                    text = current
                    break

            if not text:
                deadline = time.time() + timeout
                while time.time() < deadline:
                    current = _read_clipboard()
                    if current and current != sentinel:
                        text = current
                        break
                    time.sleep(poll_interval)
        except Exception:
            text = ""

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
