"""Voice rotation helpers for Readout (pure functions, unit-tested).

Readout keeps an ordered **voice list** in the config::

    "voice_list": [
        {"backend": "edge",   "voice": "en-US-AriaNeural"},      # index 0 = main voice
        {"backend": "edge",   "voice": "es-ES-ElviraNeural"},    # secondary voices…
        {"backend": "system", "voice": ""},                      # "" = Windows default
    ]

``active_voice_index`` points at the voice that will be used for the next
read. A single swap button / hotkey advances that index (wrapping around),
switching the engine backend automatically when a voice belongs to the
other engine. All functions here are pure — they never touch the GUI,
the clipboard, or the engines — so the rotation logic is easy to test.
"""

from typing import Any, Dict, List, Optional

# An entry in the voice list: {"backend": "system"|"edge", "voice": str}.
VoiceEntry = Dict[str, str]


def entry_display(entry: Optional[VoiceEntry]) -> str:
    """Human-readable label for an entry, e.g. ``en-US-AriaNeural (Edge)``."""
    if not entry:
        return "(none)"
    name = entry.get("voice") or "(System default)"
    engine = "Windows" if entry.get("backend") == "system" else "Edge"
    return f"{name} ({engine})"


def clamp_index(voice_list: List[VoiceEntry], index: Optional[int]) -> int:
    """Clamp ``index`` into the valid range of ``voice_list`` (0 when empty)."""
    if not voice_list:
        return 0
    try:
        idx = int(index or 0)
    except (TypeError, ValueError):
        idx = 0
    return max(0, min(idx, len(voice_list) - 1))


def next_voice_index(voice_list: List[VoiceEntry], index: Optional[int]) -> int:
    """The index after ``index`` in the rotation (wraps to 0; 0 if ≤1 entry)."""
    if len(voice_list) <= 1:
        return 0
    return (clamp_index(voice_list, index) + 1) % len(voice_list)


def migrate_voice_list(config: Dict[str, Any]) -> bool:
    """Seed ``voice_list`` from the legacy per-engine voice keys when empty.

    Older configs stored the voice as ``system_voice`` / ``edge_voice``
    (one per engine). When no rotation exists yet, seed it with the voice
    that was selected for the configured backend so nothing is lost.
    Returns True when the config changed.
    """
    if config.get("voice_list"):
        return False
    backend = config.get("backend", "system")
    key = "system_voice" if backend == "system" else "edge_voice"
    config["voice_list"] = [{"backend": backend, "voice": config.get(key, "")}]
    config["active_voice_index"] = 0
    return True


def active_voice(config: Dict[str, Any]) -> VoiceEntry:
    """The entry the next read will use.

    Falls back to the legacy per-engine key when no rotation exists yet.
    """
    vl: List[VoiceEntry] = config.get("voice_list") or []
    if not vl:
        backend = config.get("backend", "system")
        key = "system_voice" if backend == "system" else "edge_voice"
        return {"backend": backend, "voice": config.get(key, "")}
    idx = clamp_index(vl, config.get("active_voice_index", 0))
    return vl[idx]


def set_main_voice(config: Dict[str, Any], backend: str, voice: str) -> bool:
    """Make ``(backend, voice)`` the main voice (index 0).

    The previous main voice is kept as the first secondary, and any other
    copy of the new main voice is removed from the rotation (no duplicates).
    The active index resets to the new main. Returns True when the list
    actually changed.
    """
    vl: List[VoiceEntry] = config.setdefault("voice_list", [])
    if not vl:
        vl.append({"backend": backend, "voice": voice})
        config["active_voice_index"] = 0
        return True
    old = vl[0]
    if old.get("backend") == backend and old.get("voice") == voice:
        config["active_voice_index"] = 0
        return False
    # Drop other copies of the new main from the rotation…
    remaining = [
        e for e in vl[1:]
        if not (e.get("backend") == backend and e.get("voice") == voice)
    ]
    # …keep the old main around as a secondary (unless it IS the new main).
    if not (old.get("backend") == backend and old.get("voice") == voice):
        remaining.insert(0, old)
    vl[:] = [{"backend": backend, "voice": voice}] + remaining
    config["active_voice_index"] = 0
    return True


def add_secondary(config: Dict[str, Any], backend: str, voice: str) -> bool:
    """Append ``(backend, voice)`` to the rotation. False when it's a duplicate."""
    vl: List[VoiceEntry] = config.setdefault("voice_list", [])
    if any(e.get("backend") == backend and e.get("voice") == voice for e in vl):
        return False
    vl.append({"backend": backend, "voice": voice})
    return True


def remove_entry(config: Dict[str, Any], index: int) -> bool:
    """Remove the entry at ``index`` (the last entry can't be removed).

    Returns True when removed; the active index is re-clamped.
    """
    vl: List[VoiceEntry] = config.get("voice_list") or []
    if len(vl) <= 1 or not (0 <= index < len(vl)):
        return False
    del vl[index]
    config["active_voice_index"] = clamp_index(vl, config.get("active_voice_index", 0))
    return True


def prune_entries(
    voice_list: List[VoiceEntry], backend: str, valid_voices: set
) -> List[VoiceEntry]:
    """Drop entries of ``backend`` whose voice is no longer available.

    Entries belonging to other backends are left untouched (their voices
    can only be validated when that backend is active).
    """
    return [
        e for e in voice_list
        if e.get("backend") != backend or e.get("voice") in valid_voices
    ]


def filter_entries(
    entries: List[tuple],
    query: str,
    always_keep: Optional[List[str]] = None,
) -> List[tuple]:
    """Filter (display, value) voice pairs by a case-insensitive query.

    ``always_keep`` lists display strings that must stay in the result (e.g.
    the currently active voice) so the current selection never disappears
    while the user types. An empty query returns all entries unchanged.
    """
    query = (query or "").strip().lower()
    if not query:
        return list(entries)
    kept = [
        e for e in entries
        if query in e[0].lower() or query in e[1].lower()
    ]
    existing = {d for d, _ in kept}
    for display in always_keep or []:
        if display and display not in existing:
            match = next((e for e in entries if e[0] == display), None)
            if match:
                kept.append(match)
    return kept


def snap_index(
    voice_list: List[VoiceEntry], backend: str, current_index: Optional[int]
) -> int:
    """Index of the first entry on ``backend``, else ``current_index`` clamped."""
    if not voice_list:
        return 0
    for i, e in enumerate(voice_list):
        if e.get("backend") == backend:
            return i
    return clamp_index(voice_list, current_index)


def ensure_entry(
    voice_list: List[VoiceEntry], backend: str, fallback_voice: str
) -> int:
    """Append ``{backend, fallback_voice}`` when no entry uses ``backend``.

    Returns the index of the (existing or new) entry for ``backend``.
    """
    for i, e in enumerate(voice_list):
        if e.get("backend") == backend:
            return i
    voice_list.append({"backend": backend, "voice": fallback_voice})
    return len(voice_list) - 1
