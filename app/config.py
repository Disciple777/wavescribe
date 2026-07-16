"""Configuration persistence for WaveScribe.

Loads and saves settings to a config.json file in:
  - Windows: %APPDATA%\WaveScribe\config.json
  - Linux/Mac: ~/.config/wavescribe/config.json

This keeps settings safe across reinstalls and works correctly
from both dev (python main.py) and bundled (PyInstaller) builds.
"""

import json
import os
import shutil
import sys
from typing import Any, Dict

from app.state import LocalModelStatus

# ── Default Configuration ──

DEFAULT_CONFIG: Dict[str, Any] = {
    "api_key": "",
    "model_size": "base",
    "mode": "cloud",
    "punctuate_speech": True,
    "auto_capitalize": True,
    "numbers_as_digits": False,
    "local_model_status": "ready",  # models are bundled or auto-downloaded
    "hotkey_start": "ctrl+shift+r",
    "hotkey_stop": "ctrl+shift+s",
}

CONFIG_FILENAME = "config.json"


def _get_config_dir() -> str:
    """Get the platform-appropriate config directory.

    Returns:
        On Windows: %APPDATA%\WaveScribe
        On Linux/Mac: ~/.config/wavescribe
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "WaveScribe")
    else:
        return os.path.join(os.path.expanduser("~"), ".config", "wavescribe")


def _get_config_path() -> str:
    """Get the full path to config.json."""
    return os.path.join(_get_config_dir(), CONFIG_FILENAME)


def _migrate_old_config() -> None:
    """Migrate config.json from the old project-root location to the new
    APPDATA location, if the old file exists and the new one doesn't."""
    new_path = _get_config_path()
    if os.path.exists(new_path):
        return  # already migrated

    # Old location: alongside the project (two levels up from app/config.py)
    old_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        CONFIG_FILENAME,
    )
    if os.path.exists(old_path):
        try:
            os.makedirs(_get_config_dir(), exist_ok=True)
            shutil.copy2(old_path, new_path)
            print(f"Migrated config from {old_path} to {new_path}")
        except OSError as exc:
            print(f"Warning: Could not migrate config ({exc})")


def load_config() -> Dict[str, Any]:
    """Load configuration from config.json, merging with defaults.

    Automatically migrates old config files on first run.

    Returns:
        A dictionary with all config keys populated (missing keys
        fall back to DEFAULT_CONFIG values).
    """
    # Migrate old config if needed
    _migrate_old_config()

    path = _get_config_path()
    if not os.path.exists(path):
        return DEFAULT_CONFIG.copy()

    try:
        with open(path, "r", encoding="utf-8") as f:
            user_config: Dict[str, Any] = json.load(f)
        # Merge: user values override defaults, but any missing keys are filled
        return {**DEFAULT_CONFIG, **user_config}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Warning: Could not load config ({exc}). Using defaults.")
        return DEFAULT_CONFIG.copy()


def save_config(config: Dict[str, Any]) -> None:
    """Save configuration to config.json.

    Args:
        config: A dictionary of config values to persist. Missing keys
                are filled from DEFAULT_CONFIG before saving.
    """
    path = _get_config_path()
    merged = {**DEFAULT_CONFIG, **config}

    try:
        os.makedirs(_get_config_dir(), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        print(f"Warning: Could not save config ({exc}).")
