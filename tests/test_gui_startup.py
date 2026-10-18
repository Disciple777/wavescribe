"""Regression tests for the HTML UI wiring.

The view is HTML/JS (app/web) talking to ``app.webui.Bridge`` through
pywebview. These tests need no display: they check that every
``api.<method>(…)`` the JavaScript calls exists on the bridge, and that
the files the windows load are present.
"""
import os
import re

from app.webui import Bridge, WEB_DIR


def _js_api_calls() -> set:
    with open(os.path.join(WEB_DIR, "app.js"), encoding="utf-8") as f:
        source = f.read()
    return set(re.findall(r"\bapi\.(\w+)\(", source))


def test_every_js_api_call_exists_on_bridge() -> None:
    calls = _js_api_calls()
    assert len(calls) > 30  # sanity: the regex really found the calls
    missing = sorted(name for name in calls if not callable(getattr(Bridge, name, None)))
    assert missing == [], f"app.js calls bridge methods that don't exist: {missing}"


def test_web_assets_present() -> None:
    for name in ("index.html", "app.css", "app.js", "overlay.html", "fonts/Poppins-Regular.ttf"):
        assert os.path.exists(os.path.join(WEB_DIR, name)), name


def test_html_ids_used_by_js_exist() -> None:
    with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(WEB_DIR, "app.js"), encoding="utf-8") as f:
        js = f.read()
    ids = set(re.findall(r'id="([\w-]+)"', html)) | set(re.findall(r'id="([\w-]+)"', js))
    used = set(re.findall(r'\$\("#([\w-]+)"\)', js))
    assert sorted(used - ids) == []
