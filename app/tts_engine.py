"""TTS engine abstraction and manager for WaveScribe.

The manager owns one instance of every engine (System + Edge) and routes
all calls to the backend selected in Settings. Backends share a simple
interface: ``list_voices()``, ``speak()``, ``stop()``, ``is_speaking()``.
"""

import threading
from typing import List, Optional

from app.tts_errors import TTSError
from app.system_tts import SystemTTSEngine, VoiceInfo
from app.edge_tts import EdgeTTSEngine, edge_available

__all__ = ["EngineManager", "TTSError", "VoiceInfo", "available_backends"]

# name → engine class. New engines register here.
_BACKENDS = {
    "system": SystemTTSEngine,
    "edge": EdgeTTSEngine,
}


def available_backends() -> List[str]:
    """Backends that can be selected right now (edge needs its package)."""
    return ["system"] + (["edge"] if edge_available() else [])


class EngineManager:
    """Owns every engine instance and delegates to the active backend."""

    def __init__(self, backend: str = "system") -> None:
        self._engines = {name: cls() for name, cls in _BACKENDS.items()}
        self._backend = backend if backend in self._engines else "system"
        self._on_done = None
        self._lock = threading.Lock()

    # ── Backend selection ──

    def get_backend(self) -> str:
        with self._lock:
            return self._backend

    def set_backend(self, name: str) -> None:
        """Switch the active backend (unknown names fall back to 'system')."""
        if name not in self._engines:
            name = "system"
        self.stop()
        with self._lock:
            self._backend = name

    def engine(self, backend: Optional[str] = None):
        """Return the engine instance for ``backend`` (or the active one)."""
        with self._lock:
            name = backend or self._backend
        return self._engines[name]

    # ── Delegation ──

    def list_voices(self, backend: Optional[str] = None) -> List[VoiceInfo]:
        return self.engine(backend).list_voices()

    def speak(
        self,
        text: str,
        voice: str = "",
        rate_percent: int = 100,
        volume: int = 100,
        on_done=None,
        backend: Optional[str] = None,
    ) -> None:
        """Speak ``text`` with an engine. Blocks — call from a worker thread."""
        if not text:
            return
        with self._lock:
            self._on_done = on_done
        engine = self.engine(backend)
        try:
            engine.speak(
                text,
                voice=voice,
                rate_percent=rate_percent,
                volume=volume,
                on_done=self._notify_done,
            )
        except Exception as exc:
            with self._lock:
                self._on_done = None
            if isinstance(exc, TTSError):
                raise
            raise TTSError(str(exc)) from exc

    def _notify_done(self) -> None:
        with self._lock:
            callback = self._on_done
            self._on_done = None
        if callback:
            try:
                callback()
            except Exception:
                pass

    def stop(self) -> None:
        """Stop the active engine immediately."""
        self.engine().stop()

    def stop_all(self) -> None:
        """Stop every engine."""
        for engine in self._engines.values():
            engine.stop()

    def is_speaking(self) -> bool:
        """True when any engine is currently producing audio."""
        for engine in self._engines.values():
            if engine.is_speaking():
                return True
        return False
