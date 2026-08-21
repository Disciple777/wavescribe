"""Regression test: the Readout GUI must construct and update without crashing.

This guards against startup crashes like the v0.1 bug:
``TypeError: 'ReadoutState' object is not callable`` raised from
``App.__init__`` -> ``self.resizable(True, True)``. The cause was naming the
state attribute ``self.state``, which shadowed tkinter's window ``state()``
method that CustomTkinter calls internally.

Requires a display; skipped gracefully on headless machines.
"""
import threading
import time

import pytest

ctk = pytest.importorskip("customtkinter")

from readout.core import selection as selection_mod  # noqa: E402
from readout.core.state import ReadStatus, ReadoutState, create_event_queue  # noqa: E402
from readout.core.hotkeys import ReadoutHotkeys  # noqa: E402
from readout.core.engine import EngineManager  # noqa: E402
from readout.gui import App  # noqa: E402


def _display_available() -> bool:
    """Check whether a Tk display is available before building the GUI."""
    import tkinter as tk

    try:
        root = tk.Tk()
        root.destroy()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _display_available(), reason="no display available"
)


def test_gui_constructs_and_updates() -> None:
    """App() must build every card and survive repeated _update_ui calls."""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    state = ReadoutState()
    event_queue = create_event_queue()
    engine = EngineManager("system")
    hotkeys = ReadoutHotkeys(event_queue)

    app = App(state, event_queue, engine, hotkeys)

    try:
        # The tkinter window state() method must not be shadowed by the
        # app's ReadoutState — CustomTkinter calls it during setup.
        assert callable(app.state), "tkinter state() was shadowed"

        # Exercise the full UI update path a few times to catch widget-order bugs
        for _ in range(3):
            app._update_ui()
            app.update_idletasks()

        # Exercise a reading → idle transition through the state object
        state.set_status(ReadStatus.READING)
        app._update_ui()
        state.set_status(ReadStatus.IDLE)
        app._update_ui()
    finally:
        app.destroy()


def _make_app(monkeypatch, tmp_path, config_overrides=None):
    """Build an App with an isolated temp config dir and no voice loading
    threads (so tests never hit PowerShell or the network)."""
    import readout.gui as gui_mod
    import readout.core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: str(tmp_path))
    cfg_mod.save_config({**cfg_mod.DEFAULT_CONFIG, **(config_overrides or {})})
    monkeypatch.setattr(gui_mod.App, "_refresh_voices", lambda self: None)
    monkeypatch.setattr(gui_mod, "play_pressed_sound", lambda: None)
    return App(
        ReadoutState(),
        create_event_queue(),
        EngineManager("system"),
        ReadoutHotkeys(create_event_queue()),
    )


def test_app_migrates_legacy_voice_config(monkeypatch, tmp_path) -> None:
    """On first run, the rotation is seeded from the legacy voice keys."""
    app = _make_app(
        monkeypatch, tmp_path,
        {"backend": "edge", "edge_voice": "es-ES-ElviraNeural"},
    )
    try:
        assert app._config["voice_list"][0] == {
            "backend": "edge", "voice": "es-ES-ElviraNeural"
        }
        assert app._config["active_voice_index"] == 0
    finally:
        app.destroy()


def test_cycle_voice_steps_through_rotation(monkeypatch, tmp_path) -> None:
    """The cycle action advances the active voice and wraps around."""
    from readout.core.system_tts import VoiceInfo

    app = _make_app(monkeypatch, tmp_path)
    try:
        app._current_voices = [VoiceInfo(name="Voice B", gender="Female", language="en-US")]
        app._config["voice_list"] = [
            {"backend": "system", "voice": ""},
            {"backend": "system", "voice": "Voice B"},
        ]
        app._config["active_voice_index"] = 0
        app._on_cycle_voice()
        assert app._config["active_voice_index"] == 1
        assert app._config["backend"] == "system"
        app._on_cycle_voice()
        assert app._config["active_voice_index"] == 0
    finally:
        app.destroy()


def test_cycle_voice_switches_engine_when_needed(monkeypatch, tmp_path) -> None:
    """Cycling onto a voice from the other engine switches the engine too."""
    app = _make_app(monkeypatch, tmp_path)
    try:
        app._config["voice_list"] = [
            {"backend": "system", "voice": ""},
            {"backend": "edge", "voice": "es-ES-ElviraNeural"},
        ]
        app._config["active_voice_index"] = 0
        app._on_cycle_voice()
        assert app._config["active_voice_index"] == 1
        assert app.engine.get_backend() == "edge"
        assert app._config["backend"] == "edge"
    finally:
        app.destroy()


def test_voices_loaded_keeps_persisted_active_voice(monkeypatch, tmp_path) -> None:
    """Regression: restarting must NOT reset the user's voice swap back to
    the main — a persisted same-backend index survives voice loading."""
    from readout.core.system_tts import VoiceInfo

    app = _make_app(monkeypatch, tmp_path)
    try:
        app.engine.set_backend("edge")
        app._config["voice_list"] = [
            {"backend": "edge", "voice": "en-US-AriaNeural"},
            {"backend": "edge", "voice": "es-ES-ElviraNeural"},
        ]
        app._config["active_voice_index"] = 1  # the user swapped to Spanish
        app._handle_voices_loaded(
            "edge",
            [
                VoiceInfo(name="en-US-AriaNeural", gender="Female", language="en-US"),
                VoiceInfo(name="es-ES-ElviraNeural", gender="Female", language="es-ES"),
            ],
        )
        # Still on Spanish — not snapped back to the main voice.
        assert app._config["active_voice_index"] == 1
        assert app._voice_menu.get() == "es-ES-ElviraNeural"
    finally:
        app.destroy()


def test_voices_loaded_snaps_after_manual_engine_switch(monkeypatch, tmp_path) -> None:
    """Switching the Engine picker lands on an entry for that engine."""
    from readout.core.system_tts import VoiceInfo

    app = _make_app(monkeypatch, tmp_path)
    try:
        app.engine.set_backend("system")
        app._config["voice_list"] = [
            {"backend": "edge", "voice": "en-US-AriaNeural"},
            {"backend": "system", "voice": ""},
        ]
        app._config["active_voice_index"] = 0  # persisted entry is on edge
        app._handle_voices_loaded(
            "system",
            [VoiceInfo(name="Microsoft David", gender="Male", language="en-US")],
        )
        # The edge entry can't be used while system voices are loaded → snap.
        assert app._config["active_voice_index"] == 1
        assert app._voice_menu.get() == "(System default)"
    finally:
        app.destroy()


def test_voice_search_filters_dropdowns(monkeypatch, tmp_path) -> None:
    """Typing in the search box filters the main dropdown, keeps the active
    voice visible, and surfaces already-added voices in the add picker."""
    from readout.core.system_tts import VoiceInfo

    app = _make_app(monkeypatch, tmp_path)
    try:
        app._current_voices = [
            VoiceInfo(name="en-US-AriaNeural", gender="Female", language="en-US"),
            VoiceInfo(name="es-ES-ElviraNeural", gender="Female", language="es-ES"),
        ]
        app._config["voice_list"] = [{"backend": "system", "voice": ""}]
        app._voice_search.delete(0, "end")
        app._voice_search.insert(0, "elvira")
        app._on_voice_search()

        values = app._voice_menu.cget("values")
        assert "es-ES-ElviraNeural" in values
        assert "en-US-AriaNeural" not in values
        assert "(System default)" in values  # active voice always kept
        # Searching shows already-added voices in the add picker too.
        assert "(System default)" in app._add_menu.cget("values")

        app._clear_voice_search()
        assert len(app._voice_menu.cget("values")) == 3  # everything back
    finally:
        app.destroy()


def test_voice_search_no_match_keeps_active_voice(monkeypatch, tmp_path) -> None:
    """A dead-end search keeps the active voice usable; adding it (already the
    main) is a no-op with a clear notice."""
    from readout.core.system_tts import VoiceInfo

    app = _make_app(monkeypatch, tmp_path)
    try:
        app._current_voices = [
            VoiceInfo(name="en-US-AriaNeural", gender="Female", language="en-US")
        ]
        app._config["voice_list"] = [{"backend": "system", "voice": ""}]
        app._voice_search.insert(0, "zzz")
        app._on_voice_search()
        # The active voice is always kept visible in the main dropdown.
        assert app._voice_menu.cget("values") == ["(System default)"]
        app._add_menu.set("(System default)")
        app._on_add_secondary()
        assert "already in the list" in app._notice_label.cget("text")
        assert app._config["voice_list"] == [{"backend": "system", "voice": ""}]
    finally:
        app.destroy()


def test_voice_search_placeholder_never_addable(monkeypatch, tmp_path) -> None:
    """With no voices loaded, a dead-end search must disable the add button
    and reject the placeholder even if it is somehow selected."""
    import readout.gui as gui_mod

    app = _make_app(monkeypatch, tmp_path)
    try:
        app.engine.set_backend("edge")
        app._current_voices = []
        app._config["voice_list"] = [{"backend": "system", "voice": ""}]
        app._voice_search.insert(0, "zzz")
        app._on_voice_search()
        assert app._add_btn.cget("state") == "disabled"
        assert app._add_menu.get() == gui_mod._NO_MATCHES_LABEL
        app._add_menu.set(gui_mod._NO_MATCHES_LABEL)
        app._on_add_secondary()
        assert app._config["voice_list"] == [{"backend": "system", "voice": ""}]
    finally:
        app.destroy()


def test_add_notice_names_already_added_voice(monkeypatch, tmp_path) -> None:
    """Adding the main voice (which search now surfaces) explains where it is."""
    from readout.core.system_tts import VoiceInfo

    app = _make_app(monkeypatch, tmp_path)
    try:
        app._current_voices = [
            VoiceInfo(name="en-US-AriaNeural", gender="Female", language="en-US")
        ]
        app._config["voice_list"] = [{"backend": "system", "voice": "en-US-AriaNeural"}]
        app._add_menu.set("en-US-AriaNeural")
        app._on_add_secondary()
        assert "already in the list" in app._notice_label.cget("text")
        assert "main voice" in app._notice_label.cget("text")
        # Nothing was added.
        assert app._config["voice_list"] == [{"backend": "system", "voice": "en-US-AriaNeural"}]
    finally:
        app.destroy()


def test_picking_new_main_demotes_old_one(monkeypatch, tmp_path) -> None:
    """Choosing a voice in the main dropdown keeps the old main as secondary."""
    from readout.core.system_tts import VoiceInfo

    app = _make_app(monkeypatch, tmp_path)
    try:
        app._current_voices = [VoiceInfo(name="Voice B", gender="Female", language="en-US")]
        app._config["voice_list"] = [
            {"backend": "system", "voice": ""},
            {"backend": "system", "voice": "Voice B"},
        ]
        app._config["active_voice_index"] = 1
        app._on_voice_changed("Voice B")
        assert app._config["voice_list"][0] == {"backend": "system", "voice": "Voice B"}
        assert app._config["voice_list"][1] == {"backend": "system", "voice": ""}
        assert app._config["active_voice_index"] == 0
    finally:
        app.destroy()


def test_no_selection_leaves_persistent_error_state(monkeypatch) -> None:
    """A failed grab must set a persistent ERROR status (not just a brief
    notice) so the user has time to see that nothing was selected."""
    import readout.gui as gui_mod

    monkeypatch.setattr(selection_mod, "grab_selected_text", lambda *a, **k: "")
    monkeypatch.setattr(selection_mod, "is_legacy_terminal_focused", lambda: False)
    monkeypatch.setattr(gui_mod, "play_failure_sound", lambda: None)

    state = ReadoutState()
    app = App(
        state,
        create_event_queue(),
        EngineManager("system"),
        ReadoutHotkeys(create_event_queue()),
    )
    try:
        app._busy = True  # mimic _on_read_selection before starting the worker
        threading.Thread(target=app._read_selection_worker, daemon=True).start()

        deadline = time.time() + 3
        while time.time() < deadline and state.get_status() != ReadStatus.ERROR:
            time.sleep(0.02)
        assert state.get_status() == ReadStatus.ERROR
        assert state.get_error_message() == "No text selected"
        app._update_ui()  # the ERROR branch must not crash either
    finally:
        app.destroy()
