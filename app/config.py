"""Configuration persistence for WaveScribe.

Loads and saves settings to a config.json file in the project root.
Stores API key, hotkey preferences, model settings, and UI state.
"""

import json
import os
from typing import Any, Dict

# ── Default Configuration ──

DEFAULT_CONFIG: Dict[str, Any] = {
    "api_key": "",
    "model_size": "base",
    "mode": "cloud",
    "punctuate_speech": True,
    "auto_capitalize": True,
    "numbers_as_digits": False,
    "hotkey_start": "ctrl+shift+r",
    "hotkey_stop": "ctrl+shift+s",
}

CONFIG_FILENAME = "config.json"


def _get_config_path() -> str:
    """Get the path to config.json in the project root directory.

    The config.py file lives at wavescribe/app/config.py,
    so the project root is two levels up.
    """
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        CONFIG_FILENAME,
    )


def load_config() -> Dict[str, Any]:
    """Load configuration from config.json, merging with defaults.

    Returns:
        A dictionary with all config keys populated (missing keys
        fall back to DEFAULT_CONFIG values).
    """
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
        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        print(f"Warning: Could not save config ({exc}).")
