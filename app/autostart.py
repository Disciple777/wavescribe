"""Auto-start with Windows support for WaveScribe.

Registers (or removes) a ``WaveScribe`` value in the per-user Run key
(HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run) so the app
launches automatically at login.

Works correctly from both dev (``python main.py``) and bundled
(PyInstaller one-folder) builds. On non-Windows platforms these
functions are harmless no-ops.
"""

import os
import sys
from typing import Optional

if sys.platform == "win32":
    import winreg
else:
    winreg = None  # type: ignore[assignment]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "WaveScribe"


def _command() -> str:
    """Return the command line used to launch WaveScribe at login.

    - Bundled build: the executable path (quoted, handles spaces).
    - Dev mode: the Python interpreter + main.py (quoted, handles spaces).
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'

    main_py = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "main.py",
    )
    return f'"{sys.executable}" "{main_py}"'


def is_autostart_enabled() -> bool:
    """Return True if the WaveScribe Run key value currently exists."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except (OSError, FileNotFoundError):
        return False


def enable_autostart() -> bool:
    """Register WaveScribe in the Run key. Returns True on success."""
    if winreg is None:
        return False
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_WRITE
        ) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _command())
        return True
    except OSError:
        return False


def disable_autostart() -> bool:
    """Remove the WaveScribe Run key value. Returns True on success.

    Removing a value that doesn't exist counts as success (idempotent).
    """
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
        return True
    except FileNotFoundError:
        return True  # already absent — nothing to do
    except OSError:
        return False


def apply_autostart_setting(enabled: bool) -> bool:
    """Apply the desired auto-start state to the registry.

    Args:
        enabled: True to register, False to remove.

    Returns:
        True if the registry now matches the requested state.
    """
    if enabled:
        return enable_autostart()
    return disable_autostart()
