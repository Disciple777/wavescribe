"""Transcription history persistence for WaveScribe.

Stores completed dictations as timestamped entries in a JSON file
inside the app config directory (``%APPDATA%\\WaveScribe\\history.json``).

All access is thread-safe via a lock, so the transcription worker
thread can add entries while the GUI reads them.
"""

import json
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config import get_config_dir

HISTORY_FILENAME = "history.json"
MAX_ENTRIES = 200  # cap so the file never grows without bound


class HistoryManager:
    """Thread-safe store of recent transcriptions."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path or os.path.join(get_config_dir(), HISTORY_FILENAME)
        self._lock = threading.Lock()
        self._entries: List[Dict[str, Any]] = []
        self._load()

    # ── Persistence ──

    def _load(self) -> None:
        """Load entries from disk (if the file exists and is valid)."""
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entries = data.get("entries", []) if isinstance(data, dict) else []
                self._entries = [
                    e for e in entries
                    if isinstance(e, dict) and isinstance(e.get("text"), str) and e["text"].strip()
                ][:MAX_ENTRIES]
        except (json.JSONDecodeError, OSError):
            self._entries = []

    def _save(self) -> None:
        """Write entries to disk atomically-ish (temp file + rename)."""
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"entries": self._entries}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # history is best-effort — never crash the app

    # ── Public API ──

    def add(self, text: str) -> None:
        """Append a new transcription entry (newest first). No-ops on empty text."""
        if not text or not text.strip():
            return
        entry: Dict[str, Any] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "text": text.strip(),
        }
        with self._lock:
            self._entries.insert(0, entry)
            del self._entries[MAX_ENTRIES:]
            self._save()

    def get_all(self) -> List[Dict[str, Any]]:
        """Return a copy of all entries, newest first."""
        with self._lock:
            return list(self._entries)

    def get(self, index: int) -> Optional[Dict[str, Any]]:
        """Return the entry at ``index`` (0 = newest) or None."""
        with self._lock:
            if 0 <= index < len(self._entries):
                return dict(self._entries[index])
            return None

    def delete(self, index: int) -> bool:
        """Remove the entry at ``index`` (0 = newest)."""
        with self._lock:
            if 0 <= index < len(self._entries):
                del self._entries[index]
                self._save()
                return True
            return False

    def count(self) -> int:
        """Number of stored entries."""
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        """Remove all entries and rewrite the file."""
        with self._lock:
            self._entries = []
            self._save()

    @property
    def path(self) -> str:
        """Path of the history file on disk."""
        return self._path
