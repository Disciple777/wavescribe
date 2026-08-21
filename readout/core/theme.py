"""Centralised colour theme and font configuration for Readout.

A self-contained copy of the WaveScribe theme so Readout stays an
independent project. Every UI element references the constants below;
edit this single file to restyle the whole app.

Assets (Poppins / Montserrat .ttf) live in the parent project's
``assets/`` folder and are loaded privately at runtime via GDI.
"""

# ═══════════════════════════════════════════════════════════════
#  Font Configuration
# ═══════════════════════════════════════════════════════════════

FONT_PREFERRED = "Poppins"
FONT_FALLBACK = "Segoe UI"
FONT_FAMILY: str = FONT_PREFERRED

# Semantic font map — each value is a (family, size, [weight]) tuple.
FONTS: dict[str, tuple[str, int | str, ...]] = {
    "icon": (FONT_FAMILY, 16),
    "title": (FONT_FAMILY, 18, "bold"),
    "body": (FONT_FAMILY, 13),
    "body_bold": (FONT_FAMILY, 13, "bold"),
    "body_large": (FONT_FAMILY, 14),
    "body_large_bold": (FONT_FAMILY, 14, "bold"),
    "body_small": (FONT_FAMILY, 11),
    "body_small_bold": (FONT_FAMILY, 11, "bold"),
    "body_medium": (FONT_FAMILY, 12),
    "body_medium_bold": (FONT_FAMILY, 12, "bold"),
}

# ── Bundled Font Registration ──

import os as _os
import ctypes as _ctypes

_FONT_REGISTERED = False


def register_bundled_font() -> None:
    """Register bundled Poppins .ttf via Windows GDI at runtime (private).

    Safe to call multiple times; failures fall back to system fonts.
    """
    global _FONT_REGISTERED  # noqa: PLW0603
    if _FONT_REGISTERED:
        return

    try:
        # Project root = two levels up from this file (readout/core/theme.py)
        project_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        assets_dir = _os.path.join(project_root, "assets")
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
        pass  # fall back to system fonts


def resolve_font_family() -> str:
    """Detect whether the preferred font is installed (needs a Tk root)."""
    import tkinter.font as tkfont  # noqa: PLC0415

    try:
        available = set(tkfont.families())
        return FONT_PREFERRED if FONT_PREFERRED in available else FONT_FALLBACK
    except Exception:
        return FONT_FALLBACK


def init_fonts() -> None:
    """Resolve the font family and update all font tuples. Call after Tk root exists."""
    register_bundled_font()
    family = resolve_font_family()
    global FONT_FAMILY  # noqa: PLW0603
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
    "green": "#74ffae",           # idle / success
    "red": "#ff7f7f",             # error / stop
    "yellow": "#ffd016",          # busy / reading

    # ── Window / background ──
    "bg": "#212121",
    "card": "#2b2b2b",
    "border": "#3a3a3a",

    # ── Accent ──
    "accent": "#401cb6",
    "accent_hover": "#5e29da",

    # ── Text ──
    "text": "#ffffff",
    "text_dim": "#d8d8d8",

    # ── Input fields ──
    "input_bg": "#1a1a1a",

    # ── Button variants ──
    "green_hover": "#37f385",
    "red_hover": "#ff897c",
    "success": "#b0ffc9",
    "success_hover": "#5cff90",
}

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
