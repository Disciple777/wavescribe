"""Tests for the transcription history manager."""

from app.history import HistoryManager, MAX_ENTRIES


def test_add_and_get_entries(tmp_path):
    hm = HistoryManager(path=str(tmp_path / "history.json"))
    assert hm.count() == 0

    hm.add("First dictation.")
    hm.add("Second dictation.")

    entries = hm.get_all()
    assert len(entries) == 2
    # Newest first
    assert entries[0]["text"] == "Second dictation."
    assert entries[1]["text"] == "First dictation."
    assert "timestamp" in entries[0]


def test_add_ignores_empty_text(tmp_path):
    hm = HistoryManager(path=str(tmp_path / "history.json"))
    hm.add("")
    hm.add("   ")
    hm.add(None)  # type: ignore[arg-type]
    assert hm.count() == 0


def test_persists_across_reload(tmp_path):
    path = str(tmp_path / "history.json")
    hm = HistoryManager(path=path)
    hm.add("Hello world.")

    hm2 = HistoryManager(path=path)
    entries = hm2.get_all()
    assert len(entries) == 1
    assert entries[0]["text"] == "Hello world."


def test_clear_removes_entries(tmp_path):
    hm = HistoryManager(path=str(tmp_path / "history.json"))
    hm.add("Entry")
    assert hm.count() == 1

    hm.clear()
    assert hm.count() == 0
    assert hm.get_all() == []


def test_max_entries_capped(tmp_path):
    hm = HistoryManager(path=str(tmp_path / "history.json"))
    for i in range(MAX_ENTRIES + 50):
        hm.add(f"Entry {i}")
    assert hm.count() == MAX_ENTRIES
    # Newest entry is kept, oldest is dropped
    assert hm.get_all()[0]["text"] == f"Entry {MAX_ENTRIES + 49}"


def test_get_by_index(tmp_path):
    hm = HistoryManager(path=str(tmp_path / "history.json"))
    hm.add("Oldest")
    hm.add("Newest")

    assert hm.get(0)["text"] == "Newest"
    assert hm.get(1)["text"] == "Oldest"
    assert hm.get(99) is None


def test_load_corrupt_file_is_ignored(tmp_path):
    path = str(tmp_path / "history.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not valid json")

    hm = HistoryManager(path=path)
    assert hm.count() == 0
    # A new add still works and rewrites the file cleanly
    hm.add("Works fine.")
    assert hm.count() == 1
    assert hm.get_all()[0]["text"] == "Works fine."
