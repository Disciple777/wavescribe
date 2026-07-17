"""SubtitleForge — AI-powered subtitle generation from video files.

Generates .srt and .vtt subtitle files from any video using
the OpenAI Whisper API or a local Whisper model.

Usage:
    python subtitleforge/main.py
"""

import os
import sys

# Add project root to path so we can import shared modules from app/
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import customtkinter as ctk

from app.theme import register_bundled_font
from app.config import load_config


def main() -> None:
    """Start the SubtitleForge application."""

    # ── Set theme ──
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    # ── Register bundled fonts ──
    register_bundled_font()

    # ── Launch GUI ──
    from subtitleforge.gui import SubtitleForgeApp

    app = SubtitleForgeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
