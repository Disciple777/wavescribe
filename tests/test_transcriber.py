"""Tests for the transcription engine's punctuation-prompt behavior.

Verifies that when "punctuation via speech" is OFF, the GUI hands the
punctuation job to the model by passing a punctuation-guiding prompt
(cloud "prompt" / local "initial_prompt"), and that the prompt is
omitted when not requested.
"""

from app import transcriber
from app.transcriber import (
    PUNCTUATION_PROMPT,
    transcribe_cloud,
    transcribe_local,
)


class _FakeTranscript:
    def strip(self):
        return "Hello, world!"


def _wav_bytes() -> bytes:
    """Tiny WAV-ish payload (only the length check matters in these tests)."""
    return b"RIFF" + b"\x00" * 100


def _fake_client(captured: dict):
    """Build a fake OpenAI client that records the create() kwargs.

    Mirrors the real client shape: ``client.audio.transcriptions.create(...)``.
    """

    class _FakeTranscriptions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeTranscript()

    class _FakeAudio:
        def __init__(self):
            self.transcriptions = _FakeTranscriptions()

    class _FakeClient:
        def __init__(self):
            self.audio = _FakeAudio()

    return _FakeClient()


def test_transcribe_cloud_forwards_prompt(monkeypatch):
    captured = {}
    monkeypatch.setattr(transcriber, "OpenAI", lambda api_key: _fake_client(captured))

    result = transcribe_cloud(_wav_bytes(), "sk-test", prompt=PUNCTUATION_PROMPT)

    assert captured["prompt"] == PUNCTUATION_PROMPT
    assert result == "Hello, world!"


def test_transcribe_cloud_without_prompt(monkeypatch):
    captured = {}
    monkeypatch.setattr(transcriber, "OpenAI", lambda api_key: _fake_client(captured))

    transcribe_cloud(_wav_bytes(), "sk-test")

    assert "prompt" not in captured


def test_transcribe_local_forwards_initial_prompt(monkeypatch):
    captured = {}

    class _FakeModel:
        def transcribe(self, audio, **kwargs):
            captured.update(kwargs)
            return {"text": "Hello, world!"}

    monkeypatch.setattr(transcriber, "_whisper_model", _FakeModel())

    result = transcribe_local(
        _wav_bytes(),
        model_size="base",
        language="en",
        initial_prompt=PUNCTUATION_PROMPT,
    )

    assert captured["initial_prompt"] == PUNCTUATION_PROMPT
    assert captured["language"] == "en"
    assert result == "Hello, world!"


def test_transcribe_local_without_initial_prompt(monkeypatch):
    captured = {}

    class _FakeModel:
        def transcribe(self, audio, **kwargs):
            captured.update(kwargs)
            return {"text": "Hello, world!"}

    monkeypatch.setattr(transcriber, "_whisper_model", _FakeModel())

    transcribe_local(_wav_bytes())

    assert "initial_prompt" not in captured
