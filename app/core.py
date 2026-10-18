"""WaveScribe application core — everything except the view.

``WaveScribeCore`` owns the state, config, recording/transcription
pipeline, Read Out (TTS), history and listening sessions. It knows
nothing about the UI: views subscribe with :meth:`add_listener` and
receive ``(kind, payload)`` events, and call the public methods to act.

Threading model: every mutating action runs on a single *core loop*
thread that drains the shared event queue (hotkeys post plain strings
into the same queue). UI calls go through :meth:`call`, which queues the
action and waits for its result. This serialises all state changes
without sprinkling locks everywhere.
"""

import concurrent.futures
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pyperclip

from app.state import AppState, AppStatus, AppMode, LocalModelStatus, TTSReadStatus, EventQueue
from app.audio_capture import (
    AudioCapture, AudioCaptureError, float_to_wav_bytes, list_input_devices,
)
from app.transcriber import (
    transcribe_cloud, transcribe_local, is_whisper_available, install_whisper_package,
    load_whisper_model, setup_bundled_model, TranscriptionError, PUNCTUATION_PROMPT,
)
from app.formatter import (
    apply_punctuation, auto_capitalize, convert_numbers, clean_spacing,
    cleanup_redundant_punctuation, auto_add_sentence_punctuation,
)
from app.typer import type_text
from app.config import load_config, save_config
from app.autostart import apply_autostart_setting
from app.history import HistoryManager
from app.hotkeys import HotkeyManager
from app.listening import ListeningStream, is_probable_hallucination
from app.sessions import SessionStore, render_session_text, render_session_markdown
from app.sounds import (
    init_sounds, play_start_sound, play_stop_sound, play_pressed_sound,
    play_tts_success_sound, play_tts_failure_sound,
)
from app.tts_engine import EngineManager, available_backends
from app.tts_errors import TTSError
from app import selection
from app import voices as voices_mod

APP_VERSION = "2.0"

# hotkey name → config key
HOTKEY_KEYS = {
    "start": "hotkey_start",
    "stop": "hotkey_stop",
    "session": "hotkey_session",
    "tts_read": "tts_hotkey",
    "tts_swap": "tts_swap_voice_hotkey",
}

# Settings the UI may change directly via set_setting().
_BOOL_SETTINGS = {
    "punctuate_speech", "auto_capitalize", "numbers_as_digits",
    "autostart", "history_enabled", "session_paste_dictations",
}

Listener = Callable[[str, Any], None]


class WaveScribeCore:
    def __init__(
        self,
        state: AppState,
        audio: AudioCapture,
        event_queue: EventQueue,
        hotkey_mgr: HotkeyManager,
        tts_engine: EngineManager,
        config: Optional[Dict[str, Any]] = None,
        history: Optional[HistoryManager] = None,
        sessions: Optional[SessionStore] = None,
    ) -> None:
        self.state = state
        self.audio = audio
        self.queue = event_queue
        self.hotkeys = hotkey_mgr
        self.tts = tts_engine
        self.config = config if config is not None else load_config()
        self.history = history or HistoryManager()
        self.sessions = sessions or SessionStore()

        self._listeners: List[Listener] = []
        self._loop_thread: Optional[threading.Thread] = None
        self._stopped = False

        # Hooks the view can provide (all optional).
        self.view_is_focused: Callable[[], bool] = lambda: False
        self.view_minimize: Callable[[], None] = lambda: None

        self._apply_config_to_state()

        device = self.config.get("input_device", "default")
        self._mic_devices = self._safe_list_devices()
        if device not in [d["id"] for d in self._mic_devices]:
            device = "default"
            self.config["input_device"] = "default"
        self.audio.set_input_device(device)

        # Dictation
        self._transcription_cancelled = False
        self._hotkey_capture = False

        # Read Out
        self._tts_busy = False
        self._tts_cancel_requested = False
        self._tts_voice_pending = False
        self._tts_voices: Dict[str, list] = {}
        backend = self.config.get("tts_backend", "system")
        self.tts.set_backend(backend if backend in available_backends() else "system")

        # Sessions
        self._active_session_id: Optional[str] = None
        saved = self.config.get("active_session_id") or ""
        if saved and self.sessions.get(saved):
            self._active_session_id = saved
        self._listener_stream = ListeningStream(
            self._on_speech_segment,
            pause_s=float(self.config.get("session_pause_s", 1.2)),
            sensitivity=self.config.get("session_sensitivity", "medium"),
        )
        self._listener_stream.set_input_device(device)
        self._segments: "queue.Queue[tuple]" = queue.Queue()
        self._pending_segments = 0
        self._pending_lock = threading.Lock()

    # ═══════════════════════════════════════════════════════════
    #  Lifecycle & plumbing
    # ═══════════════════════════════════════════════════════════

    def start(self) -> None:
        """Start background work. Call once, after listeners are attached."""
        init_sounds()
        setup_bundled_model()
        if is_whisper_available() and self.state.get_local_model_status() == LocalModelStatus.READY:
            self._load_local_model_in_background()
        self.register_all_hotkeys()
        self._loop_thread = threading.Thread(target=self._loop, daemon=True, name="core-loop")
        self._loop_thread.start()
        threading.Thread(target=self._segment_worker, daemon=True, name="session-transcriber").start()
        self.tts_refresh_voices()

    def shutdown(self) -> None:
        self._stopped = True
        try:
            if self._listener_stream.running:
                self._listener_stream.stop(flush=False)
            if self.audio.is_recording():
                self.audio.stop()
        except Exception:
            pass
        self.tts.stop_all()
        save_config(self._config_for_save())

    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def _emit(self, kind: str, payload: Any = None) -> None:
        for fn in list(self._listeners):
            try:
                fn(kind, payload)
            except Exception as exc:
                print(f"Listener error ({kind}): {exc}")

    def _changed(self) -> None:
        self._emit("state", self.snapshot())

    def notice(self, msg: str, tone: str = "info", scope: str = "global", duration: int = 4000) -> None:
        self._emit("notice", {"msg": msg, "tone": tone, "scope": scope, "duration": duration})

    def call(self, fn: Callable, *args, **kwargs) -> Any:
        """Run ``fn`` on the core loop thread and return its result."""
        if threading.current_thread() is self._loop_thread or self._loop_thread is None:
            return fn(*args, **kwargs)
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self.queue.put(("call", fn, args, kwargs, fut))
        return fut.result(timeout=30)

    def post(self, fn: Callable, *args) -> None:
        """Queue ``fn`` on the core loop without waiting."""
        self.queue.put(("call", fn, args, {}, None))

    def _loop(self) -> None:
        while not self._stopped:
            try:
                event = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._handle_event(event)
            except Exception as exc:
                print(f"Core loop error: {exc!r}")

    def _handle_event(self, event: Any) -> None:
        if isinstance(event, tuple) and event and event[0] == "call":
            _, fn, args, kwargs, fut = event
            try:
                result = fn(*args, **kwargs)
                if fut is not None:
                    fut.set_result(result)
            except Exception as exc:
                if fut is not None:
                    fut.set_exception(exc)
                else:
                    raise
            return

        # Hotkeys are ignored while the user is recording a new shortcut.
        if isinstance(event, str) and self._hotkey_capture:
            return

        if event == "start_recording":
            if self.state.get_status() in (AppStatus.IDLE, AppStatus.ERROR):
                self.start_recording()
        elif event == "stop_recording":
            if self.state.get_status() == AppStatus.RECORDING:
                self.stop_recording(auto_insert=True)
        elif event == "toggle_listening":
            self.session_toggle_listening()
        elif event == "toggle_read":
            self.tts_toggle_read()
        elif event == "cycle_voice":
            self.tts_cycle_voice()
        elif isinstance(event, tuple) and event[0] == "tts_speech_done":
            self.state.set_tts_status(TTSReadStatus.IDLE)
            self._tts_apply_pending_voice_if_idle()
            self._changed()
        elif isinstance(event, tuple) and event[0] == "tts_speech_error":
            self.state.set_tts_status(TTSReadStatus.ERROR)
            self.state.set_tts_error_message(str(event[1]))
            self._tts_apply_pending_voice_if_idle()
            self._changed()
        elif isinstance(event, tuple) and event[0] == "tts_voices_loaded":
            self._tts_handle_voices_loaded(event[1], event[2])

    # ═══════════════════════════════════════════════════════════
    #  Config & snapshot
    # ═══════════════════════════════════════════════════════════

    def _apply_config_to_state(self) -> None:
        c = self.config
        self.state.set_api_key(c.get("api_key", ""))
        self.state.set_model_size(c.get("model_size", "base"))
        self.state.set_punctuate_speech(c.get("punctuate_speech", True))
        self.state.set_auto_capitalize(c.get("auto_capitalize", True))
        self.state.set_numbers_as_digits(c.get("numbers_as_digits", False))
        self.state.set_autostart_enabled(c.get("autostart", True))
        self.state.set_history_enabled(c.get("history_enabled", True))
        self.state.set_mode(AppMode.CLOUD if c.get("mode", "cloud") == "cloud" else AppMode.LOCAL)
        self.state.set_local_language(c.get("local_language", "en"))
        status = c.get("local_model_status", "package_missing")
        if status == "ready" and not is_whisper_available():
            status = "package_missing"
        try:
            self.state.set_local_model_status(LocalModelStatus(status))
        except ValueError:
            self.state.set_local_model_status(LocalModelStatus.PACKAGE_MISSING)

    def _config_for_save(self) -> Dict[str, Any]:
        s = self.state
        self.config.update({
            "api_key": s.get_api_key(),
            "model_size": s.get_model_size(),
            "punctuate_speech": s.get_punctuate_speech(),
            "auto_capitalize": s.get_auto_capitalize(),
            "numbers_as_digits": s.get_numbers_as_digits(),
            "mode": s.get_mode().value,
            "local_language": s.get_local_language(),
            "local_model_status": s.get_local_model_status().value,
            "autostart": s.get_autostart_enabled(),
            "history_enabled": s.get_history_enabled(),
            "active_session_id": self._active_session_id or "",
        })
        return self.config

    def save(self) -> None:
        save_config(self._config_for_save())

    @staticmethod
    def _safe_list_devices() -> List[Dict[str, Any]]:
        try:
            return [{"id": d["id"], "name": d["name"]} for d in list_input_devices()]
        except Exception:
            return [{"id": "default", "name": "Default Microphone"}]

    def snapshot(self) -> Dict[str, Any]:
        s = self.state
        key = s.get_api_key()
        status = s.get_status()
        return {
            "version": APP_VERSION,
            "status": status.value,
            "error": s.get_error_message(),
            "mode": s.get_mode().value,
            "has_api_key": bool(key),
            "api_key_hint": (key[:3] + "…" + key[-4:]) if len(key) > 10 else ("set" if key else ""),
            "model_size": s.get_model_size(),
            "local_language": s.get_local_language(),
            "local_model_status": s.get_local_model_status().value,
            "punctuate_speech": s.get_punctuate_speech(),
            "auto_capitalize": s.get_auto_capitalize(),
            "numbers_as_digits": s.get_numbers_as_digits(),
            "autostart": s.get_autostart_enabled(),
            "history_enabled": s.get_history_enabled(),
            "last_transcription": s.get_last_transcription(),
            "hotkeys": {name: self.config.get(key, "") for name, key in HOTKEY_KEYS.items()},
            "mic": {"devices": self._mic_devices, "selected": self.config.get("input_device", "default")},
            "theme": self.config.get("theme", "dark"),
            "tts": self._tts_snapshot(),
            "session": self._session_snapshot(),
        }

    # ═══════════════════════════════════════════════════════════
    #  Settings
    # ═══════════════════════════════════════════════════════════

    def set_setting(self, key: str, value: Any) -> None:
        s = self.state
        if key == "mode":
            s.set_mode(AppMode.CLOUD if value == "cloud" else AppMode.LOCAL)
            if s.get_status() == AppStatus.ERROR:
                s.clear_error()
        elif key == "model_size" and value in ("tiny", "base", "small"):
            s.set_model_size(value)
        elif key == "local_language" and value in ("en", "auto"):
            s.set_local_language(value)
        elif key == "punctuate_speech":
            s.set_punctuate_speech(bool(value))
        elif key == "auto_capitalize":
            s.set_auto_capitalize(bool(value))
        elif key == "numbers_as_digits":
            s.set_numbers_as_digits(bool(value))
        elif key == "history_enabled":
            s.set_history_enabled(bool(value))
        elif key == "autostart":
            if apply_autostart_setting(bool(value)):
                s.set_autostart_enabled(bool(value))
            else:
                self.notice("Couldn't change the Windows startup setting.", "error")
        elif key == "input_device":
            device = value if value == "default" else int(value)
            self.config["input_device"] = device
            self.audio.set_input_device(device)
            self._listener_stream.set_input_device(device)
            if self._listener_stream.running:
                self.notice("Microphone change applies the next time listening starts.")
        elif key == "session_pause_s":
            self.config[key] = max(0.5, min(4.0, float(value)))
            self._listener_stream.configure(pause_s=self.config[key])
        elif key == "session_sensitivity" and value in ("low", "medium", "high"):
            self.config[key] = value
            self._listener_stream.configure(sensitivity=value)
        elif key == "session_paste_dictations":
            self.config[key] = bool(value)
        elif key == "session_copy_style" and value in ("list", "numbered", "paragraphs"):
            self.config[key] = value
        elif key == "theme" and value in ("dark", "light", "system"):
            self.config[key] = value
        else:
            return
        self.save()
        self._changed()

    def save_api_key(self, key: str) -> bool:
        key = (key or "").strip()
        if not key:
            return False
        self.state.set_api_key(key)
        if self.state.get_status() == AppStatus.ERROR:
            self.state.clear_error()
        self.save()
        self._changed()
        return True

    def refresh_devices(self) -> None:
        self._mic_devices = self._safe_list_devices()
        self._changed()

    # ── Hotkeys ──

    def register_all_hotkeys(self) -> None:
        c = self.config
        try:
            self.hotkeys.set_hotkeys(c.get("hotkey_start", "ctrl+shift+r"), c.get("hotkey_stop", "ctrl+shift+s"))
            self.hotkeys.set_session_hotkey(c.get("hotkey_session", ""))
            self.hotkeys.set_tts_hotkeys(c.get("tts_hotkey", ""), c.get("tts_swap_voice_hotkey", ""))
        except Exception as exc:
            self.notice(f"Couldn't register a shortcut: {exc}", "error", duration=8000)

    def begin_hotkey_capture(self) -> None:
        self._hotkey_capture = True

    def end_hotkey_capture(self) -> None:
        self._hotkey_capture = False

    def set_hotkey(self, name: str, combo: str) -> Dict[str, Any]:
        combo = (combo or "").strip().lower()
        self._hotkey_capture = False
        if name not in HOTKEY_KEYS:
            return {"ok": False, "error": "Unknown shortcut."}
        if combo and "+" not in combo:
            return {"ok": False, "error": "Use a modifier, e.g. Ctrl+Shift+X."}
        for other, key in HOTKEY_KEYS.items():
            if other != name and combo and self.config.get(key, "").lower() == combo:
                return {"ok": False, "error": "That shortcut is already used."}
        old = self.config.get(HOTKEY_KEYS[name], "")
        self.config[HOTKEY_KEYS[name]] = combo
        try:
            self.register_all_hotkeys()
        except Exception as exc:
            self.config[HOTKEY_KEYS[name]] = old
            self.register_all_hotkeys()
            return {"ok": False, "error": f"Couldn't register that shortcut ({exc})."}
        self.save()
        self._changed()
        return {"ok": True}

    # ═══════════════════════════════════════════════════════════
    #  Dictation
    # ═══════════════════════════════════════════════════════════

    def can_record(self) -> bool:
        return not (self.state.get_mode() == AppMode.CLOUD and not self.state.get_api_key())

    def start_recording(self) -> None:
        if self.state.get_status() not in (AppStatus.IDLE, AppStatus.ERROR):
            return
        if not self.can_record():
            self.state.set_status(AppStatus.ERROR)
            self.state.set_error_message("Add your OpenAI API key in Settings, or switch to the Local model.")
            self._changed()
            return
        self.state.clear_error()
        self._transcription_cancelled = False
        self._listener_stream.set_suspended(True)
        self.state.set_status(AppStatus.RECORDING)
        play_start_sound()
        try:
            self.audio.start()
        except AudioCaptureError as e:
            self._listener_stream.set_suspended(False)
            self.state.set_status(AppStatus.ERROR)
            self.state.set_error_message(str(e))
        self._changed()

    def stop_recording(self, auto_insert: bool = False) -> None:
        if self.state.get_status() != AppStatus.RECORDING:
            return
        self.state.set_status(AppStatus.TRANSCRIBING)
        play_stop_sound()
        self._changed()
        session_id = self._active_session_id
        threading.Thread(
            target=self._finish_dictation, args=(auto_insert, session_id), daemon=True, name="dictation"
        ).start()

    def _finish_dictation(self, auto_insert: bool, session_id: Optional[str]) -> None:
        try:
            wav = self.audio.stop()
        except AudioCaptureError as e:
            self._listener_stream.set_suspended(False)
            self.post(self._dictation_failed, str(e))
            return
        self._listener_stream.set_suspended(False)
        try:
            text = self.transcribe(wav)
        except TranscriptionError as e:
            self.post(self._dictation_failed, str(e))
            return
        self.post(self._dictation_done, text, auto_insert, session_id)

    def _dictation_failed(self, msg: str) -> None:
        if self._transcription_cancelled:
            return
        self.state.set_status(AppStatus.ERROR)
        self.state.set_error_message(msg)
        self._changed()

    def _dictation_done(self, text: str, auto_insert: bool, session_id: Optional[str]) -> None:
        if self._transcription_cancelled:
            return
        self.state.set_last_transcription(text)
        self.state.set_status(AppStatus.IDLE)
        to_session = bool(session_id and self.sessions.get(session_id))
        if to_session:
            entry = self.sessions.add_entry(session_id, text, source="dictation")
            if entry:
                self._emit("note_added", {"session_id": session_id, "text": text})
                self._session_changed(session_id)
        elif self.state.get_history_enabled():
            self.history.add(text)
            self._emit("history", self.history_list())
        self._changed()
        paste = auto_insert and text and (not to_session or self.config.get("session_paste_dictations"))
        if paste:
            self._paste(text)

    def cancel_transcription(self) -> None:
        self._transcription_cancelled = True
        self.state.set_status(AppStatus.IDLE)
        self.state.clear_error()
        self._changed()

    def dismiss_error(self) -> None:
        self.state.clear_error()
        self._changed()

    def clear_transcription(self) -> None:
        self.state.set_last_transcription("")
        self._changed()

    def paste_last(self) -> None:
        text = self.state.get_last_transcription()
        if text:
            self.view_minimize()
            threading.Thread(target=lambda: (time.sleep(0.25), type_text(text)), daemon=True).start()

    def _paste(self, text: str) -> None:
        if self.view_is_focused():
            self.view_minimize()
        threading.Thread(target=type_text, args=(text,), daemon=True, name="paste").start()

    def transcribe(self, wav_bytes: bytes) -> str:
        """Transcribe + format WAV audio with the current settings (any thread)."""
        s = self.state
        mode = s.get_mode()
        prompt = None
        if not s.get_punctuate_speech():
            # Skip the English style prompt when auto-detecting the language.
            if mode == AppMode.CLOUD or s.get_local_language() == "en":
                prompt = PUNCTUATION_PROMPT
        if mode == AppMode.CLOUD:
            key = s.get_api_key()
            if not key:
                raise TranscriptionError("No API key configured. Please enter your OpenAI API key in Settings.")
            text = transcribe_cloud(wav_bytes, key, prompt=prompt)
        else:
            if s.get_local_model_status() != LocalModelStatus.READY:
                raise TranscriptionError("Whisper model not ready. Please install it in Settings.")
            text = transcribe_local(
                wav_bytes, s.get_model_size(), language=s.get_local_language(), initial_prompt=prompt,
            )
        return self.format_text(text)

    def format_text(self, text: str) -> str:
        s = self.state
        if s.get_punctuate_speech():
            text = apply_punctuation(text)
        else:
            text = auto_add_sentence_punctuation(text)
        text = clean_spacing(text)
        text = cleanup_redundant_punctuation(text)
        if s.get_auto_capitalize():
            text = auto_capitalize(text)
        if s.get_numbers_as_digits():
            text = convert_numbers(text)
        text = clean_spacing(text)
        if not s.get_auto_capitalize() and text:
            text = text[0].lower() + text[1:]
        return text

    def current_level(self) -> Dict[str, Any]:
        """Live meter data for the view (cheap; any thread)."""
        if self.state.get_status() == AppStatus.RECORDING:
            return {"level": self.audio.get_input_level(), "source": "dictation", "speaking": False}
        if self._listener_stream.running:
            return {
                "level": self._listener_stream.get_input_level(),
                "source": "session",
                "speaking": self._listener_stream.speaking,
            }
        return {"level": 0.0, "source": None, "speaking": False}

    # ── Local model ──

    def _load_local_model_in_background(self) -> None:
        def work() -> None:
            try:
                load_whisper_model(self.state.get_model_size())
            except Exception:
                self.state.set_local_model_status(LocalModelStatus.ERROR)
                self.post(self._changed)
        threading.Thread(target=work, daemon=True, name="whisper-load").start()

    def install_local_model(self) -> None:
        if self.state.get_local_model_status() in (LocalModelStatus.INSTALLING, LocalModelStatus.MODEL_LOADING):
            return
        self.state.set_local_model_status(LocalModelStatus.INSTALLING)
        self._changed()

        def work() -> None:
            try:
                install_whisper_package()
                self.state.set_local_model_status(LocalModelStatus.MODEL_LOADING)
                self.post(self._changed)
                load_whisper_model(self.state.get_model_size())
                self.state.set_local_model_status(LocalModelStatus.READY)
            except Exception:
                self.state.set_local_model_status(LocalModelStatus.ERROR)
            self.post(self.save)
            self.post(self._changed)
        threading.Thread(target=work, daemon=True, name="whisper-install").start()

    # ═══════════════════════════════════════════════════════════
    #  History
    # ═══════════════════════════════════════════════════════════

    def history_list(self) -> List[Dict[str, Any]]:
        return self.history.get_all()

    def history_open(self, index: int) -> None:
        entry = self.history.get(index)
        if entry:
            self.state.set_last_transcription(entry["text"])
            self._changed()

    def history_delete(self, index: int) -> None:
        if self.history.delete(index):
            self._emit("history", self.history_list())

    def history_clear(self) -> None:
        self.history.clear()
        self._emit("history", self.history_list())

    @staticmethod
    def copy_text(text: str) -> bool:
        try:
            pyperclip.copy(text or "")
            return True
        except Exception:
            return False

    # ═══════════════════════════════════════════════════════════
    #  Listening sessions
    # ═══════════════════════════════════════════════════════════

    def session_status(self) -> Dict[str, Any]:
        """Public, cheap session status (safe to poll from any thread)."""
        return self._session_snapshot()

    def _session_snapshot(self) -> Dict[str, Any]:
        active = self.sessions.summary(self._active_session_id) if self._active_session_id else None
        with self._pending_lock:
            pending = self._pending_segments
        return {
            "active_id": active["id"] if active else None,
            "active_name": active["name"] if active else "",
            "active_count": active["count"] if active else 0,
            "listening": self._listener_stream.running,
            "pending": pending,
            "pause_s": float(self.config.get("session_pause_s", 1.2)),
            "sensitivity": self.config.get("session_sensitivity", "medium"),
            "paste_dictations": bool(self.config.get("session_paste_dictations", False)),
            "copy_style": self.config.get("session_copy_style", "list"),
        }

    def _session_changed(self, session_id: Optional[str]) -> None:
        self._emit("sessions", self.sessions.list_sessions())
        if session_id:
            self._emit("session", self.sessions.get(session_id))

    def session_list(self) -> List[Dict[str, Any]]:
        return self.sessions.list_sessions()

    def session_get(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.sessions.get(session_id)

    def session_new(self, name: str = "", listen: bool = False) -> Dict[str, Any]:
        if self._listener_stream.running:
            self._stop_listening(sound=False)
        session = self.sessions.create(name)
        self._active_session_id = session["id"]
        self.save()
        if listen:
            self._start_listening()
        self._session_changed(session["id"])
        self._changed()
        return session

    def session_activate(self, session_id: str) -> None:
        if not self.sessions.get(session_id) or session_id == self._active_session_id:
            return
        if self._listener_stream.running:
            self._stop_listening(sound=False)
        self._active_session_id = session_id
        self.save()
        self._changed()

    def session_end(self) -> None:
        """Stop listening and stop routing dictations into the session."""
        if self._listener_stream.running:
            self._stop_listening()
        self._active_session_id = None
        self.save()
        self._changed()

    def session_toggle_listening(self) -> None:
        if self._listener_stream.running:
            self._stop_listening()
        else:
            if not self._active_session_id or not self.sessions.get(self._active_session_id):
                session = self.sessions.create()
                self._active_session_id = session["id"]
                self.save()
                self._session_changed(session["id"])
            self._start_listening()
        self._changed()

    def _start_listening(self) -> None:
        if self.state.get_mode() == AppMode.CLOUD and not self.state.get_api_key():
            self.notice("Add your OpenAI API key in Settings (or switch to Local) before listening.", "error", duration=7000)
            return
        try:
            self._listener_stream.start()
            play_start_sound()
        except AudioCaptureError as e:
            self.notice(str(e), "error", duration=7000)

    def _stop_listening(self, sound: bool = True) -> None:
        self._listener_stream.stop(flush=True)
        if sound:
            play_stop_sound()

    def _on_speech_segment(self, audio: np.ndarray) -> None:
        """Called on the VAD thread for each finished utterance."""
        session_id = self._active_session_id
        if not session_id:
            return
        with self._pending_lock:
            self._pending_segments += 1
        self._segments.put((session_id, audio))
        self.post(self._changed)

    def _segment_worker(self) -> None:
        """Transcribe session utterances one at a time, in order."""
        sr = self._listener_stream.samplerate
        while not self._stopped:
            session_id, audio = self._segments.get()
            duration = audio.size / sr
            text, error = "", ""
            for attempt in range(2):
                try:
                    text = self.transcribe(float_to_wav_bytes(audio, sr))
                    error = ""
                    break
                except TranscriptionError as e:
                    error = str(e)
                    if "empty result" in error:
                        error = ""
                        break
                    time.sleep(1.0)
            with self._pending_lock:
                self._pending_segments -= 1
            self.post(self._segment_done, session_id, text, duration, error)

    def _segment_done(self, session_id: str, text: str, duration: float, error: str) -> None:
        if error:
            self.notice(f"A note couldn't be transcribed: {error}", "error", duration=7000)
        elif text and not is_probable_hallucination(text, duration):
            if self.sessions.add_entry(session_id, text, source="live"):
                self._emit("note_added", {"session_id": session_id, "text": text})
                self._session_changed(session_id)
        self._changed()

    def session_rename(self, session_id: str, name: str) -> None:
        if self.sessions.rename(session_id, name):
            self._session_changed(session_id)
            self._changed()

    def session_delete(self, session_id: str) -> None:
        if session_id == self._active_session_id:
            self.session_end()
        self.sessions.delete(session_id)
        self._emit("sessions", self.sessions.list_sessions())

    def session_update_entry(self, session_id: str, entry_id: str, text: str) -> None:
        if self.sessions.update_entry(session_id, entry_id, text):
            self._session_changed(session_id)

    def session_delete_entry(self, session_id: str, entry_id: str) -> None:
        if self.sessions.delete_entry(session_id, entry_id):
            self._session_changed(session_id)
            self._changed()

    def session_add_text(self, session_id: str, text: str) -> None:
        if self.sessions.add_entry(session_id, text, source="typed"):
            self._session_changed(session_id)
            self._changed()

    def session_text(self, session_id: str, style: Optional[str] = None) -> str:
        s = self.sessions.get(session_id)
        if not s:
            return ""
        return render_session_text(s, style or self.config.get("session_copy_style", "list"))

    def session_markdown(self, session_id: str) -> str:
        s = self.sessions.get(session_id)
        return render_session_markdown(s) if s else ""

    # ═══════════════════════════════════════════════════════════
    #  Read Out (TTS)
    # ═══════════════════════════════════════════════════════════

    def _tts_snapshot(self) -> Dict[str, Any]:
        backends = []
        for name in available_backends():
            try:
                display = self.tts.engine(name).display_name
            except Exception:
                display = name
            backends.append({"name": name, "display": display})
        vl = self.config.get("tts_voice_list") or []
        return {
            "status": self.state.get_tts_status().value,
            "error": self.state.get_tts_error_message(),
            "busy": self._tts_busy or self.tts.is_speaking(),
            "backend": self.tts.get_backend(),
            "backends": backends,
            "rate": int(self.config.get("tts_rate_percent", 100)),
            "volume": int(self.config.get("tts_volume", 100)),
            "voice_list": [dict(e, display=voices_mod.entry_display(e)) for e in vl],
            "active_index": voices_mod.clamp_index(vl, self.config.get("tts_active_voice_index", 0)),
        }

    def tts_toggle_read(self) -> None:
        play_pressed_sound()
        if self.tts.is_speaking() or self.state.get_tts_status() == TTSReadStatus.READING:
            self.tts_stop()
            return
        if not self._tts_busy:
            self.tts_read_selection()

    def tts_read_selection(self) -> None:
        if self._tts_busy:
            return
        self._tts_begin()
        threading.Thread(target=self._tts_read_selection_worker, daemon=True, name="tts-grab").start()

    def tts_speak_text(self, text: str) -> None:
        if self._tts_busy:
            return
        text = (text or "").strip()
        if not text:
            play_tts_failure_sound()
            self.notice("Type or paste some text first.", "warn", scope="tts")
            return
        self._tts_begin()
        threading.Thread(target=self._tts_speak_worker, args=(text,), daemon=True, name="tts-speak").start()

    def _tts_begin(self) -> None:
        self._tts_busy = True
        self._tts_cancel_requested = False
        self.state.set_tts_status(TTSReadStatus.READING)
        self.state.set_tts_error_message("")
        self._changed()

    def _tts_read_selection_worker(self) -> None:
        in_terminal = selection.is_legacy_terminal_focused()
        info: dict = {}
        try:
            text = selection.grab_selected_text(in_legacy_terminal=in_terminal, info=info)
        except Exception:
            text = ""
        if not text:
            self._tts_busy = False
            play_tts_failure_sound()
            if info.get("image_only"):
                err = "Selection was an image, not text"
                msg = "The selection was an image (e.g. a sticker/emoticon), not text — select the text around it and try again."
            else:
                err = "No text selected"
                msg = ("No text selected — in this terminal, drag to select with the mouse (it copies on release), then try again."
                       if in_terminal else "No text selected — select some text and try again.")
            self.state.set_tts_error_message(err)
            self.notice(msg, "warn", scope="tts", duration=8000)
            self.queue.put(("tts_speech_done",))
            return
        self.state.set_tts_last_text(text)
        if in_terminal:
            self.notice("Read from the clipboard — in this terminal, drag-selecting copies on release.", "info", scope="tts", duration=6000)
        self._tts_speak_worker(text)

    def _tts_speak_worker(self, text: str) -> None:
        try:
            if self._tts_cancel_requested:
                return
            play_tts_success_sound()
            entry = voices_mod.active_voice(self.config)
            backend = entry.get("backend")
            if backend not in available_backends():
                backend = self.tts.get_backend()
            self.tts.speak(
                text,
                voice=entry.get("voice", ""),
                rate_percent=int(self.config.get("tts_rate_percent", 100)),
                volume=int(self.config.get("tts_volume", 100)),
                on_done=lambda: self.queue.put(("tts_speech_done",)),
                backend=backend,
            )
        except TTSError as exc:
            if not self._tts_cancel_requested:
                play_tts_failure_sound()
                self.queue.put(("tts_speech_error", str(exc)))
        except Exception as exc:
            if not self._tts_cancel_requested:
                play_tts_failure_sound()
                self.queue.put(("tts_speech_error", f"Unexpected error: {exc}"))
        finally:
            self._tts_busy = False

    def tts_stop(self) -> None:
        self._tts_cancel_requested = True
        self.tts.stop_all()
        self.state.set_tts_status(TTSReadStatus.IDLE)
        self.state.set_tts_error_message("")
        self._tts_apply_pending_voice_if_idle()
        self._changed()

    def tts_refresh_voices(self) -> None:
        backend = self.tts.get_backend()

        def work() -> None:
            try:
                voices = self.tts.list_voices(backend)
            except Exception:
                voices = []
            self.queue.put(("tts_voices_loaded", backend, voices))
        threading.Thread(target=work, daemon=True, name="tts-voices").start()

    def tts_voices(self) -> Dict[str, Any]:
        backend = self.tts.get_backend()
        voices = self._tts_voices.get(backend)
        return {
            "backend": backend,
            "loading": voices is None,
            "voices": [
                {"name": v.name, "language": getattr(v, "language", ""), "gender": getattr(v, "gender", "")}
                for v in (voices or [])
            ],
            "has_default": backend == "system",
        }

    def _tts_handle_voices_loaded(self, backend: str, voices: list) -> None:
        self._tts_voices[backend] = voices
        if backend != self.tts.get_backend():
            return
        is_system = backend == "system"
        valid = ({""} if is_system else set()) | {v.name for v in voices}

        changed = False
        vl = self.config.setdefault("tts_voice_list", [])
        if not vl:
            vl.append({"backend": backend, "voice": "" if is_system else (voices[0].name if voices else "")})
            changed = True
        if voices:
            pruned = voices_mod.prune_entries(vl, backend, valid)
            if len(pruned) != len(vl):
                vl[:] = pruned
                changed = True
            if not vl:
                vl.append({"backend": backend, "voice": "" if is_system else voices[0].name})
                changed = True
            persisted = voices_mod.clamp_index(vl, self.config.get("tts_active_voice_index", 0))
            idx = persisted if vl[persisted].get("backend") == backend else voices_mod.snap_index(vl, backend, persisted)
            entry = vl[idx]
            if entry.get("backend") != backend:
                idx = voices_mod.ensure_entry(vl, backend, "" if is_system else voices[0].name)
                entry = vl[idx]
                changed = True
            if entry.get("voice") not in valid:
                entry["voice"] = "" if is_system else voices[0].name
                changed = True
            self.config["tts_active_voice_index"] = voices_mod.clamp_index(vl, idx)
        if changed:
            save_config(self.config)
        self._emit("voices", self.tts_voices())
        self._changed()

    def tts_set_backend(self, name: str) -> None:
        if name not in available_backends() or name == self.tts.get_backend():
            return
        self.tts.set_backend(name)
        self.config["tts_backend"] = name
        save_config(self.config)
        self._emit("voices", self.tts_voices())
        self.tts_refresh_voices()
        self._changed()

    def tts_set_main_voice(self, voice: str) -> None:
        voices_mod.set_main_voice(self.config, self.tts.get_backend(), voice)
        save_config(self.config)
        self._changed()

    def tts_add_voice(self, voice: str) -> None:
        backend = self.tts.get_backend()
        entry = {"backend": backend, "voice": voice}
        if voices_mod.add_secondary(self.config, backend, voice):
            save_config(self.config)
            self.notice(f"Added {voices_mod.entry_display(entry)}", "success", scope="tts")
        else:
            self.notice(f"{voices_mod.entry_display(entry)} is already in the list.", "info", scope="tts")
        self._changed()

    def tts_remove_voice(self, index: int) -> None:
        if voices_mod.remove_entry(self.config, int(index)):
            save_config(self.config)
            self._changed()

    def tts_cycle_voice(self) -> None:
        play_pressed_sound()
        vl = self.config.get("tts_voice_list") or []
        if len(vl) <= 1:
            self.notice("Add a second voice to the rotation to use voice swapping.", "info", scope="tts")
            return
        idx = voices_mod.next_voice_index(vl, self.config.get("tts_active_voice_index", 0))
        self.config["tts_active_voice_index"] = idx
        save_config(self.config)
        entry = vl[idx]
        if self._tts_busy or self.tts.is_speaking():
            self._tts_voice_pending = True
            self.notice(f"Voice set to {voices_mod.entry_display(entry)} — applies to the next read", scope="tts")
        else:
            self._tts_apply_voice_entry(entry)
            self.notice(f"Voice: {voices_mod.entry_display(entry)}", scope="tts")
        self._changed()

    def tts_set_active_index(self, index: int) -> None:
        vl = self.config.get("tts_voice_list") or []
        if 0 <= int(index) < len(vl):
            self.config["tts_active_voice_index"] = int(index)
            save_config(self.config)
            self._tts_apply_voice_entry(vl[int(index)])
            self._changed()

    def _tts_apply_voice_entry(self, entry: Dict[str, str]) -> None:
        backend = entry.get("backend")
        if backend in available_backends() and backend != self.tts.get_backend():
            self.tts.set_backend(backend)
            self.config["tts_backend"] = backend
            save_config(self.config)
            self._emit("voices", self.tts_voices())
            self.tts_refresh_voices()

    def _tts_apply_pending_voice_if_idle(self) -> None:
        if self._tts_voice_pending and not self._tts_busy and not self.tts.is_speaking():
            self._tts_voice_pending = False
            self._tts_apply_voice_entry(voices_mod.active_voice(self.config))

    def tts_set_rate(self, value: int) -> None:
        self.config["tts_rate_percent"] = max(50, min(150, int(value)))
        save_config(self.config)

    def tts_set_volume(self, value: int) -> None:
        self.config["tts_volume"] = max(0, min(100, int(value)))
        save_config(self.config)
