"""Microphone audio capture using sounddevice.

Captures from the default microphone and returns WAV bytes.
Uses sounddevice.InputStream with a callback running on a separate thread.
"""

import io
import threading
from typing import Optional

import numpy as np
import sounddevice as sd
from scipy.io import wavfile


class AudioCaptureError(Exception):
    """Raised when audio capture fails."""
    pass


def compute_rms_level(indata: Optional[np.ndarray], floor_db: float = -60.0) -> float:
    """Map a mono float32 audio chunk to a 0.0–1.0 input level.

    Uses the RMS amplitude mapped through a dB scale so the meter feels
    natural: ``floor_db`` (silence) maps to 0.0 and 0 dB (full scale)
    maps to 1.0. The result is clamped to [0.0, 1.0].

    Args:
        indata: Audio chunk from the capture callback (shape ``(frames, 1)``).
        floor_db: dB level treated as silence.

    Returns:
        A 0.0–1.0 level for the live input meter.
    """
    if indata is None or indata.size == 0:
        return 0.0

    rms = float(np.sqrt(np.mean(np.square(indata))))
    if rms <= 1e-9:
        return 0.0

    db = 20.0 * np.log10(rms)
    level = (db - floor_db) / (-floor_db)
    return float(np.clip(level, 0.0, 1.0))


def list_input_devices() -> list:
    """Return a list of available audio input devices.

    Each entry is a dict with keys: ``id`` (int or "default"),
    ``name`` (human-readable), and ``channels`` (int).
    """
    devices = sd.query_devices()
    result = [{"id": "default", "name": "Default Microphone", "channels": 0}]
    try:
        default_input = sd.default.device
        default_idx = default_input[0] if isinstance(default_input, tuple) else default_input
    except Exception:
        default_idx = None
    for i, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) > 0:
            result.append({
                "id": i,
                "name": dev.get("name", f"Device {i}"),
                "channels": dev.get("max_input_channels", 0),
            })
    return result


def get_device_display_name(device_id) -> str:
    """Return a short display name for a device ID."""
    if device_id == "default" or device_id is None:
        return "Default"
    try:
        dev = sd.query_devices(int(device_id))
        name = dev.get("name", f"Device {device_id}")
        # Truncate long names
        return name if len(name) <= 30 else name[:27] + "..."
    except Exception:
        return str(device_id)


class AudioCapture:
    """Captures microphone input and returns WAV bytes."""

    def __init__(self, samplerate: int = 16000):
        self.samplerate = samplerate
        self.buffer: list[np.ndarray] = []
        self.stream: Optional[sd.InputStream] = None
        self._is_recording = False
        self._input_device = None  # device index or None for default
        # Smoothed input level (0.0–1.0) for the live meter, updated in the
        # audio callback thread and read from the GUI thread.
        self._level = 0.0
        self._level_lock = threading.Lock()

    def set_input_device(self, device_id) -> None:
        """Set the input device for the next recording.

        Args:
            device_id: "default" or an integer device index.
        """
        if device_id == "default" or device_id is None:
            self._input_device = None
        else:
            self._input_device = int(device_id)

    def start(self) -> None:
        """Start capturing audio from the selected microphone.

        Raises AudioCaptureError if no microphone is available.
        """
        try:
            if self._input_device is not None:
                # Verify the specific device exists
                sd.check_inputdevice(self._input_device)
            else:
                default_input = sd.default.device
                if default_input is None or (isinstance(default_input, tuple) and default_input[0] is None):
                    raise AudioCaptureError("No default input device found. Please connect a microphone.")
        except AudioCaptureError:
            raise
        except Exception as e:
            raise AudioCaptureError(f"Failed to query audio devices: {e}")

        self.buffer = []
        self._is_recording = True
        with self._level_lock:
            self._level = 0.0

        try:
            kwargs = dict(
                samplerate=self.samplerate,
                channels=1,
                callback=self._callback,
                dtype=np.float32,
            )
            if self._input_device is not None:
                kwargs["device"] = self._input_device
            self.stream = sd.InputStream(**kwargs)
            self.stream.start()
        except Exception as e:
            self._is_recording = False
            raise AudioCaptureError(f"Failed to start audio stream: {e}")

    def _callback(self, indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags) -> None:
        """Callback invoked by sounddevice for each audio block."""
        if status:
            print(f"Audio callback status: {status}")
        self.buffer.append(indata.copy())

        # Update the smoothed input level for the live meter.
        # Fast-enough attack while staying stable on the GUI side.
        raw_level = compute_rms_level(indata)
        with self._level_lock:
            self._level = self._level * 0.65 + raw_level * 0.35

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
        with self._level_lock:
            self._level = 0.0

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

    def get_input_level(self) -> float:
        """Return the current smoothed input level (0.0–1.0) for the live meter.

        Thread-safe: written in the audio callback thread, read from the GUI.
        """
        with self._level_lock:
            return self._level
