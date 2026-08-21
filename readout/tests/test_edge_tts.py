"""Tests for readout/core/edge_tts.py (no network access, no real audio).

The ``_MciPlayer`` tests drive a fake ``winmm`` so no MP3 is opened and no
sound is produced.
"""

import ctypes
import time

from readout.core.edge_tts import (
    _fallback_voices,
    _MciPlayer,
    edge_rate,
    edge_volume,
)


def test_edge_rate_format():
    assert edge_rate(100) == "+0%"
    assert edge_rate(50) == "-50%"
    assert edge_rate(120) == "+20%"
    assert edge_rate(150) == "+50%"


def test_edge_volume_format():
    assert edge_volume(100) == "+0%"
    assert edge_volume(80) == "-20%"
    assert edge_volume(0) == "-100%"
    assert edge_volume(120) == "+20%"  # clamped nowhere — caller passes 0..100


def test_fallback_voice_list_is_reasonable():
    voices = _fallback_voices()
    assert len(voices) > 10
    assert any(v.name == "en-US-AriaNeural" for v in voices)
    assert all(v.language for v in voices)  # "en-US", "es-MX", ...


# ── _MciPlayer (fake winmm, no sound) ──

class FakeWinmm:
    """A stand-in for ctypes.windll.winmm that records MCI commands."""

    def __init__(self) -> None:
        self.mode = "stopped"
        self.commands = []

    def mciSendStringW(self, cmd, buf=None, buflen=0, hwnd=0):
        self.commands.append(cmd)
        if cmd.startswith("play "):
            self.mode = "playing"
        elif cmd.startswith("stop ") or cmd.startswith("close "):
            self.mode = "stopped"
        elif cmd.startswith("status "):
            if buf is not None:
                buf.value = self.mode
        return 0


def test_mci_stop_interrupts_playback(monkeypatch):
    """stop() must halt playback — the poll loop checks a stop Event."""
    fake = FakeWinmm()
    monkeypatch.setattr(ctypes.windll, "winmm", fake)

    player = _MciPlayer()
    done = []
    player.play(r"C:\fake\readout.mp3", on_done=lambda: done.append(True))

    time.sleep(0.4)
    assert player.is_playing(), "playback thread should be running"

    player.stop()
    time.sleep(0.4)
    assert not player.is_playing(), "thread should exit after stop()"
    assert done == [], "on_done must not fire when playback is cancelled"

    # The player must have issued a stop/close for the alias.
    cmds = " | ".join(fake.commands)
    assert "stop readout_audio" in cmds
    assert "close readout_audio" in cmds


def test_mci_natural_completion_calls_on_done(monkeypatch):
    """When the track ends by itself, on_done fires and playback stops."""
    class AutoFinishWinmm(FakeWinmm):
        def mciSendStringW(self, cmd, buf=None, buflen=0, hwnd=0):
            if cmd.startswith("status ") and self.mode == "playing":
                self._polls = getattr(self, "_polls", 0) + 1
                if self._polls >= 3:
                    self.mode = "stopped"  # track ends
            super().mciSendStringW(cmd, buf, buflen, hwnd)

    fake = AutoFinishWinmm()
    monkeypatch.setattr(ctypes.windll, "winmm", fake)

    player = _MciPlayer()
    done = []
    player.play(r"C:\fake\readout.mp3", on_done=lambda: done.append(True))

    time.sleep(1.0)
    assert done == [True], "on_done should fire once on natural completion"
    assert not player.is_playing()


def test_mci_stop_before_start_leaves_no_playback(monkeypatch):
    """stop() before the thread even opens the device is a safe no-op."""
    fake = FakeWinmm()
    monkeypatch.setattr(ctypes.windll, "winmm", fake)

    player = _MciPlayer()
    player.stop()  # nothing playing yet
    assert not player.is_playing()


def test_mci_replay_does_not_kill_new_playback(monkeypatch):
    """A stale playback thread must close the device before a new play opens it,
    and must never fire the newer playback's on_done."""
    fake = FakeWinmm()
    monkeypatch.setattr(ctypes.windll, "winmm", fake)

    player = _MciPlayer()
    done_a, done_b = [], []
    player.play(r"C:\fake\a.mp3", on_done=lambda: done_a.append(True))
    time.sleep(0.2)
    player.play(r"C:\fake\b.mp3", on_done=lambda: done_b.append(True))
    time.sleep(0.5)

    assert done_a == [], "cancelled playback must not fire its on_done"
    assert done_b == [], "new playback not finished yet"
    assert player.is_playing(), "new playback should still be running"

    # The old device was closed before the new file was opened (device lock).
    opens = [i for i, c in enumerate(fake.commands) if c.startswith("open ")]
    closes = [i for i, c in enumerate(fake.commands) if c.startswith("close ")]
    assert closes and opens and closes[0] < opens[-1]
