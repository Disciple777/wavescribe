"""Edge neural voices (free, online) via the ``edge-tts`` package.

``edge-tts`` talks to Microsoft Edge's neural TTS endpoint — no API key,
no account, but it **does** need an internet connection. Audio is
synthesised to a temp MP3 and played through Windows' built-in MCI
(``mciSendString``), so no extra media libraries are required.

This module degrades gracefully: if ``edge-tts`` isn't installed, voice
listing falls back to a curated list and ``speak()`` raises a clear
``TTSError`` telling the user how to install it.
"""

import asyncio
import ctypes
import os
import tempfile
import threading
import time
from typing import List, Optional

from app.tts_errors import TTSError
from app.system_tts import VoiceInfo

try:
    import edge_tts
    _EDGE_AVAILABLE = True
except Exception:  # pragma: no cover — ImportError/OSError
    edge_tts = None  # type: ignore[assignment]
    _EDGE_AVAILABLE = False

# Curated fallback list used when the live voice list can't be fetched.
_FALLBACK_VOICES = [
    "en-US-AriaNeural", "en-US-JennyNeural", "en-US-GuyNeural",
    "en-US-EmmaMultilingualNeural", "en-GB-SoniaNeural", "en-GB-RyanNeural",
    "es-MX-DaliaNeural", "es-MX-JorgeNeural", "es-ES-ElviraNeural", "es-ES-AlvaroNeural",
    "fr-FR-DeniseNeural", "de-DE-KatjaNeural", "pt-BR-FranciscaNeural",
    "it-IT-ElsaNeural", "ja-JP-NanamiNeural", "zh-CN-XiaoxiaoNeural",
]

_VOICES_TTL_S = 3600  # re-fetch the voice list at most once per hour


def edge_available() -> bool:
    """True when the optional ``edge-tts`` package is installed."""
    return _EDGE_AVAILABLE


def edge_rate(rate_percent: int) -> str:
    """Map a 50..150 speed percentage to edge-tts' rate format (e.g. ``+0%``)."""
    return f"{rate_percent - 100:+d}%"


def edge_volume(volume: int) -> str:
    """Map a 0..100 volume to edge-tts' volume format (0 → ``-100%``, 100 → ``+0%``)."""
    return f"{volume - 100:+d}%"


def _fallback_voices() -> List[VoiceInfo]:
    return [
        VoiceInfo(name=v, language="-".join(v.split("-")[:2]))
        for v in _FALLBACK_VOICES
    ]


def _mci_send(cmd: str, buf=None, buf_len: int = 0) -> int:
    """Send an MCI string command; return the error code (0 = success)."""
    try:
        return int(ctypes.windll.winmm.mciSendStringW(cmd, buf, buf_len, 0))
    except Exception:
        return 1


class _MciPlayer:
    """Plays MP3 files through Windows MCI (zero extra dependencies).

    Playback runs in a background thread that polls ``status … mode`` rather
    than using ``play … wait``. ``wait`` blocks the MCI subsystem for that
    device, so a ``stop`` sent from another thread may not interrupt it — the
    polled loop checks a stop ``Event`` every ~100ms, making ``stop()`` deterministic.
    """

    def __init__(self) -> None:
        self._alias = "wavescribe_audio"
        self._thread: Optional[threading.Thread] = None
        self._path: Optional[str] = None
        self._on_done = None
        self._stop_event: Optional[threading.Event] = None
        self._lock = threading.Lock()
        self._device_lock = threading.Lock()

    def play(self, path: str, on_done=None) -> None:
        """Start playing ``path`` in a background thread."""
        self.stop()
        stop_event = threading.Event()
        with self._lock:
            self._stop_event = stop_event
            self._path = path
            self._on_done = on_done
            self._thread = threading.Thread(
                target=self._play_blocking, args=(stop_event,),
                daemon=True, name="wavescribe-playback",
            )
        self._thread.start()

    def _play_blocking(self, stop_event: threading.Event) -> None:
        with self._device_lock:
            alias = self._alias
            with self._lock:
                path = self._path
            try:
                err = _mci_send(f'open "{path}" type mpegvideo alias {alias}')
                if err != 0:
                    err = _mci_send(f'open "{path}" alias {alias}')
                if err != 0:
                    return
                _mci_send(f"play {alias}")

                buf = ctypes.create_unicode_buffer(128)
                started = False
                deadline = time.time() + 5.0
                while not stop_event.is_set() and not started:
                    _mci_send(f"status {alias} mode", buf, len(buf))
                    mode = buf.value.strip().lower()
                    if mode == "playing":
                        started = True
                    elif not mode or time.time() > deadline:
                        break
                    time.sleep(0.05)
                while started and not stop_event.is_set():
                    _mci_send(f"status {alias} mode", buf, len(buf))
                    if buf.value.strip().lower() != "playing":
                        break
                    time.sleep(0.1)
                if stop_event.is_set():
                    _mci_send(f"stop {alias}")
            except Exception:
                pass
            finally:
                _mci_send(f"close {alias}")
                try:
                    if path and os.path.exists(path):
                        os.remove(path)
                except OSError:
                    pass
                with self._lock:
                    owned = self._path is path
                    on_done = self._on_done if owned else None
                    if owned:
                        self._path = None
                        self._on_done = None
                if on_done:
                    try:
                        on_done()
                    except Exception:
                        pass

    def stop(self) -> None:
        """Stop playback and release the MCI device."""
        with self._lock:
            stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()
        _mci_send(f"stop {self._alias}")
        _mci_send(f"close {self._alias}")
        with self._lock:
            self._on_done = None

    def is_playing(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()


class EdgeTTSEngine:
    """Speaks text with Microsoft Edge neural voices (online)."""

    name = "edge"
    display_name = "Edge neural (online)"

    def __init__(self) -> None:
        self._player = _MciPlayer()
        self._voices_cache: Optional[List[VoiceInfo]] = None
        self._voices_fetched_at = 0.0
        self._generation = 0
        self._gen_lock = threading.Lock()

    # ── Voices ──

    def list_voices(self, force_refresh: bool = False) -> List[VoiceInfo]:
        """Return the available Edge voices (cached for an hour, network fetch on first call)."""
        now = time.time()
        if (
            self._voices_cache is not None
            and not force_refresh
            and now - self._voices_fetched_at < _VOICES_TTL_S
        ):
            return self._voices_cache

        fetched = self._fetch_voices()
        if fetched:
            self._voices_cache = fetched
        elif self._voices_cache is None:
            self._voices_cache = _fallback_voices()
        self._voices_fetched_at = now
        return self._voices_cache

    @staticmethod
    def _fetch_voices() -> List[VoiceInfo]:
        if not _EDGE_AVAILABLE:
            return []
        try:
            raw = asyncio.run(edge_tts.list_voices())
        except Exception:
            return []
        voices: List[VoiceInfo] = []
        for item in raw or []:
            name = item.get("ShortName") or item.get("Name") or ""
            if not name:
                continue
            voices.append(
                VoiceInfo(
                    name=name,
                    gender=(item.get("Gender") or "").lower(),
                    language=item.get("Locale") or "",
                )
            )
        voices.sort(key=lambda v: (v.language, v.name))
        return voices

    # ── Speaking ──

    def speak(
        self,
        text: str,
        voice: str = "en-US-AriaNeural",
        rate_percent: int = 100,
        volume: int = 100,
        on_done=None,
    ) -> None:
        """Synthesise ``text`` to an MP3 and play it. Blocks until the MP3 is generated."""
        if not _EDGE_AVAILABLE:
            raise TTSError(
                "Edge neural voices need the optional 'edge-tts' package.\n"
                "Install it with:  pip install edge-tts\n"
                "The offline Windows voices are always available in Settings."
            )

        self.stop()

        with self._gen_lock:
            generation = self._generation

        mp3_path = os.path.join(
            tempfile.gettempdir(), f"wavescribe_{int(time.time() * 1000)}.mp3"
        )

        async def _synthesize() -> None:
            communicate = edge_tts.Communicate(
                text,
                voice,
                rate=edge_rate(rate_percent),
                volume=edge_volume(volume),
            )
            await communicate.save(mp3_path)

        try:
            asyncio.run(_synthesize())
        except Exception as exc:
            raise TTSError(
                f"Edge speech synthesis failed ({exc}). Check your internet connection."
            ) from exc

        if not os.path.exists(mp3_path):
            raise TTSError("Edge speech synthesis produced no audio. Try again.")

        with self._gen_lock:
            cancelled = generation != self._generation
        if cancelled:
            try:
                os.remove(mp3_path)
            except OSError:
                pass
            return

        self._player.play(mp3_path, on_done=on_done)

    def stop(self) -> None:
        with self._gen_lock:
            self._generation += 1
        self._player.stop()

    def is_speaking(self) -> bool:
        return self._player.is_playing()
