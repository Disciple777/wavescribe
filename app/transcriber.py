"""Transcription engine for WaveScribe.

Supports two modes:
- Cloud: sends WAV audio to the OpenAI Whisper API.
- Local: runs Whisper locally via the openai-whisper package.
"""

import io
import os
import shutil
import subprocess
import sys
import numpy as np
from typing import Any, Dict, List, Optional

from openai import OpenAI, APIError, AuthenticationError, APITimeoutError, RateLimitError


class TranscriptionError(Exception):
    """Raised when transcription fails."""
    pass


# ── Timed transcription (shared with SubtitleForge) ──


def transcribe_cloud_timed(
    wav_bytes: bytes,
    api_key: str,
    model: str = "whisper-1",
    language: str | None = None,
    prompt: str | None = None,
) -> List[Dict[str, Any]]:
    """Transcribe WAV audio and return timed segments with start/end times.

    Uses ``response_format="verbose_json"`` so the API returns per-word
    or per-phrase segments ideal for SRT/VTT subtitle generation.

    Args:
        wav_bytes: WAV file contents as bytes.
        api_key: OpenAI API key.
        model: Whisper model (default: whisper-1).
        language: Optional language code (e.g. "en", "es").
        prompt: Optional prompt to guide the model.

    Returns:
        List of segment dicts, each with keys:
          - "id" (int): segment index
          - "start" (float): start time in seconds
          - "end" (float): end time in seconds
          - "text" (str): transcribed text for this segment
          - "avg_logprob" (float): confidence

    Raises:
        TranscriptionError: If the API call fails.
    """
    if not api_key:
        raise TranscriptionError(
            "No API key configured. Please enter your OpenAI API key."
        )

    if not wav_bytes or len(wav_bytes) < 100:
        raise TranscriptionError("No audio data to transcribe.")

    try:
        client = OpenAI(api_key=api_key)

        audio_file = io.BytesIO(wav_bytes)
        audio_file.name = "audio.wav"

        kwargs: Dict[str, Any] = dict(
            model=model,
            file=audio_file,
            response_format="verbose_json",
        )
        if language:
            kwargs["language"] = language
        if prompt:
            kwargs["prompt"] = prompt

        transcript = client.audio.transcriptions.create(**kwargs)

        segments: List[Dict[str, Any]] = []
        for seg in transcript.segments:
            segments.append({
                "id": seg.id,
                "start": seg.start,
                "end": seg.end,
                "text": seg.text.strip(),
                "avg_logprob": getattr(seg, "avg_logprob", None),
            })

        if not segments:
            raise TranscriptionError(
                "Transcription returned no timed segments. Try again."
            )

        return segments

    except AuthenticationError:
        raise TranscriptionError(
            "Invalid API key. Please check your OpenAI API key."
        )
    except RateLimitError:
        raise TranscriptionError(
            "Rate limit exceeded. Please wait and try again."
        )
    except APITimeoutError:
        raise TranscriptionError(
            "Request timed out. Check your internet connection."
        )
    except APIError as e:
        raise TranscriptionError(f"OpenAI API error: {e}")
    except TranscriptionError:
        raise
    except Exception as e:
        raise TranscriptionError(f"Unexpected transcription error: {e}")


def transcribe_local_timed(
    wav_bytes: bytes,
    model_size: str = "base",
    language: str | None = "en",
) -> List[Dict[str, Any]]:
    """Transcribe WAV audio using the local Whisper model and return timed segments.

    Args:
        wav_bytes: WAV file contents as bytes (16-bit PCM).
        model_size: Whisper model size (tiny / base / small).
        language: Language code or None for auto-detect.

    Returns:
        List of segment dicts, each with keys:
          - "id" (int): segment index
          - "start" (float): start time in seconds
          - "end" (float): end time in seconds
          - "text" (str): transcribed text for this segment
          - "avg_logprob" (float): confidence

    Raises:
        TranscriptionError: If model is not loaded or transcription fails.
    """
    global _whisper_model

    if _whisper_model is None:
        # Attempt to load on-demand
        if not is_whisper_available():
            raise TranscriptionError(
                "Whisper package is not installed. Please install it in Settings."
            )
        load_whisper_model(model_size)

    if not wav_bytes or len(wav_bytes) < 100:
        raise TranscriptionError("No audio data to transcribe.")

    try:
        import whisper  # noqa: F811

        audio_array = (
            np.frombuffer(wav_bytes, dtype=np.int16).astype(np.float32)
            / 32768.0
        )

        kwargs: Dict[str, Any] = dict(
            task="transcribe",
            verbose=False,  # don't print progress to console
        )
        if language:
            kwargs["language"] = language

        result = _whisper_model.transcribe(audio_array, **kwargs)

        segments: List[Dict[str, Any]] = []
        for seg in result.get("segments", []):
            segments.append({
                "id": seg["id"],
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"].strip(),
                "avg_logprob": seg.get("avg_logprob", None),
            })

        if not segments:
            raise TranscriptionError(
                "Transcription returned no timed segments."
            )

        return segments

    except TranscriptionError:
        raise
    except Exception as e:
        raise TranscriptionError(f"Local transcription error: {e}")


# ── Bundled Model Setup ──

def _get_whisper_cache_dir() -> str:
    """Get the Whisper model cache directory (same folder used by whisper.load_model)."""
    return os.path.join(os.path.expanduser("~"), ".cache", "whisper")


def setup_bundled_model() -> bool:
    """Copy the bundled Whisper model file(s) to the user's whisper cache.

    When running from a PyInstaller bundle, the model .pt file is stored
    in the ``whisper_models/`` directory inside the bundle.  On first run
    (or if the user cache is empty), we copy it there so
    ``whisper.load_model()`` finds it without downloading.

    Returns:
        True if the model is now available (either already cached or
        successfully copied), False otherwise.
    """
    cache_dir = _get_whisper_cache_dir()
    target_path = os.path.join(cache_dir, "base.pt")

    # Already cached?  Done.
    if os.path.exists(target_path):
        return True

    # Look for bundled model file (PyInstaller puts it in _internal/whisper_models/)
    # In dev mode, there's no bundling — model may need downloading later
    bundle_sources = [
        # PyInstaller bundle path
        os.path.join(os.path.dirname(sys.executable), "_internal", "whisper_models", "base.pt"),
        # Alternative: alongside the exe
        os.path.join(os.path.dirname(sys.executable), "whisper_models", "base.pt"),
    ]

    # Also check relative to the running script (for dev/testing with --add-data)
    try:
        bundle_sources.append(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "whisper_models", "base.pt")
        )
    except NameError:
        pass

    source_path = None
    for sp in bundle_sources:
        if os.path.exists(sp):
            source_path = sp
            break

    if source_path is None:
        return False  # No bundled model found

    try:
        os.makedirs(cache_dir, exist_ok=True)
        shutil.copy2(source_path, target_path)
        return True
    except OSError:
        return False


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
