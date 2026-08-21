"""Tests for readout/core/config.py."""

import json
import os

from readout.core import config as cfg


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "get_config_dir", lambda: str(tmp_path))
    loaded = cfg.load_config()
    assert loaded["hotkey"] == "ctrl+alt+space"
    assert loaded["backend"] == "system"
    assert loaded["autostart"] is False
    assert loaded["rate_percent"] == 100
    assert loaded["voice_list"] == []
    assert loaded["active_voice_index"] == 0
    assert loaded["swap_voice_hotkey"] == "ctrl+alt+v"


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "get_config_dir", lambda: str(tmp_path))
    custom = cfg.DEFAULT_CONFIG.copy()
    custom["hotkey"] = "ctrl+shift+p"
    custom["backend"] = "edge"
    custom["rate_percent"] = 120
    cfg.save_config(custom)

    loaded = cfg.load_config()
    assert loaded["hotkey"] == "ctrl+shift+p"
    assert loaded["backend"] == "edge"
    assert loaded["rate_percent"] == 120
    assert loaded["volume"] == 100  # backfilled from defaults


def test_missing_keys_backfilled_from_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "get_config_dir", lambda: str(tmp_path))
    path = cfg._get_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"hotkey": "ctrl+shift+z"}, f)

    loaded = cfg.load_config()
    assert loaded["hotkey"] == "ctrl+shift+z"
    assert loaded["backend"] == "system"
    assert loaded["volume"] == 100


def test_corrupt_config_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "get_config_dir", lambda: str(tmp_path))
    path = cfg._get_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ this is not json !!")

    loaded = cfg.load_config()
    assert loaded["hotkey"] == "ctrl+alt+space"
