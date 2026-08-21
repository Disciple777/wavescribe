"""Tests for readout/core/selection.py (mocked clipboard & keyboard)."""

from readout.core import selection


class FakeClipboard:
    """In-memory clipboard that can also simulate being locked."""

    def __init__(self) -> None:
        self._data = ""
        self.locked = False

    def paste(self) -> str:
        if self.locked:
            raise RuntimeError("clipboard locked by another app")
        return self._data

    def copy(self, text: str) -> bool:
        if self.locked:
            raise RuntimeError("clipboard locked by another app")
        self._data = text
        return True


def _install_mocks(monkeypatch, clip: FakeClipboard, copy_fn):
    def safe_read() -> str:
        try:
            return clip.paste() or ""
        except Exception:
            return ""

    def safe_write(text: str) -> bool:
        try:
            clip.copy(text)
            return True
        except Exception:
            return False

    monkeypatch.setattr(selection, "_read_clipboard", safe_read)
    monkeypatch.setattr(selection, "_write_clipboard", safe_write)
    monkeypatch.setattr(selection, "_simulate_copy", copy_fn)
    monkeypatch.setattr(selection, "release_modifiers", lambda: None)
    # Force the normal (non-legacy-console) path regardless of which window
    # happens to be focused when the tests run.
    monkeypatch.setattr(selection, "is_legacy_terminal_focused", lambda: False)
    # Grab now probes for image-only copies; keep tests hermetic.
    monkeypatch.setattr(selection, "clipboard_has_image", lambda: False)


def test_grabs_text_and_restores_clipboard(monkeypatch):
    clip = FakeClipboard()
    clip.copy("original content")
    copies = []

    def fake_copy(shortcut: str) -> None:
        copies.append(shortcut)
        clip._data = "Hello selected world"  # the app copies the selection

    _install_mocks(monkeypatch, clip, fake_copy)
    result = selection.grab_selected_text(timeout=0.2)
    assert result == "Hello selected world"
    assert clip._data == "original content"  # user clipboard restored
    assert copies == ["ctrl+c"]


def test_no_selection_returns_empty_and_restores(monkeypatch):
    clip = FakeClipboard()
    clip.copy("original")
    _install_mocks(monkeypatch, clip, lambda s: None)  # copy never changes clipboard

    result = selection.grab_selected_text(timeout=0.15, poll_interval=0.01)
    assert result == ""
    assert clip._data == "original"


def test_ctrl_insert_fallback_for_terminals(monkeypatch):
    clip = FakeClipboard()
    clip.copy("orig")
    copies = []

    def fake_copy(shortcut: str) -> None:
        copies.append(shortcut)
        if shortcut == "ctrl+insert":
            clip._data = "terminal text"

    _install_mocks(monkeypatch, clip, fake_copy)
    result = selection.grab_selected_text(timeout=0.2, poll_interval=0.01)
    assert result == "terminal text"
    assert copies == ["ctrl+c", "ctrl+insert"]
    assert clip._data == "orig"


def test_ctrl_shift_c_third_fallback_for_windows_terminal(monkeypatch):
    clip = FakeClipboard()
    clip.copy("orig")
    copies = []

    def fake_copy(shortcut: str) -> None:
        copies.append(shortcut)
        if shortcut == "ctrl+shift+c":
            clip._data = "windows terminal text"

    _install_mocks(monkeypatch, clip, fake_copy)
    result = selection.grab_selected_text(timeout=0.2, poll_interval=0.01)
    assert result == "windows terminal text"
    assert copies == ["ctrl+c", "ctrl+insert", "ctrl+shift+c"]
    assert clip._data == "orig"


def test_simulate_copy_holds_all_modifiers(monkeypatch):
    events = []
    monkeypatch.setattr(selection.pyautogui, "keyDown", lambda k: events.append(("down", k)))
    monkeypatch.setattr(selection.pyautogui, "keyUp", lambda k: events.append(("up", k)))

    selection._simulate_copy("ctrl+shift+c")
    assert events == [
        ("down", "ctrl"),
        ("down", "shift"),
        ("down", "c"),
        ("up", "c"),
        ("up", "shift"),
        ("up", "ctrl"),
    ]


def test_clipboard_restored_even_when_copy_throws(monkeypatch):
    clip = FakeClipboard()
    clip.copy("precious")

    def boom(_shortcut: str) -> None:
        raise RuntimeError("copy failed")

    _install_mocks(monkeypatch, clip, boom)
    result = selection.grab_selected_text(timeout=0.1)
    assert result == ""
    assert clip._data == "precious"


def test_locked_clipboard_returns_empty(monkeypatch):
    clip = FakeClipboard()
    clip.locked = True
    _install_mocks(monkeypatch, clip, lambda s: None)
    assert selection.grab_selected_text() == ""


def test_long_text_is_capped(monkeypatch):
    clip = FakeClipboard()
    clip.copy("x")
    long_text = "word " * 5000

    def fake_copy(_shortcut: str) -> None:
        clip._data = long_text

    _install_mocks(monkeypatch, clip, fake_copy)
    result = selection.grab_selected_text(timeout=0.2, max_text_length=100)
    assert len(result) == 100


def test_release_modifiers_releases_all(monkeypatch):
    released = []
    monkeypatch.setattr(selection.keyboard, "release", lambda k: released.append(k))
    selection.release_modifiers()
    assert released == ["ctrl", "alt", "shift", "windows"]


def test_sentinel_is_random():
    assert selection._make_sentinel() != selection._make_sentinel()
    assert selection._make_sentinel().startswith("READOUT_COPY_")


def test_copy_shortcuts_chain_order():
    assert selection._COPY_SHORTCUTS == ("ctrl+c", "ctrl+insert", "ctrl+shift+c")


def test_legacy_terminal_reads_clipboard_without_touching_keyboard(monkeypatch):
    """In a legacy console the selection is auto-copied on mouse-release, so
    grab must read the clipboard directly — no sentinel, no simulated keys,
    no Ctrl+C (which would interrupt the running CLI)."""
    clip = FakeClipboard()
    clip.copy("text auto-copied by the terminal")
    actions = []

    monkeypatch.setattr(selection, "is_legacy_terminal_focused", lambda: True)
    monkeypatch.setattr(selection, "_read_clipboard", clip.paste)
    monkeypatch.setattr(selection, "_write_clipboard", lambda t: actions.append(("write", t)) or True)
    monkeypatch.setattr(selection, "_simulate_copy", lambda s: actions.append(("copy", s)))
    monkeypatch.setattr(selection, "release_modifiers", lambda: actions.append(("release",)))

    result = selection.grab_selected_text(timeout=0.1)
    assert result == "text auto-copied by the terminal"
    assert actions == []  # clipboard untouched, no keys simulated
    assert clip._data == "text auto-copied by the terminal"


def test_legacy_terminal_with_empty_clipboard_returns_empty(monkeypatch):
    clip = FakeClipboard()
    monkeypatch.setattr(selection, "is_legacy_terminal_focused", lambda: True)
    monkeypatch.setattr(selection, "_read_clipboard", clip.paste)
    assert selection.grab_selected_text(timeout=0.1) == ""


def test_terminal_detection_is_safe_without_ctypes(monkeypatch):
    monkeypatch.setattr(selection, "ctypes", None)
    assert selection.is_legacy_terminal_focused() is False


def test_explicit_terminal_hint_overrides_detection(monkeypatch):
    """Callers can pass the foreground state so grab and messaging agree."""
    clip = FakeClipboard()
    clip.copy("terminal text")
    monkeypatch.setattr(selection, "is_legacy_terminal_focused", lambda: False)
    monkeypatch.setattr(selection, "_read_clipboard", clip.paste)

    result = selection.grab_selected_text(in_legacy_terminal=True)
    assert result == "terminal text"  # took the clipboard path anyway
    assert clip._data == "terminal text"  # and never clobbered it


# ── Sanitizing & format-aware clipboard reading ──

def test_sanitize_text_strips_binary_junk():
    assert selection.sanitize_text("ab\x00\x01cd\x7f\x9fef") == "abcdef"
    assert selection.sanitize_text("hi\ufffdthere") == "hithere"
    assert selection.sanitize_text("\ufeffHello world") == "Hello world"
    assert selection.sanitize_text("line1\nline2\ttab") == "line1\nline2\ttab"
    assert selection.sanitize_text("  \x00\x01  ") == ""
    # Emoji are legitimate text — they must survive.
    assert selection.sanitize_text("Nice 👍 job") == "Nice 👍 job"


def test_extract_html_text_strips_wrapper_and_tags():
    body = (
        b"<html><body><!--StartFragment--><b>Hello</b> "
        b"<img src='x.png'> <i>world</i><!--EndFragment--></body></html>"
    )
    # CF_HTML header: body starts at offset 40 here.
    header = b"Version:0.9\r\nStartHTML:40\r\nEndHTML:%d\r\n" % (40 + len(body))
    assert selection._extract_html_text(header + body) == "Hello world"


def test_extract_rtf_text_basic():
    raw = b"{\\rtf1\\ansi\\f0 Hello \\b world \\par }"
    assert selection._extract_rtf_text(raw) == "Hello world"


def test_decode_unicode_and_ansi_formats():
    assert selection._decode_format(13, "héllo\x00\x00".encode("utf-16-le")) == "héllo"
    assert selection._decode_format(1, b"plain\x00") == "plain"
    assert selection._decode_format(13, b"") == ""


def test_read_clipboard_sanitizes_and_uses_fallback(monkeypatch):
    monkeypatch.setattr(selection.pyperclip, "paste", lambda: "ab\x00\x01cd")
    assert selection._read_clipboard() == "abcd"

    def raise_locked():
        raise RuntimeError("no text format")

    monkeypatch.setattr(selection.pyperclip, "paste", raise_locked)
    monkeypatch.setattr(selection, "_read_clipboard_ctypes", lambda: "fallback text")
    assert selection._read_clipboard() == "fallback text"


def test_ctypes_readers_safe_without_ctypes(monkeypatch):
    monkeypatch.setattr(selection, "ctypes", None)
    assert selection._read_clipboard_ctypes() == ""
    assert selection.clipboard_has_image() is False


def test_image_only_selection_reports_image_flag(monkeypatch):
    """Copying a sticker/emoticon puts an image on the clipboard: no text is
    returned, but the caller learns a copy DID happen (image_only=True)."""
    clip = FakeClipboard()
    clip.copy("orig")

    def fake_copy(_shortcut: str) -> None:
        clip._data = ""  # an image was copied — the clipboard has no text

    _install_mocks(monkeypatch, clip, fake_copy)
    monkeypatch.setattr(selection, "clipboard_has_image", lambda: True)

    info: dict = {}
    result = selection.grab_selected_text(timeout=0.1, poll_interval=0.01, info=info)
    assert result == ""
    assert info.get("image_only") is True
    assert clip._data == "orig"  # user clipboard restored


def test_no_selection_does_not_report_image(monkeypatch):
    """When nothing was copied the sentinel stays on the clipboard, so the
    image probe reports False and no image flag is set."""
    clip = FakeClipboard()
    clip.copy("orig")
    _install_mocks(monkeypatch, clip, lambda s: None)
    monkeypatch.setattr(selection, "clipboard_has_image", lambda: False)

    info: dict = {}
    result = selection.grab_selected_text(timeout=0.1, poll_interval=0.01, info=info)
    assert result == ""
    assert "image_only" not in info


def test_grabbed_text_is_sanitized(monkeypatch):
    clip = FakeClipboard()
    clip.copy("orig")

    def fake_copy(_shortcut: str) -> None:
        clip._data = "Hello\x00\x01world\ufffd"

    _install_mocks(monkeypatch, clip, fake_copy)
    result = selection.grab_selected_text(timeout=0.1)
    assert result == "Helloworld"
    assert clip._data == "orig"


def test_clipboard_has_image_detection(monkeypatch):
    """Standard image ids and registered image names are recognized as
    image-only selections; text formats are not."""
    monkeypatch.setattr(selection._user32, "OpenClipboard", lambda _: 1)
    monkeypatch.setattr(selection._user32, "CloseClipboard", lambda: 0)

    # CF_UNICODETEXT (13) only → not an image.
    monkeypatch.setattr(selection, "_enum_clipboard_formats", lambda: [13])
    assert selection.clipboard_has_image() is False

    # CF_DIB (8) present → image.
    monkeypatch.setattr(selection, "_enum_clipboard_formats", lambda: [8, 13])
    assert selection.clipboard_has_image() is True

    # Registered format with an image-ish name → image.
    monkeypatch.setattr(selection, "_enum_clipboard_formats", lambda: [0xC001])
    monkeypatch.setattr(selection, "_format_name", lambda f: "PNG")
    assert selection.clipboard_has_image() is True

    # Registered format with a text-ish name → not an image.
    monkeypatch.setattr(selection, "_format_name", lambda f: "HTML Format")
    assert selection.clipboard_has_image() is False
