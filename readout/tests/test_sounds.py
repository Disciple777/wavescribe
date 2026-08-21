"""Tests for readout/core/sounds.py (WAV synthesis, no real audio output)."""

import os
import struct

from readout.core import sounds


def _is_wav(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def test_generate_wav_produces_valid_mono_16bit_riff() -> None:
    data = sounds._generate_wav([440.0, 880.0], [0.2, 0.2], 0.1)
    assert _is_wav(data)
    assert struct.unpack("<H", data[22:24])[0] == 1   # mono
    assert struct.unpack("<H", data[34:36])[0] == 16  # 16-bit samples


def test_three_cues_generate_distinct_wavs() -> None:
    pressed = sounds._generate_wav([660.0, 660.0], [0.18, 0.18], 0.12)
    success = sounds._generate_wav([523.0, 784.0], [0.22, 0.22], 0.20)
    failure = sounds._generate_wav([260.0, 110.0], [0.26, 0.14], 0.30)
    assert _is_wav(pressed) and _is_wav(success) and _is_wav(failure)
    assert len({pressed, success, failure}) == 3


def test_init_sounds_writes_three_files_and_is_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    sounds._INITIALIZED = False
    sounds.init_sounds()

    for path in (sounds._PRESSED_WAV_PATH, sounds._SUCCESS_WAV_PATH, sounds._FAILURE_WAV_PATH):
        assert path is not None and os.path.exists(path)
        with open(path, "rb") as f:
            assert _is_wav(f.read())

    # Calling again must not regenerate or clobber the paths.
    sounds.init_sounds()
    assert sounds._PRESSED_WAV_PATH == os.path.join(str(tmp_path), "readout_pressed.wav")


def test_play_functions_call_winsound_with_generated_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    sounds._INITIALIZED = False
    sounds.init_sounds()

    played: list[str] = []
    monkeypatch.setattr(sounds.winsound, "PlaySound", lambda path, flags: played.append(path))

    sounds.play_pressed_sound()
    sounds.play_success_sound()
    sounds.play_failure_sound()

    assert len(played) == 3
    assert all(os.path.exists(p) for p in played)
    # Each cue maps to a distinct file.
    assert len(set(played)) == 3


def test_play_is_silent_noop_before_init(monkeypatch) -> None:
    sounds._INITIALIZED = False
    sounds._PRESSED_WAV_PATH = None
    sounds._SUCCESS_WAV_PATH = None
    sounds._FAILURE_WAV_PATH = None

    called: list = []
    monkeypatch.setattr(sounds.winsound, "PlaySound", lambda *a: called.append(a))

    sounds.play_pressed_sound()
    sounds.play_success_sound()
    sounds.play_failure_sound()
    assert called == []
