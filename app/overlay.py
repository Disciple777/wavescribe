"""Small always-on-top status overlay for WaveScribe.

Shows a compact, borderless indicator in the bottom-right corner
of the screen while recording or transcribing, so the user can
see the app status even when the main window is minimized.
"""

import customtkinter as ctk

from app.theme import FONTS, COLOR_GREEN, COLOR_RED, COLOR_YELLOW, COLOR_CARD, COLOR_TEXT, COLOR_BORDER


class StatusOverlay:
    """Compact, always-on-top status indicator.

    Displays a colored dot + status text in the bottom-right corner.
    Automatically hides when idle or in error state.
    """

    def __init__(self) -> None:
        self.window: ctk.CTkToplevel | None = None
        self._label: ctk.CTkLabel | None = None
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

        self._label = ctk.CTkLabel(
            frame,
            text="",
            font=FONTS["body_bold"],
            text_color=COLOR_TEXT,
        )
        self._label.pack(fill="both", expand=True, padx=18, pady=(8, 10))

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

    def hide(self) -> None:
        """Hide the overlay if it's currently visible."""
        if self.window is not None:
            self.window.withdraw()
        self._visible = False

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
