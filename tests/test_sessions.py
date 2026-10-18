"""Tests for listening-session persistence and rendering."""

from app.sessions import SessionStore, render_session_text, render_session_markdown


def test_create_add_and_reload(tmp_path) -> None:
    store = SessionStore(str(tmp_path))
    s = store.create("Game test")
    assert s["name"] == "Game test"
    store.add_entry(s["id"], "The jump feels floaty.")
    store.add_entry(s["id"], "  ")  # ignored
    store.add_entry(s["id"], "Add a pause menu.", source="dictation")

    reloaded = SessionStore(str(tmp_path)).get(s["id"])
    assert [e["text"] for e in reloaded["entries"]] == ["The jump feels floaty.", "Add a pause menu."]
    assert reloaded["entries"][1]["source"] == "dictation"


def test_default_name_and_listing_order(tmp_path) -> None:
    store = SessionStore(str(tmp_path))
    a = store.create()
    assert a["name"].startswith("Session · ")
    b = store.create("Second")
    store.add_entry(a["id"], "newest activity")
    listing = store.list_sessions()
    assert listing[0]["id"] == a["id"]
    assert listing[0]["count"] == 1 and listing[0]["preview"] == "newest activity"
    assert {x["id"] for x in listing} == {a["id"], b["id"]}


def test_edit_delete_rename(tmp_path) -> None:
    store = SessionStore(str(tmp_path))
    s = store.create("x")
    e1 = store.add_entry(s["id"], "one")
    e2 = store.add_entry(s["id"], "two")
    assert store.update_entry(s["id"], e1["id"], "uno")
    assert store.delete_entry(s["id"], e2["id"])
    assert store.update_entry(s["id"], e1["id"], "") and store.get(s["id"])["entries"] == []
    assert store.rename(s["id"], "Renamed")
    assert not store.rename(s["id"], "  ")
    assert store.get(s["id"])["name"] == "Renamed"
    assert store.delete(s["id"]) and store.get(s["id"]) is None
    assert SessionStore(str(tmp_path)).list_sessions() == []


def test_render_styles() -> None:
    session = {"name": "S", "created": "2026-10-17 10:00:00", "entries": [
        {"text": "First note.", "time": "2026-10-17 10:01:00"},
        {"text": "Second note.", "time": "2026-10-17 10:02:30"},
    ]}
    assert render_session_text(session, "list") == "- First note.\n- Second note."
    assert render_session_text(session, "numbered") == "1. First note.\n2. Second note."
    assert render_session_text(session, "paragraphs") == "First note.\n\nSecond note."
    assert render_session_text(session, "list", timestamps=True).startswith("- [10:01] First")
    md = render_session_markdown(session)
    assert md.startswith("# S") and "- [10:02] Second note." in md
