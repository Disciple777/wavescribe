"""Small always-on-top status overlay for WaveScribe.

Shows a compact, borderless indicator in the bottom-right corner
of the screen while recording or transcribing, so the user can
see the app status even when the main window is minimized.
"""

import customtkinter as ctk

from app.theme import FONTS, COLOR_GREEN, COLOR_RED, COLOR_YELLOW, COLOR_CARD, COLOR_TEXT, COLOR_BORDER, COLOR_INPUT_BG


class StatusOverlay:
    """Compact, always-on-top status indicator.

    Displays a colored dot + status text in the bottom-right corner,
    plus a thin audio-level bar that appears during recording.
    Automatically hides when idle or in error state.
    """

    def __init__(self) -> None:
        self.window: ctk.CTkToplevel | None = None
        self._label: ctk.CTkLabel | None = None
        self._meter_bar: ctk.CTkProgressBar | None = None
        self._meter_visible = False
        self._visible = False

    # ── Window lifecycle ──

    def _ensure_window(self) -> None:
        """Create the overlay window on first use (lazy init)."""
        if self.window is not None:
            return

        self.window = ctk.CTkToplevel()
        self.window.overrideredirect(True)        # No title bar / borders
        self.window.attributes("-topmost", True)   # Always on top
        self.window.attributes("-alpha", 0.92)     # Slight transparency
        self.window.configure(fg_color=COLOR_CARD)

        # Inner frame for rounded-corner appearance
        frame = ctk.CTkFrame(
            self.window,
            fg_color=COLOR_CARD,
            corner_radius=10,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        frame.pack(fill="both", expand=True, padx=2, pady=2)

        # ── Content container ──
        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=18, pady=(8, 10))

        self._label = ctk.CTkLabel(
            inner,
            text="",
            font=FONTS["body_bold"],
            text_color=COLOR_TEXT,
        )
        self._label.pack(fill="x")

        # ── Thin input-level bar (hidden by default) ──
        self._meter_bar = ctk.CTkProgressBar(
            inner,
            mode="determinate",
            height=4,
            corner_radius=2,
            fg_color=COLOR_INPUT_BG,
            progress_color=COLOR_GREEN,
        )
        self._meter_bar.set(0)

        self.window.withdraw()  # Start hidden

    # ── Public API ──

    def show(self, status_text: str, color: str) -> None:
        """Show the overlay with the given status text and dot color.

        Positions the window in the bottom-right corner of the screen.
        """
        self._ensure_window()

        self._label.configure(text=f"●  {status_text}", text_color=color)
        self.window.deiconify()
        self.window.update_idletasks()

        # Position: 20px from right, 50px from bottom
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        w = self.window.winfo_reqwidth()
        h = self.window.winfo_reqheight()
        x = screen_w - w - 20
        y = screen_h - h - 60
        self.window.geometry(f"+{x}+{y}")

        self._visible = True

        # Hide the meter bar on first show — it starts packed only when
        # set_level receives a non-zero value.
        self._hide_meter()

    def hide(self) -> None:
        """Hide the overlay if it's currently visible."""
        if self.window is not None:
            self.window.withdraw()
        self._visible = False
        self._hide_meter()

    def _hide_meter(self) -> None:
        """Pack-forget the meter bar and reset its state."""
        if self._meter_bar is not None and self._meter_visible:
            self._meter_bar.pack_forget()
        self._meter_visible = False

    # ── Hysteresis threshold ──
    # The bar appears on the first non-zero level and stays visible
    # until the level goes truly silent (< 0.001). This prevents
    # rapid flickering during brief pauses in speech.
    _METER_HIDE_THRESHOLD = 0.001

    def set_level(self, level: float, color: str = COLOR_GREEN) -> None:
        """Update the input-level meter bar (does nothing when idle).

        Call this every 50-100ms while recording. Pass ``level`` as
        a float between 0.0 (silent) and 1.0 (loudest).

        The bar appears on first non-zero sample and stays visible
        until the level drops to ``_METER_HIDE_THRESHOLD`` (0.001),
        preventing flicker during brief pauses.
        """
        if not self._visible or self._meter_bar is None:
            return

        if level >= self._METER_HIDE_THRESHOLD and not self._meter_visible:
            self._meter_bar.pack(fill="x", pady=(6, 0))
            self._meter_visible = True
            # Re-request geometry after adding the bar
            self.window.update_idletasks()
            self._reposition()
        elif level < self._METER_HIDE_THRESHOLD and self._meter_visible:
            self._meter_bar.pack_forget()
            self._meter_visible = False
            self.window.update_idletasks()
            self._reposition()

        self._meter_bar.set(level)
        self._meter_bar.configure(progress_color=color)

    def _reposition(self) -> None:
        """Re-position the overlay window in the bottom-right corner.

        Called after the content changes so the window stays snug.
        """
        if self.window is None:
            return
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        w = self.window.winfo_reqwidth()
        h = self.window.winfo_reqheight()
        x = screen_w - w - 20
        y = screen_h - h - 60
        self.window.geometry(f"+{x}+{y}")

    def update(self, status_text: str, color: str) -> None:
        """Update the overlay text/color, showing it if currently hidden."""
        if not self._visible:
            self.show(status_text, color)
        else:
            self._label.configure(text=f"●  {status_text}", text_color=color)

    def destroy(self) -> None:
        """Destroy the overlay window entirely (call on app exit)."""
        if self.window is not None:
            self.window.destroy()
            self.window = None
        self._visible = False

    @property
    def is_visible(self) -> bool:
        return self._visible
