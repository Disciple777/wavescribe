"""Tests for readout/core/engine.py (no speech output, no network)."""

from readout.core.engine import EngineManager, available_backends


def test_available_backends_always_include_system():
    backends = available_backends()
    assert "system" in backends
    assert backends[0] == "system"


def test_manager_defaults_to_system():
    mgr = EngineManager()
    assert mgr.get_backend() == "system"
    assert not mgr.is_speaking()


def test_manager_honours_requested_backend():
    mgr = EngineManager("system")
    assert mgr.engine("system").name == "system"
    assert mgr.engine("edge").name == "edge"


def test_set_backend_unknown_falls_back_to_system():
    mgr = EngineManager()
    mgr.set_backend("nonexistent")
    assert mgr.get_backend() == "system"


def test_empty_text_speak_is_a_noop():
    mgr = EngineManager()
    mgr.speak("")  # must not raise or touch any engine


def test_system_engine_is_offline_and_speaks_nothing_by_default():
    mgr = EngineManager("system")
    engine = mgr.engine("system")
    assert engine.display_name == "Windows voices (offline)"


def test_speak_routes_to_explicit_backend():
    """speak(backend=…) must use that engine, not the active one."""
    mgr = EngineManager("system")
    calls = []
    mgr.engine("edge").speak = lambda *a, **kw: calls.append(kw)  # shadow, no network
    mgr.speak("hola", voice="es-ES-ElviraNeural", backend="edge")
    assert calls and calls[0]["voice"] == "es-ES-ElviraNeural"
    assert calls[0]["rate_percent"] == 100


def test_speak_without_backend_uses_active_engine():
    mgr = EngineManager("system")
    calls = []
    mgr.engine("system").speak = lambda *a, **kw: calls.append(kw)
    mgr.speak("hello")
    assert calls and calls[0]["voice"] == ""


def test_stop_all_stops_every_engine():
    mgr = EngineManager("system")
    stopped = []
    mgr.engine("system").stop = lambda: stopped.append("system")
    mgr.engine("edge").stop = lambda: stopped.append("edge")
    mgr.stop_all()
    assert sorted(stopped) == ["edge", "system"]
