"""SubtitleForge GUI — beautiful CustomTkinter interface for subtitle generation.

Features:
  - Video file drag-and-drop / browse
  - Cloud (OpenAI Whisper API) and Local (openai-whisper) modes
  - API key management (persisted via shared config)
  - Language selection
  - Real-time progress bar
  - SRT preview with syntax highlighting
  - Save as .srt or .vtt
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Any, Dict, List, Optional

import customtkinter as ctk

# Shared WaveScribe modules
from app.theme import COLORS, FONTS, init_fonts, register_bundled_font
from app.config import load_config, save_config

# SubtitleForge modules
from subtitleforge.audio_extractor import (
    is_ffmpeg_available,
    ensure_ffmpeg as _ensure_ffmpeg,
    extract_audio_to_wav,
    get_ffmpeg_path,
    get_video_info,
    is_supported_video,
    AudioExtractionError,
    SUPPORTED_FORMATS,
)
from subtitleforge.transcriber import (
    transcribe_audio_timed,
    is_whisper_available,
    load_whisper_model,
    TranscriptionError,
)
from subtitleforge.srt_generator import (
    segments_to_srt,
    segments_to_vtt,
    estimate_subtitle_count,
)

# ── Color Constants (reuse WaveScribe theme) ──
COLOR_BG = COLORS["bg"]
COLOR_CARD = COLORS["card"]
COLOR_BORDER = COLORS["border"]
COLOR_ACCENT = COLORS["accent"]
COLOR_ACCENT_HOVER = COLORS["accent_hover"]
COLOR_TEXT = COLORS["text"]
COLOR_TEXT_DIM = COLORS["text_dim"]
COLOR_INPUT_BG = COLORS["input_bg"]
COLOR_GREEN = COLORS["green"]
COLOR_RED = COLORS["red"]
COLOR_YELLOW = COLORS["yellow"]
COLOR_SUCCESS = COLORS["success"]

# ── App Title ──
APP_TITLE = "SubtitleForge"
APP_VERSION = "1.0.0"

# ── Language options ──
LANGUAGES = {
    "Auto-detect": None,
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Italian": "it",
    "Portuguese": "pt",
    "Russian": "ru",
    "Japanese": "ja",
    "Korean": "ko",
    "Chinese (Simplified)": "zh",
    "Arabic": "ar",
    "Hindi": "hi",
    "Dutch": "nl",
    "Polish": "pl",
    "Turkish": "tr",
    "Vietnamese": "vi",
    "Thai": "th",
}


class SubtitleForgeApp(ctk.CTk):
    """Main application window for SubtitleForge."""

    def __init__(self) -> None:
        super().__init__()

        # ── Window setup ──
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry("820x680")  # shorter — scrollable if screen is small
        self.minsize(600, 480)
        self.configure(fg_color=COLOR_BG)

        # Center on screen
        self._center_window()

        # ── Fonts ──
        init_fonts()
        self._apply_fonts()

        # ── State ──
        self._video_path: Optional[str] = None
        self._segments: List[Dict[str, Any]] = []
        self._srt_content: str = ""
        self._config = load_config()
        self._api_key = self._config.get("api_key", "")
        self._is_processing = False
        self._ffmpeg_checked = False

        # ── Scrollable container (so all content is reachable on small screens) ──
        self._content = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=COLOR_ACCENT,
            scrollbar_button_hover_color=COLOR_ACCENT_HOVER,
        )
        self._content.pack(fill="both", expand=True)

        # ── Build UI (into the scrollable container) ──
        self._build_header()
        self._build_video_section()
        self._build_settings_section()
        self._build_progress_section()
        self._build_preview_section()
        self._build_action_buttons()

        # ── Keyboard shortcuts ──
        self.bind("<Control-o>", lambda e: self._on_select_video())
        self.bind("<Control-s>", lambda e: self._on_save_srt())
        self.bind("<Return>", lambda e: self._on_generate() if not self._is_processing else None)

        # ── Check ffmpeg on startup ──
        self.after(500, self._check_ffmpeg)

    # ── Window helpers ──

    def _center_window(self) -> None:
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        # Use +x+y to position without resizing (preserves the geometry from __init__)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _apply_fonts(self) -> None:
        """Apply fonts to the app."""
        self._font_title = ctk.CTkFont(**{
            "family": FONTS["title"][0], "size": FONTS["title"][1], "weight": "bold"})
        self._font_body = ctk.CTkFont(**{
            "family": FONTS["body"][0], "size": FONTS["body"][1]})
        self._font_body_bold = ctk.CTkFont(**{
            "family": FONTS["body_bold"][0], "size": FONTS["body_bold"][1], "weight": "bold"})
        self._font_large = ctk.CTkFont(**{
            "family": FONTS["body_large"][0], "size": FONTS["body_large"][1]})
        self._font_small = ctk.CTkFont(**{
            "family": FONTS["body_small"][0], "size": FONTS["body_small"][1]})
        self._font_medium = ctk.CTkFont(**{
            "family": FONTS["body_medium"][0], "size": FONTS["body_medium"][1]})
        self._font_mono = ctk.CTkFont(**{
            "family": "Consolas", "size": 12})

    # ── UI Builders ──

    def _build_header(self) -> None:
        """App header with title and subtitle."""
        header_frame = ctk.CTkFrame(self._content, fg_color="transparent")
        header_frame.pack(fill="x", padx=24, pady=(20, 8))

        # App icon and title
        title_label = ctk.CTkLabel(
            header_frame,
            text="🎬  SubtitleForge",
            font=self._font_title,
            text_color=COLOR_TEXT,
        )
        title_label.pack(side="left")

        # Version badge
        version_label = ctk.CTkLabel(
            header_frame,
            text=f"v{APP_VERSION}",
            font=self._font_small,
            text_color=COLOR_TEXT_DIM,
        )
        version_label.pack(side="left", padx=(8, 0), pady=(6, 0))

        # Subtitle
        subtitle_label = ctk.CTkLabel(
            self._content,
            text="Generate professional .srt subtitles from any video file",
            font=self._font_small,
            text_color=COLOR_TEXT_DIM,
            anchor="w",
        )
        subtitle_label.pack(fill="x", padx=24, pady=(0, 12))

        # Separator
        separator = ctk.CTkFrame(self._content, height=1, fg_color=COLOR_BORDER)
        separator.pack(fill="x", padx=24, pady=(0, 12))

    def _build_video_section(self) -> None:
        """Video file selection card."""
        card = ctk.CTkFrame(self._content, fg_color=COLOR_CARD, corner_radius=12)
        card.pack(fill="x", padx=24, pady=(0, 12))

        # Header
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 8))

        icon_label = ctk.CTkLabel(header, text="🎬", font=self._font_large)
        icon_label.pack(side="left", padx=(0, 8))

        title_label = ctk.CTkLabel(
            header, text="Video Selection",
            font=self._font_body_bold, text_color=COLOR_TEXT,
        )
        title_label.pack(side="left")

        # File info display
        self._file_info_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._file_info_frame.pack(fill="x", padx=16, pady=(0, 6))

        self._file_name_label = ctk.CTkLabel(
            self._file_info_frame,
            text="No video selected",
            font=self._font_medium,
            text_color=COLOR_TEXT_DIM,
            anchor="w",
        )
        self._file_name_label.pack(fill="x")

        self._file_details_label = ctk.CTkLabel(
            self._file_info_frame,
            text="",
            font=self._font_small,
            text_color=COLOR_TEXT_DIM,
            anchor="w",
        )
        self._file_details_label.pack(fill="x", pady=(2, 0))

        # Browse button
        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(6, 14))

        self._select_btn = ctk.CTkButton(
            btn_frame,
            text="📁  Select Video",
            font=self._font_medium,
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER,
            text_color=COLOR_TEXT,
            height=36,
            corner_radius=8,
            command=self._on_select_video,
        )
        self._select_btn.pack(side="left")

        # Supported formats hint
        formats_hint = ", ".join(SUPPORTED_FORMATS)
        ctk.CTkLabel(
            btn_frame,
            text=f"Supported: {formats_hint}",
            font=self._font_small,
            text_color=COLOR_TEXT_DIM,
            anchor="w",
        ).pack(side="left", padx=(12, 0))

        # Drop zone overlay hint
        self._drop_hint = ctk.CTkLabel(
            card,
            text="💡  Tip: You can drag & drop a video file onto the window",
            font=self._font_small,
            text_color=COLOR_TEXT_DIM,
        )
        self._drop_hint.pack(pady=(0, 10))

    def _build_settings_section(self) -> None:
        """Transcription settings card with mode toggle, API key, and language."""
        card = ctk.CTkFrame(self._content, fg_color=COLOR_CARD, corner_radius=12)
        card.pack(fill="x", padx=24, pady=(0, 12))

        # Header
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 10))

        icon_label = ctk.CTkLabel(header, text="⚙️", font=self._font_large)
        icon_label.pack(side="left", padx=(0, 8))

        title_label = ctk.CTkLabel(
            header, text="Transcription Settings",
            font=self._font_body_bold, text_color=COLOR_TEXT,
        )
        title_label.pack(side="left")

        # Mode toggle
        mode_frame = ctk.CTkFrame(card, fg_color="transparent")
        mode_frame.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(
            mode_frame, text="Mode:",
            font=self._font_medium, text_color=COLOR_TEXT_DIM,
        ).pack(side="left", padx=(0, 10))

        self._mode_var = ctk.StringVar(value=self._config.get("mode", "cloud"))
        self._mode_switch = ctk.CTkSegmentedButton(
            mode_frame,
            values=["☁️  Cloud", "🤖  Local"],
            variable=self._mode_var,
            font=self._font_medium,
            selected_color=COLOR_ACCENT,
            selected_hover_color=COLOR_ACCENT_HOVER,
            unselected_color=COLOR_INPUT_BG,
            unselected_hover_color="#333333",
            dynamic_resizing=False,
            command=self._on_mode_change,
        )
        self._mode_switch.pack(side="left")

        # Cloud vs Local hint
        self._mode_hint = ctk.CTkLabel(
            mode_frame,
            text="Uses OpenAI Whisper API (requires API key)",
            font=self._font_small,
            text_color=COLOR_TEXT_DIM,
        )
        self._mode_hint.pack(side="left", padx=(12, 0))

        # API Key row
        api_frame = ctk.CTkFrame(card, fg_color="transparent")
        api_frame.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(
            api_frame, text="API Key:",
            font=self._font_medium, text_color=COLOR_TEXT_DIM,
        ).pack(side="left", padx=(0, 10))

        self._api_key_var = ctk.StringVar(value=self._api_key)
        self._api_entry = ctk.CTkEntry(
            api_frame,
            textvariable=self._api_key_var,
            placeholder_text="sk-...  Enter your OpenAI API key",
            font=self._font_medium,
            fg_color=COLOR_INPUT_BG,
            text_color=COLOR_TEXT,
            border_color=COLOR_BORDER,
            height=34,
            show="•",
            width=360,
        )
        self._api_entry.pack(side="left", padx=(0, 8))

        self._save_api_btn = ctk.CTkButton(
            api_frame,
            text="Save",
            font=self._font_medium,
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER,
            text_color=COLOR_TEXT,
            height=34,
            width=60,
            corner_radius=6,
            command=self._on_save_api_key,
        )
        self._save_api_btn.pack(side="left")

        # Language row
        lang_frame = ctk.CTkFrame(card, fg_color="transparent")
        lang_frame.pack(fill="x", padx=16, pady=(0, 14))

        ctk.CTkLabel(
            lang_frame, text="Language:",
            font=self._font_medium, text_color=COLOR_TEXT_DIM,
        ).pack(side="left", padx=(0, 10))

        self._language_var = ctk.StringVar(value="Auto-detect")
        self._language_menu = ctk.CTkOptionMenu(
            lang_frame,
            values=list(LANGUAGES.keys()),
            variable=self._language_var,
            font=self._font_medium,
            fg_color=COLOR_INPUT_BG,
            button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER,
            text_color=COLOR_TEXT,
            dropdown_fg_color=COLOR_CARD,
            dropdown_hover_color=COLOR_ACCENT,
            dropdown_text_color=COLOR_TEXT,
            dynamic_resizing=False,
            width=200,
        )
        self._language_menu.pack(side="left")

        # Local model install hint (hidden initially)
        self._local_hint_label = ctk.CTkLabel(
            lang_frame,
            text="",
            font=self._font_small,
            text_color=COLOR_YELLOW,
        )
        self._local_hint_label.pack(side="left", padx=(12, 0))

        # ── Words per block slider ──
        words_frame = ctk.CTkFrame(card, fg_color="transparent")
        words_frame.pack(fill="x", padx=16, pady=(0, 14))

        ctk.CTkLabel(
            words_frame, text="Words per block:",
            font=self._font_medium, text_color=COLOR_TEXT_DIM,
        ).pack(side="left", padx=(0, 10))

        self._words_slider = ctk.CTkSlider(
            words_frame,
            from_=0,
            to=15,
            number_of_steps=15,
            fg_color=COLOR_INPUT_BG,
            progress_color=COLOR_ACCENT,
            button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER,
            width=200,
            command=self._on_words_per_block_change,
        )
        self._words_slider.set(0)
        self._words_slider.pack(side="left", padx=(0, 10))

        self._words_value_label = ctk.CTkLabel(
            words_frame,
            text="Auto (default)",
            font=self._font_small,
            text_color=COLOR_TEXT_DIM,
            width=110,
            anchor="w",
        )
        self._words_value_label.pack(side="left")

        ctk.CTkLabel(
            words_frame,
            text="💡  Set to 3-5 for very short single-line subtitles",
            font=self._font_small,
            text_color=COLOR_TEXT_DIM,
        ).pack(side="left", padx=(12, 0))

        # Update mode hint
        self._update_mode_hint()

    def _build_progress_section(self) -> None:
        """Progress bar and status display."""
        card = ctk.CTkFrame(self._content, fg_color=COLOR_CARD, corner_radius=12)
        card.pack(fill="x", padx=24, pady=(0, 12))

        self._progress_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._progress_frame.pack(fill="x", padx=16, pady=(12, 12))

        # Status text
        self._status_label = ctk.CTkLabel(
            self._progress_frame,
            text="Ready • Select a video file to begin",
            font=self._font_medium,
            text_color=COLOR_TEXT_DIM,
            anchor="w",
        )
        self._status_label.pack(fill="x")

        # Progress bar
        self._progress_bar = ctk.CTkProgressBar(
            self._progress_frame,
            fg_color=COLOR_INPUT_BG,
            progress_color=COLOR_ACCENT,
            height=8,
            corner_radius=4,
        )
        self._progress_bar.pack(fill="x", pady=(8, 0))
        self._progress_bar.set(0)

        # Progress details row
        details_frame = ctk.CTkFrame(self._progress_frame, fg_color="transparent")
        details_frame.pack(fill="x", pady=(4, 0))

        self._progress_text = ctk.CTkLabel(
            details_frame,
            text="",
            font=self._font_small,
            text_color=COLOR_TEXT_DIM,
            anchor="w",
        )
        self._progress_text.pack(side="left")

        self._progress_percent = ctk.CTkLabel(
            details_frame,
            text="",
            font=self._font_small,
            text_color=COLOR_TEXT_DIM,
            anchor="e",
        )
        self._progress_percent.pack(side="right")

    def _build_preview_section(self) -> None:
        """SRT preview text area with syntax-style display."""
        card = ctk.CTkFrame(self._content, fg_color=COLOR_CARD, corner_radius=12)
        card.pack(fill="x", padx=24, pady=(0, 12))

        # Header
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(10, 6))

        icon_label = ctk.CTkLabel(header, text="📝", font=self._font_large)
        icon_label.pack(side="left", padx=(0, 8))

        title_label = ctk.CTkLabel(
            header, text="Subtitle Preview",
            font=self._font_body_bold, text_color=COLOR_TEXT,
        )
        title_label.pack(side="left")

        self._subtitle_count_label = ctk.CTkLabel(
            header,
            text="",
            font=self._font_small,
            text_color=COLOR_TEXT_DIM,
        )
        self._subtitle_count_label.pack(side="right")

        # Preview textbox
        self._preview_text = ctk.CTkTextbox(
            card,
            font=self._font_mono,
            fg_color=COLOR_INPUT_BG,
            text_color=COLOR_TEXT,
            border_color=COLOR_BORDER,
            border_width=1,
            corner_radius=8,
            wrap="word",
            height=200,
        )
        self._preview_text.pack(fill="x", padx=16, pady=(6, 14))

        # Placeholder text
        self._preview_text.insert("1.0", "📄  Subtitle preview will appear here...\n\n"
                                        "Select a video, configure settings, and click Generate.")
        self._preview_text.configure(state="disabled")

    def _build_action_buttons(self) -> None:
        """Bottom action bar with Generate and Save buttons."""
        btn_frame = ctk.CTkFrame(self._content, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(0, 20))

        # Generate button (primary action)
        self._generate_btn = ctk.CTkButton(
            btn_frame,
            text="▶  Generate Subtitles",
            font=self._font_large,
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER,
            text_color=COLOR_TEXT,
            height=44,
            corner_radius=10,
            command=self._on_generate,
        )
        self._generate_btn.pack(side="left", padx=(0, 10))

        # Save SRT button
        self._save_srt_btn = ctk.CTkButton(
            btn_frame,
            text="💾  Save .SRT",
            font=self._font_large,
            fg_color="#2a2a2a",
            hover_color="#3a3a3a",
            text_color=COLOR_TEXT,
            height=44,
            corner_radius=10,
            state="disabled",
            command=self._on_save_srt,
        )
        self._save_srt_btn.pack(side="left", padx=(0, 10))

        # Save VTT button
        self._save_vtt_btn = ctk.CTkButton(
            btn_frame,
            text="💾  Save .VTT",
            font=self._font_large,
            fg_color="#2a2a2a",
            hover_color="#3a3a3a",
            text_color=COLOR_TEXT,
            height=44,
            corner_radius=10,
            state="disabled",
            command=self._on_save_vtt,
        )
        self._save_vtt_btn.pack(side="left")

        # Clear button
        self._clear_btn = ctk.CTkButton(
            btn_frame,
            text="🗑  Clear",
            font=self._font_medium,
            fg_color="transparent",
            hover_color="#3a3a3a",
            text_color=COLOR_TEXT_DIM,
            height=44,
            corner_radius=10,
            border_color=COLOR_BORDER,
            border_width=1,
            command=self._on_clear,
        )
        self._clear_btn.pack(side="right")

    # ── Event Handlers ──

    def _check_ffmpeg(self) -> None:
        """Check ffmpeg availability on startup and auto-download if missing."""
        if is_ffmpeg_available():
            return

        self._set_status(
            "🔌  ffmpeg not found on your system. Downloading a portable copy...",
            is_warning=True,
        )
        self._update_progress(0.0, "Preparing ffmpeg download...")

        def _download():
            def progress(pct, text):
                self.after(0, lambda p=pct, t=text: self._update_progress(p, t))
                self.after(0, lambda t=text: self._set_status(f"🔌  {t}", is_warning=True))

            success = _ensure_ffmpeg(progress_callback=progress)

            if success:
                fpath = get_ffmpeg_path()
                self.after(0, lambda: self._set_status(
                    f"✅  ffmpeg ready! ({fpath})" if fpath else "✅  ffmpeg ready!"
                ))
                self.after(0, lambda: self._update_progress(1.0, "ffmpeg ready!"))
            else:
                self.after(0, lambda: self._set_status(
                    "❌  Could not download ffmpeg. Please install manually from ffmpeg.org",
                    is_warning=True,
                ))
                self.after(0, lambda: self._update_progress(0, ""))

        thread = threading.Thread(target=_download, daemon=True)
        thread.start()

    def _on_select_video(self) -> None:
        """Open file dialog to select a video file."""
        filetypes = [
            ("Video files", " ".join(f"*{ext}" for ext in SUPPORTED_FORMATS)),
            ("All files", "*.*"),
        ]
        path = filedialog.askopenfilename(
            title="Select a video file",
            filetypes=filetypes,
        )
        if path:
            self._load_video(path)

    def _load_video(self, path: str) -> None:
        """Load and display info for the selected video."""
        if not os.path.exists(path):
            messagebox.showerror("Error", f"File not found:\n{path}")
            return

        if not is_supported_video(path):
            ext = os.path.splitext(path)[1]
            messagebox.showerror(
                "Unsupported Format",
                f"Unsupported video format: {ext}\n\n"
                f"Supported formats: {', '.join(SUPPORTED_FORMATS)}",
            )
            return

        self._video_path = path
        info = get_video_info(path)

        # Update file info display
        self._file_name_label.configure(
            text=f"📄  {info['filename']}",
            text_color=COLOR_GREEN,
        )
        self._file_details_label.configure(
            text=f"Size: {info['size_mb']:.1f} MB  •  "
                 f"Duration: {info['duration_str']}  •  "
                 f"Est. subtitles: {estimate_subtitle_count(info['duration_sec'] or 0)}",
        )

        # Reset preview
        self._clear_preview()

        # Show clear next-step guidance
        mode = self._get_mode()
        if mode == "cloud" and not self._api_key_var.get().strip():
            self._set_status(
                f"📁  {info['filename']} loaded!  👉  Enter your API key in Settings above, then click \"▶ Generate Subtitles\" below",
                is_warning=True,
            )
        else:
            self._set_status(
                f"📁  {info['filename']} loaded!  👉  Click \"▶ Generate Subtitles\" below to start  (or press Enter)",
            )

        # Enable generate and highlight it
        self._generate_btn.configure(state="normal", fg_color=COLOR_GREEN)
        self._generate_btn.configure(text="🎬  START — Generate Subtitles")

    def _on_words_per_block_change(self, value: float) -> None:
        """Handle words-per-block slider change."""
        rounded = round(value)
        self._words_slider.set(float(rounded))
        if rounded == 0:
            self._words_value_label.configure(text="Auto (default)")
        else:
            self._words_value_label.configure(text=f"{rounded} words")

    def _on_mode_change(self, _=None) -> None:
        """Handle mode toggle between Cloud and Local."""
        self._update_mode_hint()
        # Refresh the status message to match the new mode
        if self._video_path:
            fname = os.path.basename(self._video_path)
            api_key = self._api_key_var.get().strip()
            if self._get_mode() == "cloud" and not api_key:
                self._set_status(
                    f"📁  {fname} loaded!  👉  Enter your API key in Settings above, "
                    "then click the START button below",
                    is_warning=True,
                )
            else:
                self._set_status(
                    f"📁  {fname} loaded!  👉  Click the START button below  (or press Enter)",
                )

    def _update_mode_hint(self) -> None:
        """Update the hint text and UI based on current mode."""
        mode = self._get_mode()
        if mode == "cloud":
            self._mode_hint.configure(
                text="Uses OpenAI Whisper API (requires API key)",
                text_color=COLOR_TEXT_DIM,
            )
            self._api_entry.configure(state="normal")
            self._save_api_btn.configure(state="normal")
            self._local_hint_label.configure(text="")
        else:
            self._mode_hint.configure(
                text="Uses local Whisper model (no API key needed)",
                text_color=COLOR_GREEN,
            )
            self._api_entry.configure(state="disabled")
            self._save_api_btn.configure(state="disabled")

            if not is_whisper_available():
                self._local_hint_label.configure(
                    text="⚠️  Whisper package not installed. First use will install it.",
                )

    def _get_mode(self) -> str:
        """Get the current mode string from the segmented button."""
        val = self._mode_var.get()
        return "cloud" if "Cloud" in val else "local"

    def _get_language_code(self) -> Optional[str]:
        """Get the selected language code."""
        lang_name = self._language_var.get()
        return LANGUAGES.get(lang_name)

    def _on_save_api_key(self) -> None:
        """Save API key to persistent config."""
        key = self._api_key_var.get().strip()
        if not key:
            messagebox.showwarning("API Key", "Please enter an API key first.")
            return

        self._api_key = key
        self._config["api_key"] = key
        save_config(self._config)

        # Visual feedback
        self._save_api_btn.configure(text="✓  Saved", fg_color=COLOR_GREEN)
        self.after(2000, lambda: self._save_api_btn.configure(
            text="Save", fg_color=COLOR_ACCENT,
        ))

        # If a video is already loaded, update status to reflect API key is ready
        if self._video_path:
            fname = os.path.basename(self._video_path)
            self._set_status(
                f"📁  {fname} loaded + API key set!  👉  Click \"🎬  START — Generate Subtitles\" below",
            )

    def _on_generate(self) -> None:
        """Start subtitle generation in a background thread."""
        if self._is_processing:
            return

        if not self._video_path or not os.path.exists(self._video_path):
            messagebox.showwarning(
                "No Video",
                "Please select a video file first.",
            )
            return

        mode = self._get_mode()

        if mode == "cloud":
            api_key = self._api_key_var.get().strip()
            if not api_key:
                messagebox.showwarning(
                    "API Key Required",
                    "Please enter your OpenAI API key in Settings, "
                    "or switch to Local mode.",
                )
                return
            self._api_key = api_key
            # Save key
            self._config["api_key"] = api_key
            save_config(self._config)

        if mode == "local" and not is_whisper_available():
            # Offer to install
            result = messagebox.askyesno(
                "Install Whisper",
                "The openai-whisper package is not installed. "
                "Would you like to install it now?\n\n"
                "This will download ~2 GB of dependencies and may take a few minutes.",
            )
            if result:
                self._install_local_model()
            return

        self._is_processing = True
        self._set_ui_busy(True)
        self._clear_preview()
        self._set_status("🎯  Starting subtitle generation...")
        self._update_progress(0.05, "Preparing...")

        # Run in background thread
        thread = threading.Thread(target=self._generate_worker, daemon=True)
        thread.start()

    def _generate_worker(self) -> None:
        """Background worker: extract audio → transcribe → generate SRT."""
        try:
            # Step 1: Extract audio
            self.after(0, lambda: self._set_status("🎵  Extracting audio from video..."))
            self.after(0, lambda: self._update_progress(0.1, "Extracting audio..."))

            wav_bytes = extract_audio_to_wav(self._video_path)

            audio_size_mb = len(wav_bytes) / (1024 * 1024)
            self.after(0, lambda: self._update_progress(
                0.25,
                f"Audio extracted ({audio_size_mb:.1f} MB). Transcribing...",
            ))

            # Step 2: Transcribe
            self.after(0, lambda: self._set_status("🧠  Transcribing audio with AI..."))
            self.after(0, lambda: self._update_progress(0.3, "Processing through Whisper..."))

            mode = self._get_mode()
            language = self._get_language_code()

            segments = transcribe_audio_timed(
                wav_bytes=wav_bytes,
                api_key=self._api_key,
                mode=mode,
                language=language,
            )

            self._segments = segments
            self.after(0, lambda: self._update_progress(
                0.8,
                f"✅  {len(segments)} segments transcribed.",
            ))

            # Step 3: Generate SRT
            self.after(0, lambda: self._set_status("📝  Generating SRT subtitles..."))
            self.after(0, lambda: self._update_progress(0.9, "Formatting subtitles..."))

            max_words = round(self._words_slider.get())
            srt_content = segments_to_srt(segments, max_words_per_block=max_words)
            self._srt_content = srt_content

            # Step 4: Show in preview
            self.after(0, lambda: self._show_preview(srt_content, len(segments)))
            self.after(0, lambda: self._update_progress(1.0, "Done!"))

            self.after(0, lambda: self._set_status(
                f"✅  Done! {len(segments)} subtitles generated. Click Save to export.",
            ))

            # Enable save buttons
            self.after(0, lambda: self._save_srt_btn.configure(state="normal"))
            self.after(0, lambda: self._save_vtt_btn.configure(state="normal"))

        except AudioExtractionError as e:
            self.after(0, lambda: self._show_error(f"Audio Extraction Error", str(e)))
        except TranscriptionError as e:
            self.after(0, lambda: self._show_error(f"Transcription Error", str(e)))
        except Exception as e:
            self.after(0, lambda: self._show_error(
                "Unexpected Error",
                f"An unexpected error occurred:\n{e}",
            ))
        finally:
            self.after(0, lambda: self._set_ui_busy(False))
            self._is_processing = False

    def _install_local_model(self) -> None:
        """Install the local Whisper model in a background thread."""
        self._is_processing = True
        self._set_ui_busy(True)
        self._set_status("📦  Installing Whisper package...")

        def _install():
            try:
                from app.transcriber import install_whisper_package
                install_whisper_package()
                self.after(0, lambda: self._set_status(
                    "✅  Whisper installed! Loading model..."
                ))
                self.after(0, lambda: self._update_progress(0.5, "Loading model..."))

                # Load the model
                load_whisper_model("base")

                self.after(0, lambda: self._on_generate())
            except Exception as e:
                self.after(0, lambda: self._show_error(
                    "Installation Error",
                    f"Failed to install Whisper:\n{e}",
                ))
                self.after(0, lambda: self._set_ui_busy(False))
                self._is_processing = False

        thread = threading.Thread(target=_install, daemon=True)
        thread.start()

    def _on_save_srt(self) -> None:
        """Save the generated SRT content to a file."""
        if not self._srt_content:
            return

        # Suggest filename based on video
        suggested = "subtitles.srt"
        if self._video_path:
            base = os.path.splitext(os.path.basename(self._video_path))[0]
            suggested = f"{base}.srt"

        path = filedialog.asksaveasfilename(
            title="Save Subtitles as .srt",
            defaultextension=".srt",
            filetypes=[("SRT Subtitle", "*.srt"), ("All files", "*.*")],
            initialfile=suggested,
        )
        if not path:
            return

        try:
            content = self._srt_content
            # Remove BOM if user doesn't want it (but keep it by default for Windows compat)
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(content)

            self._set_status(f"✅  Saved: {os.path.basename(path)}")
            messagebox.showinfo(
                "Saved!",
                f"Subtitles saved to:\n{path}\n\n"
                f"You can now import this .srt file into CapCut or any video editor.",
            )
        except OSError as e:
            messagebox.showerror("Save Error", f"Could not save file:\n{e}")

    def _on_save_vtt(self) -> None:
        """Save the generated subtitles as WebVTT format."""
        if not self._segments:
            return

        vtt_content = segments_to_vtt(self._segments)

        suggested = "subtitles.vtt"
        if self._video_path:
            base = os.path.splitext(os.path.basename(self._video_path))[0]
            suggested = f"{base}.vtt"

        path = filedialog.asksaveasfilename(
            title="Save Subtitles as .vtt",
            defaultextension=".vtt",
            filetypes=[("WebVTT Subtitle", "*.vtt"), ("All files", "*.*")],
            initialfile=suggested,
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(vtt_content)

            self._set_status(f"✅  Saved: {os.path.basename(path)}")
        except OSError as e:
            messagebox.showerror("Save Error", f"Could not save file:\n{e}")

    def _on_clear(self) -> None:
        """Clear the current session."""
        self._video_path = None
        self._segments = []
        self._srt_content = ""
        self._clear_preview()
        self._file_name_label.configure(text="No video selected", text_color=COLOR_TEXT_DIM)
        self._file_details_label.configure(text="")
        self._set_status("Ready • Select a video file to begin")
        self._update_progress(0, "")
        self._save_srt_btn.configure(state="disabled")
        self._save_vtt_btn.configure(state="disabled")
        self._generate_btn.configure(state="disabled", fg_color=COLOR_ACCENT)
        self._generate_btn.configure(text="🎬  START — Generate Subtitles")

    # ── UI Helpers ──

    def _set_status(self, text: str, is_warning: bool = False) -> None:
        """Update the status label."""
        color = COLOR_YELLOW if is_warning else COLOR_TEXT_DIM
        self._status_label.configure(text=text, text_color=color)

    def _update_progress(self, value: float, text: str = "") -> None:
        """Update the progress bar and detail text."""
        self._progress_bar.set(value)
        self._progress_text.configure(text=text)
        self._progress_percent.configure(text=f"{int(value * 100)}%")

    def _set_ui_busy(self, busy: bool) -> None:
        """Enable/disable UI elements during processing."""
        state = "disabled" if busy else "normal"
        self._generate_btn.configure(state=state, fg_color=COLOR_ACCENT if not busy else "#555")
        self._select_btn.configure(state=state)

        if busy:
            self._generate_btn.configure(text="⏳  Processing...")
        elif self._video_path:
            self._generate_btn.configure(text="🎬  START — Generate Subtitles")
        else:
            self._generate_btn.configure(text="🎬  START — Generate Subtitles")

    def _clear_preview(self) -> None:
        """Clear the preview text area."""
        self._preview_text.configure(state="normal")
        self._preview_text.delete("1.0", "end")
        self._preview_text.configure(state="disabled")
        self._subtitle_count_label.configure(text="")

    def _show_preview(self, content: str, count: int) -> None:
        """Display SRT content in the preview area."""
        self._preview_text.configure(state="normal")
        self._preview_text.delete("1.0", "end")
        self._preview_text.insert("1.0", content)
        self._preview_text.configure(state="disabled")
        self._subtitle_count_label.configure(
            text=f"{count} subtitles",
            text_color=COLOR_GREEN,
        )

    def _show_error(self, title: str, message: str) -> None:
        """Display an error to the user."""
        self._set_status(f"❌  {title}: {message[:80]}{'...' if len(message) > 80 else ''}")
        self._progress_bar.set(0)
        self._progress_text.configure(text="Failed")
        self._progress_percent.configure(text="")

        messagebox.showerror(title, message)
