r"""Auto-start with Windows for Readout (HKCU Run key).

Mirrors WaveScribe's app/autostart.py: registers a ``Readout`` value in
``HKCU\Software\Microsoft\Windows\CurrentVersion\Run`` so the app launches
at login. Works from dev (``python readout/main.py``) and bundled builds;
harmless no-ops on non-Windows platforms.
"""

import os
import sys
from typing import Optional

if sys.platform == "win32":
    import winreg
else:
    winreg = None  # type: ignore[assignment]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "Readout"


def _command() -> str:
    """Command line used to launch Readout at login."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'

    main_py = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "main.py",
    )
    return f'"{sys.executable}" "{main_py}"'


def is_autostart_enabled() -> bool:
    """True if the Readout Run key value currently exists."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except (OSError, FileNotFoundError):
        return False


def enable_autostart() -> bool:
    """Register Readout in the Run key. Returns True on success."""
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
    """Remove the Readout Run key value (idempotent). Returns True on success."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
        return True
    except FileNotFoundError:
        return True  # already absent
    except OSError:
        return False


def apply_autostart_setting(enabled: bool) -> bool:
    """Apply the desired auto-start state to the registry."""
    if enabled:
        return enable_autostart()
    return disable_autostart()
