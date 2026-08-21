"""Short synthesized sound effects for Readout.

Three cues tell the user what happened when the hotkey fires:

  - pressed  — short quiet blip: the hotkey was heard, a read is being attempted
  - success  — rising tone: text was grabbed and speech is starting
  - failure  — low buzz: nothing was selected, or the read failed

The WAV files are synthesized in-process using only Python's standard
library (the same approach as WaveScribe's app/sounds.py) and played via
``winsound`` asynchronously so they never block the UI or the TTS thread.
"""

import array
import math
import os
import struct
import tempfile
import winsound

# Cached paths to the generated WAV files
_PRESSED_WAV_PATH: str | None = None
_SUCCESS_WAV_PATH: str | None = None
_FAILURE_WAV_PATH: str | None = None
_INITIALIZED = False


def _generate_wav(
    frequencies: list[float],
    amplitudes: list[float],
    duration_s: float,
    sample_rate: int = 22050,
) -> bytes:
    """Build a mono 16-bit WAV from a linear sweep of frequencies/amplitudes.

    Args:
        frequencies: Frequency at each segment boundary (Hz).
        amplitudes:  Amplitude (0-1) at each segment boundary.
        duration_s:  Total duration in seconds.
        sample_rate: Samples per second.

    Returns:
        Complete WAV file bytes (RIFF header + PCM data).
    """
    n_samples = int(sample_rate * duration_s)
    segments = len(frequencies)

    samples: list[int] = []
    for i in range(n_samples):
        t = i / sample_rate
        frac = i / n_samples
        seg_idx = min(int(frac * segments), segments - 1)
        freq = frequencies[seg_idx]
        amp = amplitudes[seg_idx]
        val = int(amp * 32767 * math.sin(2.0 * math.pi * freq * t))
        samples.append(val)

    n_channels = 1
    sample_width = 2  # 16-bit
    data_size = n_samples * sample_width

    header = struct.pack(
        b"<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,  # chunk size
        1,  # PCM
        n_channels,
        sample_rate,
        sample_rate * n_channels * sample_width,  # byte rate
        n_channels * sample_width,  # block align
        sample_width * 8,  # bits per sample
        b"data",
        data_size,
    )

    return header + array.array("h", samples).tobytes()


def _write_wav(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def init_sounds() -> None:
    """Generate the three cue WAV files in the system temp directory.

    Safe to call multiple times — only generates once.  The cues are
    purely cosmetic: if the temp dir is unwritable or a file is locked
    (e.g. another instance running), the app keeps working silently
    rather than crashing at startup.
    """
    global _PRESSED_WAV_PATH, _SUCCESS_WAV_PATH, _FAILURE_WAV_PATH, _INITIALIZED  # noqa: PLW0603
    if _INITIALIZED:
        return

    try:
        temp = tempfile.gettempdir()
        pressed_path = os.path.join(temp, "readout_pressed.wav")
        success_path = os.path.join(temp, "readout_success.wav")
        failure_path = os.path.join(temp, "readout_failure.wav")

        # Hotkey heard — short, quiet blip so frequent presses aren't annoying.
        _write_wav(pressed_path, _generate_wav([660.0, 660.0], [0.18, 0.18], 0.12))
        # Text grabbed, speech starting — pleasant rising tone (C5 → G5).
        _write_wav(success_path, _generate_wav([523.0, 784.0], [0.22, 0.22], 0.20))
        # Nothing to read / failed — low descending buzz with a fade.
        _write_wav(failure_path, _generate_wav([260.0, 110.0], [0.26, 0.14], 0.30))

        _PRESSED_WAV_PATH = pressed_path
        _SUCCESS_WAV_PATH = success_path
        _FAILURE_WAV_PATH = failure_path
    except OSError:
        # A beep must never take the app down — leave paths None so the
        # play_* helpers stay silent, and don't retry every launch.
        _PRESSED_WAV_PATH = _SUCCESS_WAV_PATH = _FAILURE_WAV_PATH = None
    _INITIALIZED = True


def _play(path: str | None) -> None:
    """Play a generated WAV asynchronously (no-op if not generated yet)."""
    if path and os.path.exists(path):
        winsound.PlaySound(
            path,
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )


def play_pressed_sound() -> None:
    """Play the 'hotkey heard, attempting a read' cue."""
    _play(_PRESSED_WAV_PATH)


def play_success_sound() -> None:
    """Play the 'text grabbed, reading started' cue."""
    _play(_SUCCESS_WAV_PATH)


def play_failure_sound() -> None:
    """Play the 'nothing was read' cue (no selection / error)."""
    _play(_FAILURE_WAV_PATH)
