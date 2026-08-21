"""Tests for the auto-start registry helpers.

These tests never touch the real Windows registry — they verify the
non-Windows no-op behavior and the generated command line.
"""

import sys

from app import autostart


def test_command_includes_main_py():
    cmd = autostart._command()
    assert cmd.startswith('"')
    assert cmd.endswith('"')
    assert "main.py" in cmd


def test_frozen_build_command_is_just_the_exe(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\WaveScribe\WaveScribe.exe")
    cmd = autostart._command()
    assert cmd == r'"C:\Program Files\WaveScribe\WaveScribe.exe"'
    assert "main.py" not in cmd


def test_non_windows_platform_is_noop(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(autostart, "winreg", None)

    assert autostart.is_autostart_enabled() is False
    assert autostart.enable_autostart() is False
    assert autostart.disable_autostart() is False
    assert autostart.apply_autostart_setting(True) is False
    assert autostart.apply_autostart_setting(False) is False
