"""WaveScribe GUI — CustomTkinter desktop window.

The main window with recording controls, transcription display,
settings panel, status indicators, a floating status overlay,
and config persistence.
"""

import queue
import threading
from typing import Optional

import customtkinter as ctk

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
)
from app.formatter import apply_punctuation, auto_capitalize, convert_numbers, clean_spacing, cleanup_redundant_punctuation
from app.typer import type_text
from app.config import load_config, save_config
from app.overlay import StatusOverlay
from app.hotkeys import HotkeyManager
from app.sounds import init_sounds, play_start_sound, play_stop_sound
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
    ):
        super().__init__()

        # Resolve fonts (registers bundled Poppins .ttf) and init sounds
        init_fonts()
        init_sounds()

        self.app_state = state
        self.audio = audio
        self.event_queue = event_queue
        self.hotkey_mgr = hotkey_mgr

        # ── Load config and apply to state ──
        self._config = load_config()
        self._apply_config_to_state()
        self._config_dirty = False

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

        # ── Guard to prevent double auto-insert ──
        self._auto_inserted = False

        # ── Flag to allow cancelling an in-progress transcription ──
        self._transcription_cancelled = False

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

        # Scrollable content area (everything below the header)
        self._content_frame = ctk.CTkScrollableFrame(
            self, fg_color=COLOR_BG, corner_radius=0,
            scrollbar_button_color=COLOR_CARD,
            scrollbar_button_hover_color=COLOR_BORDER,
        )
        self._content_frame.pack(fill="both", expand=True, padx=0, pady=0)

        self._build_control_card()
        self._build_transcription_card()
        self._build_settings_card()
        self._build_footer()

        # ── Global key-binding for hotkey capture ──
        self.bind('<KeyPress>', self._on_global_keypress)

        # ── Start polling event queue ──
        self._poll_queue()

        # ── Update UI for initial state ──
        self._update_ui()

    # ── Config helpers ──

    def _apply_config_to_state(self) -> None:
        """Apply loaded config values to the AppState."""
        self.app_state.set_api_key(self._config.get("api_key", ""))
        self.app_state.set_model_size(self._config.get("model_size", "base"))
        self.app_state.set_punctuate_speech(self._config.get("punctuate_speech", True))
        self.app_state.set_auto_capitalize(self._config.get("auto_capitalize", True))
        self.app_state.set_numbers_as_digits(self._config.get("numbers_as_digits", False))
        mode_str = self._config.get("mode", "cloud")
        self.app_state.set_mode(AppMode.CLOUD if mode_str == "cloud" else AppMode.LOCAL)

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
            "local_model_status": self.app_state.get_local_model_status().value,
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
            self._content_frame, fg_color=COLOR_CARD, corner_radius=10,
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
            self._content_frame, fg_color=COLOR_CARD, corner_radius=10,
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
            card, text="📝  Insert into Window",
            font=FONTS["body"], height=36,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            text_color="#ffffff",
            corner_radius=8,
            state="disabled",
            command=self._on_insert_click,
        )
        self.insert_btn.pack(fill="x", padx=20, pady=(0, 16))

    def _build_settings_card(self) -> None:
        """Build the settings panel with shortcut customization, model, API key, formatting."""
        card = ctk.CTkFrame(
            self._content_frame, fg_color=COLOR_CARD, corner_radius=10,
            border_width=1, border_color=COLOR_BORDER,
        )
        card.pack(fill="x", padx=16, pady=(12, 0))

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

    def _build_footer(self) -> None:
        """Build the app footer."""
        footer_frame = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        footer_frame.pack(fill="x", padx=16, pady=(12, 16))

        ctk.CTkLabel(
            footer_frame, text="WaveScribe v0.1.0",
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

    def _handle_event(self, event: str) -> None:
        """Handle an event from the queue (from hotkeys or other threads)."""
        if event == "start_recording":
            if self.app_state.get_status() == AppStatus.IDLE:
                self._start_recording()
        elif event == "stop_recording":
            if self.app_state.get_status() == AppStatus.RECORDING:
                self._stop_recording(auto_insert=True)

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

                    text = transcribe_cloud(wav_bytes, api_key)
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
                    text = transcribe_local(wav_bytes, model_size)

                # If transcription was cancelled, don't update UI
                if self._transcription_cancelled:
                    return

                # Reset the flag for next use
                self._transcription_cancelled = False

                # Apply smart formatting steps based on individual toggles
                if self.app_state.get_punctuate_speech():
                    text = apply_punctuation(text)
                    text = clean_spacing(text)
                    text = cleanup_redundant_punctuation(text)
                if self.app_state.get_auto_capitalize():
                    text = auto_capitalize(text)
                if self.app_state.get_numbers_as_digits():
                    text = convert_numbers(text)
                # Always clean up spacing
                text = clean_spacing(text)

                # If auto-capitalize is OFF, lowercase the first letter
                # (Whisper intrinsically capitalizes the first letter)
                if not self.app_state.get_auto_capitalize() and text:
                    text = text[0].lower() + text[1:]

                self.app_state.set_last_transcription(text)
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
