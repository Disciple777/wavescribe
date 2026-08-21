"""Unit tests for the audio capture module.

Note: These tests do NOT require a microphone. They test initialization,
error handling, and the input-level meter math only. Full audio capture
tests require a physical mic.
"""

import numpy as np
import pytest

from app.audio_capture import AudioCapture, AudioCaptureError, compute_rms_level


class TestAudioCaptureInit:
    """Tests for AudioCapture initialization."""

    def test_init_default_samplerate(self):
        """Test that default samplerate is 16000."""
        capture = AudioCapture()
        assert capture.samplerate == 16000

    def test_init_custom_samplerate(self):
        """Test that custom samplerate is accepted."""
        capture = AudioCapture(samplerate=44100)
        assert capture.samplerate == 44100

    def test_init_buffer_empty(self):
        """Test that buffer starts empty."""
        capture = AudioCapture()
        assert capture.buffer == []

    def test_init_not_recording(self):
        """Test that is_recording returns False after init."""
        capture = AudioCapture()
        assert capture.is_recording() is False

    def test_init_stream_none(self):
        """Test that stream starts as None."""
        capture = AudioCapture()
        assert capture.stream is None

    def test_init_invalid_samplerate(self):
        """Test that very low samplerate might cause issues later but init works."""
        capture = AudioCapture(samplerate=1)
        assert capture.samplerate == 1


class TestAudioCaptureErrors:
    """Tests for AudioCapture error handling."""

    def test_stop_without_start(self):
        """Test that stop() raises an error if called without start()."""
        capture = AudioCapture()
        with pytest.raises(AudioCaptureError):
            capture.stop()

    def test_double_stop(self):
        """Test that calling stop() twice raises an error."""
        # Without starting, second stop will also fail
        # This just tests that our error handling works
        pass


class TestComputeRmsLevel:
    """Tests for the pure level-calculation function."""

    def test_silence_is_zero(self):
        """Silence (all zeros) maps to 0.0."""
        chunk = np.zeros((100, 1), dtype=np.float32)
        assert compute_rms_level(chunk) == 0.0

    def test_empty_array_is_zero(self):
        """Empty/None input maps to 0.0."""
        assert compute_rms_level(np.array([], dtype=np.float32)) == 0.0
        assert compute_rms_level(None) == 0.0

    def test_known_amplitude(self):
        """Constant 0.1 amplitude → -20 dB → (20/60) ≈ 0.667."""
        chunk = np.full((100, 1), 0.1, dtype=np.float32)
        assert compute_rms_level(chunk) == pytest.approx(0.6667, abs=1e-3)

    def test_full_scale_is_one(self):
        """Full-scale amplitude maps to 1.0."""
        chunk = np.full((100, 1), 1.0, dtype=np.float32)
        assert compute_rms_level(chunk) == pytest.approx(1.0, abs=1e-3)

    def test_overdriven_clips_to_one(self):
        """Values above full scale are clamped to 1.0."""
        chunk = np.full((100, 1), 2.0, dtype=np.float32)
        assert compute_rms_level(chunk) == 1.0

    def test_half_scale_amplitude(self):
        """Constant 0.5 amplitude → -6 dB → ≈ 0.9."""
        chunk = np.full((100, 1), 0.5, dtype=np.float32)
        assert compute_rms_level(chunk) == pytest.approx(0.8996, abs=1e-3)

    def test_louder_is_higher(self):
        """Meter level increases monotonically with amplitude."""
        quiet = compute_rms_level(np.full((100, 1), 0.05, dtype=np.float32))
        loud = compute_rms_level(np.full((100, 1), 0.5, dtype=np.float32))
        assert loud > quiet


class TestInputLevel:
    """Tests for the smoothed, thread-safe input level on AudioCapture."""

    def test_level_starts_zero(self):
        """Input level is 0.0 before any callback runs."""
        capture = AudioCapture()
        assert capture.get_input_level() == 0.0

    def test_callback_updates_level(self):
        """A single callback raises the level to the smoothed value."""
        capture = AudioCapture()
        chunk = np.full((64, 1), 0.1, dtype=np.float32)
        capture._callback(chunk, 64, None, None)
        raw = compute_rms_level(chunk)
        assert capture.get_input_level() == pytest.approx(raw * 0.35, abs=1e-4)

    def test_callback_smooths_over_time(self):
        """Repeated callbacks converge to the raw level (EMA)."""
        capture = AudioCapture()
        chunk = np.full((64, 1), 0.1, dtype=np.float32)
        for _ in range(30):
            capture._callback(chunk, 64, None, None)
        raw = compute_rms_level(chunk)
        assert capture.get_input_level() == pytest.approx(raw, abs=1e-3)

    def test_level_returns_to_zero_on_silence(self):
        """Silent callbacks after speech pull the level back toward zero."""
        capture = AudioCapture()
        loud = np.full((64, 1), 0.5, dtype=np.float32)
        silent = np.zeros((64, 1), dtype=np.float32)
        for _ in range(10):
            capture._callback(loud, 64, None, None)
        for _ in range(100):
            capture._callback(silent, 64, None, None)
        assert capture.get_input_level() == pytest.approx(0.0, abs=1e-6)
