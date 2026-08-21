"""Configuration persistence for Readout.

Settings live in a JSON file at:
  - Windows: %APPDATA%\\Readout\\config.json
  - Linux/Mac: ~/.config/readout/config.json

Mirrors the proven pattern from WaveScribe's app/config.py.
"""

import json
import os
import sys
from typing import Any, Dict

# ── Default Configuration ──

DEFAULT_CONFIG: Dict[str, Any] = {
    "hotkey": "ctrl+alt+space",  # toggle: read selection / stop speaking
    "backend": "system",         # "system" (offline) | "edge" (neural, online)
    "system_voice": "",          # legacy: "" = Windows default voice
    "edge_voice": "en-US-AriaNeural",
    "rate_percent": 100,         # 50..150 (100 = normal speed)
    "volume": 100,               # 0..100
    "autostart": False,          # launch Readout with Windows
    # Voice rotation: index 0 is the main voice, the rest are secondary.
    "voice_list": [],            # [{"backend": "system"|"edge", "voice": str}, …]
    "active_voice_index": 0,     # pointer into voice_list (what the next read uses)
    "swap_voice_hotkey": "ctrl+alt+v",  # cycle through the voice list
}

CONFIG_FILENAME = "config.json"
APP_DIR_NAME = "Readout"


def get_config_dir() -> str:
    """Return the platform-appropriate config directory."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, APP_DIR_NAME)
    return os.path.join(os.path.expanduser("~"), ".config", "readout")


def _get_config_path() -> str:
    """Full path to config.json."""
    return os.path.join(get_config_dir(), CONFIG_FILENAME)


def load_config() -> Dict[str, Any]:
    """Load configuration, merging saved values over the defaults."""
    path = _get_config_path()
    if not os.path.exists(path):
        return DEFAULT_CONFIG.copy()

    try:
        with open(path, "r", encoding="utf-8") as f:
            user_config: Dict[str, Any] = json.load(f)
        return {**DEFAULT_CONFIG, **user_config}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Warning: could not load config ({exc}). Using defaults.")
        return DEFAULT_CONFIG.copy()


def save_config(config: Dict[str, Any]) -> None:
    """Persist configuration, filling any missing keys from defaults."""
    path = _get_config_path()
    merged = {**DEFAULT_CONFIG, **config}

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        print(f"Warning: could not save config ({exc}).")
