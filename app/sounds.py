"""Short synthesized sound effects for WaveScribe.

Generates two simple WAV files at startup — a rising tone for
start-recording and a descending tone for stop-recording — and
plays them asynchronously via ``winsound`` so they never block
the UI.

All audio is synthesised in-process with no external dependencies
beyond Python's standard library.
"""

import array
import math
import os
import struct
import tempfile
import winsound

# Cached paths to generated WAV files
_START_WAV_PATH: str | None = None
_STOP_WAV_PATH: str | None = None
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


def init_sounds() -> None:
    """Generate the two WAV files in the system temp directory.

    Safe to call multiple times — only generates once.
    """
    global _START_WAV_PATH, _STOP_WAV_PATH, _INITIALIZED  # noqa: PLW0603
    if _INITIALIZED:
        return

    temp = tempfile.gettempdir()
    _START_WAV_PATH = os.path.join(temp, "wavescribe_start.wav")
    _STOP_WAV_PATH = os.path.join(temp, "wavescribe_stop.wav")

    # Start recording — rising chirp (600 → 1200 Hz, 150 ms)
    start_wav = _generate_wav(
        frequencies=[600.0, 1200.0],
        amplitudes=[0.25, 0.25],
        duration_s=0.15,
    )
    with open(_START_WAV_PATH, "wb") as f:
        f.write(start_wav)

    # Stop recording — gentle descending tone (1000 → 500 Hz, 120 ms, fade out)
    stop_wav = _generate_wav(
        frequencies=[1000.0, 500.0],
        amplitudes=[0.25, 0.08],
        duration_s=0.12,
    )
    with open(_STOP_WAV_PATH, "wb") as f:
        f.write(stop_wav)

    _INITIALIZED = True


def play_start_sound() -> None:
    """Play the start-recording sound effect asynchronously.

    If ``init_sounds()`` hasn't been called yet, this is a no-op.
    """
    if _START_WAV_PATH and os.path.exists(_START_WAV_PATH):
        winsound.PlaySound(
            _START_WAV_PATH,
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )


def play_stop_sound() -> None:
    """Play the stop-recording sound effect asynchronously.

    If ``init_sounds()`` hasn't been called yet, this is a no-op.
    """
    if _STOP_WAV_PATH and os.path.exists(_STOP_WAV_PATH):
        winsound.PlaySound(
            _STOP_WAV_PATH,
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
