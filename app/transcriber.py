"""Transcription engine for WaveScribe.

Sends WAV audio bytes to OpenAI Whisper API and returns transcribed text.
Supports cloud (OpenAI Whisper API) mode.
"""

import io
from typing import Optional

from openai import OpenAI, APIError, AuthenticationError, APITimeoutError, RateLimitError


class TranscriptionError(Exception):
    """Raised when transcription fails."""
    pass


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


def transcribe_local(wav_bytes: bytes, model_size: str = "base") -> str:
    """Placeholder for local transcription mode.

    Local Whisper model support will be added in a future release.
    """
    return "[Local transcription is not yet available. Please use Cloud mode with an OpenAI API key.]"
