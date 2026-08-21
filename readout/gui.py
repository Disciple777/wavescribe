"""Readout settings window (CustomTkinter).

The window is where the user configures Readout: hotkey, voice engine,
voice, speed, volume and auto-start. All background work (grabbing the
selection, speech synthesis, listing voices) runs in daemon threads and
communicates back through a thread-safe event queue that this window
polls every 100ms — the same architecture as WaveScribe's GUI.

Closing the window hides it to the system tray; the tray "Quit" item
exits for real.
"""

import queue
import threading
import time
from typing import List, Optional

import customtkinter as ctk
import keyboard

from readout.autostart import apply_autostart_setting
from readout.core import selection
from readout.core import voices as voices_mod
from readout.core.config import load_config, save_config
from readout.core.sounds import (
    init_sounds,
    play_failure_sound,
    play_pressed_sound,
    play_success_sound,
)
from readout.core.engine import EngineManager, available_backends
from readout.core.errors import TTSError
from readout.core.voices import VoiceEntry
from readout.core.state import ReadStatus, ReadoutState
from readout.core.system_tts import VoiceInfo
from readout.core.theme import (
    COLOR_ACCENT,
    COLOR_ACCENT_HOVER,
    COLOR_BG,
    COLOR_BORDER,
    COLOR_CARD,
    COLOR_GREEN,
    COLOR_GREEN_HOVER,
    COLOR_INPUT_BG,
    COLOR_RED,
    COLOR_RED_HOVER,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_YELLOW,
    FONTS,
    init_fonts,
)

WINDOW_WIDTH = 500
WINDOW_HEIGHT = 780

# Sentinel labels shown inside the voice pickers — never real voices.
_NO_MATCHES_LABEL = "(no matches — clear search)"
_ALL_ADDED_LABEL = "(all voices added — search to find one)"


# ── Hotkey modifier decoding ──
# tkinter's event.state on Windows reports a phantom Alt bit (Mod1, 0x0008)
# on many systems — even bare key presses come back as "Alt held". The
# ctrl/shift bits are accurate, so those come from event.state, while alt/win
# come from the keyboard library's *physical* key state (verified: this is the
# only trustworthy source for those keys on Windows).

def decode_hotkey_modifiers(state: int, alt_down: bool = False, win_down: bool = False) -> list:
    """Return the modifier names for a captured hotkey combo.

    Args:
        state: tkinter ``event.state`` bitmask (ctrl = 0x4, shift = 0x1).
        alt_down: whether the Alt key is physically held (keyboard lib truth).
        win_down: whether the Windows key is physically held (keyboard lib truth).
    """
    mods = []
    if state & 0x0004:
        mods.append("ctrl")
    if state & 0x0001:
        mods.append("shift")
    if alt_down:
        mods.append("alt")
    if win_down:
        mods.append("win")
    return mods


def _is_physically_down(key_name: str) -> bool:
    """True when ``key_name`` is physically held, per the keyboard library."""
    try:
        return bool(keyboard.is_pressed(key_name))
    except Exception:
        return False


# Tkinter keysym → keyboard-library key name (fallback for the main key).
_TKINTER_KEY_MAP = {
    "return": "enter",
    "escape": "esc",
    "prior": "page_up",
    "next": "page_down",
    "slash": "/",
    "asterisk": "*",
    "minus": "-",
    "plus": "+",
    "comma": ",",
    "period": ".",
    "semicolon": ";",
    "apostrophe": "'",
    "bracketleft": "[",
    "bracketright": "]",
    "backslash": "\\",
    "space": "space",
}


class App(ctk.CTk):
    """Main Readout settings window."""

    def __init__(
        self,
        state: ReadoutState,
        event_queue: queue.Queue,
        engine: EngineManager,
        hotkeys,
    ) -> None:
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        init_fonts()

        self.app_state = state  # NB: NOT `self.state` — that shadows CTk's state() method
        self.event_queue = event_queue
        self.engine = engine
        self.hotkeys = hotkeys
        self._config = load_config()

        self._busy = False                 # a read/grab is in progress
        self._cancel_requested = False     # a stop was requested mid-run
        self._capturing = False            # hotkey capture mode
        self._capture_mode = "read"       # which hotkey is being captured
        self._current_voices: List[VoiceInfo] = []
        self._notice_after_id: Optional[str] = None
        self._notice_voice_pending = False  # a voice swap arrived mid-read

        # If the configured backend isn't available, fall back to system.
        if self._config.get("backend") not in available_backends():
            self._config["backend"] = "system"
            save_config(self._config)
        self.engine.set_backend(self._config.get("backend", "system"))

        # Seed the voice rotation from the legacy per-engine voice keys on
        # first run, so existing configs keep their selected voice as the main.
        if voices_mod.migrate_voice_list(self._config):
            save_config(self._config)

        # ── Generate the feedback cue sounds (pressed / success / failure) ──
        init_sounds()

        # ── Window setup ──
        self.title("Readout")
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.resizable(True, True)
        self.minsize(WINDOW_WIDTH, 680)
        self.configure(fg_color=COLOR_BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<KeyPress>", self._on_global_keypress)

        # ── Build UI ──
        self._content_frame = ctk.CTkScrollableFrame(
            self, fg_color=COLOR_BG, scrollbar_button_color=COLOR_CARD,
            scrollbar_button_hover_color=COLOR_BORDER,
        )
        self._content_frame.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        self._build_header_card()
        self._build_control_card()
        self._build_voice_card()
        self._build_speed_card()
        self._build_hotkey_card()
        self._build_general_card()
        self._build_footer()

        # ── Start polling event queue ──
        self._poll_queue()
        self._update_ui()

        # ── Populate voice list for the active backend ──
        self._refresh_voices()

    # ═══════════════════════════════════════════════════════════
    #  UI construction
    # ═══════════════════════════════════════════════════════════

    def _card(self) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            self._content_frame, fg_color=COLOR_CARD, corner_radius=10,
            border_width=1, border_color=COLOR_BORDER,
        )
        card.pack(fill="x", padx=8, pady=(10, 0))
        return card

    def _build_header_card(self) -> None:
        card = self._card()
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(16, 4))

        ctk.CTkLabel(
            header, text="🔊  Readout", font=FONTS["title"], text_color=COLOR_TEXT
        ).pack(anchor="w")

        ctk.CTkLabel(
            card,
            text="Select any text anywhere, press your hotkey, and listen.",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 16))

    def _build_control_card(self) -> None:
        card = self._card()

        # ── Status row ──
        status_row = ctk.CTkFrame(card, fg_color="transparent")
        status_row.pack(fill="x", padx=20, pady=(14, 0))

        self._status_dot = ctk.CTkLabel(
            status_row, text="●", font=FONTS["icon"], text_color=COLOR_GREEN, width=20,
        )
        self._status_dot.pack(side="left")

        self._status_label = ctk.CTkLabel(
            status_row, text="", font=FONTS["body"], text_color=COLOR_TEXT, anchor="w",
        )
        self._status_label.pack(side="left", fill="x", expand=True, padx=(8, 8))

        # ── Action buttons ──
        buttons_row = ctk.CTkFrame(card, fg_color="transparent")
        buttons_row.pack(fill="x", padx=20, pady=(14, 4))

        self._read_btn = ctk.CTkButton(
            buttons_row, text="🔊  Read selected text", height=40,
            font=FONTS["body_large"], fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, corner_radius=8,
            command=self._on_read_selection,
        )
        self._read_btn.pack(side="left", fill="x", expand=True)

        self._stop_btn = ctk.CTkButton(
            buttons_row, text="⏹  Stop", height=40, width=92,
            font=FONTS["body_large"], fg_color=COLOR_RED,
            hover_color=COLOR_RED_HOVER, corner_radius=8,
            state="disabled", command=self._on_stop,
        )
        self._stop_btn.pack(side="right", padx=(8, 0))

        # ── Transient notice line (warnings / confirmations) ──
        self._notice_label = ctk.CTkLabel(
            card, text="", font=FONTS["body_small"], text_color=COLOR_YELLOW,
            anchor="w", wraplength=440,
        )
        self._notice_label.pack(fill="x", padx=20, pady=(0, 4))

        # ── Manual text box ──
        ctk.CTkLabel(
            card, text="…or paste text here to hear it:",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=20, pady=(6, 0))

        self._text_box = ctk.CTkTextbox(
            card, height=80, font=FONTS["body"], fg_color=COLOR_INPUT_BG,
            border_width=1, border_color=COLOR_BORDER, corner_radius=8,
            wrap="word",
        )
        self._text_box.pack(fill="x", padx=20, pady=(4, 4))
        self._text_box.insert("1.0", "")

        self._speak_box_btn = ctk.CTkButton(
            card, text="▶  Speak text", height=30,
            font=FONTS["body_small"], fg_color=COLOR_GREEN,
            hover_color=COLOR_GREEN_HOVER, text_color="#000000", corner_radius=6,
            command=self._on_speak_box,
        )
        self._speak_box_btn.pack(fill="x", padx=20, pady=(0, 16))

    def _build_voice_card(self) -> None:
        card = self._card()
        ctk.CTkLabel(
            card, text="🗣  Voice", font=FONTS["body_large_bold"], text_color=COLOR_TEXT,
        ).pack(anchor="w", padx=20, pady=(16, 6))

        # ── Backend picker ──
        backend_row = ctk.CTkFrame(card, fg_color="transparent")
        backend_row.pack(fill="x", padx=20, pady=(0, 4))

        ctk.CTkLabel(
            backend_row, text="Engine", font=FONTS["body_small"], text_color=COLOR_TEXT_DIM,
        ).pack(side="left")

        self._backend_options = [
            (name, self.engine.engine(name).display_name) for name in available_backends()
        ]
        self._backend_menu = ctk.CTkOptionMenu(
            backend_row, values=[d for _, d in self._backend_options],
            font=FONTS["body_small"], fg_color=COLOR_INPUT_BG,
            button_color=COLOR_ACCENT, button_hover_color=COLOR_ACCENT_HOVER,
            command=self._on_backend_changed,
        )
        self._backend_menu.pack(side="right")
        current = self.engine.get_backend()
        current_display = next(d for n, d in self._backend_options if n == current)
        self._backend_menu.set(current_display)

        # ── Voice search: filters every voice dropdown live ──
        search_row = ctk.CTkFrame(card, fg_color="transparent")
        search_row.pack(fill="x", padx=20, pady=(6, 0))

        self._voice_search = ctk.CTkEntry(
            search_row, placeholder_text="🔍  Search voices (e.g. Spanish, Aria)…",
            font=FONTS["body_small"], fg_color=COLOR_INPUT_BG,
            border_width=1, border_color=COLOR_BORDER, corner_radius=6, height=28,
        )
        self._voice_search.pack(side="left", fill="x", expand=True)
        self._voice_search.bind("<KeyRelease>", lambda _e: self._on_voice_search())

        self._search_clear_btn = ctk.CTkButton(
            search_row, text="✕", width=28, height=26,
            font=FONTS["body_small"], fg_color="transparent",
            hover_color=COLOR_BORDER, text_color=COLOR_TEXT_DIM, corner_radius=4,
            command=self._clear_voice_search,
        )
        self._search_clear_btn.pack(side="right", padx=(6, 0))

        # ── Voice picker ──
        voice_row = ctk.CTkFrame(card, fg_color="transparent")
        voice_row.pack(fill="x", padx=20, pady=(8, 4))

        ctk.CTkLabel(
            voice_row, text="⭐  Main voice", font=FONTS["body_small"], text_color=COLOR_TEXT_DIM,
        ).pack(side="left")

        self._voice_menu = ctk.CTkOptionMenu(
            voice_row, values=["(loading…)"],
            font=FONTS["body_small"], fg_color=COLOR_INPUT_BG,
            button_color=COLOR_ACCENT, button_hover_color=COLOR_ACCENT_HOVER,
            command=self._on_voice_changed,
        )
        self._voice_menu.pack(side="right", fill="x", expand=True, padx=(12, 0))

        self._voices_state_label = ctk.CTkLabel(
            card, text="", font=FONTS["body_small"], text_color=COLOR_TEXT_DIM, anchor="w",
        )
        self._voices_state_label.pack(fill="x", padx=20, pady=(0, 8))

        # ── Secondary voices (the rotation: main + these, cycled by the
        #    swap button / hotkey). Rows only list the current engine's
        #    voices; entries on the other engine show as a read-only label
        #    with their engine badge (switch the Engine picker to edit them). ──
        ctk.CTkLabel(
            card, text="🔁  Voice list — press Cycle or the swap hotkey to swap",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 4))

        self._secondary_rows_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._secondary_rows_frame.pack(fill="x", padx=20, pady=(0, 4))

        add_row = ctk.CTkFrame(card, fg_color="transparent")
        add_row.pack(fill="x", padx=20, pady=(0, 8))

        self._add_menu = ctk.CTkOptionMenu(
            add_row, values=["(no voices)"], width=0,
            font=FONTS["body_small"], fg_color=COLOR_INPUT_BG,
            button_color=COLOR_ACCENT, button_hover_color=COLOR_ACCENT_HOVER,
        )
        self._add_menu.pack(side="left", fill="x", expand=True)

        self._add_btn = ctk.CTkButton(
            add_row, text="＋  Add voice", height=26, width=110,
            font=FONTS["body_small"], fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, corner_radius=6,
            command=self._on_add_secondary,
        )
        self._add_btn.pack(side="right", padx=(8, 0))

        self._cycle_btn = ctk.CTkButton(
            card, text="🔁  Cycle voice", height=32,
            font=FONTS["body_small"], fg_color=COLOR_GREEN,
            hover_color=COLOR_GREEN_HOVER, text_color="#000000", corner_radius=6,
            command=self._on_cycle_voice,
        )
        self._cycle_btn.pack(fill="x", padx=20, pady=(0, 16))

    def _build_speed_card(self) -> None:
        card = self._card()

        # ── Speed ──
        speed_row = ctk.CTkFrame(card, fg_color="transparent")
        speed_row.pack(fill="x", padx=20, pady=(14, 0))
        ctk.CTkLabel(
            speed_row, text="Speed", font=FONTS["body_small"], text_color=COLOR_TEXT_DIM,
        ).pack(side="left")
        self._rate_value_label = ctk.CTkLabel(
            speed_row, text="100%", font=FONTS["body_small"], text_color=COLOR_TEXT,
            width=48, anchor="e",
        )
        self._rate_value_label.pack(side="right")

        self._rate_slider = ctk.CTkSlider(
            card, from_=50, to=150, number_of_steps=20,
            progress_color=COLOR_ACCENT, button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER, command=self._on_rate_changed,
        )
        self._rate_slider.pack(fill="x", padx=20, pady=(4, 4))
        self._rate_slider.set(self._config.get("rate_percent", 100))

        # ── Volume ──
        volume_row = ctk.CTkFrame(card, fg_color="transparent")
        volume_row.pack(fill="x", padx=20, pady=(10, 0))
        ctk.CTkLabel(
            volume_row, text="Volume", font=FONTS["body_small"], text_color=COLOR_TEXT_DIM,
        ).pack(side="left")
        self._volume_value_label = ctk.CTkLabel(
            volume_row, text="100", font=FONTS["body_small"], text_color=COLOR_TEXT,
            width=48, anchor="e",
        )
        self._volume_value_label.pack(side="right")

        self._volume_slider = ctk.CTkSlider(
            card, from_=0, to=100, number_of_steps=20,
            progress_color=COLOR_ACCENT, button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER, command=self._on_volume_changed,
        )
        self._volume_slider.pack(fill="x", padx=20, pady=(4, 16))
        self._volume_slider.set(self._config.get("volume", 100))

    def _build_hotkey_card(self) -> None:
        card = self._card()
        ctk.CTkLabel(
            card, text="⌨  Hotkey", font=FONTS["body_large_bold"], text_color=COLOR_TEXT,
        ).pack(anchor="w", padx=20, pady=(16, 4))

        ctk.CTkLabel(
            card,
            text="Select text anywhere, then press this shortcut to read it aloud. "
                 "Press it again to stop.",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM, anchor="w", wraplength=440,
        ).pack(fill="x", padx=20, pady=(0, 6))

        hotkey_row = ctk.CTkFrame(card, fg_color="transparent")
        hotkey_row.pack(fill="x", padx=20, pady=(0, 6))

        self._hotkey_display = ctk.CTkLabel(
            hotkey_row, text=self._config.get("hotkey", "ctrl+alt+space"),
            font=FONTS["body_large_bold"], text_color=COLOR_GREEN, anchor="w",
        )
        self._hotkey_display.pack(side="left", fill="x", expand=True)

        self._capture_btn = ctk.CTkButton(
            hotkey_row, text="🎯 Change", height=28, width=84,
            font=FONTS["body_small"], fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, corner_radius=6,
            command=lambda: self._on_capture_click("read"),
        )
        self._capture_btn.pack(side="right")

        # ── Swap-voice hotkey (cycles through the voice list) ──
        swap_row = ctk.CTkFrame(card, fg_color="transparent")
        swap_row.pack(fill="x", padx=20, pady=(0, 6))

        ctk.CTkLabel(
            swap_row, text="Swap voice hotkey",
            font=FONTS["body_small"], text_color=COLOR_TEXT_DIM, anchor="w",
        ).pack(side="left", padx=(0, 8))

        self._swap_hotkey_display = ctk.CTkLabel(
            swap_row, text=self._config.get("swap_voice_hotkey", "ctrl+alt+v"),
            font=FONTS["body_large_bold"], text_color=COLOR_GREEN, anchor="w",
        )
        self._swap_hotkey_display.pack(side="left", fill="x", expand=True)

        self._swap_capture_btn = ctk.CTkButton(
            swap_row, text="🎯 Change", height=28, width=84,
            font=FONTS["body_small"], fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, corner_radius=6,
            command=lambda: self._on_capture_click("swap"),
        )
        self._swap_capture_btn.pack(side="right")

        self._capture_hint = ctk.CTkLabel(
            card, text="", font=FONTS["body_small"], text_color=COLOR_YELLOW,
            anchor="w", wraplength=440,
        )
        self._capture_hint.pack(fill="x", padx=20, pady=(0, 14))

    def _build_general_card(self) -> None:
        card = self._card()
        ctk.CTkLabel(
            card, text="⚙  General", font=FONTS["body_large_bold"], text_color=COLOR_TEXT,
        ).pack(anchor="w", padx=20, pady=(16, 6))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkLabel(
            row, text="Start Readout automatically with Windows",
            font=FONTS["body_small"], text_color=COLOR_TEXT, anchor="w",
        ).pack(side="left")

        self._autostart_switch = ctk.CTkSwitch(
            row, text="", progress_color=COLOR_ACCENT, command=self._on_autostart_toggle,
        )
        self._autostart_switch.pack(side="right")
        if self._config.get("autostart", False):
            self._autostart_switch.select()
        else:
            self._autostart_switch.deselect()

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=(14, 18))

        info_row = ctk.CTkFrame(footer, fg_color="transparent")
        info_row.pack(fill="x")

        ctk.CTkLabel(
            info_row, text="Readout v0.1.0", font=FONTS["body_small"],
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
            command=self._on_quit,
        )
        quit_btn.pack(side="right")

    # ═══════════════════════════════════════════════════════════
    #  Event queue polling
    # ═══════════════════════════════════════════════════════════

    def _poll_queue(self) -> None:
        try:
            while True:
                event = self.event_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _handle_event(self, event) -> None:
        if isinstance(event, tuple):
            kind = event[0]
            if kind == "speech_done":
                self.app_state.set_status(ReadStatus.IDLE)
                self._update_ui()
                self._apply_pending_voice_if_idle()
            elif kind == "speech_error":
                self.app_state.set_status(ReadStatus.ERROR)
                self.app_state.set_error_message(str(event[1]))
                self._update_ui()
                self._apply_pending_voice_if_idle()
            elif kind == "apply_pending_voice":
                self._apply_pending_voice_if_idle()
            elif kind == "voices_loaded":
                self._handle_voices_loaded(event[1], event[2])
            elif kind == "notice":
                msg = str(event[1])
                duration = int(event[2]) if len(event) > 2 else 4000
                self._show_notice(msg, duration)
            elif kind == "status_changed":
                self._update_ui()
            return

        if event == "toggle_read":
            self._on_toggle_read()
        elif event == "read_selection":
            self._on_read_selection()
        elif event == "stop_reading":
            self._on_stop()
        elif event == "cycle_voice":
            self._on_cycle_voice()
        elif event == "show_window":
            self.show_window()
        elif event == "quit":
            self._on_quit()

    # ═══════════════════════════════════════════════════════════
    #  Reading actions
    # ═══════════════════════════════════════════════════════════

    def _on_toggle_read(self) -> None:
        """Hotkey toggle: stop if speaking, otherwise read the selection."""
        # Always confirm the hotkey was heard, even if we then stop or the
        # read is ignored while busy — the user should never get silence.
        play_pressed_sound()
        # Stopping always wins — even mid-grab — so the hotkey reliably stops
        # both engines (Edge plays in the background, Windows speech blocks).
        if self.engine.is_speaking() or self.app_state.get_status() == ReadStatus.READING:
            self._on_stop()
            return
        if self._busy:
            return  # a grab/speak is already in flight — ignore
        self._on_read_selection()

    def _on_read_selection(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._cancel_requested = False
        self.app_state.set_status(ReadStatus.READING)
        self.app_state.set_error_message("")
        self._update_ui()
        threading.Thread(
            target=self._read_selection_worker, daemon=True, name="readout-grab"
        ).start()

    def _read_selection_worker(self) -> None:
        # Check the foreground window once so the grab and the messaging
        # below always describe the same window.
        in_terminal = selection.is_legacy_terminal_focused()
        info: dict = {}
        try:
            text = selection.grab_selected_text(
                in_legacy_terminal=in_terminal, info=info
            )
        except Exception:
            text = ""
        if not text:
            self._busy = False
            play_failure_sound()
            # Persistent ERROR state (status line stays red until the next
            # action) so the user has time to see what happened — the brief
            # flash before was easy to miss.
            self.app_state.set_status(ReadStatus.ERROR)
            if info.get("image_only"):
                # A copy DID happen — the selection was an image (sticker/
                # emoticon) with no readable text. Say that precisely instead
                # of the generic "no text selected".
                self.app_state.set_error_message("Selection was an image, not text")
                msg = (
                    "The selection was an image (e.g. a sticker/emoticon), not "
                    "text — select the text around it and try again."
                )
            else:
                self.app_state.set_error_message("No text selected")
                if in_terminal:
                    msg = (
                        "No text selected — in this terminal, drag to select with "
                        "the mouse (it copies on release), then try again."
                    )
                else:
                    msg = "No text selected — select some text and try again."
            self.event_queue.put(("notice", msg, 8000))
            self.event_queue.put(("status_changed",))
            # A voice swap that arrived mid-grab can apply now that we're idle.
            self.event_queue.put(("apply_pending_voice",))
            return
        self.app_state.set_last_text(text)
        if in_terminal:
            # Legacy consoles have no copy shortcut; QuickEdit copied the
            # selection to the clipboard on mouse-release, which is what we
            # just read.  Say so, so a stale clipboard never confuses.
            self.event_queue.put((
                "notice",
                "Read from the clipboard — in this terminal, drag-selecting "
                "copies on release.",
                6000,
            ))
        self._speak_in_worker(text)

    def _on_speak_box(self) -> None:
        if self._busy:
            return
        text = self._text_box.get("1.0", "end").strip()
        if not text:
            play_failure_sound()
            self.app_state.set_status(ReadStatus.ERROR)
            self.app_state.set_error_message("Type or paste some text first.")
            self._show_notice("Type or paste some text first.", 8000)
            self._update_ui()
            return
        self._busy = True
        self._cancel_requested = False
        self.app_state.set_status(ReadStatus.READING)
        self.app_state.set_error_message("")
        self._update_ui()
        threading.Thread(
            target=self._speak_in_worker, args=(text,), daemon=True, name="readout-speak"
        ).start()

    def _speak_in_worker(self, text: str) -> None:
        try:
            if self._cancel_requested:
                return
            # Text was grabbed and speech is starting — confirm success.
            # (Fires just before speak(); a fast failure would then also
            # play the failure cue, which is acceptable feedback.)
            play_success_sound()
            # Speak with the ACTIVE voice from the rotation; it may belong to
            # the other engine, so route explicitly instead of assuming the
            # engine the Settings window currently shows.
            entry = voices_mod.active_voice(self._config)
            backend = entry.get("backend")
            if backend not in available_backends():
                backend = self.engine.get_backend()
            self.engine.speak(
                text,
                voice=entry.get("voice", ""),
                rate_percent=int(self._config.get("rate_percent", 100)),
                volume=int(self._config.get("volume", 100)),
                on_done=lambda: self.event_queue.put(("speech_done",)),
                backend=backend,
            )
        except TTSError as exc:
            if not self._cancel_requested:  # stopping the engine may surface errors
                play_failure_sound()
                self.event_queue.put(("speech_error", str(exc)))
        except Exception as exc:  # defensive — never kill the worker silently
            if not self._cancel_requested:
                play_failure_sound()
                self.event_queue.put(("speech_error", f"Unexpected error: {exc}"))
        finally:
            self._busy = False

    def _on_stop(self) -> None:
        self._cancel_requested = True
        # Stop every engine: speech may be running on the active voice's
        # engine, which can differ from the one Settings currently shows.
        self.engine.stop_all()
        self.app_state.set_status(ReadStatus.IDLE)
        self.app_state.set_error_message("")
        self._update_ui()
        # A voice swap that arrived mid-read can apply now that we're idle.
        self._apply_pending_voice_if_idle()

    # ═══════════════════════════════════════════════════════════
    #  Voice / backend selection
    # ═══════════════════════════════════════════════════════════

    def _refresh_voices(self) -> None:
        backend = self.engine.get_backend()
        self._voices_state_label.configure(text="Loading voices…")
        threading.Thread(
            target=self._voices_worker, args=(backend,), daemon=True,
            name="readout-voices",
        ).start()

    def _voices_worker(self, backend: str) -> None:
        try:
            voices = self.engine.list_voices(backend)
        except Exception:
            voices = []
        self.event_queue.put(("voices_loaded", backend, voices))

    def _handle_voices_loaded(self, backend: str, voices: List[VoiceInfo]) -> None:
        if backend != self.engine.get_backend():
            return  # user switched engines while we were loading
        self._current_voices = voices

        is_system = backend == "system"
        entries: List[tuple] = [("(System default)", "")] if is_system else []
        entries += [(v.name, v.name) for v in voices]
        if not entries:
            entries = [("(no voices found)", "")]
        valid = {value for _, value in entries}

        # Keep the rotation consistent with what this engine actually offers.
        changed = False
        vl = self._config.setdefault("voice_list", [])
        if not vl:
            vl.append({"backend": backend, "voice": "" if is_system else (voices[0].name if voices else "")})
            changed = True

        if voices:
            # Drop rotation entries whose voice no longer exists on this engine.
            pruned = voices_mod.prune_entries(vl, backend, valid)
            if len(pruned) != len(vl):
                vl[:] = pruned
                changed = True
            if not vl:
                vl.append({"backend": backend, "voice": "" if is_system else voices[0].name})
                changed = True
            # Make sure the rotation has an entry for this backend. Honour the
            # persisted swap position when it still points at this backend
            # (so the user's voice choice survives restarts); only snap to the
            # first entry on the backend when the persisted position belongs
            # to the other engine (e.g. the user just switched the Engine
            # picker).
            persisted = voices_mod.clamp_index(
                vl, self._config.get("active_voice_index", 0)
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
            self._config["active_voice_index"] = voices_mod.clamp_index(vl, idx)
        else:
            self._config["active_voice_index"] = voices_mod.clamp_index(
                vl, self._config.get("active_voice_index", 0)
            )

        if changed:
            save_config(self._config)

        count = len(voices)
        self._voices_state_label.configure(
            text=f"{count} voice{'s' if count != 1 else ''} available"
        )
        self._refresh_voice_widgets()
        self._update_voice_preview()

    def _on_backend_changed(self, display: str) -> None:
        name = next((n for n, d in self._backend_options if d == display), "system")
        if name == self.engine.get_backend():
            return
        self.engine.set_backend(name)
        self._config["backend"] = name
        save_config(self._config)
        self._refresh_voices()

    def _on_voice_changed(self, display: str) -> None:
        """The user picked a new main voice: it becomes index 0 of the rotation
        and the previous main is kept as the first secondary."""
        # Find the voice name behind the display label ("" for system default).
        value = ""
        for v in self._current_voices:
            if v.name == display:
                value = v.name
                break
        voices_mod.set_main_voice(self._config, self.engine.get_backend(), value)
        save_config(self._config)
        self._rebuild_secondary_rows()
        self._update_voice_preview()

    def _update_voice_preview(self) -> None:
        display = self._voice_menu.get()
        for v in self._current_voices:
            if v.name == display:
                bits = [b for b in (v.language, v.gender) if b]
                preview = f"{v.name}  ·  {' / '.join(bits)}" if bits else v.name
                self._voices_state_label.configure(text=preview)
                return
        self._voices_state_label.configure(
            text=f"{len(self._current_voices)} voices available"
        )

    # ═══════════════════════════════════════════════════════════
    #  Voice rotation (main + secondary voices, swap button / hotkey)
    # ═══════════════════════════════════════════════════════════

    def _voice_entries(self) -> List[tuple]:
        """(display, value) pairs for the current backend's loaded voices."""
        is_system = self.engine.get_backend() == "system"
        entries: List[tuple] = [("(System default)", "")] if is_system else []
        entries += [(v.name, v.name) for v in self._current_voices]
        if not entries:
            entries = [("(no voices found)", "")]
        return entries

    # ═══════════════════════════════════════════════════════════
    #  Voice search (live filter for every voice dropdown)
    # ═══════════════════════════════════════════════════════════

    def _voice_search_query(self) -> str:
        if getattr(self, "_voice_search", None) is None:
            return ""
        return self._voice_search.get().strip()

    def _filtered_entries(self) -> List[tuple]:
        """All voices of the current backend, filtered by the search box.

        The active voice is always kept so the current selection never
        disappears while the user types.
        """
        entries = self._voice_entries()
        active = voices_mod.active_voice(self._config)
        always: List[str] = []
        if active.get("backend") == self.engine.get_backend():
            display = next((d for d, v in entries if v == active["voice"]), None)
            if display:
                always = [display]
        return voices_mod.filter_entries(entries, self._voice_search_query(), always)

    def _on_voice_search(self) -> None:
        """Live-filter the main dropdown, secondary rows and add picker."""
        self._refresh_voice_widgets()
        self._update_voice_preview()

    def _clear_voice_search(self) -> None:
        self._voice_search.delete(0, "end")
        self._refresh_voice_widgets()
        self._update_voice_preview()
        self._voice_search.focus_set()

    def _refresh_voice_widgets(self) -> None:
        """Re-apply the search filter to the main dropdown + secondary rows."""
        entries = self._filtered_entries()
        if not entries:
            entries = [(_NO_MATCHES_LABEL, "")]
        self._voice_menu.configure(values=[d for d, _ in entries])
        active = voices_mod.active_voice(self._config)
        display = None
        if active.get("backend") == self.engine.get_backend():
            display = next((d for d, v in entries if v == active["voice"]), None)
        self._voice_menu.set(display if display is not None else entries[0][0])
        self._rebuild_secondary_rows(entries)

    def _on_cycle_voice(self) -> None:
        """Advance to the next voice in the rotation (button / hotkey / tray)."""
        play_pressed_sound()
        vl = self._config.get("voice_list") or []
        if len(vl) <= 1:
            self._show_notice(
                "Add a secondary voice to use voice swapping — pick one below "
                "and press ＋ Add voice"
            )
            return
        idx = voices_mod.next_voice_index(vl, self._config.get("active_voice_index", 0))
        self._config["active_voice_index"] = idx
        save_config(self._config)
        entry = vl[idx]
        if self._busy or self.engine.is_speaking():
            # Don't cut the current read — remember the swap and apply it once
            # the read finishes (the engine switch could stop current speech).
            self._notice_voice_pending = True
            self._show_notice(
                f"Voice set to {voices_mod.entry_display(entry)} — applies to the next read"
            )
            return
        self._apply_voice_entry(entry)
        self._show_notice(f"Voice: {voices_mod.entry_display(entry)}")

    def _apply_voice_entry(self, entry: VoiceEntry) -> None:
        """Make ``entry`` the active voice, switching engines if needed."""
        backend = entry.get("backend")
        if backend in available_backends() and backend != self.engine.get_backend():
            self.engine.set_backend(backend)
            self._config["backend"] = backend
            save_config(self._config)
            display = next(
                (d for n, d in self._backend_options if n == backend), backend
            )
            self._backend_menu.set(display)
            # Voices for the new backend load async; the dropdown will land on
            # the active voice in _handle_voices_loaded.
            self._voice_menu.configure(values=["(switching…)"])
            self._voice_menu.set("(switching…)")
            self._refresh_voices()
        else:
            self._rebuild_secondary_rows()

    def _apply_pending_voice_if_idle(self) -> None:
        """Apply a voice swap that arrived while a read was in progress."""
        if (
            getattr(self, "_notice_voice_pending", False)
            and not self._busy
            and not self.engine.is_speaking()
        ):
            self._notice_voice_pending = False
            self._apply_voice_entry(voices_mod.active_voice(self._config))

    def _rebuild_secondary_rows(self, entries: Optional[List[tuple]] = None) -> None:
        """Rebuild the secondary-voice rows + add row; sync the main dropdown."""
        if getattr(self, "_secondary_rows_frame", None) is None:
            return
        if entries is None:
            entries = self._filtered_entries()
        for w in self._secondary_rows_frame.winfo_children():
            w.destroy()

        backend = self.engine.get_backend()
        vl = self._config.get("voice_list") or []
        active = voices_mod.active_voice(self._config)

        # Keep the main Voice dropdown showing the active voice.
        if active.get("backend") == backend:
            display = next((d for d, v in entries if v == active["voice"]), None)
            if display is not None:
                self._voice_menu.set(display)

        for i in range(1, len(vl)):
            entry = vl[i]
            row = ctk.CTkFrame(self._secondary_rows_frame, fg_color="transparent")
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
                    command=lambda value, i=i: self._on_secondary_changed(i, value),
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
                command=lambda i=i: self._on_secondary_remove(i),
            )
            remove_btn.pack(side="right")

        # ── Add row: when searching, show EVERY match (even voices already in
        #    the rotation, so you can find e.g. the main voice); otherwise only
        #    voices not yet in the list, to keep it clutter-free. ──
        query = self._voice_search_query()
        in_list = {e.get("voice") for e in vl if e.get("backend") == backend}
        if query:
            candidates = entries
        else:
            candidates = [(d, v) for d, v in entries if v not in in_list]
        # A real candidate is anything that isn't a sentinel placeholder.
        real = [c for c in candidates if c[0] not in (_NO_MATCHES_LABEL, _ALL_ADDED_LABEL)]
        if real:
            self._add_menu.configure(values=[d for d, _ in real])
            self._add_menu.set(real[0][0])
            self._add_btn.configure(state="normal")
        else:
            # Keep the button disabled — the placeholder must never be addable.
            hint = _NO_MATCHES_LABEL if candidates else _ALL_ADDED_LABEL
            self._add_menu.configure(values=[hint])
            self._add_menu.set(hint)
            self._add_btn.configure(state="disabled")

    def _on_secondary_changed(self, index: int, display: str) -> None:
        """The user picked a different voice in a secondary row's dropdown."""
        value = next(
            (v.name for v in self._current_voices if v.name == display), ""
        )
        vl = self._config.get("voice_list") or []
        if 0 < index < len(vl):
            vl[index] = {"backend": self.engine.get_backend(), "voice": value}
            save_config(self._config)
        self._rebuild_secondary_rows()

    def _on_secondary_remove(self, index: int) -> None:
        """Remove a secondary voice from the rotation."""
        if voices_mod.remove_entry(self._config, index):
            save_config(self._config)
        self._rebuild_secondary_rows()
        self._update_voice_preview()

    def _on_add_secondary(self) -> None:
        """Add the currently selected add-row voice to the rotation."""
        display = self._add_menu.get()
        if display in (_NO_MATCHES_LABEL, _ALL_ADDED_LABEL, "(no voices)"):
            self._rebuild_secondary_rows()
            return
        value = next(
            (v.name for v in self._current_voices if v.name == display), ""
        )
        backend = self.engine.get_backend()
        vl = self._config.get("voice_list") or []
        existing = [
            i for i, e in enumerate(vl)
            if e.get("backend") == backend and e.get("voice") == value
        ]
        if existing:
            # The search bar surfaces voices that are already in the rotation
            # (like the main voice) — say exactly where it already lives.
            where = "it's the main voice" if existing[0] == 0 else "it's already a secondary voice"
            self._show_notice(f"{display} is already in the list — {where}")
        elif voices_mod.add_secondary(self._config, backend, value):
            save_config(self._config)
            self._show_notice(
                f"Added {voices_mod.entry_display({'backend': backend, 'voice': value})}"
            )
        self._rebuild_secondary_rows()

    # ═══════════════════════════════════════════════════════════
    #  Sliders
    # ═══════════════════════════════════════════════════════════

    def _on_rate_changed(self, value) -> None:
        pct = int(round(value))
        self._config["rate_percent"] = pct
        save_config(self._config)
        self._rate_value_label.configure(text=f"{pct}%")

    def _on_volume_changed(self, value) -> None:
        vol = int(round(value))
        self._config["volume"] = vol
        save_config(self._config)
        self._volume_value_label.configure(text=str(vol))

    # ═══════════════════════════════════════════════════════════
    #  Hotkey capture
    # ═══════════════════════════════════════════════════════════

    def _on_capture_click(self, mode: str = "read") -> None:
        """Start capturing a hotkey: ``mode`` is "read" or "swap"."""
        if self._capturing:
            self._stop_capturing()
            return
        self._capturing = True
        self._capture_mode = mode
        self._capture_btn.configure(
            text="⏹ Cancel", fg_color=COLOR_RED, hover_color=COLOR_RED_HOVER
        )
        self._swap_capture_btn.configure(
            text="⏹ Cancel", fg_color=COLOR_RED, hover_color=COLOR_RED_HOVER
        )
        target = "the read hotkey" if mode == "read" else "the voice-swap hotkey"
        self._capture_hint.configure(
            text=f"Press your shortcut for {target} (with Ctrl / Alt / Shift)… Esc to cancel."
        )
        # Unregister the global hotkeys while capturing so pressing the current
        # combo (e.g. to change it) can't trigger a read from the settings window.
        try:
            self.hotkeys._remove()
        except Exception:
            pass
        self.focus_force()

    def _on_global_keypress(self, event) -> None:
        """Build a hotkey combo from a tkinter KeyPress.

        Modifiers use ``decode_hotkey_modifiers`` (tkinter bits for ctrl/shift,
        keyboard-library physical truth for alt/win — see the note above). The
        main key comes from the keyboard hook's physical name so the captured
        combo is layout-independent: Ctrl+Shift+7 stays ``ctrl+shift+7`` on
        any keyboard layout.
        """
        if not self._capturing:
            return

        keysym = event.keysym.lower()

        # Ignore bare modifier presses — they are reflected in event.state.
        if keysym in ("control_l", "control_r", "shift_l", "shift_r", "alt_l", "alt_r", "super_l", "super_r"):
            return

        if keysym == "escape":
            self._stop_capturing()
            return

        mods = decode_hotkey_modifiers(
            event.state,
            alt_down=_is_physically_down("alt"),
            win_down=_is_physically_down("win"),
        )

        key = self._recent_physical_key()
        if not key:
            key = _TKINTER_KEY_MAP.get(keysym, keysym)
        if not key:
            return

        # Require at least one modifier so plain typing is never hijacked
        # into a hotkey (function keys are allowed bare).
        if not mods and not key.startswith("f"):
            return

        combo = "+".join(mods + [key])
        self._apply_hotkey(combo)

    def _recent_physical_key(self) -> str:
        """Return the physical key the user just pressed (keyboard hook truth)."""
        try:
            fresh = time.time() - self.hotkeys.last_key_time < 0.75
        except Exception:
            return ""
        return self.hotkeys.last_key_name if fresh else ""

    def _apply_hotkey(self, combo: str) -> None:
        if self._capture_mode == "swap":
            self._config["swap_voice_hotkey"] = combo
            self._swap_hotkey_display.configure(text=combo)
            message = f"Voice-swap hotkey set to {combo}"
        else:
            self._config["hotkey"] = combo
            self._hotkey_display.configure(text=combo)
            message = f"Hotkey set to {combo}"
        save_config(self._config)
        self.hotkeys.set_hotkeys(
            self._config.get("hotkey", "ctrl+alt+space"),
            self._config.get("swap_voice_hotkey", "ctrl+alt+v"),
        )
        self._stop_capturing()
        self._show_notice(message)

    def _stop_capturing(self) -> None:
        self._capturing = False
        self._capture_btn.configure(
            text="🎯 Change", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER
        )
        if getattr(self, "_swap_capture_btn", None) is not None:
            self._swap_capture_btn.configure(
                text="🎯 Change", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER
            )
        self._capture_hint.configure(text="")
        # Re-register the (possibly new) hotkeys if capture was cancelled.
        try:
            self.hotkeys.set_hotkeys(
                self._config.get("hotkey", "ctrl+alt+space"),
                self._config.get("swap_voice_hotkey", "ctrl+alt+v"),
            )
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    #  General / window lifecycle
    # ═══════════════════════════════════════════════════════════

    def _on_autostart_toggle(self) -> None:
        enabled = bool(self._autostart_switch.get())
        self._config["autostart"] = enabled
        save_config(self._config)
        ok = apply_autostart_setting(enabled)
        if ok:
            self._show_notice("Auto-start " + ("enabled" if enabled else "disabled"))
        else:
            self._show_notice("Could not update the Windows auto-start setting")

    def _show_notice(self, msg: str, duration_ms: int = 4000) -> None:
        self._notice_label.configure(text=msg)
        if self._notice_after_id:
            self.after_cancel(self._notice_after_id)
        self._notice_after_id = self.after(duration_ms, lambda: self._notice_label.configure(text=""))

    def show_window(self) -> None:
        """Show (or restore) the main window from the tray."""
        self.deiconify()
        self.lift()
        self.focus_force()

    def _on_close(self) -> None:
        """Hide to the tray instead of quitting."""
        self.withdraw()

    def _on_quit(self) -> None:
        self.engine.stop_all()
        try:
            self.hotkeys._remove()
        except Exception:
            pass
        self.destroy()

    # ═══════════════════════════════════════════════════════════
    #  Status display
    # ═══════════════════════════════════════════════════════════

    def _update_ui(self) -> None:
        status = self.app_state.get_status()
        if status == ReadStatus.READING:
            self._status_dot.configure(text_color=COLOR_YELLOW)
            self._status_label.configure(text="Reading… (hotkey again to stop)")
            self._stop_btn.configure(state="normal")
            self._read_btn.configure(state="disabled")
            self._speak_box_btn.configure(state="disabled")
        elif status == ReadStatus.ERROR:
            self._status_dot.configure(text_color=COLOR_RED)
            self._status_label.configure(text=f"Error: {self.app_state.get_error_message()}")
            self._stop_btn.configure(state="disabled")
            self._read_btn.configure(state="normal")
            self._speak_box_btn.configure(state="normal")
        else:
            hotkey = self._config.get("hotkey", "ctrl+alt+space")
            self._status_dot.configure(text_color=COLOR_GREEN)
            self._status_label.configure(
                text=f"Idle — select text anywhere, press {hotkey} to listen"
            )
            self._stop_btn.configure(state="disabled")
            self._read_btn.configure(state="normal")
            self._speak_box_btn.configure(state="normal")
