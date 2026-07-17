"""Transcription adapter for SubtitleForge.

Wraps the shared ``app.transcriber`` module to provide a unified
interface for timed transcription (returning segments with start/end
timestamps), usable from both Cloud (OpenAI Whisper API) and Local
(openai-whisper package) modes.
"""

from typing import Any, Dict, List, Optional

from app.transcriber import (
    TranscriptionError,
    is_whisper_available,
    load_whisper_model,
    transcribe_cloud_timed,
    transcribe_local_timed,
)


def transcribe_audio_timed(
    wav_bytes: bytes,
    api_key: str = "",
    mode: str = "cloud",
    model: str = "whisper-1",
    language: Optional[str] = None,
    prompt: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Transcribe WAV audio and return timed segments.

    Unified wrapper that dispatches to the appropriate backend
    (Cloud or Local) based on the mode parameter.

    Args:
        wav_bytes: WAV file contents as bytes (16-bit PCM, 16 kHz).
        api_key: OpenAI API key (required for Cloud mode).
        mode: ``"cloud"`` or ``"local"``.
        model: Model name (``"whisper-1"`` for cloud, ``"tiny"``/``"base"``/``"small"`` for local).
        language: Optional language code (``"en"``, ``"es"``, etc.).
        prompt: Optional prompt to guide the model (cloud only).

    Returns:
        List of segment dicts::

            [
                {"id": 0, "start": 0.0, "end": 2.5, "text": "Hello world", "avg_logprob": -0.1},
                {"id": 1, "start": 2.5, "end": 5.0, "text": "This is a test", "avg_logprob": -0.2},
            ]

    Raises:
        TranscriptionError: If transcription fails.
    """
    if mode == "cloud":
        return transcribe_cloud_timed(
            wav_bytes=wav_bytes,
            api_key=api_key,
            model=model,
            language=language,
            prompt=prompt,
        )
    elif mode == "local":
        return transcribe_local_timed(
            wav_bytes=wav_bytes,
            model_size=model,
            language=language or "en",
        )
    else:
        raise TranscriptionError(f"Unknown transcription mode: {mode}")


__all__ = [
    "transcribe_audio_timed",
    "transcribe_cloud_timed",
    "transcribe_local_timed",
    "is_whisper_available",
    "load_whisper_model",
    "TranscriptionError",
]
