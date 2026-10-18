"""Tests for the UI-agnostic WaveScribeCore (no display or microphone needed)."""

import pytest

from app import core as core_mod
from app.config import DEFAULT_CONFIG
from app.core import WaveScribeCore
from app.history import HistoryManager
from app.sessions import SessionStore
from app.state import AppState, AppStatus, create_event_queue


class FakeHotkeys:
    def __init__(self):
        self.registered = {}

    def set_hotkeys(self, start, stop):
        self.registered.update(start=start, stop=stop)

    def set_session_hotkey(self, combo):
        self.registered["session"] = combo

    def set_tts_hotkeys(self, read, swap):
        self.registered.update(tts_read=read, tts_swap=swap)


class FakeTTS:
    def __init__(self):
        self.backend = "system"

    def set_backend(self, name):
        self.backend = name

    def get_backend(self):
        return self.backend

    def engine(self, name=None):
        return type("E", (), {"display_name": name or self.backend})()

    def is_speaking(self):
        return False

    def stop_all(self):
        pass


class FakeAudio:
    def __init__(self, wav=b"x" * 200):
        self.wav = wav
        self.recording = False

    def set_input_device(self, device):
        pass

    def start(self):
        self.recording = True

    def stop(self):
        self.recording = False
        return self.wav

    def is_recording(self):
        return self.recording

    def get_input_level(self):
        return 0.5


class SyncThread:
    """Runs the target immediately so tests stay deterministic."""

    def __init__(self, target=None, args=(), kwargs=None, **_):
        self._run = lambda: target(*args, **(kwargs or {}))

    def start(self):
        self._run()


@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.setattr(core_mod.threading, "Thread", SyncThread)
    monkeypatch.setattr(core_mod, "save_config", lambda cfg: None)
    for name in ("play_start_sound", "play_stop_sound", "play_pressed_sound"):
        monkeypatch.setattr(core_mod, name, lambda: None)
    monkeypatch.setattr(core_mod, "list_input_devices", lambda: [{"id": "default", "name": "Default"}])
    config = dict(DEFAULT_CONFIG, api_key="sk-test-123456789", mode="cloud")
    c = WaveScribeCore(
        AppState(), FakeAudio(), create_event_queue(), FakeHotkeys(), FakeTTS(),
        config=config,
        history=HistoryManager(str(tmp_path / "history.json")),
        sessions=SessionStore(str(tmp_path / "sessions")),
    )
    c.events = []
    c.add_listener(lambda kind, payload: c.events.append((kind, payload)))
    monkeypatch.setattr(c, "transcribe", lambda wav: "Hello world.")
    monkeypatch.setattr(c, "_paste", lambda text: c.events.append(("pasted", text)))
    return c


def _dictate(core, auto_insert=True):
    core.start_recording()
    assert core.state.get_status() == AppStatus.RECORDING
    core.stop_recording(auto_insert=auto_insert)
    # The worker ran synchronously; now run the completion it posted.
    while not core.queue.empty():
        core._handle_event(core.queue.get())


def test_snapshot_is_json_serialisable(core) -> None:
    import json
    snap = core.snapshot()
    json.dumps(snap)
    assert snap["hotkeys"]["session"] == "ctrl+shift+l"
    assert snap["api_key_hint"].startswith("sk-") and "123456789" not in snap["api_key_hint"]


def test_dictation_goes_to_history_and_pastes(core) -> None:
    _dictate(core)
    assert core.state.get_last_transcription() == "Hello world."
    assert [e["text"] for e in core.history.get_all()] == ["Hello world."]
    assert ("pasted", "Hello world.") in core.events


def test_dictation_routes_to_active_session_without_paste(core) -> None:
    session = core.session_new("Playtest")
    _dictate(core)
    stored = core.sessions.get(session["id"])
    assert [e["text"] for e in stored["entries"]] == ["Hello world."]
    assert core.history.get_all() == []
    assert not any(kind == "pasted" for kind, _ in core.events)

    core.set_setting("session_paste_dictations", True)
    _dictate(core)
    assert ("pasted", "Hello world.") in core.events

    core.session_end()
    _dictate(core)
    assert len(core.history.get_all()) == 1


def test_segments_append_and_filter_hallucinations(core) -> None:
    s = core.session_new()
    core._segment_done(s["id"], "Make the menu font bigger.", 2.0, "")
    core._segment_done(s["id"], "Thank you.", 1.0, "")  # Whisper artefact
    core._segment_done(s["id"], "", 1.0, "network down")
    texts = [e["text"] for e in core.sessions.get(s["id"])["entries"]]
    assert texts == ["Make the menu font bigger."]
    assert any(k == "notice" and p["tone"] == "error" for k, p in core.events)
    assert core.session_text(s["id"]) == "- Make the menu font bigger."


def test_set_hotkey_validation(core) -> None:
    assert core.set_hotkey("session", "ctrl+shift+r") == {"ok": False, "error": "That shortcut is already used."}
    assert not core.set_hotkey("session", "l")["ok"]
    assert core.set_hotkey("session", "ctrl+alt+L") == {"ok": True}
    assert core.hotkeys.registered["session"] == "ctrl+alt+l"


def test_hotkeys_ignored_while_capturing(core) -> None:
    core.begin_hotkey_capture()
    core._handle_event("start_recording")
    assert core.state.get_status() == AppStatus.IDLE
    core.end_hotkey_capture()
    core._handle_event("start_recording")
    assert core.state.get_status() == AppStatus.RECORDING


def test_cloud_without_key_cannot_record(core) -> None:
    core.state.set_api_key("")
    core.start_recording()
    assert core.state.get_status() == AppStatus.ERROR
    assert "API key" in core.state.get_error_message()
