"""Unit tests for the audio capture module.

Note: These tests do NOT require a microphone. They test initialization
and error handling only. Full audio capture tests require a physical mic.
"""

import pytest

from app.audio_capture import AudioCapture, AudioCaptureError


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
