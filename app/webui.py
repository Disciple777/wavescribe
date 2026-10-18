"""WaveScribe desktop view: HTML/CSS UI hosted in pywebview (Edge WebView2).

``WebUI`` owns two windows — the main app window and a small transparent,
always-on-top status overlay — and bridges them to ``WaveScribeCore``:

* JS → Python: ``window.pywebview.api.<method>()`` calls on :class:`Bridge`.
* Python → JS: core events are queued and delivered by an outbox thread
  via ``WS.receive(kind, payload)``, so the core never blocks on the UI.
"""

import ctypes
import json
import os
import queue
import threading
import time
from typing import Any, Dict, Optional

import webview

from app.core import WaveScribeCore

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
OVERLAY_W, OVERLAY_H = 360, 66
NOTE_FLASH_S = 2.6


def _work_area():
    """(left, top, right, bottom) of the primary screen minus the taskbar."""
    try:
        import ctypes.wintypes as wt
        r = wt.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return 0, 0, 1366, 728


class Bridge:
    """Methods callable from JavaScript (pywebview skips ``_``-prefixed names)."""

    def __init__(self, ui: "WebUI") -> None:
        self._ui = ui
        self._core = ui.core

    def _call(self, fn, *args):
        return self._core.call(fn, *args)

    # ── Boot ──
    def get_initial(self) -> Dict[str, Any]:
        c = self._core
        data = self._call(lambda: {
            "state": c.snapshot(),
            "history": c.history_list(),
            "sessions": c.session_list(),
            "voices": c.tts_voices(),
        })
        self._ui._ready = True
        return data

    def ui_ready(self) -> None:
        self._ui._ready = True

    def set_focused(self, focused: bool) -> None:
        self._ui._focused = bool(focused)

    # ── Dictation ──
    def start_recording(self) -> None:
        self._call(self._core.start_recording)

    def stop_recording(self) -> None:
        self._call(self._core.stop_recording, False)

    def cancel_transcription(self) -> None:
        self._call(self._core.cancel_transcription)

    def clear_transcription(self) -> None:
        self._call(self._core.clear_transcription)

    def paste_last(self) -> None:
        self._call(self._core.paste_last)

    def copy_text(self, text: str) -> bool:
        return self._core.copy_text(text)

    # ── Settings ──
    def set_setting(self, key: str, value: Any) -> None:
        self._call(self._core.set_setting, key, value)

    def save_api_key(self, key: str) -> bool:
        return self._call(self._core.save_api_key, key)

    def refresh_devices(self) -> None:
        self._call(self._core.refresh_devices)

    def install_local_model(self) -> None:
        self._call(self._core.install_local_model)

    def set_hotkey(self, name: str, combo: str) -> Dict[str, Any]:
        return self._call(self._core.set_hotkey, name, combo)

    def begin_hotkey_capture(self) -> None:
        self._call(self._core.begin_hotkey_capture)

    def end_hotkey_capture(self) -> None:
        self._call(self._core.end_hotkey_capture)

    # ── History ──
    def history_open(self, index: int) -> None:
        self._call(self._core.history_open, int(index))

    def history_delete(self, index: int) -> None:
        self._call(self._core.history_delete, int(index))

    def history_clear(self) -> None:
        self._call(self._core.history_clear)

    # ── Sessions ──
    def session_get(self, session_id: str):
        return self._core.session_get(session_id)

    def session_new(self, name: str = "", listen: bool = False):
        return self._call(self._core.session_new, name or "", bool(listen))

    def session_activate(self, session_id: str) -> None:
        self._call(self._core.session_activate, session_id)

    def session_end(self) -> None:
        self._call(self._core.session_end)

    def session_toggle_listening(self) -> None:
        self._call(self._core.session_toggle_listening)

    def session_rename(self, session_id: str, name: str) -> None:
        self._call(self._core.session_rename, session_id, name)

    def session_delete(self, session_id: str) -> None:
        self._call(self._core.session_delete, session_id)

    def session_update_entry(self, session_id: str, entry_id: str, text: str) -> None:
        self._call(self._core.session_update_entry, session_id, entry_id, text)

    def session_delete_entry(self, session_id: str, entry_id: str) -> None:
        self._call(self._core.session_delete_entry, session_id, entry_id)

    def session_add_text(self, session_id: str, text: str) -> None:
        self._call(self._core.session_add_text, session_id, text)

    def session_text(self, session_id: str, style: Optional[str] = None) -> str:
        return self._core.session_text(session_id, style)

    def session_export(self, session_id: str) -> None:
        self._ui.export_session(session_id)

    # ── Read Out ──
    def tts_read_selection(self) -> None:
        self._call(self._core.tts_read_selection)

    def tts_stop(self) -> None:
        self._call(self._core.tts_stop)

    def tts_speak_text(self, text: str) -> None:
        self._call(self._core.tts_speak_text, text)

    def tts_set_backend(self, name: str) -> None:
        self._call(self._core.tts_set_backend, name)

    def tts_set_main_voice(self, voice: str) -> None:
        self._call(self._core.tts_set_main_voice, voice)

    def tts_add_voice(self, voice: str) -> None:
        self._call(self._core.tts_add_voice, voice)

    def tts_remove_voice(self, index: int) -> None:
        self._call(self._core.tts_remove_voice, int(index))

    def tts_cycle_voice(self) -> None:
        self._call(self._core.tts_cycle_voice)

    def tts_set_active_index(self, index: int) -> None:
        self._call(self._core.tts_set_active_index, int(index))

    def tts_set_rate(self, value: int) -> None:
        self._call(self._core.tts_set_rate, int(value))

    def tts_set_volume(self, value: int) -> None:
        self._call(self._core.tts_set_volume, int(value))

    # ── App ──
    def quit(self) -> None:
        threading.Thread(target=self._ui.quit, daemon=True).start()


class WebUI:
    def __init__(self, core: WaveScribeCore, icon_path: Optional[str] = None) -> None:
        self.core = core
        self.icon_path = icon_path
        self._ready = False
        self._focused = False
        self._quitting = False
        self._outbox: "queue.Queue[tuple]" = queue.Queue()
        self._overlay_visible = False
        self._overlay_placed = False
        self._note_flash: Optional[tuple] = None  # (until, text)
        self._dark_titlebar: Optional[bool] = None

        left, top, right, bottom = _work_area()
        width = min(1080, right - left - 40)
        height = min(760, bottom - top - 40)
        self.window = webview.create_window(
            "WaveScribe",
            url=os.path.join(WEB_DIR, "index.html"),
            js_api=Bridge(self),
            width=width, height=height, min_size=(720, 540),
            x=left + (right - left - width) // 2, y=top + (bottom - top - height) // 2,
            background_color="#0d0e12",
        )
        self.overlay = webview.create_window(
            "WaveScribe status",
            url=os.path.join(WEB_DIR, "overlay.html"),
            width=OVERLAY_W, height=OVERLAY_H,
            frameless=True, on_top=True, transparent=True, focus=False,
            hidden=True, easy_drag=False, resizable=False,
        )
        self.window.events.closing += self._on_closing

        core.add_listener(self._on_core_event)
        core.view_is_focused = lambda: self._focused
        core.view_minimize = self.window.minimize

    # ── Lifecycle ──

    def run(self) -> None:
        webview.start(self._on_started, gui="edgechromium", http_server=True)

    def _on_started(self) -> None:
        # pywebview's JS calls block forever on a page that hasn't loaded,
        # so nothing is sent to a window before its `loaded` event.
        self.window.events.loaded.wait(20)
        self._place_overlay()
        self._apply_window_chrome()
        threading.Thread(target=self._io_loop, daemon=True, name="ui-io").start()

    def show(self) -> None:
        self.window.show()
        self.window.restore()

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        time.sleep(0.15)  # let the I/O thread finish its last window call
        try:
            self.core.shutdown()
        finally:
            for w in (self.overlay, self.window):
                try:
                    w.destroy()
                except Exception:
                    pass

    def _on_closing(self):
        if self._quitting:
            return True
        self.window.hide()  # keep running in the tray
        return False

    def _apply_window_chrome(self) -> None:
        self._apply_titlebar(self._wants_dark())
        ico = self.icon_path.replace(".png", ".ico") if self.icon_path else ""
        if ico and os.path.exists(ico):
            try:
                import clr  # noqa: F401  (pythonnet, used by pywebview on Windows)
                from System.Drawing import Icon  # type: ignore
                self.window.native.Icon = Icon(ico)
            except Exception:
                pass

    def _wants_dark(self) -> bool:
        return self.core.config.get("theme", "dark") != "light"

    def _apply_titlebar(self, dark: bool) -> None:
        """Match the native title bar to the theme (Windows 10 20H1+ / 11)."""
        if dark == self._dark_titlebar or self.window.native is None:
            return
        self._dark_titlebar = dark
        try:
            hwnd = self.window.native.Handle.ToInt32()
            value = ctypes.c_int(1 if dark else 0)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), 4) == 0:
                    break
            # Windows 10 only repaints the caption after a size change.
            w, h = self.window.width, self.window.height
            self.window.resize(w + 1, h)
            self.window.resize(w, h)
        except Exception:
            pass

    # ── Core → JS ──

    def _on_core_event(self, kind: str, payload: Any) -> None:
        # Runs on core threads: only queue work, never touch the windows here.
        if kind == "note_added":
            self._note_flash = (time.time() + NOTE_FLASH_S, payload.get("text", ""))
            return
        self._outbox.put((kind, payload))

    def _js(self, window, script: str) -> None:
        if self._quitting:
            return
        try:
            window.run_js(script)
        except Exception:
            pass

    def _io_loop(self) -> None:
        """The only thread that talks to the windows: events, meter, overlay (~15 fps)."""
        was_active = False
        last_overlay = None
        while not self._quitting:
            time.sleep(0.066)
            self._flush_outbox()

            lvl = self.core.current_level()
            active = lvl["source"] is not None
            if self._ready and (active or was_active):
                self._js(self.window, f"WS.receive('level', {json.dumps(lvl)})")
            was_active = active

            state = self._overlay_state(lvl)
            if state is None:
                if self._overlay_visible:
                    self._overlay_visible = False
                    self.overlay.hide()
                last_overlay = None
                continue
            if not self._overlay_visible:
                self._overlay_visible = True
                self.overlay.show()
                self.overlay.events.loaded.wait(5)
            if state != last_overlay:
                self._js(self.overlay, f"OV.set({json.dumps(state)})")
                last_overlay = state
            if state.get("bars"):
                self._js(self.overlay, f"OV.level({lvl['level']:.3f}, {json.dumps(bool(lvl.get('speaking')))})")

    def _flush_outbox(self) -> None:
        items = []
        while True:
            try:
                items.append(self._outbox.get_nowait())
            except queue.Empty:
                break
        if not items or not self._ready:
            return
        latest_state = None
        ordered = []
        for kind, payload in items:
            if kind == "state":
                latest_state = payload
            else:
                ordered.append((kind, payload))
        if latest_state is not None:
            ordered.insert(0, ("state", latest_state))
            if latest_state.get("theme") != "system":
                self._apply_titlebar(latest_state.get("theme") == "dark")
        for kind, payload in ordered:
            self._js(self.window, f"WS.receive({json.dumps(kind)}, {json.dumps(payload)})")

    def _overlay_state(self, lvl: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        core = self.core
        status = core.state.get_status().value
        sess = core.session_status()
        hk = core.config.get("hotkey_stop", "")
        if status == "recording":
            sub = "Adding to session" if sess["active_id"] else (f"{_pretty(hk)} to finish" if hk else "")
            return {"cls": "rec", "icon": "mic", "title": "Recording", "sub": sub, "bars": True}
        if status == "transcribing":
            return {"cls": "busy", "icon": "spin", "title": "Transcribing…", "sub": ""}
        flash = self._note_flash
        if flash and time.time() < flash[0]:
            return {"cls": "added", "icon": "check", "title": "Note added", "sub": flash[1]}
        if sess["listening"]:
            bits = [sess["active_name"], f"{sess['active_count']} note{'s' if sess['active_count'] != 1 else ''}"]
            if sess["pending"]:
                bits.append("transcribing…")
            speaking = bool(lvl.get("speaking"))
            return {
                "cls": "live", "icon": "wave", "title": "Hearing you…" if speaking else "Listening",
                "sub": " · ".join(b for b in bits if b), "bars": True,
            }
        return None

    def _place_overlay(self) -> None:
        if self._overlay_placed:
            return
        try:
            _, _, right, bottom = _work_area()
            self.overlay.move(right - OVERLAY_W - 12, bottom - OVERLAY_H - 40)
            self._overlay_placed = True
        except Exception:
            pass

    # ── Export ──

    def export_session(self, session_id: str) -> None:
        session = self.core.session_get(session_id)
        if not session:
            return
        safe = "".join(ch for ch in session["name"] if ch.isalnum() or ch in " -_").strip() or "session"
        try:
            result = self.window.create_file_dialog(
                webview.FileDialog.SAVE, save_filename=f"{safe}.md",
                file_types=("Markdown (*.md)", "Text (*.txt)"),
            )
        except Exception:
            result = None
        path = result[0] if isinstance(result, (list, tuple)) and result else result
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.core.session_markdown(session_id))
            self.core.notice(f"Exported to {os.path.basename(path)}", "success")
        except OSError as exc:
            self.core.notice(f"Couldn't export: {exc}", "error")


_KEY_NAMES = {"braceright": "]", "braceleft": "[", "bracketright": "]", "bracketleft": "[", "win": "Win"}


def _pretty(combo: str) -> str:
    return "+".join(_KEY_NAMES.get(p, p.capitalize() if len(p) > 1 else p.upper()) for p in combo.split("+"))
