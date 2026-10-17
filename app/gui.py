"""WaveScribe GUI — CustomTkinter desktop window.

The main window with recording controls, transcription display,
settings panel, status indicators, a floating status overlay,
and config persistence.
"""

import queue
import threading
from tkinter import messagebox
from typing import Optional

import customtkinter as ctk
import pyperclip

from app.state import AppState, AppStatus, AppMode, LocalModelStatus, EventQueue
from app.audio_capture import AudioCapture, AudioCaptureError
from app.transcriber import (
    transcribe_cloud,
    transcribe_local,
    is_whisper_available,
    install_whisper_package,
    load_whisper_model,
    setup_bundled_model,
    TranscriptionError,
    PUNCTUATION_PROMPT,
)
from app.formatter import apply_punctuation, auto_capitalize, convert_numbers, clean_spacing, cleanup_redundant_punctuation, auto_add_sentence_punctuation
from app.typer import type_text
from app.config import load_config, save_config
from app.autostart import apply_autostart_setting
from app.history import HistoryManager
from app.overlay import StatusOverlay
from app.hotkeys import HotkeyManager
from app.sounds import init_sounds, play_start_sound, play_stop_sound, play_pressed_sound, play_tts_success_sound, play_tts_failure_sound
from app.theme import (
    FONTS,
    init_fonts,
    COLOR_GREEN,
    COLOR_RED,
    COLOR_YELLOW,
    COLOR_BG,
    COLOR_CARD,
    COLOR_BORDER,
    COLOR_ACCENT,
    COLOR_ACCENT_HOVER,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_INPUT_BG,
    COLOR_GREEN_HOVER,
    COLOR_RED_HOVER,
    COLOR_SUCCESS,
    COLOR_SUCCESS_HOVER,
)
from app.tts_engine import EngineManager, available_backends
from app.tts_errors import TTSError
from app import selection
from app import voices as voices_mod
from app.audio_capture import list_input_devices, get_device_display_name
from app.state import TTSReadStatus

# ── Window Dimensions ──

WINDOW_WIDTH = 620
WINDOW_HEIGHT = 740


class App(ctk.CTk):
    """WaveScribe main application window."""

    def __init__(
        self,
        state: AppState,
        audio: AudioCapture,
        event_queue: EventQueue,
        hotkey_mgr: HotkeyManager,
        tts_engine: EngineManager,
    ):
        super().__init__()

        # Resolve fonts (registers bundled Poppins .ttf) and init sounds
        init_fonts()
        init_sounds()

        self.app_state = state
        self.audio = audio
        self.event_queue = event_queue
        self.hotkey_mgr = hotkey_mgr
        self.tts_engine = tts_engine

        # ── Load config and apply to state ──
        self._config = load_config()
        self._apply_config_to_state()
        self._config_dirty = False

        # ── Transcription history (persisted to history.json) ──
        self._history = HistoryManager()

        # ── Copy bundled Whisper model to cache if needed ──
        # This ensures the model file is on disk without an extra download
        setup_bundled_model()

        # ── If whisper is available and marked ready, load model in background ──
        # load_whisper_model() actually calls whisper.load_model() to populate the
        # global _whisper_model variable; without this, transcribe_local() will
        # refuse with "Whisper model is not loaded."
        if (
            is_whisper_available()
            and self.app_state.get_local_model_status() == LocalModelStatus.READY
        ):
            self._load_local_model_in_background()

        # ── Status overlay (floating indicator) ──
        self._overlay = StatusOverlay()

        # ── Hotkey capture state ──
        self._capturing = False
        self._capturing_start: bool = False
        self._tts_capturing = False

        # ── Guard to prevent double auto-insert ──
        self._auto_inserted = False

        # ── Flag to allow cancelling an in-progress transcription ──
        self._transcription_cancelled = False

        # ── TTS state ──
        self._tts_busy = False
        self._tts_cancel_requested = False
        self._tts_current_voices = []
        self._tts_notice_voice_pending = False

        # ── Apply TTS backend from config ──
        tts_backend = self._config.get("tts_backend", "system")
        if tts_backend not in available_backends():
            tts_backend = "system"
        self.tts_engine.set_backend(tts_backend)

        # ── Window setup ──
        self.title("WaveScribe")
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.resizable(True, True)
        self.minsize(WINDOW_WIDTH, 700)
        self.configure(fg_color=COLOR_BG)

        # Center on screen
        self._center_window()

        # Protocol for window close (hide to tray instead)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── Build UI ──
        self._build_header()

        # ── Tab switcher (Dictation / Read Out) ──
        self._active_tab = "Dictation"
        tab_frame = ctk.CTkFrame(self, fg_color=COLOR_CARD, height=40, corner_radius=0)
        tab_frame.pack(fill="x", padx=0, pady=0)
        tab_frame.pack_propagate(False)
        self._tab_switcher = ctk.CTkSegmentedButton(
            tab_frame,
            values=["Dictation", "Read Out"],
            selected_color=COLOR_ACCENT,
            selected_hover_color=COLOR_ACCENT_HOVER,
            font=FONTS["body_medium"],
            command=self._on_tab_switch,
        )
        self._tab_switcher.pack(expand=True)
        self._tab_switcher.set("Dictation")

        # ── Dictation tab content (scrollable) ──
        self._dictation_content = ctk.CTkScrollableFrame(
            self, fg_color=COLOR_BG, corner_radius=0,
            scrollbar_button_color=COLOR_CARD,
            scrollbar_button_hover_color=COLOR_BORDER,
        )

        self._build_control_card()
        self._build_transcription_card()
        self._build_history_card()
        self._build_settings_card()
        self._build_footer()

        # ── Read Out tab content (scrollable) ──
        self._readout_content = ctk.CTkScrollableFrame(
            self, fg_color=COLOR_BG, corner_radius=0,
            scrollbar_button_color=COLOR_CARD,
            scrollbar_button_hover_color=COLOR_BORDER,
        )

        self._build_tts_control_card()
        self._build_tts_voice_card()
        self._build_tts_speed_card()
        self._build_tts_hotkey_card()
        self._build_tts_footer()

        # ── Show dictation tab by default ──
        self._dictation_content.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Global key-binding for hotkey capture ──
        self.bind('<KeyPress>', self._on_global_keypress)

        # ── Start polling event queue ──
        self._poll_queue()

        # ── Start polling input level (live meter) ──
        self._poll_input_level()

        # ── Update UI for initial state ──
        self._update_ui()
        # ── Populate TTS voice list for the active backend ──
        self._tts_refresh_voices()

    # ── Config helpers ──

    def _apply_config_to_state(self) -> None:
        """Apply loaded config values to the AppState."""
        self.app_state.set_api_key(self._config.get("api_key", ""))
        self.app_state.set_model_size(self._config.get("model_size", "base"))
        self.app_state.set_punctuate_speech(self._config.get("punctuate_speech", True))
        self.app_state.set_auto_capitalize(self._config.get("auto_capitalize", True))
        self.app_state.set_numbers_as_digits(self._config.get("numbers_as_digits", False))
        self.app_state.set_autostart_enabled(self._config.get("autostart", True))
        self.app_state.set_history_enabled(self._config.get("history_enabled", True))
        mode_str = self._config.get("mode", "cloud")
        self.app_state.set_mode(AppMode.CLOUD if mode_str == "cloud" else AppMode.LOCAL)
        self.app_state.set_local_language(self._config.get("local_language", "en"))

        # Detect local model status — if config says ready but package is gone, reset
        cfg_status = self._config.get("local_model_status", "package_missing")
        if cfg_status == "ready" and not is_whisper_available():
            cfg_status = "package_missing"
        self.app_state.set_local_model_status(LocalModelStatus(cfg_status))

    def _save_current_config(self) -> None:
        """Persist current state + hotkey preferences to config.json."""
        mode = self.app_state.get_mode()
        self._config.update({
            "api_key": self.app_state.get_api_key(),
            "model_size": self.app_state.get_model_size(),
            "punctuate_speech": self.app_state.get_punctuate_speech(),
            "auto_capitalize": self.app_state.get_auto_capitalize(),
            "numbers_as_digits": self.app_state.get_numbers_as_digits(),
            "mode": mode.value,
            "local_language": self.app_state.get_local_language(),
            "local_model_status": self.app_state.get_local_model_status().value,
            "autostart": self.app_state.get_autostart_enabled(),
            "history_enabled": self.app_state.get_history_enabled(),
        })
        save_config(self._config)

    # ── Window helpers ──

    def _center_window(self) -> None:
        """Center the window on the screen."""
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = (screen_w - WINDOW_WIDTH) // 2
        y = (screen_h - WINDOW_HEIGHT) // 2
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")

    def _on_close(self) -> None:
        """Hide to system tray instead of closing."""
        self.withdraw()

    def show_window(self) -> None:
        """Show and bring the window to front."""
        self.deiconify()
        self.lift()
        self.focus_force()

    def destroy(self) -> None:
        """Clean up overlay and save config before fully quitting."""
        self._overlay.destroy()
        if self._config_dirty:
            self._save_current_config()
        super().destroy()

    # ── UI Builders ──

    def _build_header(self) -> None:
        """Build the header bar with title and status."""
        header_frame = ctk.CTkFrame(self, fg_color=COLOR_CARD, height=50, corner_radius=0)
        header_frame.pack(fill="x", padx=0, pady=0)
        header_frame.pack_propagate(False)

        inner = ctk.CTkFrame(header_frame, fg_color="transparent")
        inner.pack(fill="both", padx=16, pady=10)

        # Status dot (colored circle)
        self.status_dot = ctk.CTkLabel(
            inner, text="●", font=FONTS["icon"], text_color=COLOR_GREEN, width=20
        )
        self.status_dot.pack(side="left")

        # Title
        ctk.CTkLabel(
            inner, text="WaveScribe", font=FONTS["title"],
            text_color=COLOR_TEXT
        ).pack(side="left", padx=(6, 0))

        # Spacer
        ctk.CTkFrame(inner, fg_color="transparent", width=0).pack(
            side="left", fill="x", expand=True
        )

        # Status text
        self.status_label = ctk.CTkLabel(
            inner, text="Idle", font=FONTS["body"],
            text_color=COLOR_TEXT_DIM
        )
        self.status_label.pack(side="right")

        # Separator
        ctk.CTkFrame(self, fg_color=COLOR_BORDER, height=1).pack(fill="x")

    def _build_control_card(self) -> None:
        """Build the control card (recording status, mode, buttons)."""
        card = ctk.CTkFrame(
            self._dictation_content, fg_color=COLOR_CARD, corner_radius=10,
            border_width=1, border_color=COLOR_BORDER,
        )
        card.pack(fill="x", padx=16, pady=(16, 0))

        # ── Status section ──
        status_frame = ctk.CTkFrame(card, fg_color="transparent")
        status_frame.pack(fill="x", padx=20, pady=(16, 8))

        self.recording_status = ctk.CTkLabel(
            status_frame, text="● Idle",
            font=FONTS["body_large"], text_color=COLOR_TEXT_DIM
        )
        self.recording_status.pack(side="left")

        # ── Cancel Transcription button (visible only during transcribing) ──
        self._cancel_transcribe_btn = ctk.CTkButton(
            status_frame, text="✕ Cancel",
            font=FONTS["body_small"], height=22, width=80,
            fg_color=COLOR_RED, hover_color=COLOR_RED_HOVER,
            text_color="#ffffff", corner_radius=6,
            command=self._on_cancel_transcription,
        )

        # ── Input level meter (always visible, shows 0% when idle) ──
        # The meter is always packed so there's no layout shift when
        # recording starts/stops.  At rest it just shows an empty bar
        # at 0% with a dark fill so it blends into the background.
        self._meter_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._meter_frame.pack(fill="x", padx=0, pady=0)

        self._meter_row = ctk.CTkFrame(self._meter_frame, fg_color="transparent")

        ctk.CTkLabel(
            self._meter_row, text="🎙️  Input",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM,
            anchor="w",
        ).pack(side="left")

        self._meter_bar = ctk.CTkProgressBar(
            self._meter_row,
            mode="determinate",
            height=10,
            corner_radius=5,
            fg_color=COLOR_INPUT_BG,
            progress_color=COLOR_INPUT_BG,  # dark fill blends in when idle
        )
        self._meter_bar.pack(side="left", fill="x", expand=True, padx=(8, 8))
        self._meter_bar.set(0)

        self._meter_label = ctk.CTkLabel(
            self._meter_row, text="0%",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM,
            anchor="e", width=44,
        )
        self._meter_label.pack(side="right")

        # Always visible so the layout never shifts
        self._meter_row.pack(fill="x", padx=20, pady=(0, 8))

        # ── Mode toggle ──
        mode_frame = ctk.CTkFrame(card, fg_color="transparent")
        mode_frame.pack(fill="x", padx=20, pady=(4, 8))

        ctk.CTkLabel(
            mode_frame, text="Mode:",
            font=FONTS["body_medium"], text_color=COLOR_TEXT_DIM
        ).pack(side="left")

        self.mode_segmented = ctk.CTkSegmentedButton(
            mode_frame,
            values=["Cloud", "Local"],
            selected_color=COLOR_ACCENT,
            selected_hover_color=COLOR_ACCENT_HOVER,
            font=FONTS["body_medium"],
            command=self._on_mode_change,
        )
        self.mode_segmented.pack(side="left", padx=(8, 0))
        mode_label = "Cloud" if self.app_state.get_mode() == AppMode.CLOUD else "Local"
        self.mode_segmented.set(mode_label)

        # ── Shortcuts display ──
        shortcuts_frame = ctk.CTkFrame(card, fg_color="transparent")
        shortcuts_frame.pack(fill="x", padx=20, pady=(4, 12))

        start_label = self._config.get("hotkey_start", "ctrl+shift+r")
        stop_label = self._config.get("hotkey_stop", "ctrl+shift+s")
        shortcuts_text = (
            f"{self._format_hotkey_display(start_label)}  Start Recording\n"
            f"{self._format_hotkey_display(stop_label)}  Stop & Transcribe"
        )
        self._shortcuts_label = ctk.CTkLabel(
            shortcuts_frame, text=shortcuts_text,
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM,
            justify="left"
        )
        self._shortcuts_label.pack(anchor="w")

        # ── Action buttons ──
        buttons_frame = ctk.CTkFrame(card, fg_color="transparent")
        buttons_frame.pack(fill="x", padx=20, pady=(0, 16))

        self.record_btn = ctk.CTkButton(
            buttons_frame, text="🎤  Record",
            font=FONTS["body_large"], height=40,
            fg_color=COLOR_GREEN, hover_color=COLOR_GREEN_HOVER,
            text_color="#000000",
            corner_radius=8,
            command=self._on_record_click,
        )
        self.record_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.stop_btn = ctk.CTkButton(
            buttons_frame, text="⏹  Stop",
            font=FONTS["body_large"], height=40,
            fg_color=COLOR_RED, hover_color=COLOR_RED_HOVER,
            text_color="#ffffff",
            text_color_disabled="#3a3a3a",
            corner_radius=8,
            state="disabled",
            command=self._on_stop_click,
        )
        self.stop_btn.pack(side="right", fill="x", expand=True, padx=(6, 0))

    def _build_transcription_card(self) -> None:
        """Build the transcription display card."""
        card = ctk.CTkFrame(
            self._dictation_content, fg_color=COLOR_CARD, corner_radius=10,
            border_width=1, border_color=COLOR_BORDER,
        )
        card.pack(fill="both", padx=16, pady=(12, 0), expand=True)

        # Header
        header_frame = ctk.CTkFrame(card, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(16, 8))

        ctk.CTkLabel(
            header_frame, text="Transcription",
            font=FONTS["body_large_bold"], text_color=COLOR_TEXT
        ).pack(side="left")

        # Clear button
        self.clear_btn = ctk.CTkButton(
            header_frame, text="✕",
            font=FONTS["body_large"], width=30, height=24,
            fg_color="transparent", hover_color=COLOR_BORDER,
            text_color=COLOR_TEXT_DIM, corner_radius=4,
            command=self._on_clear_transcription,
        )
        self.clear_btn.pack(side="right")

        # Textbox
        self.transcription_text = ctk.CTkTextbox(
            card,
            font=FONTS["body"],
            text_color=COLOR_TEXT,
            fg_color=COLOR_INPUT_BG,
            border_width=1,
            border_color=COLOR_BORDER,
            corner_radius=8,
            wrap="word",
            height=140,
        )
        self.transcription_text.pack(fill="both", padx=20, pady=(0, 8), expand=True)
        self.transcription_text.insert("1.0", "Your transcription will appear here...")
        self.transcription_text.configure(state="disabled")

        # Insert button
        self.insert_btn = ctk.CTkButton(
            card, text="📝  Copy Text",
            font=FONTS["body"], height=36,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            text_color="#ffffff",
            corner_radius=8,
            state="disabled",
            command=self._on_insert_click,
        )
        self.insert_btn.pack(fill="x", padx=20, pady=(0, 16))

    def _build_history_card(self) -> None:
        """Build the transcription history card (open / copy / clear entries)."""
        self._history_card = ctk.CTkFrame(
            self._dictation_content, fg_color=COLOR_CARD, corner_radius=10,
            border_width=1, border_color=COLOR_BORDER,
        )
        self._history_card.pack(fill="x", padx=16, pady=(12, 0))

        header_frame = ctk.CTkFrame(self._history_card, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(16, 8))

        ctk.CTkLabel(
            header_frame, text="📜 History",
            font=FONTS["body_large_bold"], text_color=COLOR_TEXT
        ).pack(side="left")

        self._history_count_label = ctk.CTkLabel(
            header_frame, text="",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM,
        )
        self._history_count_label.pack(side="left", padx=(8, 0))

        self._history_clear_btn = ctk.CTkButton(
            header_frame, text="🗑  Clear",
            font=FONTS["body_small"], height=24, width=64,
            fg_color="transparent", hover_color=COLOR_BORDER,
            text_color=COLOR_TEXT_DIM, corner_radius=4,
            state="disabled",
            command=self._on_history_clear,
        )
        self._history_clear_btn.pack(side="right")

        # Empty state hint (shown when there are no entries)
        self._history_empty_label = ctk.CTkLabel(
            self._history_card, text="No saved transcriptions yet.",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM,
        )
        self._history_empty_label.pack(fill="x", padx=20, pady=(0, 12))

        # Scrollable list of entries
        self._history_list = ctk.CTkScrollableFrame(
            self._history_card, fg_color="transparent",
            height=150, corner_radius=0,
            scrollbar_button_color=COLOR_CARD,
            scrollbar_button_hover_color=COLOR_BORDER,
        )
        self._history_list.pack(fill="x", padx=12, pady=(0, 12))

        # Populate from disk
        self._refresh_history()

    def _refresh_history(self) -> None:
        """Rebuild the history list from the HistoryManager (main thread only).

        Skips rebuilding when nothing changed, so frequent UI updates
        don't churn widgets or reset the scroll position.
        """
        entries = self._history.get_all()
        signature = (len(entries), entries[0]["text"] if entries else None)
        if signature == getattr(self, "_history_signature", None):
            return
        self._history_signature = signature

        # Toggle between the empty hint and the scrollable list
        if entries:
            self._history_empty_label.pack_forget()
            self._history_list.pack(fill="x", padx=12, pady=(0, 12))
        else:
            self._history_list.pack_forget()
            self._history_empty_label.pack(fill="x", padx=20, pady=(0, 12))

        # Rebuild rows
        for w in self._history_list.winfo_children():
            w.destroy()

        for i, entry in enumerate(entries):
            row = ctk.CTkFrame(self._history_list, fg_color="transparent")
            row.pack(fill="x", pady=(2, 2))

            first_line = entry["text"].strip().splitlines()[0] if entry["text"].strip() else ""
            display = first_line if len(first_line) <= 56 else first_line[:56] + "…"

            ctk.CTkLabel(
                row, text=f"{entry['timestamp']}  {display}",
                font=FONTS["body_small"], text_color=COLOR_TEXT,
                anchor="w",
            ).pack(side="left", fill="x", expand=True)

            copy_btn = ctk.CTkButton(
                row, text="📋", width=30, height=24,
                font=FONTS["body_small"],
                fg_color=COLOR_INPUT_BG, hover_color=COLOR_BORDER,
                text_color=COLOR_TEXT, corner_radius=4,
                command=lambda i=i: self._on_history_copy(i),
            )
            copy_btn.pack(side="right", padx=(4, 0))

            open_btn = ctk.CTkButton(
                row, text="Open", width=52, height=24,
                font=FONTS["body_small"],
                fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                text_color="#ffffff", corner_radius=4,
                command=lambda i=i: self._on_history_open(i),
            )
            open_btn.pack(side="right", padx=(4, 0))

        self._history_count_label.configure(text=f"{len(entries)} saved")
        self._history_clear_btn.configure(state="normal" if entries else "disabled")

    def _on_history_open(self, index: int) -> None:
        """Load a history entry into the transcription box."""
        entry = self._history.get(index)
        if entry:
            self.app_state.set_last_transcription(entry["text"])
            self._update_ui()

    def _on_history_copy(self, index: int) -> None:
        """Copy a history entry to the clipboard with brief feedback."""
        entry = self._history.get(index)
        if entry:
            try:
                pyperclip.copy(entry["text"])
            except Exception:
                # Clipboard unavailable (e.g. locked by another app) — skip quietly
                return
            self._history_count_label.configure(text="✓ copied to clipboard")
            self.after(
                1500,
                lambda: self._history_count_label.configure(
                    text=f"{self._history.count()} saved"
                ),
            )

    def _on_history_clear(self) -> None:
        """Clear all history entries after confirmation."""
        if not messagebox.askyesno(
            "Clear History", "Clear all saved transcriptions?"
        ):
            return
        self._history.clear()
        self._refresh_history()

    def _build_settings_card(self) -> None:
        """Build the settings panel with shortcut customization, model, API key, formatting."""
        card = ctk.CTkFrame(
            self._dictation_content, fg_color=COLOR_CARD, corner_radius=10,
            border_width=1, border_color=COLOR_BORDER,
        )
        card.pack(fill="x", padx=16, pady=(12, 0))
        # Keep a reference so _update_ui can re-order the History card before it
        self._settings_card = card

        # Header
        header_frame = ctk.CTkFrame(card, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(16, 12))

        ctk.CTkLabel(
            header_frame, text="Settings",
            font=FONTS["body_large_bold"], text_color=COLOR_TEXT
        ).pack(side="left")

        # ════════════════════════════════════════
        # ── Shortcuts section ──
        # ════════════════════════════════════════
        shortcuts_frame = ctk.CTkFrame(card, fg_color="transparent")
        shortcuts_frame.pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkLabel(
            shortcuts_frame, text="Shortcuts",
            font=FONTS["body_medium"], text_color=COLOR_TEXT_DIM
        ).pack(anchor="w")

        # Hotkey entries
        hotkey_grid = ctk.CTkFrame(shortcuts_frame, fg_color="transparent")
        hotkey_grid.pack(fill="x", pady=(4, 0))

        # --- Start hotkey ---
        ctk.CTkLabel(
            hotkey_grid, text="Start Recording",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM
        ).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(4, 4))

        self._start_hotkey_var = ctk.StringVar(
            value=self._config.get("hotkey_start", "ctrl+shift+r")
        )
        self._start_hotkey_entry = ctk.CTkEntry(
            hotkey_grid,
            textvariable=self._start_hotkey_var,
            font=FONTS["body_medium"],
            fg_color=COLOR_INPUT_BG,
            border_color=COLOR_BORDER,
            corner_radius=6,
            width=160,
        )
        self._start_hotkey_entry.grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=(4, 4))

        self._capture_start_btn = ctk.CTkButton(
            hotkey_grid, text="🎯 Capture",
            font=FONTS["body_small"], height=28, width=80,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            text_color="#ffffff", corner_radius=6,
            command=lambda: self._start_capturing(is_start=True),
        )
        self._capture_start_btn.grid(row=0, column=2, padx=(0, 0), pady=(4, 4))

        # --- Stop hotkey ---
        ctk.CTkLabel(
            hotkey_grid, text="Stop & Transcribe",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM
        ).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(4, 4))

        self._stop_hotkey_var = ctk.StringVar(
            value=self._config.get("hotkey_stop", "ctrl+shift+s")
        )
        self._stop_hotkey_entry = ctk.CTkEntry(
            hotkey_grid,
            textvariable=self._stop_hotkey_var,
            font=FONTS["body_medium"],
            fg_color=COLOR_INPUT_BG,
            border_color=COLOR_BORDER,
            corner_radius=6,
            width=160,
        )
        self._stop_hotkey_entry.grid(row=1, column=1, sticky="ew", padx=(0, 6), pady=(4, 4))

        self._capture_stop_btn = ctk.CTkButton(
            hotkey_grid, text="🎯 Capture",
            font=FONTS["body_small"], height=28, width=80,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            text_color="#ffffff", corner_radius=6,
            command=lambda: self._start_capturing(is_start=False),
        )
        self._capture_stop_btn.grid(row=1, column=2, padx=(0, 0), pady=(4, 4))

        hotkey_grid.columnconfigure(1, weight=1)

        # Save shortcuts button
        self._save_shortcuts_btn = ctk.CTkButton(
            shortcuts_frame,
            text="💾  Save Shortcuts",
            font=FONTS["body_small"], height=28,
            fg_color=COLOR_GREEN, hover_color=COLOR_GREEN_HOVER,
            text_color="#000000",
            corner_radius=6,
            command=self._on_save_shortcuts,
        )
        self._save_shortcuts_btn.pack(anchor="w", pady=(6, 0))

        # Separator
        ctk.CTkFrame(
            card, fg_color=COLOR_BORDER, height=1
        ).pack(fill="x", padx=20, pady=(8, 0))

        # ════════════════════════════════════════
        # ── Model Size ──
        # ════════════════════════════════════════
        model_frame = ctk.CTkFrame(card, fg_color="transparent")
        model_frame.pack(fill="x", padx=20, pady=(12, 12))

        ctk.CTkLabel(
            model_frame, text="Model Size",
            font=FONTS["body_medium"], text_color=COLOR_TEXT_DIM
        ).pack(anchor="w")

        self.model_segmented = ctk.CTkSegmentedButton(
            model_frame,
            values=["tiny", "base", "small"],
            selected_color=COLOR_ACCENT,
            selected_hover_color=COLOR_ACCENT_HOVER,
            font=FONTS["body_medium"],
            command=self._on_model_change,
        )
        self.model_segmented.pack(fill="x", pady=(4, 0))
        self.model_segmented.set(self.app_state.get_model_size())

        # ════════════════════════════════════════
        # ── Local Language ──
        # ════════════════════════════════════════
        self._local_lang_frame = ctk.CTkFrame(card, fg_color="transparent")
        # Note: packed/unpacked dynamically in _update_ui based on mode

        ctk.CTkLabel(
            self._local_lang_frame, text="Local Language",
            font=FONTS["body_medium"], text_color=COLOR_TEXT_DIM
        ).pack(anchor="w")

        lang_label = ctk.CTkLabel(
            self._local_lang_frame,
            text="Choose the transcription language for the local model.",
            font=FONTS["body_small"],
            text_color=COLOR_TEXT_DIM,
            anchor="w",
            justify="left",
        )
        lang_label.pack(fill="x", pady=(4, 4))

        self._local_lang_segmented = ctk.CTkSegmentedButton(
            self._local_lang_frame,
            values=["English (fast)", "Auto Detect"],
            selected_color=COLOR_ACCENT,
            selected_hover_color=COLOR_ACCENT_HOVER,
            font=FONTS["body_medium"],
            command=self._on_local_language_change,
        )
        self._local_lang_segmented.pack(fill="x")
        # Set initial value based on state
        current_lang = self.app_state.get_local_language()
        if current_lang == "en":
            self._local_lang_segmented.set("English (fast)")
        else:
            self._local_lang_segmented.set("Auto Detect")

        # ════════════════════════════════════════
        # ── Local Model Install ──
        # ════════════════════════════════════════
        self._local_model_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._local_model_frame.pack(fill="x", padx=20, pady=(0, 12))

        ctk.CTkLabel(
            self._local_model_frame, text="Local Model",
            font=FONTS["body_medium"], text_color=COLOR_TEXT_DIM
        ).pack(anchor="w")

        self._local_model_status_label = ctk.CTkLabel(
            self._local_model_frame,
            text="",
            font=FONTS["body_small"],
            text_color=COLOR_TEXT_DIM,
            anchor="w",
            justify="left",
        )
        self._local_model_status_label.pack(fill="x", pady=(4, 4))

        self._install_model_btn = ctk.CTkButton(
            self._local_model_frame,
            text="📥  Install Whisper Model",
            font=FONTS["body_small"], height=28,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            text_color="#ffffff",
            corner_radius=6,
            command=self._on_install_local_model,
        )
        self._install_model_btn.pack(anchor="w")

        # Separator
        ctk.CTkFrame(
            card, fg_color=COLOR_BORDER, height=1
        ).pack(fill="x", padx=20, pady=(8, 0))

        # ════════════════════════════════════════
        # ── Microphone ──
        # ════════════════════════════════════════
        mic_frame = ctk.CTkFrame(card, fg_color="transparent")
        mic_frame.pack(fill="x", padx=20, pady=(12, 12))

        ctk.CTkLabel(
            mic_frame, text="Microphone",
            font=FONTS["body_medium"], text_color=COLOR_TEXT_DIM
        ).pack(anchor="w")

        self._mic_devices = list_input_devices()
        self._mic_device_names = [d["name"] for d in self._mic_devices]
        self._mic_device_ids = [d["id"] for d in self._mic_devices]

        self._mic_menu = ctk.CTkOptionMenu(
            mic_frame, values=self._mic_device_names,
            font=FONTS["body_medium"], fg_color=COLOR_INPUT_BG,
            button_color=COLOR_ACCENT, button_hover_color=COLOR_ACCENT_HOVER,
            command=self._on_mic_changed,
        )
        self._mic_menu.pack(fill="x", pady=(4, 0))

        # Set current selection from config
        saved_device = self._config.get("input_device", "default")
        if saved_device in self._mic_device_ids:
            idx = self._mic_device_ids.index(saved_device)
            self._mic_menu.set(self._mic_device_names[idx])
        else:
            self._mic_menu.set(self._mic_device_names[0])
            self.audio.set_input_device("default")

        # ════════════════════════════════════════
        # ── API Key ──
        # ════════════════════════════════════════
        api_frame = ctk.CTkFrame(card, fg_color="transparent")
        api_frame.pack(fill="x", padx=20, pady=(0, 12))

        ctk.CTkLabel(
            api_frame, text="OpenAI API Key",
            font=FONTS["body_medium"], text_color=COLOR_TEXT_DIM
        ).pack(anchor="w")

        api_input_frame = ctk.CTkFrame(api_frame, fg_color="transparent")
        api_input_frame.pack(fill="x", pady=(4, 0))

        self.api_entry = ctk.CTkEntry(
            api_input_frame,
            placeholder_text="sk-...",
            show="*",
            font=FONTS["body_medium"],
            fg_color=COLOR_INPUT_BG,
            border_color=COLOR_BORDER,
            corner_radius=6,
        )
        self.api_entry.pack(side="left", fill="x", expand=True)

        # Pre-fill API key from config
        saved_key = self.app_state.get_api_key()
        if saved_key:
            self.api_entry.insert(0, saved_key)

        self.save_api_btn = ctk.CTkButton(
            api_input_frame, text="Save",
            font=FONTS["body_medium"], height=32,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            text_color="#ffffff",
            corner_radius=6,
            command=self._on_save_api_key,
        )
        self.save_api_btn.pack(side="right", padx=(8, 0))

        # ════════════════════════════════════════
        # ── Smart Formatting ──
        # ════════════════════════════════════════
        format_frame = ctk.CTkFrame(card, fg_color="transparent")
        format_frame.pack(fill="x", padx=20, pady=(0, 16))

        ctk.CTkLabel(
            format_frame, text="Smart Formatting",
            font=FONTS["body_medium"], text_color=COLOR_TEXT_DIM
        ).pack(anchor="w")

        # --- Punctuation via Speech ---
        punct_frame = ctk.CTkFrame(format_frame, fg_color="transparent")
        punct_frame.pack(fill="x", pady=(4, 0))

        ctk.CTkLabel(
            punct_frame, text="🎤 Punctuation via speech  (\"comma\" → \",\")",
            font=FONTS["body_small"], text_color=COLOR_TEXT,
            anchor="w"
        ).pack(side="left")

        self._punct_switch = ctk.CTkSwitch(
            punct_frame, text="",
            font=FONTS["body_small"],
            progress_color=COLOR_ACCENT,
            command=self._on_punctuate_speech_toggle,
        )
        self._punct_switch.pack(side="right")
        if self.app_state.get_punctuate_speech():
            self._punct_switch.select()
        else:
            self._punct_switch.deselect()

        # --- Auto-capitalize ---
        cap_frame = ctk.CTkFrame(format_frame, fg_color="transparent")
        cap_frame.pack(fill="x", pady=(4, 0))

        ctk.CTkLabel(
            cap_frame, text="A🔠 Auto-capitalize sentences",
            font=FONTS["body_small"], text_color=COLOR_TEXT,
            anchor="w"
        ).pack(side="left")

        self._capitalize_switch = ctk.CTkSwitch(
            cap_frame, text="",
            font=FONTS["body_small"],
            progress_color=COLOR_ACCENT,
            command=self._on_auto_capitalize_toggle,
        )
        self._capitalize_switch.pack(side="right")
        if self.app_state.get_auto_capitalize():
            self._capitalize_switch.select()
        else:
            self._capitalize_switch.deselect()

        # --- Numbers as Digits ---
        num_frame = ctk.CTkFrame(format_frame, fg_color="transparent")
        num_frame.pack(fill="x", pady=(4, 0))

        ctk.CTkLabel(
            num_frame, text="1️⃣ Numbers as digits  (\"one\" → \"1\")",
            font=FONTS["body_small"], text_color=COLOR_TEXT,
            anchor="w"
        ).pack(side="left")

        self._numbers_switch = ctk.CTkSwitch(
            num_frame, text="",
            font=FONTS["body_small"],
            progress_color=COLOR_ACCENT,
            command=self._on_numbers_as_digits_toggle,
        )
        self._numbers_switch.pack(side="right")
        if self.app_state.get_numbers_as_digits():
            self._numbers_switch.select()
        else:
            self._numbers_switch.deselect()

        # ════════════════════════════════════════
        # ── General ──
        # ════════════════════════════════════════
        general_frame = ctk.CTkFrame(card, fg_color="transparent")
        general_frame.pack(fill="x", padx=20, pady=(0, 16))

        ctk.CTkLabel(
            general_frame, text="General",
            font=FONTS["body_medium"], text_color=COLOR_TEXT_DIM
        ).pack(anchor="w")

        # --- Start with Windows ---
        autostart_frame = ctk.CTkFrame(general_frame, fg_color="transparent")
        autostart_frame.pack(fill="x", pady=(4, 0))

        ctk.CTkLabel(
            autostart_frame, text="🪟  Start WaveScribe automatically with Windows",
            font=FONTS["body_small"], text_color=COLOR_TEXT,
            anchor="w",
        ).pack(side="left")

        self._autostart_switch = ctk.CTkSwitch(
            autostart_frame, text="",
            font=FONTS["body_small"],
            progress_color=COLOR_ACCENT,
            command=self._on_autostart_toggle,
        )
        self._autostart_switch.pack(side="right")
        if self.app_state.get_autostart_enabled():
            self._autostart_switch.select()
        else:
            self._autostart_switch.deselect()

        # --- Save transcription history ---
        history_frame = ctk.CTkFrame(general_frame, fg_color="transparent")
        history_frame.pack(fill="x", pady=(4, 0))

        ctk.CTkLabel(
            history_frame, text="📜  Save transcription history",
            font=FONTS["body_small"], text_color=COLOR_TEXT,
            anchor="w",
        ).pack(side="left")

        self._history_switch = ctk.CTkSwitch(
            history_frame, text="",
            font=FONTS["body_small"],
            progress_color=COLOR_ACCENT,
            command=self._on_history_toggle,
        )
        self._history_switch.pack(side="right")
        if self.app_state.get_history_enabled():
            self._history_switch.select()
        else:
            self._history_switch.deselect()

    def _build_footer(self) -> None:
        """Build the app footer."""
        footer_frame = ctk.CTkFrame(self._dictation_content, fg_color="transparent")
        footer_frame.pack(fill="x", padx=16, pady=(12, 16))

        ctk.CTkLabel(
            footer_frame, text="WaveScribe v1.3",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM
        ).pack()

    # ── Hotkey Capture (tkinter-based) ──
    #
    # Uses tkinter's native <KeyPress> binding to detect key combinations.
    # This is far more reliable than keyboard.hook() on Windows because it
    # runs within the normal tkinter event loop — no external hooks needed.
    #
    # How it works:
    #   1. Click "🎯 Capture" → enters capture mode, window takes focus
    #   2. Press any key combination (e.g. Ctrl+Shift+T)
    #   3. The permanent <KeyPress> binding on the window fires
    #   4. _on_global_keypress reads event.state (modifier bitmask) and
    #      event.keysym to build the combo string
    #   5. Entry field updates and capture mode ends automatically
    #   6. Escape cancels capture without changing the entry

    _TKINTER_KEY_MAP = {
        'return': 'enter',
        'escape': 'esc',
        'prior': 'page_up',
        'next': 'page_down',
        'home': 'home',
        'end': 'end',
        'insert': 'insert',
        'print': 'print_screen',
        'pause': 'pause',
        'menu': 'menu',
    }

    def _on_global_keypress(self, event) -> None:
        """Handle <KeyPress> events on the main window.

        When capture mode is active, reads the modifier state from
        ``event.state`` and the pressed key from ``event.keysym``
        to build the shortcut combo. Auto-finalizes when a valid
        modifier+key combination is detected.
        """
        # Handle TTS hotkey capture
        if self._tts_capturing:
            self._on_tts_global_keypress(event)
            return

        if not self._capturing:
            return

        keysym = event.keysym

        # Ignore plain modifier key presses (they are already reflected in state)
        if keysym.lower() in (
            'control_l', 'control_r', 'shift_l', 'shift_r',
            'alt_l', 'alt_r', 'super_l', 'super_r',
        ):
            return

        # Escape cancels capture
        if keysym.lower() == 'escape':
            self._stop_capturing()
            return

        # Detect which modifier keys are held from event.state bitmask.
        # Standard tkinter modifier masks on Windows:
        #   0x0001 = Shift     0x0002 = Caps Lock
        #   0x0004 = Control   0x0008 = Alt
        #   0x0080 = Windows/Super
        mods = []
        if event.state & 0x0004:
            mods.append('ctrl')
        if event.state & 0x0001:
            mods.append('shift')
        if event.state & 0x0008:
            mods.append('alt')
        if event.state & 0x0080:
            mods.append('win')

        if not mods:
            # A regular key without any modifier is not a valid shortcut
            return

        # Translate tkinter keysym → keyboard library key name
        key = self._TKINTER_KEY_MAP.get(keysym.lower(), keysym.lower())

        combo = '+'.join(mods + [key])

        # Update the correct entry field
        if self._capturing_start:
            self._start_hotkey_var.set(combo)
        else:
            self._stop_hotkey_var.set(combo)

        # Auto-stop — combo was captured successfully
        self._stop_capturing()

    def _start_capturing(self, is_start: bool) -> None:
        """Toggle hotkey capture mode on/off.

        Click once to start: focuses the window to receive keyboard
        events. The permanent <KeyPress> binding (``_on_global_keypress``)
        will detect the next valid modifier+key combination and
        automatically finalize.

        Click again on any Capture button to cancel without changes.
        """
        btn = self._capture_start_btn if is_start else self._capture_stop_btn
        other_btn = self._capture_stop_btn if is_start else self._capture_start_btn

        # ── If already capturing, stop (toggle off / cancel) ──
        if self._capturing:
            self._stop_capturing()
            return

        # ── Start capturing ──
        self._capturing = True
        self._capturing_start = is_start

        btn.configure(text="⏹ Stop", state="normal")
        other_btn.configure(state="disabled")
        self._save_shortcuts_btn.configure(state="disabled")

        # Force-focus the main window so it receives the KeyPress events.
        # ``focus_force`` is used instead of ``focus_set`` because the
        # just-clicked button still holds focus; ``focus_set`` may not
        # override this on Windows.
        self.focus_force()

    def _on_tts_global_keypress(self, event) -> None:
        """Handle keypress during TTS hotkey capture mode."""
        if not self._tts_capturing:
            return

        keysym = event.keysym.lower()

        if keysym in ("control_l", "control_r", "shift_l", "shift_r", "alt_l", "alt_r", "super_l", "super_r"):
            return

        if keysym == "escape":
            self._tts_stop_capturing()
            return

        # Detect modifiers
        mods = []
        if event.state & 0x0004:
            mods.append("ctrl")
        if event.state & 0x0001:
            mods.append("shift")
        if event.state & 0x0008:
            mods.append("alt")
        if event.state & 0x0080:
            mods.append("win")

        if not mods:
            return

        key = self._TKINTER_KEY_MAP.get(keysym, keysym)
        combo = "+".join(mods + [key])
        self._tts_apply_hotkey(combo)

    def _stop_capturing(self) -> None:
        """Stop capture mode and reset UI."""
        self._capturing = False

        # Reset both buttons and re-enable the Save button
        self._capture_start_btn.configure(text="🎯 Capture", state="normal")
        self._capture_stop_btn.configure(text="🎯 Capture", state="normal")
        self._save_shortcuts_btn.configure(state="normal")

    # ── Event Queue Polling ──

    def _poll_queue(self) -> None:
        """Poll the event queue for hotkey events (runs every 100ms)."""
        try:
            while True:
                event = self.event_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass

        self.after(100, self._poll_queue)

    def _handle_event(self, event) -> None:
        """Handle an event from the queue (from hotkeys or other threads)."""
        # Route TTS events
        if isinstance(event, str) and event in ("toggle_read", "cycle_voice"):
            self._handle_tts_event(event)
            return
        if isinstance(event, tuple) and isinstance(event[0], str) and event[0].startswith("tts_"):
            self._handle_tts_event(event)
            return

        if event == "start_recording":
            if self.app_state.get_status() == AppStatus.IDLE:
                self._start_recording()
        elif event == "stop_recording":
            if self.app_state.get_status() == AppStatus.RECORDING:
                self._stop_recording(auto_insert=True)

    # ── Input Level Meter ──

    def _poll_input_level(self) -> None:
        """Update the live input meter (polled every 50ms while recording)."""
        status = self.app_state.get_status()
        if status == AppStatus.RECORDING:
            level = self.audio.get_input_level()
            # Colour shifts green → yellow → red as the input gets louder
            if level < 0.55:
                color = COLOR_GREEN
            elif level < 0.8:
                color = COLOR_YELLOW
            else:
                color = COLOR_RED
            self._meter_bar.set(level)
            self._meter_bar.configure(progress_color=color)
            self._meter_label.configure(text=f"{round(level * 100)}%")
            # Update the floating overlay bar too
            self._overlay.set_level(level, color)
            self._previously_recording = True
        else:
            # Reset the bar once when transitioning from recording → idle,
            # so we don't spam 20 widget updates per second forever.
            if getattr(self, '_previously_recording', False):
                self._previously_recording = False
                self._overlay.set_level(0.0, COLOR_GREEN)
                self._meter_bar.set(0)
                self._meter_bar.configure(progress_color=COLOR_INPUT_BG)
                self._meter_label.configure(text="0%")
        self.after(50, self._poll_input_level)

    # ── Action Handlers ──

    def _on_record_click(self) -> None:
        """Record button clicked."""
        self._start_recording()

    def _on_stop_click(self) -> None:
        """Stop button clicked."""
        self._stop_recording(auto_insert=False)

    def _start_recording(self) -> None:
        """Start audio capture."""
        self._auto_inserted = False
        self._transcription_cancelled = False
        self.app_state.set_status(AppStatus.RECORDING)
        self._update_ui()
        play_start_sound()

        try:
            self.audio.start()
        except AudioCaptureError as e:
            self.app_state.set_status(AppStatus.ERROR)
            self.app_state.set_error_message(str(e))
            self._update_ui()

    def _stop_recording(self, auto_insert: bool = False) -> None:
        """Stop audio capture and transcribe.

        Args:
            auto_insert: If True, automatically type the transcription
                into the focused window after it completes.
        """
        self.app_state.set_status(AppStatus.TRANSCRIBING)
        self._update_ui()
        play_stop_sound()

        # Stop audio capture in a background thread
        def _do_stop() -> None:
            try:
                wav_bytes = self.audio.stop()

                # Check formatting setting
                mode = self.app_state.get_mode()
                # When "punctuation via speech" is OFF, hand punctuation over to
                # the model by guiding it with a punctuated example prompt. This
                # keeps the output consistently punctuated instead of sometimes
                # having the model emit punctuation and sometimes not.
                punctuation_prompt = None
                if not self.app_state.get_punctuate_speech():
                    # Skip the style prompt when auto-detecting the language, so
                    # the English example can't bias language detection.
                    local_lang = self.app_state.get_local_language()
                    if mode == AppMode.CLOUD or local_lang == "en":
                        punctuation_prompt = PUNCTUATION_PROMPT
                if mode == AppMode.CLOUD:
                    api_key = self.app_state.get_api_key()
                    if not api_key:
                        self.app_state.set_status(AppStatus.ERROR)
                        self.app_state.set_error_message(
                            "No API key configured. "
                            "Please enter your OpenAI API key in Settings."
                        )
                        # Schedule UI update on main thread
                        self.after(0, self._update_ui)
                        return

                    text = transcribe_cloud(wav_bytes, api_key, prompt=punctuation_prompt)
                else:
                    local_status = self.app_state.get_local_model_status()
                    if local_status != LocalModelStatus.READY:
                        self.app_state.set_status(AppStatus.ERROR)
                        self.app_state.set_error_message(
                            "Whisper model not ready. Please install it in Settings."
                        )
                        self.after(0, self._update_ui)
                        return

                    model_size = self.app_state.get_model_size()
                    local_language = self.app_state.get_local_language()
                    text = transcribe_local(
                        wav_bytes, model_size,
                        language=local_language, initial_prompt=punctuation_prompt,
                    )

                # If transcription was cancelled, don't update UI
                if self._transcription_cancelled:
                    return

                # Reset the flag for next use
                self._transcription_cancelled = False

                # Apply smart formatting steps based on individual toggles
                if self.app_state.get_punctuate_speech():
                    text = apply_punctuation(text)
                else:
                    # When spoken punctuation is OFF, auto-add punctuation marks
                    # so the text has proper sentence endings (especially important
                    # for the local model which outputs no punctuation at all)
                    text = auto_add_sentence_punctuation(text)
                # Always clean spacing and redundant punctuation
                text = clean_spacing(text)
                text = cleanup_redundant_punctuation(text)
                if self.app_state.get_auto_capitalize():
                    text = auto_capitalize(text)
                if self.app_state.get_numbers_as_digits():
                    text = convert_numbers(text)
                # Always clean up spacing (final pass)
                text = clean_spacing(text)

                # If auto-capitalize is OFF, lowercase the first letter
                # (Whisper intrinsically capitalizes the first letter)
                if not self.app_state.get_auto_capitalize() and text:
                    text = text[0].lower() + text[1:]

                self.app_state.set_last_transcription(text)
                # Save to transcription history (if enabled)
                if self.app_state.get_history_enabled():
                    self._history.add(text)
                self.app_state.set_status(AppStatus.IDLE)
                # Schedule UI update on main thread
                self.after(0, self._update_ui)

                # Auto-insert transcription into focused window if requested
                if auto_insert and text:
                    self.after(200, self._auto_insert_text)

            except (AudioCaptureError, TranscriptionError) as e:
                self.app_state.set_status(AppStatus.ERROR)
                self.app_state.set_error_message(str(e))
                # Schedule UI update on main thread
                self.after(0, self._update_ui)

        thread = threading.Thread(target=_do_stop, daemon=True)
        thread.start()

    def _auto_insert_text(self) -> None:
        """Minimize window and type transcription into the focused field.

        type_text() already has an internal 0.3s sleep to allow the
        minimize animation to complete before typing begins.

        Uses ``_auto_inserted`` guard to prevent double-pasting
        if ``_stop_recording`` is somehow called twice.
        """
        if self._auto_inserted:
            return
        self._auto_inserted = True

        text = self.app_state.get_last_transcription()
        if text:
            self.iconify()
            type_text(text)

    def _on_insert_click(self) -> None:
        """Insert transcription into focused window."""
        text = self.app_state.get_last_transcription()
        if text:
            # Minimize the window so the user can click the target
            self.iconify()
            self._auto_inserted = True
            self.after(200, lambda: type_text(text))

    def _on_clear_transcription(self) -> None:
        """Clear the transcription text."""
        self.app_state.set_last_transcription("")
        self._update_ui()

    def _on_cancel_transcription(self) -> None:
        """Cancel the current transcription and return to idle."""
        self._transcription_cancelled = True
        self.app_state.set_status(AppStatus.IDLE)
        self.app_state.clear_error()
        self._update_ui()

    def _on_mode_change(self, value: str) -> None:
        """Handle mode toggle change and persist immediately."""
        mode = AppMode.CLOUD if value == "Cloud" else AppMode.LOCAL
        self.app_state.set_mode(mode)
        self._save_current_config()
        self.app_state.set_status(AppStatus.IDLE)
        self._update_ui()

    def _load_local_model_in_background(self) -> None:
        """Load the bundled/local Whisper model into memory in a background thread.

        This calls ``load_whisper_model()`` which runs
        ``whisper.load_model()`` to populate the global ``_whisper_model``
        variable.  Without this, ``transcribe_local()`` will refuse with
        "Whisper model is not loaded."
        """
        def _do_load() -> None:
            try:
                model_size = self.app_state.get_model_size()
                load_whisper_model(model_size)
            except Exception:
                self.app_state.set_local_model_status(LocalModelStatus.ERROR)
                self.after(0, self._update_ui)

        thread = threading.Thread(target=_do_load, daemon=True)
        thread.start()

    def _on_install_local_model(self) -> None:
        """Install the Whisper package and load the model in a background thread."""
        self.app_state.set_local_model_status(LocalModelStatus.INSTALLING)
        self._install_model_btn.configure(state="disabled", text="⏳  Installing Whisper...")
        self._local_model_status_label.configure(
            text="Downloading and installing openai-whisper package...",
            text_color=COLOR_YELLOW,
        )

        def _do_install() -> None:
            try:
                install_whisper_package()

                self.app_state.set_local_model_status(LocalModelStatus.MODEL_LOADING)
                self.after(0, lambda: self._local_model_status_label.configure(
                    text="Downloading Whisper model weights (first-time download)...",
                    text_color=COLOR_YELLOW,
                ))

                model_size = self.app_state.get_model_size()
                load_whisper_model(model_size)

                self.app_state.set_local_model_status(LocalModelStatus.READY)
                self.after(0, self._update_ui)

            except (TranscriptionError, Exception):
                self.app_state.set_local_model_status(LocalModelStatus.ERROR)
                self.after(0, self._update_ui)

        thread = threading.Thread(target=_do_install, daemon=True)
        thread.start()

    def _update_local_model_ui(self, status: str, is_error: bool = False) -> None:
        """Update the local model install UI after install attempt."""
        self._install_model_btn.configure(state="normal")
        self._local_model_status_label.configure(
            text=status,
            text_color=COLOR_RED if is_error else COLOR_GREEN,
        )

    def _on_model_change(self, value: str) -> None:
        """Handle model size selection change and persist immediately."""
        self.app_state.set_model_size(value)
        self._save_current_config()

    def _on_save_api_key(self) -> None:
        """Save the API key from the entry field and persist to config immediately."""
        key = self.api_entry.get().strip()
        if key:
            self.app_state.set_api_key(key)
            self._config["api_key"] = key
            self._save_current_config()
            self._config_dirty = False
            # Visual feedback
            self.save_api_btn.configure(
                text="✓ Saved", fg_color=COLOR_GREEN, text_color="#000000"
            )
            self.after(2000, lambda: self.save_api_btn.configure(
                text="Save", fg_color=COLOR_ACCENT, text_color="#ffffff"
            ))
            # Update UI so Record button enables if Cloud mode + key was missing
            self._update_ui()

    def _on_punctuate_speech_toggle(self) -> None:
        """Handle punctuation via speech toggle and persist immediately."""
        enabled = bool(self._punct_switch.get())
        self.app_state.set_punctuate_speech(enabled)
        self._save_current_config()

    def _on_auto_capitalize_toggle(self) -> None:
        """Handle auto-capitalize toggle and persist immediately."""
        enabled = bool(self._capitalize_switch.get())
        self.app_state.set_auto_capitalize(enabled)
        self._save_current_config()

    def _on_numbers_as_digits_toggle(self) -> None:
        """Handle numbers as digits toggle and persist immediately."""
        enabled = bool(self._numbers_switch.get())
        self.app_state.set_numbers_as_digits(enabled)
        self._save_current_config()

    def _on_autostart_toggle(self) -> None:
        """Apply the start-with-Windows setting to the registry and persist."""
        enabled = bool(self._autostart_switch.get())
        if apply_autostart_setting(enabled):
            self.app_state.set_autostart_enabled(enabled)
            self._save_current_config()
        else:
            # Registry write failed — revert the switch to the actual state
            if enabled:
                self._autostart_switch.deselect()
            else:
                self._autostart_switch.select()

    def _on_mic_changed(self, display_name: str) -> None:
        """Handle microphone device selection change."""
        idx = self._mic_device_names.index(display_name) if display_name in self._mic_device_names else 0
        device_id = self._mic_device_ids[idx]
        self._config["input_device"] = device_id
        self.audio.set_input_device(device_id)
        self._save_current_config()

    def _on_history_toggle(self) -> None:
        """Handle history toggle and persist immediately."""
        enabled = bool(self._history_switch.get())
        self.app_state.set_history_enabled(enabled)
        self._save_current_config()
        self._update_ui()

    def _on_local_language_change(self, value: str) -> None:
        """Handle local language selection change and persist immediately."""
        lang = "en" if value == "English (fast)" else "auto"
        self.app_state.set_local_language(lang)
        self._save_current_config()

    def _on_save_shortcuts(self) -> None:
        """Save the custom hotkeys, re-register them, and persist to config."""
        start_hk = self._start_hotkey_var.get().strip().lower()
        stop_hk = self._stop_hotkey_var.get().strip().lower()

        # Basic validation: must contain '+'
        if "+" not in start_hk or "+" not in stop_hk:
            self._save_shortcuts_btn.configure(
                text="⚠️  Invalid shortcut (use ctrl+shift+x)",
                fg_color=COLOR_RED,
            )
            self.after(2500, lambda: self._save_shortcuts_btn.configure(
                text="💾  Save Shortcuts",
                fg_color=COLOR_GREEN,
            ))
            return

        # Update config
        self._config["hotkey_start"] = start_hk
        self._config["hotkey_stop"] = stop_hk
        self._config_dirty = True

        # Re-register hotkeys
        self.hotkey_mgr.set_hotkeys(start_hk, stop_hk)

        # Update the shortcuts display in the control card
        self._update_shortcuts_display(start_hk, stop_hk)

        # Save config immediately
        self._save_current_config()
        self._config_dirty = False

        # Visual feedback
        self._save_shortcuts_btn.configure(
            text="✅  Shortcuts saved!", fg_color=COLOR_GREEN, text_color="#000000"
        )
        self.after(2000, lambda: self._save_shortcuts_btn.configure(
            text="💾  Save Shortcuts",
            fg_color=COLOR_GREEN, text_color="#000000",
        ))

    @staticmethod
    def _format_hotkey_display(hk: str) -> str:
        """Format a hotkey string for display (e.g. 'ctrl+shift+r' → 'Ctrl+Shift+R')."""
        return "+".join(p.capitalize() for p in hk.split("+"))

    def _update_shortcuts_display(self, start_hk: str, stop_hk: str) -> None:
        """Refresh the shortcuts info shown in the control card label."""
        text = (
            f"{self._format_hotkey_display(start_hk)}  Start Recording\n"
            f"{self._format_hotkey_display(stop_hk)}  Stop & Transcribe"
        )
        if hasattr(self, "_shortcuts_label"):
            self._shortcuts_label.configure(text=text)

    # ── UI Updates ──

    def _update_local_model_visibility(self) -> None:
        """Show/hide the local model install controls based on the current status."""
        status = self.app_state.get_local_model_status()

        if status == LocalModelStatus.PACKAGE_MISSING:
            self._local_model_frame.pack(fill="x", padx=20, pady=(0, 12))
            self._local_model_status_label.configure(
                text="Whisper package not installed. Click below to install.",
                text_color=COLOR_TEXT_DIM,
            )
            self._install_model_btn.configure(
                state="normal",
                text="📥  Install Whisper Model",
                fg_color=COLOR_ACCENT,
            )
        elif status == LocalModelStatus.INSTALLING:
            self._local_model_frame.pack(fill="x", padx=20, pady=(0, 12))
            self._local_model_status_label.configure(
                text="⏳  Installing openai-whisper package...",
                text_color=COLOR_YELLOW,
            )
            self._install_model_btn.configure(state="disabled", text="⏳  Installing...")
        elif status == LocalModelStatus.MODEL_LOADING:
            self._local_model_frame.pack(fill="x", padx=20, pady=(0, 12))
            self._local_model_status_label.configure(
                text="⏳  Downloading Whisper model weights (first-time)...",
                text_color=COLOR_YELLOW,
            )
            self._install_model_btn.configure(state="disabled", text="⏳  Downloading...")
        elif status == LocalModelStatus.READY:
            self._local_model_frame.pack(fill="x", padx=20, pady=(0, 12))
            self._local_model_status_label.configure(
                text="✅  Whisper model ready",
                text_color=COLOR_GREEN,
            )
            self._install_model_btn.configure(
                state="normal",
                text="✅  Reinstall Model",
                fg_color=COLOR_ACCENT,
            )
        elif status == LocalModelStatus.ERROR:
            self._local_model_frame.pack(fill="x", padx=20, pady=(0, 12))
            self._local_model_status_label.configure(
                text="❌  Installation failed. Check connection and try again.",
                text_color=COLOR_RED,
            )
            self._install_model_btn.configure(
                state="normal",
                text="🔄  Retry Installation",
                fg_color=COLOR_RED,
            )

    def _update_ui(self) -> None:
        """Update all UI elements based on current state and show overlay."""
        status = self.app_state.get_status()
        error_msg = self.app_state.get_error_message()

        # ── Status dot ──
        dot_color = {
            AppStatus.IDLE: COLOR_GREEN,
            AppStatus.RECORDING: COLOR_RED,
            AppStatus.TRANSCRIBING: COLOR_YELLOW,
            AppStatus.ERROR: COLOR_RED,
        }.get(status, COLOR_GREEN)
        self.status_dot.configure(text_color=dot_color)

        # ── Status text ──
        status_text = self.app_state.get_status_text()
        self.status_label.configure(text=status_text)

        # ── Recording status ──
        recording_map = {
            AppStatus.IDLE: "● Idle",
            AppStatus.RECORDING: "● Recording...",
            AppStatus.TRANSCRIBING: "● Transcribing...",
            AppStatus.ERROR: "● Error",
        }
        self.recording_status.configure(text=recording_map.get(status, "● Idle"))

        if status in (AppStatus.ERROR, AppStatus.RECORDING):
            self.recording_status.configure(text_color=COLOR_RED)
        elif status == AppStatus.TRANSCRIBING:
            self.recording_status.configure(text_color=COLOR_YELLOW)
        else:
            self.recording_status.configure(text_color=COLOR_TEXT_DIM)

        # ── Cancel transcription button — visible only during transcribing ──
        if status == AppStatus.TRANSCRIBING:
            self._cancel_transcribe_btn.pack(side="right", padx=(8, 0))
        else:
            self._cancel_transcribe_btn.pack_forget()

        # ── Input level meter — always visible, no layout shift ──
        # (the meter_row is packed once during build and never moved)

        # ── Buttons ──
        # Determine if Record should be enabled (Cloud needs API key, Local always ok)
        mode = self.app_state.get_mode()
        cloud_missing_key = (mode == AppMode.CLOUD and not self.app_state.get_api_key())
        record_enabled = not cloud_missing_key

        if status == AppStatus.IDLE:
            if record_enabled:
                self.record_btn.configure(state="normal")
            else:
                self.record_btn.configure(state="disabled")
            self.stop_btn.configure(state="disabled")
        elif status == AppStatus.RECORDING:
            self.record_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
        else:  # transcribing or error
            self.record_btn.configure(state="disabled")
            self.stop_btn.configure(state="disabled")

        # ── Transcription display ──
        text = self.app_state.get_last_transcription()
        self.transcription_text.configure(state="normal")
        self.transcription_text.delete("1.0", "end")
        if text:
            self.transcription_text.insert("1.0", text)
            self.insert_btn.configure(state="normal")
        else:
            self.transcription_text.insert(
                "1.0", "Your transcription will appear here..."
            )
            self.insert_btn.configure(state="disabled")
        self.transcription_text.configure(state="disabled")

        # ── Cloud mode missing API key notice ──
        if status == AppStatus.IDLE and cloud_missing_key:
            self.transcription_text.configure(state="normal")
            self.transcription_text.delete("1.0", "end")
            self.transcription_text.insert(
                "1.0",
                "🔑  Enter your OpenAI API key in Settings above, then click Record.\n\n"
                "Or switch to the Local model to transcribe without an API key."
            )
            self.transcription_text.configure(state="disabled")
            self.insert_btn.configure(state="disabled")

        # ── Error display ──
        elif status == AppStatus.ERROR and error_msg:
            self.transcription_text.configure(state="normal")
            self.transcription_text.delete("1.0", "end")
            self.transcription_text.insert("1.0", f"⚠️ {error_msg}")
            self.transcription_text.configure(state="disabled")
            self.insert_btn.configure(state="disabled")

        # ── Local model install UI ──
        self._update_local_model_visibility()

        # ── Local language visibility — only show in Local mode ──
        if mode == AppMode.LOCAL:
            self._local_lang_frame.pack(fill="x", padx=20, pady=(0, 12))
        else:
            self._local_lang_frame.pack_forget()

        # ── History card visibility — hidden when history is disabled ──
        if self.app_state.get_history_enabled():
            self._history_card.pack(
                fill="x", padx=16, pady=(12, 0), before=self._settings_card
            )
            self._refresh_history()
        else:
            self._history_card.pack_forget()

        # ── Overlay (floating status indicator) ──
        overlay_map = {
            AppStatus.RECORDING: ("Recording", COLOR_RED),
            AppStatus.TRANSCRIBING: ("Transcribing", COLOR_YELLOW),
        }
        if status in overlay_map:
            label, color = overlay_map[status]
            self._overlay.update(label, color)
        else:
            self._overlay.hide()

    # ═══════════════════════════════════════════════════════════
    #  Tab switching
    # ═══════════════════════════════════════════════════════════

    def _on_tab_switch(self, tab_name: str) -> None:
        """Switch between Dictation and Read Out tabs."""
        if tab_name == self._active_tab:
            return
        self._active_tab = tab_name
        if tab_name == "Dictation":
            self._readout_content.pack_forget()
            self._dictation_content.pack(fill="both", expand=True, padx=0, pady=0)
        else:
            self._dictation_content.pack_forget()
            self._readout_content.pack(fill="both", expand=True, padx=0, pady=0)

    # ═══════════════════════════════════════════════════════════
    #  Read Out (TTS) tab — UI builders
    # ═══════════════════════════════════════════════════════════

    def _tts_card(self, parent=None) -> ctk.CTkFrame:
        container = parent or self._readout_content
        card = ctk.CTkFrame(
            container, fg_color=COLOR_CARD, corner_radius=10,
            border_width=1, border_color=COLOR_BORDER,
        )
        card.pack(fill="x", padx=16, pady=(10, 0))
        return card

    def _build_tts_control_card(self) -> None:
        card = self._tts_card()
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(16, 4))

        ctk.CTkLabel(
            header, text="🔊  Read Out", font=FONTS["title"], text_color=COLOR_TEXT
        ).pack(anchor="w")

        ctk.CTkLabel(
            card,
            text="Select any text anywhere, press your hotkey, and listen.",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 16))

        # ── Status row ──
        status_row = ctk.CTkFrame(card, fg_color="transparent")
        status_row.pack(fill="x", padx=20, pady=(0, 0))

        self._tts_status_dot = ctk.CTkLabel(
            status_row, text="●", font=FONTS["icon"], text_color=COLOR_GREEN, width=20,
        )
        self._tts_status_dot.pack(side="left")

        self._tts_status_label = ctk.CTkLabel(
            status_row, text="", font=FONTS["body"], text_color=COLOR_TEXT, anchor="w",
        )
        self._tts_status_label.pack(side="left", fill="x", expand=True, padx=(8, 8))

        # ── Action buttons ──
        buttons_row = ctk.CTkFrame(card, fg_color="transparent")
        buttons_row.pack(fill="x", padx=20, pady=(14, 4))

        self._tts_read_btn = ctk.CTkButton(
            buttons_row, text="🔊  Read selected text", height=40,
            font=FONTS["body_large"], fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, corner_radius=8,
            command=self._on_tts_read_selection,
        )
        self._tts_read_btn.pack(side="left", fill="x", expand=True)

        self._tts_stop_btn = ctk.CTkButton(
            buttons_row, text="⏹  Stop", height=40, width=92,
            font=FONTS["body_large"], fg_color=COLOR_RED,
            hover_color=COLOR_RED_HOVER, corner_radius=8,
            state="disabled", command=self._on_tts_stop,
        )
        self._tts_stop_btn.pack(side="right", padx=(8, 0))

        # ── Notice line ──
        self._tts_notice_label = ctk.CTkLabel(
            card, text="", font=FONTS["body_small"], text_color=COLOR_YELLOW,
            anchor="w", wraplength=520,
        )
        self._tts_notice_label.pack(fill="x", padx=20, pady=(0, 4))

        # ── Manual text box ──
        ctk.CTkLabel(
            card, text="…or paste text here to hear it:",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=20, pady=(6, 0))

        self._tts_text_box = ctk.CTkTextbox(
            card, height=80, font=FONTS["body"], fg_color=COLOR_INPUT_BG,
            border_width=1, border_color=COLOR_BORDER, corner_radius=8,
            wrap="word",
        )
        self._tts_text_box.pack(fill="x", padx=20, pady=(4, 4))
        self._tts_text_box.insert("1.0", "")

        self._tts_speak_box_btn = ctk.CTkButton(
            card, text="▶  Speak text", height=30,
            font=FONTS["body_small"], fg_color=COLOR_GREEN,
            hover_color=COLOR_GREEN_HOVER, text_color="#000000", corner_radius=6,
            command=self._on_tts_speak_box,
        )
        self._tts_speak_box_btn.pack(fill="x", padx=20, pady=(0, 16))

    def _build_tts_voice_card(self) -> None:
        card = self._tts_card()
        ctk.CTkLabel(
            card, text="🗣  Voice", font=FONTS["body_large_bold"], text_color=COLOR_TEXT,
        ).pack(anchor="w", padx=20, pady=(16, 6))

        # ── Backend picker ──
        backend_row = ctk.CTkFrame(card, fg_color="transparent")
        backend_row.pack(fill="x", padx=20, pady=(0, 4))

        ctk.CTkLabel(
            backend_row, text="Engine", font=FONTS["body_small"], text_color=COLOR_TEXT_DIM,
        ).pack(side="left")

        self._tts_backend_options = [
            (name, self.tts_engine.engine(name).display_name) for name in available_backends()
        ]
        self._tts_backend_menu = ctk.CTkOptionMenu(
            backend_row, values=[d for _, d in self._tts_backend_options],
            font=FONTS["body_small"], fg_color=COLOR_INPUT_BG,
            button_color=COLOR_ACCENT, button_hover_color=COLOR_ACCENT_HOVER,
            command=self._on_tts_backend_changed,
        )
        self._tts_backend_menu.pack(side="right")
        current = self.tts_engine.get_backend()
        current_display = next(d for n, d in self._tts_backend_options if n == current)
        self._tts_backend_menu.set(current_display)

        # ── Voice search ──
        search_row = ctk.CTkFrame(card, fg_color="transparent")
        search_row.pack(fill="x", padx=20, pady=(6, 0))

        self._tts_voice_search = ctk.CTkEntry(
            search_row, placeholder_text="🔍  Search voices (e.g. Spanish, Aria)…",
            font=FONTS["body_small"], fg_color=COLOR_INPUT_BG,
            border_width=1, border_color=COLOR_BORDER, corner_radius=6, height=28,
        )
        self._tts_voice_search.pack(side="left", fill="x", expand=True)
        self._tts_voice_search.bind("<KeyRelease>", lambda _e: self._tts_on_voice_search())

        self._tts_search_clear_btn = ctk.CTkButton(
            search_row, text="✕", width=28, height=26,
            font=FONTS["body_small"], fg_color="transparent",
            hover_color=COLOR_BORDER, text_color=COLOR_TEXT_DIM, corner_radius=4,
            command=self._tts_clear_voice_search,
        )
        self._tts_search_clear_btn.pack(side="right", padx=(6, 0))

        # ── Voice picker ──
        voice_row = ctk.CTkFrame(card, fg_color="transparent")
        voice_row.pack(fill="x", padx=20, pady=(8, 4))

        ctk.CTkLabel(
            voice_row, text="⭐  Main voice", font=FONTS["body_small"], text_color=COLOR_TEXT_DIM,
        ).pack(side="left")

        self._tts_voice_menu = ctk.CTkOptionMenu(
            voice_row, values=["(loading…)"],
            font=FONTS["body_small"], fg_color=COLOR_INPUT_BG,
            button_color=COLOR_ACCENT, button_hover_color=COLOR_ACCENT_HOVER,
            command=self._on_tts_voice_changed,
        )
        self._tts_voice_menu.pack(side="right", fill="x", expand=True, padx=(12, 0))

        self._tts_voices_state_label = ctk.CTkLabel(
            card, text="", font=FONTS["body_small"], text_color=COLOR_TEXT_DIM, anchor="w",
        )
        self._tts_voices_state_label.pack(fill="x", padx=20, pady=(0, 8))

        # ── Secondary voices ──
        ctk.CTkLabel(
            card, text="🔁  Voice list — press Cycle or the swap hotkey to swap",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 4))

        self._tts_secondary_rows_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._tts_secondary_rows_frame.pack(fill="x", padx=20, pady=(0, 4))

        add_row = ctk.CTkFrame(card, fg_color="transparent")
        add_row.pack(fill="x", padx=20, pady=(0, 8))

        self._tts_add_menu = ctk.CTkOptionMenu(
            add_row, values=["(no voices)"], width=0,
            font=FONTS["body_small"], fg_color=COLOR_INPUT_BG,
            button_color=COLOR_ACCENT, button_hover_color=COLOR_ACCENT_HOVER,
        )
        self._tts_add_menu.pack(side="left", fill="x", expand=True)

        self._tts_add_btn = ctk.CTkButton(
            add_row, text="＋  Add voice", height=26, width=110,
            font=FONTS["body_small"], fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, corner_radius=6,
            command=self._on_tts_add_secondary,
        )
        self._tts_add_btn.pack(side="right", padx=(8, 0))

        self._tts_cycle_btn = ctk.CTkButton(
            card, text="🔁  Cycle voice", height=32,
            font=FONTS["body_small"], fg_color=COLOR_GREEN,
            hover_color=COLOR_GREEN_HOVER, text_color="#000000", corner_radius=6,
            command=self._on_tts_cycle_voice,
        )
        self._tts_cycle_btn.pack(fill="x", padx=20, pady=(0, 16))

    def _build_tts_speed_card(self) -> None:
        card = self._tts_card()

        # ── Speed ──
        speed_row = ctk.CTkFrame(card, fg_color="transparent")
        speed_row.pack(fill="x", padx=20, pady=(14, 0))
        ctk.CTkLabel(
            speed_row, text="Speed", font=FONTS["body_small"], text_color=COLOR_TEXT_DIM,
        ).pack(side="left")
        self._tts_rate_value_label = ctk.CTkLabel(
            speed_row, text="100%", font=FONTS["body_small"], text_color=COLOR_TEXT,
            width=48, anchor="e",
        )
        self._tts_rate_value_label.pack(side="right")

        self._tts_rate_slider = ctk.CTkSlider(
            card, from_=50, to=150, number_of_steps=20,
            progress_color=COLOR_ACCENT, button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER, command=self._on_tts_rate_changed,
        )
        self._tts_rate_slider.pack(fill="x", padx=20, pady=(4, 4))
        self._tts_rate_slider.set(self._config.get("tts_rate_percent", 100))

        # ── Volume ──
        volume_row = ctk.CTkFrame(card, fg_color="transparent")
        volume_row.pack(fill="x", padx=20, pady=(10, 0))
        ctk.CTkLabel(
            volume_row, text="Volume", font=FONTS["body_small"], text_color=COLOR_TEXT_DIM,
        ).pack(side="left")
        self._tts_volume_value_label = ctk.CTkLabel(
            volume_row, text="100", font=FONTS["body_small"], text_color=COLOR_TEXT,
            width=48, anchor="e",
        )
        self._tts_volume_value_label.pack(side="right")

        self._tts_volume_slider = ctk.CTkSlider(
            card, from_=0, to=100, number_of_steps=20,
            progress_color=COLOR_ACCENT, button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER, command=self._on_tts_volume_changed,
        )
        self._tts_volume_slider.pack(fill="x", padx=20, pady=(4, 16))
        self._tts_volume_slider.set(self._config.get("tts_volume", 100))

    def _build_tts_hotkey_card(self) -> None:
        card = self._tts_card()
        ctk.CTkLabel(
            card, text="⌨  Hotkey", font=FONTS["body_large_bold"], text_color=COLOR_TEXT,
        ).pack(anchor="w", padx=20, pady=(16, 4))

        ctk.CTkLabel(
            card,
            text="Select text anywhere, then press this shortcut to read it aloud. "
                 "Press it again to stop.",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM, anchor="w", wraplength=520,
        ).pack(fill="x", padx=20, pady=(0, 6))

        hotkey_row = ctk.CTkFrame(card, fg_color="transparent")
        hotkey_row.pack(fill="x", padx=20, pady=(0, 6))

        self._tts_hotkey_display = ctk.CTkLabel(
            hotkey_row, text=self._config.get("tts_hotkey", "ctrl+alt+space"),
            font=FONTS["body_large_bold"], text_color=COLOR_GREEN, anchor="w",
        )
        self._tts_hotkey_display.pack(side="left", fill="x", expand=True)

        self._tts_capture_btn = ctk.CTkButton(
            hotkey_row, text="🎯 Change", height=28, width=84,
            font=FONTS["body_small"], fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, corner_radius=6,
            command=lambda: self._on_tts_capture_click("read"),
        )
        self._tts_capture_btn.pack(side="right")

        # ── Swap-voice hotkey ──
        swap_row = ctk.CTkFrame(card, fg_color="transparent")
        swap_row.pack(fill="x", padx=20, pady=(0, 6))

        ctk.CTkLabel(
            swap_row, text="Swap voice hotkey",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM, anchor="w",
        ).pack(side="left", padx=(0, 8))

        self._tts_swap_hotkey_display = ctk.CTkLabel(
            swap_row, text=self._config.get("tts_swap_voice_hotkey", "ctrl+alt+v"),
            font=FONTS["body_large_bold"], text_color=COLOR_GREEN, anchor="w",
        )
        self._tts_swap_hotkey_display.pack(side="left", fill="x", expand=True)

        self._tts_swap_capture_btn = ctk.CTkButton(
            swap_row, text="🎯 Change", height=28, width=84,
            font=FONTS["body_small"], fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, corner_radius=6,
            command=lambda: self._on_tts_capture_click("swap"),
        )
        self._tts_swap_capture_btn.pack(side="right")

        self._tts_capture_hint = ctk.CTkLabel(
            card, text="", font=FONTS["body_small"], text_color=COLOR_YELLOW,
            anchor="w", wraplength=520,
        )
        self._tts_capture_hint.pack(fill="x", padx=20, pady=(0, 14))

    def _build_tts_footer(self) -> None:
        footer = ctk.CTkFrame(self._readout_content, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=(14, 18))

        info_row = ctk.CTkFrame(footer, fg_color="transparent")
        info_row.pack(fill="x")

        ctk.CTkLabel(
            info_row, text="Read Out v1.0", font=FONTS["body_small"],
            text_color=COLOR_TEXT_DIM,
        ).pack(side="left")

        ctk.CTkLabel(
            info_row, text="Closing hides to the tray", font=FONTS["body_small"],
            text_color=COLOR_TEXT_DIM,
        ).pack(side="left", padx=(12, 0))

        quit_btn = ctk.CTkButton(
            info_row, text="✕ Quit", height=24, width=60,
            font=FONTS["body_small"], fg_color="transparent",
            hover_color=COLOR_BORDER, text_color=COLOR_TEXT_DIM, corner_radius=4,
            command=self.destroy,
        )
        quit_btn.pack(side="right")

    # ═══════════════════════════════════════════════════════════
    #  Read Out (TTS) — event queue handling
    # ═══════════════════════════════════════════════════════════

    def _handle_tts_event(self, event) -> None:
        """Handle TTS-related events from the queue."""
        if isinstance(event, tuple):
            kind = event[0]
            if kind == "tts_speech_done":
                self.app_state.set_tts_status(TTSReadStatus.IDLE)
                self._update_tts_ui()
                self._tts_apply_pending_voice_if_idle()
            elif kind == "tts_speech_error":
                self.app_state.set_tts_status(TTSReadStatus.ERROR)
                self.app_state.set_tts_error_message(str(event[1]))
                self._update_tts_ui()
                self._tts_apply_pending_voice_if_idle()
            elif kind == "tts_voices_loaded":
                self._tts_handle_voices_loaded(event[1], event[2])
            elif kind == "tts_notice":
                msg = str(event[1])
                duration = int(event[2]) if len(event) > 2 else 4000
                self._tts_show_notice(msg, duration)
            elif kind == "tts_status_changed":
                self._update_tts_ui()
            return

        if event == "toggle_read":
            self._on_tts_toggle_read()
        elif event == "cycle_voice":
            self._on_tts_cycle_voice()

    # ═══════════════════════════════════════════════════════════
    #  Read Out (TTS) — actions
    # ═══════════════════════════════════════════════════════════

    def _on_tts_toggle_read(self) -> None:
        play_pressed_sound()
        if self.tts_engine.is_speaking() or self.app_state.get_tts_status() == TTSReadStatus.READING:
            self._on_tts_stop()
            return
        if self._tts_busy:
            return
        self._on_tts_read_selection()

    def _on_tts_read_selection(self) -> None:
        if self._tts_busy:
            return
        self._tts_busy = True
        self._tts_cancel_requested = False
        self.app_state.set_tts_status(TTSReadStatus.READING)
        self.app_state.set_tts_error_message("")
        self._update_tts_ui()
        threading.Thread(
            target=self._tts_read_selection_worker, daemon=True, name="wavescribe-tts-grab"
        ).start()

    def _tts_read_selection_worker(self) -> None:
        in_terminal = selection.is_legacy_terminal_focused()
        info: dict = {}
        try:
            text = selection.grab_selected_text(
                in_legacy_terminal=in_terminal, info=info
            )
        except Exception:
            text = ""
        if not text:
            self._tts_busy = False
            play_tts_failure_sound()
            self.app_state.set_tts_status(TTSReadStatus.ERROR)
            if info.get("image_only"):
                self.app_state.set_tts_error_message("Selection was an image, not text")
                msg = "The selection was an image (e.g. a sticker/emoticon), not text — select the text around it and try again."
            else:
                self.app_state.set_tts_error_message("No text selected")
                if in_terminal:
                    msg = "No text selected — in this terminal, drag to select with the mouse (it copies on release), then try again."
                else:
                    msg = "No text selected — select some text and try again."
            self.event_queue.put(("tts_notice", msg, 8000))
            self.event_queue.put(("tts_status_changed",))
            self.event_queue.put(("tts_speech_done",))
            return
        self.app_state.set_tts_last_text(text)
        if in_terminal:
            self.event_queue.put(("tts_notice", "Read from the clipboard — in this terminal, drag-selecting copies on release.", 6000))
        self._tts_speak_in_worker(text)

    def _on_tts_speak_box(self) -> None:
        if self._tts_busy:
            return
        text = self._tts_text_box.get("1.0", "end").strip()
        if not text:
            play_tts_failure_sound()
            self.app_state.set_tts_status(TTSReadStatus.ERROR)
            self.app_state.set_tts_error_message("Type or paste some text first.")
            self._tts_show_notice("Type or paste some text first.", 8000)
            self._update_tts_ui()
            return
        self._tts_busy = True
        self._tts_cancel_requested = False
        self.app_state.set_tts_status(TTSReadStatus.READING)
        self.app_state.set_tts_error_message("")
        self._update_tts_ui()
        threading.Thread(
            target=self._tts_speak_in_worker, args=(text,), daemon=True, name="wavescribe-tts-speak"
        ).start()

    def _tts_speak_in_worker(self, text: str) -> None:
        try:
            if self._tts_cancel_requested:
                return
            play_tts_success_sound()
            entry = voices_mod.active_voice(self._config)
            backend = entry.get("backend")
            if backend not in available_backends():
                backend = self.tts_engine.get_backend()
            self.tts_engine.speak(
                text,
                voice=entry.get("voice", ""),
                rate_percent=int(self._config.get("tts_rate_percent", 100)),
                volume=int(self._config.get("tts_volume", 100)),
                on_done=lambda: self.event_queue.put(("tts_speech_done",)),
                backend=backend,
            )
        except TTSError as exc:
            if not self._tts_cancel_requested:
                play_tts_failure_sound()
                self.event_queue.put(("tts_speech_error", str(exc)))
        except Exception as exc:
            if not self._tts_cancel_requested:
                play_tts_failure_sound()
                self.event_queue.put(("tts_speech_error", f"Unexpected error: {exc}"))
        finally:
            self._tts_busy = False

    def _on_tts_stop(self) -> None:
        self._tts_cancel_requested = True
        self.tts_engine.stop_all()
        self.app_state.set_tts_status(TTSReadStatus.IDLE)
        self.app_state.set_tts_error_message("")
        self._update_tts_ui()
        self._tts_apply_pending_voice_if_idle()

    # ═══════════════════════════════════════════════════════════
    #  Read Out (TTS) — voice/backend selection
    # ═══════════════════════════════════════════════════════════

    def _tts_refresh_voices(self) -> None:
        backend = self.tts_engine.get_backend()
        self._tts_voices_state_label.configure(text="Loading voices…")
        threading.Thread(
            target=self._tts_voices_worker, args=(backend,), daemon=True,
            name="wavescribe-tts-voices",
        ).start()

    def _tts_voices_worker(self, backend: str) -> None:
        try:
            voices = self.tts_engine.list_voices(backend)
        except Exception:
            voices = []
        self.event_queue.put(("tts_voices_loaded", backend, voices))

    def _tts_handle_voices_loaded(self, backend: str, voices: list) -> None:
        if backend != self.tts_engine.get_backend():
            return
        self._tts_current_voices = voices

        is_system = backend == "system"
        entries = [("(System default)", "")] if is_system else []
        entries += [(v.name, v.name) for v in voices]
        if not entries:
            entries = [("(no voices found)", "")]
        valid = {value for _, value in entries}

        changed = False
        vl = self._config.setdefault("tts_voice_list", [])
        if not vl:
            vl.append({"backend": backend, "voice": "" if is_system else (voices[0].name if voices else "")})
            changed = True

        if voices:
            pruned = voices_mod.prune_entries(vl, backend, valid)
            if len(pruned) != len(vl):
                vl[:] = pruned
                changed = True
            if not vl:
                vl.append({"backend": backend, "voice": "" if is_system else voices[0].name})
                changed = True
            persisted = voices_mod.clamp_index(
                vl, self._config.get("tts_active_voice_index", 0)
            )
            if vl[persisted].get("backend") == backend:
                idx = persisted
            else:
                idx = voices_mod.snap_index(vl, backend, persisted)
            entry = vl[idx]
            if entry.get("backend") != backend:
                idx = voices_mod.ensure_entry(
                    vl, backend, "" if is_system else voices[0].name
                )
                entry = vl[idx]
                changed = True
            if entry.get("voice") not in valid:
                entry["voice"] = "" if is_system else voices[0].name
                changed = True
            self._config["tts_active_voice_index"] = voices_mod.clamp_index(vl, idx)
        else:
            self._config["tts_active_voice_index"] = voices_mod.clamp_index(
                vl, self._config.get("tts_active_voice_index", 0)
            )

        if changed:
            save_config(self._config)

        count = len(voices)
        self._tts_voices_state_label.configure(
            text=f"{count} voice{'s' if count != 1 else ''} available"
        )
        self._tts_refresh_voice_widgets()
        self._tts_update_voice_preview()

    def _on_tts_backend_changed(self, display: str) -> None:
        name = next((n for n, d in self._tts_backend_options if d == display), "system")
        if name == self.tts_engine.get_backend():
            return
        self.tts_engine.set_backend(name)
        self._config["tts_backend"] = name
        save_config(self._config)
        self._tts_refresh_voices()

    def _on_tts_voice_changed(self, display: str) -> None:
        value = ""
        for v in self._tts_current_voices:
            if v.name == display:
                value = v.name
                break
        voices_mod.set_main_voice(self._config, self.tts_engine.get_backend(), value)
        save_config(self._config)
        self._tts_rebuild_secondary_rows()
        self._tts_update_voice_preview()

    def _tts_update_voice_preview(self) -> None:
        display = self._tts_voice_menu.get()
        for v in self._tts_current_voices:
            if v.name == display:
                bits = [b for b in (v.language, v.gender) if b]
                preview = f"{v.name}  ·  {' / '.join(bits)}" if bits else v.name
                self._tts_voices_state_label.configure(text=preview)
                return
        self._tts_voices_state_label.configure(
            text=f"{len(self._tts_current_voices)} voices available"
        )

    def _tts_voice_entries(self) -> list:
        is_system = self.tts_engine.get_backend() == "system"
        entries = [("(System default)", "")] if is_system else []
        entries += [(v.name, v.name) for v in self._tts_current_voices]
        if not entries:
            entries = [("(no voices found)", "")]
        return entries

    def _tts_filtered_entries(self) -> list:
        entries = self._tts_voice_entries()
        active = voices_mod.active_voice(self._config)
        always = []
        if active.get("backend") == self.tts_engine.get_backend():
            display = next((d for d, v in entries if v == active["voice"]), None)
            if display:
                always = [display]
        query = ""
        if getattr(self, "_tts_voice_search", None) is not None:
            query = self._tts_voice_search.get().strip()
        return voices_mod.filter_entries(entries, query, always)

    def _tts_on_voice_search(self) -> None:
        self._tts_refresh_voice_widgets()
        self._tts_update_voice_preview()

    def _tts_clear_voice_search(self) -> None:
        self._tts_voice_search.delete(0, "end")
        self._tts_refresh_voice_widgets()
        self._tts_update_voice_preview()
        self._tts_voice_search.focus_set()

    def _tts_refresh_voice_widgets(self) -> None:
        entries = self._tts_filtered_entries()
        if not entries:
            entries = [("(no matches — clear search)", "")]
        self._tts_voice_menu.configure(values=[d for d, _ in entries])
        active = voices_mod.active_voice(self._config)
        display = None
        if active.get("backend") == self.tts_engine.get_backend():
            display = next((d for d, v in entries if v == active["voice"]), None)
        self._tts_voice_menu.set(display if display is not None else entries[0][0])
        self._tts_rebuild_secondary_rows(entries)

    def _on_tts_cycle_voice(self) -> None:
        play_pressed_sound()
        vl = self._config.get("tts_voice_list") or []
        if len(vl) <= 1:
            self._tts_show_notice(
                "Add a secondary voice to use voice swapping — pick one below and press ＋ Add voice"
            )
            return
        idx = voices_mod.next_voice_index(vl, self._config.get("tts_active_voice_index", 0))
        self._config["tts_active_voice_index"] = idx
        save_config(self._config)
        entry = vl[idx]
        if self._tts_busy or self.tts_engine.is_speaking():
            self._tts_notice_voice_pending = True
            self._tts_show_notice(
                f"Voice set to {voices_mod.entry_display(entry)} — applies to the next read"
            )
            return
        self._tts_apply_voice_entry(entry)
        self._tts_show_notice(f"Voice: {voices_mod.entry_display(entry)}")

    def _tts_apply_voice_entry(self, entry) -> None:
        backend = entry.get("backend")
        if backend in available_backends() and backend != self.tts_engine.get_backend():
            self.tts_engine.set_backend(backend)
            self._config["tts_backend"] = backend
            save_config(self._config)
            display = next(
                (d for n, d in self._tts_backend_options if n == backend), backend
            )
            self._tts_backend_menu.set(display)
            self._tts_voice_menu.configure(values=["(switching…)"])
            self._tts_voice_menu.set("(switching…)")
            self._tts_refresh_voices()
        else:
            self._tts_rebuild_secondary_rows()

    def _tts_apply_pending_voice_if_idle(self) -> None:
        if (
            getattr(self, "_tts_notice_voice_pending", False)
            and not self._tts_busy
            and not self.tts_engine.is_speaking()
        ):
            self._tts_notice_voice_pending = False
            self._tts_apply_voice_entry(voices_mod.active_voice(self._config))

    def _tts_rebuild_secondary_rows(self, entries=None) -> None:
        if getattr(self, "_tts_secondary_rows_frame", None) is None:
            return
        if entries is None:
            entries = self._tts_filtered_entries()
        for w in self._tts_secondary_rows_frame.winfo_children():
            w.destroy()

        backend = self.tts_engine.get_backend()
        vl = self._config.get("tts_voice_list") or []
        active = voices_mod.active_voice(self._config)

        if active.get("backend") == backend:
            display = next((d for d, v in entries if v == active["voice"]), None)
            if display is not None:
                self._tts_voice_menu.set(display)

        for i in range(1, len(vl)):
            entry = vl[i]
            row = ctk.CTkFrame(self._tts_secondary_rows_frame, fg_color="transparent")
            row.pack(fill="x", pady=(2, 0))

            badge_text = "Windows" if entry.get("backend") == "system" else "Edge"
            ctk.CTkLabel(
                row, text=badge_text, width=56, font=FONTS["body_small"],
                text_color=COLOR_TEXT_DIM, anchor="w",
            ).pack(side="left")

            if entry.get("backend") == backend and len(entries) > 1:
                menu = ctk.CTkOptionMenu(
                    row, values=[d for d, _ in entries], width=0,
                    font=FONTS["body_small"], fg_color=COLOR_INPUT_BG,
                    button_color=COLOR_ACCENT, button_hover_color=COLOR_ACCENT_HOVER,
                    command=lambda value, i=i: self._on_tts_secondary_changed(i, value),
                )
                menu.pack(side="left", fill="x", expand=True, padx=(4, 4))
                display = next(
                    (d for d, v in entries if v == entry.get("voice")), None
                )
                menu.set(display if display is not None else entries[0][0])
            else:
                ctk.CTkLabel(
                    row, text=entry.get("voice") or "(System default)",
                    font=FONTS["body_small"], text_color=COLOR_TEXT, anchor="w",
                ).pack(side="left", fill="x", expand=True, padx=(4, 4))

            remove_btn = ctk.CTkButton(
                row, text="✕", width=28, height=24,
                font=FONTS["body_small"], fg_color="transparent",
                hover_color=COLOR_BORDER, text_color=COLOR_TEXT_DIM, corner_radius=4,
                command=lambda i=i: self._on_tts_secondary_remove(i),
            )
            remove_btn.pack(side="right")

        # Add row
        query = ""
        if getattr(self, "_tts_voice_search", None) is not None:
            query = self._tts_voice_search.get().strip()
        in_list = {e.get("voice") for e in vl if e.get("backend") == backend}
        if query:
            candidates = entries
        else:
            candidates = [(d, v) for d, v in entries if v not in in_list]
        real = [c for c in candidates if c[0] not in ("(no matches — clear search)", "(all voices added — search to find one)")]
        if real:
            self._tts_add_menu.configure(values=[d for d, _ in real])
            self._tts_add_menu.set(real[0][0])
            self._tts_add_btn.configure(state="normal")
        else:
            hint = "(no matches — clear search)" if candidates else "(all voices added — search to find one)"
            self._tts_add_menu.configure(values=[hint])
            self._tts_add_menu.set(hint)
            self._tts_add_btn.configure(state="disabled")

    def _on_tts_secondary_changed(self, index: int, display: str) -> None:
        value = next(
            (v.name for v in self._tts_current_voices if v.name == display), ""
        )
        vl = self._config.get("tts_voice_list") or []
        if 0 < index < len(vl):
            vl[index] = {"backend": self.tts_engine.get_backend(), "voice": value}
            save_config(self._config)
        self._tts_rebuild_secondary_rows()

    def _on_tts_secondary_remove(self, index: int) -> None:
        if voices_mod.remove_entry(self._config, index):
            save_config(self._config)
        self._tts_rebuild_secondary_rows()
        self._tts_update_voice_preview()

    def _on_tts_add_secondary(self) -> None:
        display = self._tts_add_menu.get()
        if display in ("(no matches — clear search)", "(all voices added — search to find one)", "(no voices)"):
            self._tts_rebuild_secondary_rows()
            return
        value = next(
            (v.name for v in self._tts_current_voices if v.name == display), ""
        )
        backend = self.tts_engine.get_backend()
        vl = self._config.get("tts_voice_list") or []
        existing = [
            i for i, e in enumerate(vl)
            if e.get("backend") == backend and e.get("voice") == value
        ]
        if existing:
            where = "it's the main voice" if existing[0] == 0 else "it's already a secondary voice"
            self._tts_show_notice(f"{display} is already in the list — {where}")
        elif voices_mod.add_secondary(self._config, backend, value):
            save_config(self._config)
            self._tts_show_notice(
                f"Added {voices_mod.entry_display({'backend': backend, 'voice': value})}"
            )
        self._tts_rebuild_secondary_rows()

    # ── TTS Sliders ──

    def _on_tts_rate_changed(self, value) -> None:
        pct = int(round(value))
        self._config["tts_rate_percent"] = pct
        save_config(self._config)
        self._tts_rate_value_label.configure(text=f"{pct}%")

    def _on_tts_volume_changed(self, value) -> None:
        vol = int(round(value))
        self._config["tts_volume"] = vol
        save_config(self._config)
        self._tts_volume_value_label.configure(text=str(vol))

    # ── TTS Hotkey capture ──

    def _on_tts_capture_click(self, mode: str = "read") -> None:
        if self._tts_capturing:
            self._tts_stop_capturing()
            return
        self._tts_capturing = True
        self._tts_capture_mode = mode
        self._tts_capture_btn.configure(
            text="⏹ Cancel", fg_color=COLOR_RED, hover_color=COLOR_RED_HOVER
        )
        self._tts_swap_capture_btn.configure(
            text="⏹ Cancel", fg_color=COLOR_RED, hover_color=COLOR_RED_HOVER
        )
        target = "the read hotkey" if mode == "read" else "the voice-swap hotkey"
        self._tts_capture_hint.configure(
            text=f"Press your shortcut for {target} (with Ctrl / Alt / Shift)… Esc to cancel."
        )
        try:
            self.hotkey_mgr._remove_tts_hotkeys()
        except Exception:
            pass
        self.focus_force()

    def _tts_stop_capturing(self) -> None:
        self._tts_capturing = False
        self._tts_capture_btn.configure(
            text="🎯 Change", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER
        )
        self._tts_swap_capture_btn.configure(
            text="🎯 Change", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER
        )
        self._tts_capture_hint.configure(text="")
        self.hotkey_mgr.set_tts_hotkeys(
            self._config.get("tts_hotkey", "ctrl+alt+space"),
            self._config.get("tts_swap_voice_hotkey", "ctrl+alt+v"),
        )

    def _tts_apply_hotkey(self, combo: str) -> None:
        if getattr(self, "_tts_capture_mode", "read") == "swap":
            self._config["tts_swap_voice_hotkey"] = combo
            self._tts_swap_hotkey_display.configure(text=combo)
            message = f"Voice-swap hotkey set to {combo}"
        else:
            self._config["tts_hotkey"] = combo
            self._tts_hotkey_display.configure(text=combo)
            message = f"Read hotkey set to {combo}"
        save_config(self._config)
        self.hotkey_mgr.set_tts_hotkeys(
            self._config.get("tts_hotkey", "ctrl+alt+space"),
            self._config.get("tts_swap_voice_hotkey", "ctrl+alt+v"),
        )
        self._tts_stop_capturing()
        self._tts_show_notice(message)

    def _tts_show_notice(self, msg: str, duration: int = 4000) -> None:
        if getattr(self, "_tts_notice_after_id", None) is not None:
            self.after_cancel(self._tts_notice_after_id)
        self._tts_notice_label.configure(text=msg)
        self._tts_notice_after_id = self.after(duration, lambda: self._tts_notice_label.configure(text=""))

    # ── TTS UI update ──

    def _update_tts_ui(self) -> None:
        status = self.app_state.get_tts_status()
        error_msg = self.app_state.get_tts_error_message()

        dot_color = {
            TTSReadStatus.IDLE: COLOR_GREEN,
            TTSReadStatus.READING: COLOR_YELLOW,
            TTSReadStatus.ERROR: COLOR_RED,
        }.get(status, COLOR_GREEN)
        self._tts_status_dot.configure(text_color=dot_color)

        status_text = {TTSReadStatus.IDLE: "Idle", TTSReadStatus.READING: "Reading…", TTSReadStatus.ERROR: f"Error: {error_msg}"}
        self._tts_status_label.configure(text=status_text.get(status, "Idle"))

        if status == TTSReadStatus.IDLE:
            self._tts_read_btn.configure(state="normal")
            self._tts_stop_btn.configure(state="disabled")
        elif status == TTSReadStatus.READING:
            self._tts_read_btn.configure(state="disabled")
            self._tts_stop_btn.configure(state="normal")
        else:
            self._tts_read_btn.configure(state="disabled")
            self._tts_stop_btn.configure(state="disabled")
