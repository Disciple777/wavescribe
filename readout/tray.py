"""System tray icon for Readout (pystray + Pillow).

Menu actions only push events into the thread-safe queue; the GUI's
poll loop performs the actual work, so no cross-thread UI calls happen.
"""

import queue

import pystray
from PIL import Image, ImageDraw


def _create_icon() -> Image.Image:
    """Draw a simple speaker-with-waves icon (no asset file needed)."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Speaker body
    draw.rounded_rectangle([10, 22, 20, 42], radius=3, fill="#5e29da")
    draw.polygon([(20, 22), (32, 12), (32, 52), (20, 42)], fill="#5e29da")

    # Sound waves
    draw.arc([24, 14, 48, 36], -70, 70, fill="#74ffae", width=4)
    draw.arc([24, 28, 54, 54], -70, 70, fill="#74ffae", width=4)
    return img


def create_tray(event_queue: queue.Queue) -> pystray.Icon:
    """Create the Readout tray icon. Call ``.run()`` in a daemon thread."""
    menu = pystray.Menu(
        pystray.MenuItem(
            "▶  Read selected text",
            lambda: event_queue.put("read_selection"),
            default=True,
        ),
        pystray.MenuItem("⏹  Stop reading", lambda: event_queue.put("stop_reading")),
        pystray.MenuItem("🔁  Cycle voice", lambda: event_queue.put("cycle_voice")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("⚙  Show Readout", lambda: event_queue.put("show_window")),
        pystray.MenuItem("✕  Quit", lambda: event_queue.put("quit")),
    )

    return pystray.Icon("readout", _create_icon(), "Readout", menu)
