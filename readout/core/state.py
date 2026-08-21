"""Tiny thread-safe state + event queue for Readout.

Follows the same pattern as WaveScribe's app/state.py: state is guarded
by a lock and background threads communicate with the GUI through a
thread-safe queue.Queue.
"""

import queue
import threading
from dataclasses import dataclass, field
from enum import Enum


class ReadStatus(Enum):
    IDLE = "idle"
    READING = "reading"
    ERROR = "error"


@dataclass
class ReadoutState:
    """Thread-safe application state."""

    status: ReadStatus = ReadStatus.IDLE
    error_message: str = ""
    last_text: str = ""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ── Status ──

    def get_status(self) -> ReadStatus:
        with self._lock:
            return self.status

    def set_status(self, status: ReadStatus) -> None:
        with self._lock:
            self.status = status

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
            if self.status == ReadStatus.ERROR:
                self.status = ReadStatus.IDLE

    # ── Last read text ──

    def get_last_text(self) -> str:
        with self._lock:
            return self.last_text

    def set_last_text(self, text: str) -> None:
        with self._lock:
            self.last_text = text


# ── Event Queue ──

EventQueue = queue.Queue


def create_event_queue() -> EventQueue:
    """Create a new event queue for thread ↔ GUI communication."""
    return queue.Queue()
