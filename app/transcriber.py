"""Transcription engine for WaveScribe.

Supports two modes:
- Cloud: sends WAV audio to the OpenAI Whisper API.
- Local: runs Whisper locally via the openai-whisper package.
"""

import io
import subprocess
import sys
import numpy as np
from typing import Optional

from openai import OpenAI, APIError, AuthenticationError, APITimeoutError, RateLimitError


class TranscriptionError(Exception):
    """Raised when transcription fails."""
    pass


# ── Cloud mode ──

def transcribe_cloud(wav_bytes: bytes, api_key: str, model: str = "whisper-1") -> str:
    """Send WAV audio to OpenAI Whisper API and return transcribed text.

    Args:
        wav_bytes: WAV file contents as bytes.
        api_key: OpenAI API key.
        model: Whisper model to use (default: whisper-1).

    Returns:
        Transcribed text string.

    Raises:
        TranscriptionError: If the API call fails for any reason.
    """
    if not api_key:
        raise TranscriptionError(
            "No API key configured. Please enter your OpenAI API key in Settings."
        )

    if not wav_bytes or len(wav_bytes) < 100:
        raise TranscriptionError(
            "No audio data to transcribe. Please record something first."
        )

    try:
        client = OpenAI(api_key=api_key)

        # Create a BytesIO stream with a .wav name so the API knows the format
        audio_file = io.BytesIO(wav_bytes)
        audio_file.name = "audio.wav"

        transcript = client.audio.transcriptions.create(
            model=model,
            file=audio_file,
            response_format="text",
        )

        if not transcript or not transcript.strip():
            raise TranscriptionError(
                "Transcription returned empty result. Please try again."
            )

        return transcript.strip()

    except AuthenticationError:
        raise TranscriptionError(
            "Invalid API key. Please check your OpenAI API key in Settings."
        )
    except RateLimitError:
        raise TranscriptionError(
            "Rate limit exceeded. Please wait a moment and try again."
        )
    except APITimeoutError:
        raise TranscriptionError(
            "Request timed out. Please check your internet connection and try again."
        )
    except APIError as e:
        raise TranscriptionError(
            f"OpenAI API error: {e}"
        )
    except TranscriptionError:
        raise
    except Exception as e:
        raise TranscriptionError(
            f"Unexpected transcription error: {e}"
        )


# ── Local mode helpers ──

_whisper_model = None
"""Cached Whisper model instance (lazy-loaded, shared across calls)."""


def is_whisper_available() -> bool:
    """Check whether the openai-whisper package is importable."""
    try:
        import whisper  # noqa: F401
        return True
    except ImportError:
        return False


def install_whisper_package() -> None:
    """Install the openai-whisper package via pip.

    Runs ``pip install openai-whisper`` using the currently running
    Python interpreter's pip.  Raises TranscriptionError on failure.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "openai-whisper"],
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout for downloading + compiling
        )
        if result.returncode != 0:
            raise TranscriptionError(
                f"Failed to install Whisper.\n{result.stderr.strip()}"
            )
    except subprocess.TimeoutExpired:
        raise TranscriptionError(
            "Whisper installation timed out. Check your internet connection and try again."
        )
    except OSError as e:
        raise TranscriptionError(
            f"Could not run pip: {e}"
        )


def load_whisper_model(model_size: str = "base") -> None:
    """Download (if needed) and load the requested Whisper model into memory.

    The model is cached globally so subsequent calls are instant.
    """
    global _whisper_model
    import whisper
    _whisper_model = whisper.load_model(model_size)


def transcribe_local(wav_bytes: bytes, model_size: str = "base") -> str:
    """Transcribe WAV audio using a local Whisper model.

    Args:
        wav_bytes: WAV file contents as bytes.
        model_size: Whisper model size to use (tiny / base / small).

    Returns:
        Transcribed text string.

    Raises:
        TranscriptionError: If the model isn't loaded or transcription fails.
    """
    global _whisper_model

    if _whisper_model is None:
        raise TranscriptionError(
            "Whisper model is not loaded. Please install and load the model in Settings first."
        )

    if not wav_bytes or len(wav_bytes) < 100:
        raise TranscriptionError(
            "No audio data to transcribe. Please record something first."
        )

    try:
        import whisper
        # Convert WAV bytes → float32 numpy array (Whisper expects -1..1 range)
        audio_array = (
            np.frombuffer(wav_bytes, dtype=np.int16).astype(np.float32)
            / 32768.0
        )

        result = _whisper_model.transcribe(
            audio_array,
            language="en",       # faster than auto-detect
            task="transcribe",
        )

        text = result.get("text", "").strip()
        if not text:
            raise TranscriptionError(
                "Transcription returned empty result. Please try again."
            )
        return text

    except TranscriptionError:
        raise
    except Exception as e:
        raise TranscriptionError(f"Local transcription error: {e}")
