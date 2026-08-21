"""Tests for readout/core/voices.py — the voice rotation helpers (pure)."""

from readout.core import voices


def test_migrate_seeds_from_legacy_system_voice():
    config = {"backend": "system", "system_voice": "", "edge_voice": "x"}
    assert voices.migrate_voice_list(config) is True
    assert config["voice_list"] == [{"backend": "system", "voice": ""}]
    assert config["active_voice_index"] == 0


def test_migrate_seeds_from_legacy_edge_voice():
    config = {"backend": "edge", "edge_voice": "es-ES-ElviraNeural", "system_voice": ""}
    assert voices.migrate_voice_list(config) is True
    assert config["voice_list"] == [{"backend": "edge", "voice": "es-ES-ElviraNeural"}]


def test_migrate_leaves_existing_rotation_alone():
    config = {"voice_list": [{"backend": "system", "voice": "A"}], "active_voice_index": 1}
    assert voices.migrate_voice_list(config) is False
    assert config["voice_list"] == [{"backend": "system", "voice": "A"}]


def test_clamp_index():
    vl = [{"backend": "system", "voice": ""}]
    assert voices.clamp_index(vl, None) == 0
    assert voices.clamp_index(vl, -3) == 0
    assert voices.clamp_index(vl, 99) == 0
    assert voices.clamp_index([], 5) == 0
    vl3 = [{"backend": "system", "voice": ""}] * 3
    assert voices.clamp_index(vl3, 5) == 2


def test_next_voice_index_wraps():
    vl = [{"backend": "system", "voice": ""}] * 3
    assert voices.next_voice_index(vl, 0) == 1
    assert voices.next_voice_index(vl, 1) == 2
    assert voices.next_voice_index(vl, 2) == 0
    assert voices.next_voice_index(vl, 99) == 0  # clamps then advances


def test_next_voice_index_single_entry_is_static():
    vl = [{"backend": "system", "voice": ""}]
    assert voices.next_voice_index(vl, 0) == 0


def test_active_voice_uses_index():
    config = {
        "voice_list": [
            {"backend": "edge", "voice": "en-US-AriaNeural"},
            {"backend": "edge", "voice": "es-ES-ElviraNeural"},
        ],
        "active_voice_index": 1,
    }
    assert voices.active_voice(config)["voice"] == "es-ES-ElviraNeural"
    config["active_voice_index"] = 99
    assert voices.active_voice(config)["voice"] == "es-ES-ElviraNeural"  # clamped


def test_active_voice_falls_back_to_legacy():
    config = {"backend": "edge", "edge_voice": "es-MX-DaliaNeural"}
    assert voices.active_voice(config) == {"backend": "edge", "voice": "es-MX-DaliaNeural"}


def test_set_main_voice_promotes_and_demotes():
    config = {
        "voice_list": [
            {"backend": "edge", "voice": "en-US-AriaNeural"},
            {"backend": "edge", "voice": "es-ES-ElviraNeural"},
        ],
        "active_voice_index": 1,
    }
    voices.set_main_voice(config, "edge", "es-ES-ElviraNeural")
    assert config["voice_list"][0] == {"backend": "edge", "voice": "es-ES-ElviraNeural"}
    # The old main was demoted to the first secondary; no duplicates remain.
    assert config["voice_list"][1] == {"backend": "edge", "voice": "en-US-AriaNeural"}
    assert len(config["voice_list"]) == 2
    assert config["active_voice_index"] == 0


def test_set_main_voice_new_voice_keeps_old_as_secondary():
    config = {
        "voice_list": [{"backend": "edge", "voice": "en-US-AriaNeural"}],
        "active_voice_index": 0,
    }
    voices.set_main_voice(config, "edge", "en-GB-SoniaNeural")
    assert config["voice_list"] == [
        {"backend": "edge", "voice": "en-GB-SoniaNeural"},
        {"backend": "edge", "voice": "en-US-AriaNeural"},
    ]


def test_set_main_voice_same_voice_is_noop():
    config = {
        "voice_list": [{"backend": "system", "voice": ""}],
        "active_voice_index": 0,
    }
    assert voices.set_main_voice(config, "system", "") is False
    assert config["voice_list"] == [{"backend": "system", "voice": ""}]


def test_add_secondary_appends_and_rejects_duplicates():
    config = {"voice_list": [{"backend": "edge", "voice": "en-US-AriaNeural"}]}
    assert voices.add_secondary(config, "edge", "es-ES-ElviraNeural") is True
    assert len(config["voice_list"]) == 2
    assert voices.add_secondary(config, "edge", "es-ES-ElviraNeural") is False
    assert len(config["voice_list"]) == 2
    # same name on a different engine is a different voice
    assert voices.add_secondary(config, "system", "es-ES-ElviraNeural") is True


def test_remove_entry_keeps_at_least_one():
    config = {"voice_list": [{"backend": "edge", "voice": "A"}], "active_voice_index": 0}
    assert voices.remove_entry(config, 0) is False
    assert len(config["voice_list"]) == 1

    config = {
        "voice_list": [
            {"backend": "edge", "voice": "A"},
            {"backend": "edge", "voice": "B"},
            {"backend": "system", "voice": ""},
        ],
        "active_voice_index": 2,
    }
    assert voices.remove_entry(config, 1) is True
    assert config["voice_list"] == [
        {"backend": "edge", "voice": "A"},
        {"backend": "system", "voice": ""},
    ]
    assert config["active_voice_index"] == 1  # re-clamped


def test_prune_entries_only_touches_matching_backend():
    vl = [
        {"backend": "edge", "voice": "en-US-AriaNeural"},
        {"backend": "edge", "voice": "dead-voice"},
        {"backend": "system", "voice": ""},
    ]
    pruned = voices.prune_entries(vl, "edge", {"en-US-AriaNeural"})
    assert pruned == [
        {"backend": "edge", "voice": "en-US-AriaNeural"},
        {"backend": "system", "voice": ""},
    ]


def test_snap_index_prefers_matching_backend():
    vl = [
        {"backend": "edge", "voice": "A"},
        {"backend": "system", "voice": ""},
        {"backend": "edge", "voice": "B"},
    ]
    assert voices.snap_index(vl, "system", 0) == 1
    assert voices.snap_index(vl, "edge", 0) == 0
    # no match → current index (clamped)
    vl2 = [{"backend": "edge", "voice": "A"}]
    assert voices.snap_index(vl2, "system", 0) == 0


def test_ensure_entry_appends_when_missing():
    vl = [{"backend": "edge", "voice": "A"}]
    assert voices.ensure_entry(vl, "system", "") == 1
    assert vl[1] == {"backend": "system", "voice": ""}
    assert voices.ensure_entry(vl, "system", "") == 1  # existing → same index


def test_filter_entries_empty_query_returns_all():
    entries = [("en-US-AriaNeural", "en-US-AriaNeural"), ("es-ES-ElviraNeural", "es-ES-ElviraNeural")]
    assert voices.filter_entries(entries, "") == entries
    assert voices.filter_entries(entries, "   ") == entries


def test_filter_entries_matches_display_and_value():
    entries = [("en-US-AriaNeural", "en-US-AriaNeural"), ("es-ES-ElviraNeural", "es-ES-ElviraNeural")]
    assert voices.filter_entries(entries, "aria") == [("en-US-AriaNeural", "en-US-AriaNeural")]
    assert voices.filter_entries(entries, "elvira") == [("es-ES-ElviraNeural", "es-ES-ElviraNeural")]
    assert voices.filter_entries(entries, "zzz") == []


def test_filter_entries_is_case_insensitive():
    entries = [("en-US-AriaNeural", "en-US-AriaNeural")]
    assert voices.filter_entries(entries, "ARIA") == entries


def test_filter_entries_always_keep_appends_non_matches():
    entries = [("en-US-AriaNeural", "en-US-AriaNeural"), ("es-ES-ElviraNeural", "es-ES-ElviraNeural")]
    kept = voices.filter_entries(entries, "elvira", always_keep=["en-US-AriaNeural"])
    assert [d for d, _ in kept] == ["es-ES-ElviraNeural", "en-US-AriaNeural"]
    # A display that already matches is never duplicated.
    kept2 = voices.filter_entries(entries, "aria", always_keep=["en-US-AriaNeural"])
    assert len(kept2) == 1


def test_filter_entries_ignores_unknown_always_keep():
    entries = [("en-US-AriaNeural", "en-US-AriaNeural")]
    assert voices.filter_entries(entries, "aria", always_keep=["not-a-voice"]) == entries


def test_entry_display():
    assert voices.entry_display({"backend": "edge", "voice": "es-ES-ElviraNeural"}) == \
        "es-ES-ElviraNeural (Edge)"
    assert voices.entry_display({"backend": "system", "voice": ""}) == \
        "(System default) (Windows)"
    assert voices.entry_display(None) == "(none)"
