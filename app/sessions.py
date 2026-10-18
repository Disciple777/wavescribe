"""Listening sessions: a named, ordered collection of spoken notes.

A session gathers everything said while it is active — utterances picked
up by hands-free listening and push-to-talk dictations — into one text
instead of separate history entries. Typical use: narrating feedback
while live-testing an app or a game, then pasting the whole list into
a coding agent.

Each session is stored as ``%APPDATA%\\WaveScribe\\sessions\\<id>.json``.
All access is thread-safe.
"""

import json
import os
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config import get_config_dir

SESSIONS_DIRNAME = "sessions"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def default_session_name(when: Optional[datetime] = None) -> str:
    when = when or datetime.now()
    return f"Session · {when.strftime('%b %d, %H:%M').replace(' 0', ' ')}"


def render_session_text(session: Dict[str, Any], style: str = "list", timestamps: bool = False) -> str:
    """Join a session's notes into one text.

    ``style``: ``"list"`` → one ``- note`` bullet per line (great for agent
    prompts), ``"numbered"`` → ``1. note``, ``"paragraphs"`` → notes
    separated by blank lines, ``"plain"`` → notes joined by spaces.
    """
    notes = [e for e in session.get("entries", []) if e.get("text", "").strip()]
    lines: List[str] = []
    for i, entry in enumerate(notes, 1):
        text = entry["text"].strip()
        if timestamps and entry.get("time"):
            text = f"[{entry['time'][11:16]}] {text}"
        if style == "list":
            lines.append(f"- {text}")
        elif style == "numbered":
            lines.append(f"{i}. {text}")
        else:
            lines.append(text)
    if style == "paragraphs":
        return "\n\n".join(lines)
    if style == "plain":
        return " ".join(lines)
    return "\n".join(lines)


def render_session_markdown(session: Dict[str, Any]) -> str:
    header = f"# {session.get('name', 'Session')}\n\n_{session.get('created', '')}_\n\n"
    return header + render_session_text(session, "list", timestamps=True) + "\n"


class SessionStore:
    """Persisted listening sessions (one JSON file per session)."""

    def __init__(self, directory: Optional[str] = None) -> None:
        self._dir = directory or os.path.join(get_config_dir(), SESSIONS_DIRNAME)
        self._lock = threading.RLock()
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._load_all()

    @property
    def directory(self) -> str:
        return self._dir

    # ── Persistence ──

    def _path(self, session_id: str) -> str:
        return os.path.join(self._dir, f"{session_id}.json")

    def _load_all(self) -> None:
        if not os.path.isdir(self._dir):
            return
        for name in os.listdir(self._dir):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self._dir, name), "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("id") and isinstance(data.get("entries"), list):
                    self._sessions[data["id"]] = data
            except (OSError, json.JSONDecodeError):
                continue

    def _save(self, session: Dict[str, Any]) -> None:
        try:
            os.makedirs(self._dir, exist_ok=True)
            path = self._path(session["id"])
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(session, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError as exc:
            print(f"Warning: could not save session ({exc})")

    # ── Queries ──

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Summaries, most recently updated first."""
        with self._lock:
            items = []
            for s in self._sessions.values():
                notes = [e for e in s["entries"] if e.get("text", "").strip()]
                items.append({
                    "id": s["id"],
                    "name": s.get("name", ""),
                    "created": s.get("created", ""),
                    "updated": s.get("updated", ""),
                    "count": len(notes),
                    "preview": notes[-1]["text"][:90] if notes else "",
                })
        return sorted(items, key=lambda x: x["updated"], reverse=True)

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            s = self._sessions.get(session_id)
            return json.loads(json.dumps(s)) if s else None

    def summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Cheap ``{id, name, count}`` for status displays (no deep copy)."""
        with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return None
            return {"id": s["id"], "name": s.get("name", ""), "count": len(s["entries"])}

    # ── Mutations ──

    def create(self, name: Optional[str] = None) -> Dict[str, Any]:
        now = datetime.now()
        session = {
            "id": now.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4],
            "name": (name or "").strip() or default_session_name(now),
            "created": _now(),
            "updated": _now(),
            "entries": [],
        }
        with self._lock:
            self._sessions[session["id"]] = session
            self._save(session)
        return self.get(session["id"])  # type: ignore[return-value]

    def rename(self, session_id: str, name: str) -> bool:
        name = (name or "").strip()
        with self._lock:
            s = self._sessions.get(session_id)
            if not s or not name:
                return False
            s["name"] = name
            s["updated"] = _now()
            self._save(s)
            return True

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if self._sessions.pop(session_id, None) is None:
                return False
            try:
                os.remove(self._path(session_id))
            except OSError:
                pass
            return True

    def add_entry(self, session_id: str, text: str, source: str = "live") -> Optional[Dict[str, Any]]:
        text = (text or "").strip()
        with self._lock:
            s = self._sessions.get(session_id)
            if not s or not text:
                return None
            entry = {"id": uuid.uuid4().hex[:8], "time": _now(), "text": text, "source": source}
            s["entries"].append(entry)
            s["updated"] = entry["time"]
            self._save(s)
            return dict(entry)

    def update_entry(self, session_id: str, entry_id: str, text: str) -> bool:
        with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return False
            for e in s["entries"]:
                if e["id"] == entry_id:
                    text = (text or "").strip()
                    if not text:
                        s["entries"].remove(e)
                    else:
                        e["text"] = text
                    s["updated"] = _now()
                    self._save(s)
                    return True
            return False

    def delete_entry(self, session_id: str, entry_id: str) -> bool:
        return self.update_entry(session_id, entry_id, "")
