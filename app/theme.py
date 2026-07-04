"""Centralized colour theme and font configuration for WaveScribe.

All UI colours and fonts are defined here so you can customise the look
of the entire application by editing a single file.

Usage:
    from app.theme import COLORS, FONTS

    button.configure(fg_color=COLORS["accent"])
    label.configure(font=FONTS["body"])
"""

# ═══════════════════════════════════════════════════════════════
#  Font Configuration
# ═══════════════════════════════════════════════════════════════

# Preferred font family. Change this to any font installed on your system.
FONT_PREFERRED = "Poppins"

# Fallback if the preferred font is not installed on the user's machine.
FONT_FALLBACK = "Segoe UI"

# Placeholder — resolved at runtime after the Tk root window exists.
# (tkfont.families() requires a running Tk instance, so we defer
# the actual detection to resolve_font_family().)
FONT_FAMILY: str = FONT_PREFERRED

# Semantic font map — every text element in the app references one of these.
# Each value is a tuple of (family, size, [weight]) as expected by tkinter.
# Built with FONT_PREFERRED as default; updated in-place after Tk root exists.
FONTS: dict[str, tuple[str, int | str, ...]] = {
    "icon": (FONT_FAMILY, 16),                # status dot
    "title": (FONT_FAMILY, 18, "bold"),       # window title
    "body": (FONT_FAMILY, 13),                # general body text
    "body_bold": (FONT_FAMILY, 13, "bold"),   # emphasised body (overlay)
    "body_large": (FONT_FAMILY, 14),          # larger body, buttons (record/stop/clear)
    "body_large_bold": (FONT_FAMILY, 14, "bold"),  # section headers
    "body_small": (FONT_FAMILY, 11),          # secondary labels, footer, small buttons
    "body_small_bold": (FONT_FAMILY, 11, "bold"),  # (unused — kept for compat)
    "body_medium": (FONT_FAMILY, 12),         # mode/model segmented, entries, switches, buttons
    "body_medium_bold": (FONT_FAMILY, 12, "bold"), # (unused — kept for compat)
}

# ── Bundled Font Registration ──

import os as _os
import ctypes as _ctypes

_FONT_REGISTERED = False


def register_bundled_font() -> None:
    """Register bundled Poppins .ttf via Windows GDI at runtime.

    Uses ``AddFontResourceExW`` with the ``FR_PRIVATE`` flag so the
    font is loaded **only** for the current process and is **not**
    installed system-wide. This means every user will see Poppins
    without needing to install it manually.

    Must be called **after** the Tk root exists.
    """
    global _FONT_REGISTERED  # noqa: PLW0603
    if _FONT_REGISTERED:
        return

    try:
        assets_dir = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "assets",
        )
        font_files = [
            _os.path.join(assets_dir, "Montserrat-Regular.ttf"),
            _os.path.join(assets_dir, "Montserrat-Bold.ttf"),
            _os.path.join(assets_dir, "Poppins-Regular.ttf"),
            _os.path.join(assets_dir, "Poppins-Bold.ttf"),
        ]

        gdi32 = _ctypes.windll.gdi32
        FR_PRIVATE = 0x10

        for path in font_files:
            if _os.path.exists(path):
                gdi32.AddFontResourceExW(path, FR_PRIVATE, 0)

        _FONT_REGISTERED = True
    except Exception:
        pass  # If registration fails, fall back to system fonts


def resolve_font_family() -> str:
    """Detect which font family is available at runtime.

    Must be called **after** the Tk root window exists
    (i.e. after ``super().__init__()`` in the App constructor),
    because ``tkfont.families()`` requires a running Tk interpreter.

    Checks whether *FONT_PREFERRED* (Poppins) is installed; if not,
    falls back to *FONT_FALLBACK* (Segoe UI) which ships with Windows.
    """
    import tkinter.font as tkfont  # noqa: PLC0415 — deferred import

    try:
        available = set(tkfont.families())
        return FONT_PREFERRED if FONT_PREFERRED in available else FONT_FALLBACK
    except Exception:
        return FONT_FALLBACK


def init_fonts() -> None:
    """Resolve the actual font family and update all font tuples in-place.

    Call this once from ``App.__init__`` after the Tk root exists.
    """
    register_bundled_font()
    family = resolve_font_family()
    global FONT_FAMILY  # noqa: PLW0603 — intentional module-level mutation
    FONT_FAMILY = family

    for key in FONTS:
        val = list(FONTS[key])
        val[0] = family
        FONTS[key] = tuple(val)


# ═══════════════════════════════════════════════════════════════
#  Colour Configuration
# ═══════════════════════════════════════════════════════════════

COLORS: dict[str, str] = {
    # ── Status indicators ──
    "green": "#74ffae",           # Idle dot, success feedback, record button
    "red": "#ff7f7f",             # Error dot, stop button, recording indicator
    "yellow": "#ffd016",          # Transcribing dot

    # ── Window / background ──
    "bg": "#212121",              # Main window background (dark charcoal)
    "card": "#2b2b2b",            # Card / panel background (slightly lighter)
    "border": "#3a3a3a",          # Card borders and separators

    # ── Accent (buttons, toggles) ──
    "accent": "#401cb6",          # Primary blue accent
    "accent_hover": "#5e29da",    # Accent hover state (darker blue)

    # ── Text ──
    "text": "#ffffff",            # Primary text (white)
    "text_dim": "#d8d8d8",        # Secondary / label text (gray)

    # ── Input fields ──
    "input_bg": "#1a1a1a",        # Textbox / entry background

    # ── Button variants ──
    "green_hover": "#37f385",      # Record button hover
    "red_hover": "#ff897c",        # Stop button hover
    "success": "#b0ffc9",         # Success/save button background
    "success_hover": "#5cff90",   # Success/save button hover

}

# ── Convenience module-level constants (kept for backward compat) ──

COLOR_GREEN = COLORS["green"]
COLOR_RED = COLORS["red"]
COLOR_YELLOW = COLORS["yellow"]
COLOR_BG = COLORS["bg"]
COLOR_CARD = COLORS["card"]
COLOR_BORDER = COLORS["border"]
COLOR_ACCENT = COLORS["accent"]
COLOR_ACCENT_HOVER = COLORS["accent_hover"]
COLOR_TEXT = COLORS["text"]
COLOR_TEXT_DIM = COLORS["text_dim"]
COLOR_INPUT_BG = COLORS["input_bg"]
COLOR_GREEN_HOVER = COLORS["green_hover"]
COLOR_RED_HOVER = COLORS["red_hover"]
COLOR_SUCCESS = COLORS["success"]
COLOR_SUCCESS_HOVER = COLORS["success_hover"]
