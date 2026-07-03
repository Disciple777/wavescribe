"""Microphone audio capture using sounddevice.

Captures from the default microphone and returns WAV bytes.
Uses sounddevice.InputStream with a callback running on a separate thread.
"""

import io
from typing import Optional

import numpy as np
import sounddevice as sd
from scipy.io import wavfile


class AudioCaptureError(Exception):
    """Raised when audio capture fails."""
    pass


class AudioCapture:
    """Captures microphone input and returns WAV bytes."""

    def __init__(self, samplerate: int = 16000):
        self.samplerate = samplerate
        self.buffer: list[np.ndarray] = []
        self.stream: Optional[sd.InputStream] = None
        self._is_recording = False

    def start(self) -> None:
        """Start capturing audio from the default microphone.

        Raises AudioCaptureError if no microphone is available.
        """
        try:
            # Test that we have a valid input device
            devices = sd.query_devices()
            default_input = sd.default.device
            if default_input is None or (isinstance(default_input, tuple) and default_input[0] is None):
                raise AudioCaptureError("No default input device found. Please connect a microphone.")
        except AudioCaptureError:
            raise
        except Exception as e:
            raise AudioCaptureError(f"Failed to query audio devices: {e}")

        self.buffer = []
        self._is_recording = True

        try:
            self.stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=1,
                callback=self._callback,
                dtype=np.float32,
            )
            self.stream.start()
        except Exception as e:
            self._is_recording = False
            raise AudioCaptureError(f"Failed to start audio stream: {e}")

    def _callback(self, indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags) -> None:
        """Callback invoked by sounddevice for each audio block."""
        if status:
            print(f"Audio callback status: {status}")
        self.buffer.append(indata.copy())

    def stop(self) -> bytes:
        """Stop capturing and return the recorded audio as WAV bytes.

        Returns:
            WAV file contents as bytes.

        Raises:
            AudioCaptureError if no audio was captured.
        """
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        self._is_recording = False

        if not self.buffer:
            raise AudioCaptureError("No audio data captured. Nothing recorded.")

        # Concatenate all audio chunks into a single array
        audio = np.concatenate(self.buffer, axis=0)

        # Convert float32 [-1.0, 1.0] to int16 [-32768, 32767]
        audio_int16 = (audio * 32767).astype(np.int16)

        # Write to WAV in memory
        buf = io.BytesIO()
        wavfile.write(buf, self.samplerate, audio_int16)
        return buf.getvalue()

    def is_recording(self) -> bool:
        """Return whether audio is currently being captured."""
        return self._is_recording
