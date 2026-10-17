"""Shared TTS exception types for WaveScribe."""


class TTSError(Exception):
    """Raised when a speech engine cannot fulfil a request (bad voice,
    missing optional package, network failure, etc.).
    """
