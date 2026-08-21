"""Tests for readout/core/system_tts.py (pure logic — no speech output)."""

import os

from readout.core.system_tts import (
    VoiceInfo,
    _LIST_SCRIPT,
    _SPEAK_SCRIPT,
    _script_path,
    parse_voices_output,
    system_rate,
)


def test_system_rate_mapping():
    assert system_rate(100) == 0
    assert system_rate(50) == -5
    assert system_rate(150) == 5
    assert system_rate(110) == 1
    assert system_rate(85) == -2  # round(-1.5)


def test_parse_voices_output():
    out = "Microsoft Zira Desktop | Female\nMicrosoft Sabina Desktop | Male\n\n"
    voices = parse_voices_output(out)
    assert voices == [
        VoiceInfo(name="Microsoft Zira Desktop", gender="Female"),
        VoiceInfo(name="Microsoft Sabina Desktop", gender="Male"),
    ]


def test_parse_voices_output_empty():
    assert parse_voices_output("") == []
    assert parse_voices_output("\n   \n") == []


def test_parse_voices_output_skips_bad_lines():
    out = "\nno-pipe-line\nMicrosoft Zira Desktop | Female\n"
    voices = parse_voices_output(out)
    assert voices == [VoiceInfo(name="Microsoft Zira Desktop", gender="Female")]


def test_scripts_are_materialised_to_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "readout.core.system_tts.tempfile.gettempdir", lambda: str(tmp_path)
    )
    path = _script_path("speak", _SPEAK_SCRIPT)
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "SpeechSynthesizer" in content
    assert "[Console]::InputEncoding" in content  # UTF-8 stdin for Unicode text

    list_path = _script_path("list_voices", _LIST_SCRIPT)
    with open(list_path, encoding="utf-8") as f:
        assert "GetInstalledVoices" in f.read()


def test_powershell_exe_points_at_system_windows_powershell():
    from readout.core.system_tts import _powershell_exe

    exe = _powershell_exe()
    assert exe.endswith("powershell.exe")
    assert "WindowsPowerShell" in exe
