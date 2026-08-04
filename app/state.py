"""Thread-safe shared state management for WaveScribe.

Provides AppState dataclass with thread-safe access via threading.Lock
and event-driven communication via queue.Queue.
"""

import queue
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AppStatus(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    ERROR = "error"


class AppMode(Enum):
    LOCAL = "local"
    CLOUD = "cloud"


class LocalModelStatus(Enum):
    """Status of the local Whisper model installation."""
    PACKAGE_MISSING = "package_missing"
    INSTALLING = "installing"
    MODEL_LOADING = "model_loading"
    READY = "ready"
    ERROR = "error"


@dataclass
class AppState:
    """Thread-safe application state.

    All state mutations go through the lock to ensure safe access
    from GUI, hotkey, audio, and worker threads.
    """

    status: AppStatus = AppStatus.IDLE
    mode: AppMode = AppMode.CLOUD
    api_key: str = ""
    model_size: str = "base"
    punctuate_speech: bool = True
    auto_capitalize: bool = True
    numbers_as_digits: bool = False
    local_language: str = "en"  # "en" for English, "auto" for auto-detect
    local_model_status: LocalModelStatus = LocalModelStatus.PACKAGE_MISSING
    last_transcription: str = ""
    error_message: str = ""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ── Status ──

    def get_status(self) -> AppStatus:
        with self._lock:
            return self.status

    def set_status(self, status: AppStatus) -> None:
        with self._lock:
            self.status = status

    # ── Mode ──

    def get_mode(self) -> AppMode:
        with self._lock:
            return self.mode

    def set_mode(self, mode: AppMode) -> None:
        with self._lock:
            self.mode = mode

    # ── API Key ──

    def get_api_key(self) -> str:
        with self._lock:
            return self.api_key

    def set_api_key(self, key: str) -> None:
        with self._lock:
            self.api_key = key

    # ── Model Size ──

    def get_model_size(self) -> str:
        with self._lock:
            return self.model_size

    def set_model_size(self, size: str) -> None:
        with self._lock:
            self.model_size = size

    # ── Formatting Options ──

    def get_punctuate_speech(self) -> bool:
        with self._lock:
            return self.punctuate_speech

    def set_punctuate_speech(self, enabled: bool) -> None:
        with self._lock:
            self.punctuate_speech = enabled

    def get_auto_capitalize(self) -> bool:
        with self._lock:
            return self.auto_capitalize

    def set_auto_capitalize(self, enabled: bool) -> None:
        with self._lock:
            self.auto_capitalize = enabled

    def get_numbers_as_digits(self) -> bool:
        with self._lock:
            return self.numbers_as_digits

    def set_numbers_as_digits(self, enabled: bool) -> None:
        with self._lock:
            self.numbers_as_digits = enabled

    # ── Local Language ──

    def get_local_language(self) -> str:
        with self._lock:
            return self.local_language

    def set_local_language(self, lang: str) -> None:
        with self._lock:
            self.local_language = lang

    # ── Local Model Status ──

    def get_local_model_status(self) -> LocalModelStatus:
        with self._lock:
            return self.local_model_status

    def set_local_model_status(self, status: LocalModelStatus) -> None:
        with self._lock:
            self.local_model_status = status

    # ── Transcription ──

    def get_last_transcription(self) -> str:
        with self._lock:
            return self.last_transcription

    def set_last_transcription(self, text: str) -> None:
        with self._lock:
            self.last_transcription = text

    # ── Error ──

    def get_error_message(self) -> str:
        with self._lock:
            return self.error_message

    def set_error_message(self, msg: str) -> None:
        with self._lock:
            self.error_message = msg

    def clear_error(self) -> None:
        with self._lock:
            self.error_message = ""
            if self.status == AppStatus.ERROR:
                self.status = AppStatus.IDLE

    # ── Helper ──

    def get_status_text(self) -> str:
        """Return a human-readable status string."""
        status = self.get_status()
        return {
            AppStatus.IDLE: "Idle",
            AppStatus.RECORDING: "Recording...",
            AppStatus.TRANSCRIBING: "Transcribing...",
            AppStatus.ERROR: f"Error: {self.get_error_message()}",
        }[status]


# ── Event Queue ──

EventQueue = queue.Queue


def create_event_queue() -> EventQueue:
    """Create a new event queue for thread communication."""
    return queue.Queue()
