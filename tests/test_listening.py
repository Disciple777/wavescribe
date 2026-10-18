"""Tests for the hands-free listening segmenter and hallucination guard."""

import numpy as np

from app.listening import SpeechSegmenter, is_probable_hallucination

SR = 16000


def _tone(seconds: float, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _noise(seconds: float, amp: float = 0.002) -> np.ndarray:
    rng = np.random.default_rng(0)
    return (amp * rng.standard_normal(int(SR * seconds))).astype(np.float32)


def _feed_in_blocks(seg: SpeechSegmenter, audio: np.ndarray, block: int = 480) -> list:
    out = []
    for i in range(0, audio.size, block):
        out.extend(seg.feed(audio[i:i + block]))
    return out


def test_two_utterances_split_on_pause() -> None:
    seg = SpeechSegmenter(SR, pause_s=0.8)
    audio = np.concatenate([_noise(1.0), _tone(1.5), _noise(1.5), _tone(1.0), _noise(1.5)])
    segments = _feed_in_blocks(seg, audio)
    assert len(segments) == 2
    # Each segment ≈ speech + preroll + short tail, never the long silences.
    assert 1.4 < segments[0].size / SR < 2.3
    assert 0.9 < segments[1].size / SR < 1.8


def test_short_pause_does_not_split() -> None:
    seg = SpeechSegmenter(SR, pause_s=1.2)
    audio = np.concatenate([_noise(1.0), _tone(1.0), _noise(0.5), _tone(1.0), _noise(2.0)])
    segments = _feed_in_blocks(seg, audio)
    assert len(segments) == 1
    assert segments[0].size / SR > 2.3


def test_silence_and_clicks_produce_nothing() -> None:
    seg = SpeechSegmenter(SR, pause_s=0.8)
    click = _tone(0.06)  # 60 ms blip — below the speech-start threshold
    audio = np.concatenate([_noise(1.0), click, _noise(2.0)])
    assert _feed_in_blocks(seg, audio) == []


def test_flush_returns_in_progress_utterance() -> None:
    seg = SpeechSegmenter(SR, pause_s=1.0)
    _feed_in_blocks(seg, np.concatenate([_noise(0.5), _tone(1.0)]))
    assert seg.in_speech
    tail = seg.flush()
    assert tail is not None and tail.size / SR > 0.9
    assert not seg.in_speech


def test_long_monologue_is_cut() -> None:
    seg = SpeechSegmenter(SR, pause_s=1.0, max_segment_s=3.0)
    # 7 s of speech with a short 0.4 s breath every ~1 s
    parts = [_noise(0.5)]
    for _ in range(7):
        parts += [_tone(1.0), _noise(0.4)]
    parts.append(_noise(2.0))
    segments = _feed_in_blocks(seg, np.concatenate(parts))
    assert len(segments) >= 2
    assert all(s.size / SR <= 3.0 * 1.3 + 0.5 for s in segments)


def test_steady_noise_is_not_speech() -> None:
    """A fan / game soundtrack at a constant level must not open a segment."""
    seg = SpeechSegmenter(SR, pause_s=1.0)
    hum = _tone(20.0, amp=0.02)  # loud-ish but constant
    segments = _feed_in_blocks(seg, np.concatenate([_noise(0.2), hum]))
    assert all(s.size / SR < 8 for s in segments)
    assert not seg.in_speech


def test_speech_over_background_noise() -> None:
    seg = SpeechSegmenter(SR, pause_s=0.8)
    bed = _noise(9.0, amp=0.01)
    speech = np.zeros_like(bed)
    speech[int(SR * 3):int(SR * 4.5)] = _tone(1.5, 0.3)
    segments = _feed_in_blocks(seg, bed + speech)
    assert len(segments) == 1 and 1.4 < segments[0].size / SR < 2.4


def test_hallucination_guard() -> None:
    assert is_probable_hallucination("Thank you.", 1.0)
    assert is_probable_hallucination("  ", 1.0)
    assert is_probable_hallucination("Thanks for watching!", 2.0)
    assert not is_probable_hallucination("Thank you.", 6.0)
    assert not is_probable_hallucination("The jump button feels laggy", 2.0)
