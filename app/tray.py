"""System tray icon for WaveScribe.

Uses pystray + Pillow to provide a system tray icon with
Show and Quit menu items.
"""

import os
from typing import Callable, Optional

import pystray
from PIL import Image, ImageDraw


def _create_default_icon() -> Image.Image:
    """Create a simple default icon if the icon file is not available.

    Returns a 64x64 blue circle icon.
    """
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill="#1f6aa5")
    # Draw a simple "W" letter
    draw.text((16, 12), "W", fill="white", font_size=28)
    return img


def create_tray(
    show_callback: Callable[[], None],
    quit_callback: Callable[[], None],
    icon_path: Optional[str] = None,
) -> pystray.Icon:
    """Create a system tray icon for WaveScribe.

    Args:
        show_callback: Called when "Show WaveScribe" is clicked.
        quit_callback: Called when "Quit" is clicked.
        icon_path: Path to the icon PNG file. If None or file not found,
                   a default icon is created.

    Returns:
        A pystray.Icon object. Call .run() or .run_detached() to display it.
    """
    # Load icon from file or use default
    icon_image = None
    if icon_path and os.path.isfile(icon_path):
        try:
            icon_image = Image.open(icon_path)
        except Exception:
            icon_image = None

    if icon_image is None:
        icon_image = _create_default_icon()

    menu = pystray.Menu(
        pystray.MenuItem("Show WaveScribe", show_callback, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_callback),
    )

    return pystray.Icon(
        "wavescribe",
        icon_image,
        "WaveScribe",
        menu,
    )
