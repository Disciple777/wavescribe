/* WaveScribe UI — talks to Python through window.pywebview.api.
   Python pushes updates by calling WS.receive(kind, payload). */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (name, cls = "sm") => `<svg class="i ${cls}"><use href="#i-${name}"/></svg>`;
let api = null;

const S = {
  snap: null,
  history: [],
  sessions: [],
  sessionCache: {},     // id → full session
  selectedSession: null,
  voices: { voices: [], loading: true, has_default: false },
  page: "dictate",
  capturing: null,      // hotkey name being captured
  freshNotes: new Set(),
};

/* ───────────────── Formatting helpers ───────────────── */

const KEY_NAMES = { ctrl: "Ctrl", shift: "Shift", alt: "Alt", win: "Win", windows: "Win", space: "Space", enter: "Enter",
  esc: "Esc", page_up: "PgUp", page_down: "PgDn", braceright: "]", braceleft: "[", bracketright: "]", bracketleft: "[" };
function kbdHTML(combo) {
  if (!combo) return `<span class="kbd none">Not set</span>`;
  return `<span class="kbd">${combo.split("+").map((k) => `<kbd>${esc(KEY_NAMES[k] || (k.length === 1 ? k.toUpperCase() : k[0].toUpperCase() + k.slice(1)))}</kbd>`).join("")}</span>`;
}
function relTime(stamp) {
  if (!stamp) return "";
  const d = new Date(stamp.replace(" ", "T"));
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400 && d.getDate() === new Date().getDate()) return `Today ${stamp.slice(11, 16)}`;
  if (diff < 172800) return `Yesterday ${stamp.slice(11, 16)}`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " + stamp.slice(11, 16);
}
function plural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }

/* ───────────────── Toasts & modal ───────────────── */

function toast(msg, tone = "info", duration = 3500) {
  const icons = { success: "check-circle", error: "alert", warn: "alert", info: "info" };
  const el = document.createElement("div");
  el.className = `toast ${tone}`;
  el.innerHTML = `${icon(icons[tone] || "info")}<div>${esc(msg)}</div>`;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 220); }, duration);
}
function confirmModal(title, text, yes = "Delete") {
  return new Promise((resolve) => {
    $("#modal-title").textContent = title;
    $("#modal-text").textContent = text;
    $("#modal-yes").textContent = yes;
    const m = $("#modal");
    m.classList.add("open");
    const done = (v) => { m.classList.remove("open"); $("#modal-yes").onclick = $("#modal-no").onclick = null; resolve(v); };
    $("#modal-yes").onclick = () => done(true);
    $("#modal-no").onclick = () => done(false);
    m.onclick = (e) => { if (e.target === m) done(false); };
  });
}
async function copy(text, what = "Copied to clipboard") {
  if (await api.copy_text(text)) toast(what, "success", 1800);
  else toast("Couldn't access the clipboard.", "error");
}

/* ───────────────── Navigation ───────────────── */

function go(page) {
  S.page = page;
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${page}`));
  $("#main").scrollTop = 0;
  if (page === "sessions") {
    if (!S.selectedSession) S.selectedSession = S.snap?.session.active_id || S.sessions[0]?.id || null;
    loadSession(S.selectedSession);
    renderSessionList();
  }
  if (page === "history") renderHistory();
}

/* ───────────────── State → UI ───────────────── */

function applyTheme(theme) {
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

function setSeg(sel, value) { $$(`${sel} button`).forEach((b) => b.classList.toggle("on", b.dataset.v === value)); }

function render() {
  const s = S.snap;
  if (!s) return;
  applyTheme(s.theme);
  $("#ver").textContent = "v" + s.version;
  $$("[data-hotkey]").forEach((el) => {
    if (S.capturing === el.dataset.hotkey) return;
    el.outerHTML = kbdHTML(s.hotkeys[el.dataset.hotkey]).replace('<span class="kbd', `<span data-hotkey="${el.dataset.hotkey}" class="kbd`);
  });
  renderSidebar();
  renderDictate();
  renderReadOut();
  renderSettings();
  renderListenBar();
  if (S.page === "sessions") renderSessionList();
}

function renderSidebar() {
  const s = S.snap, sess = s.session;
  let dot = "green", label = "Ready", pulse = false;
  if (s.status === "recording") { dot = "red"; label = "Recording…"; pulse = true; }
  else if (s.status === "transcribing") { dot = "amber"; label = "Transcribing…"; }
  else if (s.status === "error") { dot = "red"; label = "Needs attention"; }
  else if (sess.listening) { dot = "green"; label = "Listening"; pulse = true; }
  else if (s.tts.status === "reading") { dot = "amber"; label = "Reading aloud"; }
  $("#side-dot").className = `dot ${dot}${pulse ? " pulse" : ""}`;
  $("#side-label").textContent = label;
  const engine = s.mode === "cloud" ? "Cloud · Whisper API" : `Local · ${s.model_size}`;
  $("#side-sub").textContent = sess.active_id ? `Session: ${sess.active_name}` : engine;
  $("#nav-live").hidden = !sess.listening;
}

function renderDictate() {
  const s = S.snap;
  const hero = $("#hero");
  hero.dataset.status = s.status;
  const title = { idle: "Ready to dictate", recording: "Recording…", transcribing: "Transcribing…", error: "Something went wrong" }[s.status];
  $("#hero-title").textContent = title;
  const sub = $("#hero-sub");
  sub.classList.toggle("error", s.status === "error");
  if (s.status === "error") sub.textContent = s.error;
  else if (s.status === "recording") sub.innerHTML = `Press ${kbdHTML(s.hotkeys.stop)} or the button to finish.`;
  else if (s.status === "transcribing") sub.textContent = "Hang tight — turning your voice into text.";
  else if (s.mode === "cloud" && !s.has_api_key) sub.innerHTML = `Add your OpenAI API key in <a href="#" data-go="settings" style="color:var(--accent-text)">Settings</a> or switch to Local.`;
  else sub.textContent = "Click the mic or use your shortcut from any app.";

  const btn = $("#mic-btn");
  btn.innerHTML = icon(s.status === "recording" ? "stop" : "mic", "lg");
  btn.title = s.status === "recording" ? "Stop and transcribe" : s.status === "transcribing" ? "Cancel" : "Start recording";
  btn.disabled = false;
  setSeg("#mode-seg", s.mode);

  const banner = $("#route-banner");
  banner.hidden = !s.session.active_id;
  if (s.session.active_id) {
    $("#route-text").innerHTML = `Dictations go into <b>${esc(s.session.active_name)}</b>${s.session.paste_dictations ? " and are pasted." : " instead of being pasted."}`;
  }

  const out = $("#out-text");
  const text = s.last_transcription;
  out.classList.toggle("empty", !text);
  out.textContent = text || "Your transcription will appear here.";
  $("#out-copy").disabled = $("#out-paste").disabled = $("#out-clear").disabled = !text;
}

function renderRecent() {
  const items = S.history.slice(0, 4);
  $("#recent-card").hidden = !S.snap?.history_enabled;
  $("#recent-list").innerHTML = items.length ? items.map((e, i) => historyRow(e, i)).join("")
    : `<div class="empty-state small">Nothing yet — your dictations will show up here.</div>`;
}
function historyRow(e, i) {
  return `<div class="list-item" data-i="${i}">
    <div class="meta">${esc(relTime(e.timestamp + ":00"))}</div>
    <div class="body">${esc(e.text)}</div>
    <div class="acts">
      <button class="btn sm ghost icon" data-act="copy" title="Copy">${icon("copy")}</button>
      <button class="btn sm ghost icon" data-act="open" title="Show in Dictate">${icon("arrow")}</button>
      <button class="btn sm ghost icon danger-text" data-act="delete" title="Delete">${icon("trash")}</button>
    </div></div>`;
}
function renderHistory() {
  const q = $("#history-search").value.trim().toLowerCase();
  const rows = S.history.map((e, i) => [e, i]).filter(([e]) => !q || e.text.toLowerCase().includes(q));
  $("#history-sub").textContent = S.snap?.history_enabled === false ? "History is turned off in Settings." : `${plural(S.history.length, "saved dictation")}.`;
  $("#history-clear").disabled = !S.history.length;
  $("#history-list").innerHTML = rows.length ? rows.map(([e, i]) => historyRow(e, i)).join("")
    : `<div class="empty-state">${icon("history", "")}<div>${q ? "No matches." : "No saved dictations yet."}</div></div>`;
}

/* ───────────────── Sessions ───────────────── */

function renderSessionList() {
  const sess = S.snap?.session || {};
  const el = $("#session-list");
  if (!S.sessions.length) { el.innerHTML = `<div class="empty-state small">No sessions yet.</div>`; return; }
  el.innerHTML = S.sessions.map((x) => {
    const active = x.id === sess.active_id;
    const live = active && sess.listening;
    return `<button class="session-item${x.id === S.selectedSession ? " sel" : ""}" data-id="${x.id}">
      <div class="n">${active ? `<i class="dot green${live ? " pulse" : ""}"></i>` : ""}<span>${esc(x.name)}</span></div>
      <div class="m">${plural(x.count, "note")} · ${esc(relTime(x.updated))}</div></button>`;
  }).join("");
}

async function loadSession(id) {
  if (!id) { renderSessionDetail(); return; }
  S.sessionCache[id] = await api.session_get(id);
  if (!S.sessionCache[id]) S.selectedSession = null;
  renderSessionDetail();
}

function renderSessionDetail() {
  const el = $("#session-detail");
  const s = S.selectedSession && S.sessionCache[S.selectedSession];
  const snap = S.snap;
  if (!s) {
    el.innerHTML = `
      <div class="sd-head"><div class="sd-name" style="border:0">Capture feedback hands-free</div></div>
      <div class="sd-meta" style="margin:6px 0 18px">Perfect for live-testing an app or a game: keep playing and just say what you notice.</div>
      <div class="how">
        <div><span class="num">1</span><b>Start listening</b>Press ${kbdHTML(snap?.hotkeys.session)} from any app, or the button below.</div>
        <div><span class="num">2</span><b>Just talk</b>Each pause turns what you said into a note. Silence is skipped.</div>
        <div><span class="num">3</span><b>Copy the list</b>Paste every note into your agent as one prompt.</div>
      </div>
      <div style="padding:0 22px 22px"><button class="btn primary lg" data-act="new-listen">${icon("mic")}Start listening now</button></div>`;
    return;
  }
  const isActive = snap.session.active_id === s.id;
  const notes = s.entries;
  el.innerHTML = `
    <div class="sd-head">
      <input class="sd-name" id="sd-name" value="${esc(s.name)}" spellcheck="false" title="Rename">
      ${isActive ? `<span class="badge green">Active</span>` : ""}
      <button class="btn sm primary" data-act="copy-all" ${notes.length ? "" : "disabled"} title="Copy every note, ready to paste into your agent">${icon("copy")}Copy all</button>
      <button class="btn sm icon" data-act="export" ${notes.length ? "" : "disabled"} title="Export as Markdown">${icon("download")}</button>
      <button class="btn sm ghost icon danger-text" data-act="delete-session" title="Delete session">${icon("trash")}</button>
    </div>
    <div class="sd-meta">Started ${esc(relTime(s.created))} · <span id="sd-count">${plural(notes.length, "note")}</span></div>
    <div id="listen-slot"></div>
    <div class="notes" id="notes">
      ${notes.length ? notes.map(noteHTML).join("") : `<div class="empty-state small" id="notes-empty">${isActive ? "Notes appear here as you talk." : "This session has no notes."}</div>`}
      <div id="pending-slot"></div>
    </div>
    <div class="add-note">
      <input class="input" id="note-input" placeholder="Type a note and press Enter">
    </div>`;
  renderListenBar();
  const box = $("#notes");
  if (box) box.lastElementChild?.scrollIntoView({ block: "nearest" });
}

function noteHTML(n) {
  const src = { live: "", dictation: `<span class="badge accent src">Shortcut</span>`, typed: `<span class="badge src">Typed</span>` }[n.source] || "";
  const fresh = S.freshNotes.has(n.id) ? " fresh" : "";
  return `<div class="note${fresh}" data-id="${n.id}">
    <div class="when">${esc((n.time || "").slice(11, 16))}${src}</div>
    <div class="note-text" contenteditable="plaintext-only" spellcheck="false">${esc(n.text)}</div>
    <div class="acts"><button class="btn sm ghost icon danger-text" data-act="del-note" title="Delete note">${icon("trash")}</button></div>
  </div>`;
}

function renderListenBar() {
  const slot = $("#listen-slot");
  const snap = S.snap;
  if (!slot || !snap) return;
  const s = S.sessionCache[S.selectedSession];
  if (!s) return;
  const sess = snap.session;
  const isActive = sess.active_id === s.id;
  const listening = isActive && sess.listening;
  let html;
  if (!isActive) {
    html = `<div class="listen-bar"><div class="state"><div class="t">Not active</div>
      <div class="s">Continue this session to add notes by voice or shortcut.</div></div>
      <button class="btn primary" data-act="resume">${icon("mic")}Continue session</button></div>`;
  } else {
    html = `<div class="listen-bar${listening ? " on" : ""}">
      ${listening ? `<div class="bars" id="sd-bars">${"<i></i>".repeat(5)}</div>` : ""}
      <div class="state"><div class="t" id="sd-state">${listening ? "Listening" : "Paused"}</div>
        <div class="s" id="sd-state-sub">${listening ? "Talk naturally — pause to end a note." : `${kbdHTML(snap.hotkeys.session)} toggles listening from any app`}</div></div>
      <button class="btn ${listening ? "" : "primary"}" data-act="toggle-listen">${icon(listening ? "pause" : "mic")}${listening ? "Pause" : "Start listening"}</button>
      <button class="btn ghost" data-act="end" title="Stop listening and stop routing dictations here">${icon("check-circle")}Finish</button></div>`;
  }
  if (slot.dataset.html !== html) { slot.innerHTML = html; slot.dataset.html = html; }
  const pend = $("#pending-slot");
  if (pend) {
    const n = isActive ? sess.pending : 0;
    const want = n ? `<div class="note pending"><div class="when">…</div><div class="note-text">Transcribing ${plural(n, "note")}…</div></div>` : "";
    if (pend.innerHTML !== want) { pend.innerHTML = want; if (n) pend.scrollIntoView({ block: "nearest" }); }
  }
}

function onLevel(d) {
  const lv = Math.min(1, d.level || 0);
  if (d.source === "dictation" || !d.source) {
    $("#dictate-meter").style.width = `${d.source ? Math.round(lv * 100) : 0}%`;
    $("#dictate-meter").style.background = lv > 0.8 ? "var(--red)" : lv > 0.55 ? "var(--amber)" : "var(--green)";
    $("#mic-ring").style.transform = `scale(${1 + (d.source ? lv * 0.35 : 0)})`;
  }
  const bars = $$("#sd-bars i");
  if (bars.length) {
    const live = d.source === "session";
    bars.forEach((b, i) => {
      const shape = [0.55, 0.85, 1, 0.8, 0.6][i];
      const jitter = live ? 0.75 + Math.random() * 0.5 : 0;
      b.style.height = `${4 + Math.round((live ? lv : 0) * 18 * shape * jitter)}px`;
    });
    const st = $("#sd-state");
    if (st && live) st.textContent = d.speaking ? "Hearing you…" : "Listening";
  }
}

/* ───────────────── Read Out ───────────────── */

function renderReadOut() {
  const t = S.snap.tts;
  const reading = t.status === "reading" || t.busy;
  $("#tts-dot").className = `dot ${reading ? "amber pulse" : t.status === "error" ? "red" : "green"}`;
  $("#tts-status").textContent = reading ? "Reading aloud…" : t.status === "error" && t.error ? t.error : "Ready";
  const active = t.voice_list[t.active_index];
  $("#tts-voice-now").textContent = active ? `Voice: ${active.display}` : "";
  $("#tts-read").disabled = reading;
  $("#tts-stop").disabled = !reading;
  $("#tts-speak").disabled = reading;

  const be = $("#tts-backend");
  const opts = t.backends.map((b) => `<option value="${b.name}">${esc(b.display)}</option>`).join("");
  if (be.dataset.opts !== opts) { be.innerHTML = opts; be.dataset.opts = opts; }
  be.value = t.backend;

  setRange("#tts-rate", t.rate, `${t.rate}%`, "#tts-rate-v");
  setRange("#tts-vol", t.volume, `${t.volume}`, "#tts-vol-v");

  $("#tts-rotation").innerHTML = t.voice_list.map((v, i) => `
    <div class="voice-chip${i === t.active_index ? " on" : ""}" data-i="${i}" title="Use this voice">
      <span class="badge${i === 0 ? " accent" : ""}">${i === 0 ? "Main" : v.backend === "system" ? "Windows" : "Edge"}</span>
      <span class="nm">${esc(v.voice || "System default")}</span>
      ${t.voice_list.length > 1 ? `<button class="btn sm ghost icon" data-act="rm" title="Remove">${icon("x")}</button>` : ""}
    </div>`).join("");
  renderVoiceSelect();
}

function renderVoiceSelect() {
  const sel = $("#tts-voice");
  if (document.activeElement === sel) return;
  const v = S.voices;
  const q = $("#tts-search").value.trim().toLowerCase();
  let list = v.voices.map((x) => ({ value: x.name, label: x.name + (x.language || x.gender ? `  ·  ${[x.language, x.gender].filter(Boolean).join(" / ")}` : "") }));
  if (v.has_default) list.unshift({ value: "", label: "System default" });
  const all = list.length;
  if (q) list = list.filter((x) => x.label.toLowerCase().includes(q));
  $("#tts-voice-count").textContent = v.loading ? "· loading…" : `· ${q ? `${list.length} of ` : ""}${all}`;
  const prev = sel.value;
  const t = S.snap?.tts;
  const active = t?.voice_list[t.active_index];
  sel.innerHTML = list.length ? list.map((x) => `<option value="${esc(x.value)}">${esc(x.label)}</option>`).join("")
    : `<option disabled>${v.loading ? "Loading voices…" : "No matching voices"}</option>`;
  const want = list.find((x) => x.value === prev) ? prev : active && active.backend === v.backend && list.find((x) => x.value === active.voice) ? active.voice : list[0]?.value;
  if (want !== undefined) sel.value = want;
  $("#tts-main").disabled = $("#tts-add").disabled = !list.length;
}

function setRange(sel, value, label, labelSel) {
  const el = $(sel);
  if (document.activeElement !== el) el.value = value;
  const pct = ((el.value - el.min) / (el.max - el.min)) * 100;
  el.style.setProperty("--pct", `${pct}%`);
  if (labelSel) $(labelSel).textContent = label;
}

/* ───────────────── Settings ───────────────── */

const HOTKEY_ROWS = [
  ["start", "Start dictation", "Begin recording from any app."],
  ["stop", "Stop & paste", "Transcribe and paste into the focused app."],
  ["session", "Start / pause listening", "Toggle hands-free listening for the active session (creates one if needed)."],
  ["tts_read", "Read selection aloud", "Press again to stop."],
  ["tts_swap", "Next voice", "Cycle through the Read Out rotation."],
];

function renderSettings() {
  const s = S.snap;
  const rows = HOTKEY_ROWS.map(([name, title, desc]) => `
    <div class="row-item"><div class="text"><div class="title">${title}</div><div class="desc">${desc}</div></div>
      <div class="ctl">${S.capturing === name ? `<span class="capture">Press keys… (Esc to cancel)</span>` : kbdHTML(s.hotkeys[name])}
        <button class="btn sm" data-capture="${name}">${S.capturing === name ? "Cancel" : "Change"}</button></div></div>`).join("");
  const hk = $("#hotkey-rows");
  if (hk.dataset.html !== rows) { hk.innerHTML = rows; hk.dataset.html = rows; }

  setSeg("#set-mode", s.mode);
  setSeg("#set-model", s.model_size);
  setSeg("#set-lang", s.local_language);
  setSeg("#set-sens", s.session.sensitivity);
  setSeg("#set-copy", s.session.copy_style);
  setSeg("#set-theme", s.theme);
  $("#row-lang").hidden = s.mode !== "local";
  $("#key-desc").textContent = s.has_api_key ? `Saved (${s.api_key_hint}). Paste a new key to replace it.` : "Needed for Cloud mode. Stored only on this PC.";

  const lm = {
    package_missing: ["Not installed yet.", "Install", false],
    installing: ["Installing the Whisper package…", "Installing…", true],
    model_loading: ["Downloading model weights (first time only)…", "Downloading…", true],
    ready: ["Installed and ready.", "Reinstall", false],
    error: ["Installation failed — check your connection.", "Retry", false],
  }[s.local_model_status] || ["", "Install", false];
  $("#local-desc").textContent = lm[0];
  $("#local-install").textContent = lm[1];
  $("#local-install").disabled = lm[2];

  const mic = $("#mic");
  const opts = s.mic.devices.map((d) => `<option value="${d.id}">${esc(d.name)}</option>`).join("");
  if (mic.dataset.opts !== opts) { mic.innerHTML = opts; mic.dataset.opts = opts; }
  mic.value = String(s.mic.selected);

  setRange("#set-pause", s.session.pause_s, `${Number(s.session.pause_s).toFixed(1)} s`, "#set-pause-v");
  $$("[data-setting]").forEach((el) => {
    const key = el.dataset.setting;
    el.checked = key === "session_paste_dictations" ? s.session.paste_dictations : !!s[key];
  });
}

/* ───────────────── Hotkey capture ───────────────── */

const CODE_MAP = { Space: "space", Enter: "enter", Tab: "tab", Backspace: "backspace", Delete: "delete", Insert: "insert",
  Home: "home", End: "end", PageUp: "page up", PageDown: "page down", ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left",
  ArrowRight: "right", Minus: "-", Equal: "=", BracketLeft: "[", BracketRight: "]", Backslash: "\\", Semicolon: ";",
  Quote: "'", Comma: ",", Period: ".", Slash: "/", Backquote: "`", Pause: "pause", PrintScreen: "print screen" };

function keyFromEvent(e) {
  if (/^Key[A-Z]$/.test(e.code)) return e.code.slice(3).toLowerCase();
  if (/^Digit\d$/.test(e.code)) return e.code.slice(5);
  if (/^F\d{1,2}$/.test(e.code)) return e.code.toLowerCase();
  if (/^Numpad\d$/.test(e.code)) return "num " + e.code.slice(6);
  return CODE_MAP[e.code] || null;
}

async function startCapture(name) {
  if (S.capturing === name) return stopCapture();
  S.capturing = name;
  await api.begin_hotkey_capture();
  renderSettings();
}
async function stopCapture() {
  S.capturing = null;
  await api.end_hotkey_capture();
  renderSettings();
}
document.addEventListener("keydown", async (e) => {
  if (!S.capturing) return;
  e.preventDefault();
  e.stopPropagation();
  if (e.key === "Escape") return stopCapture();
  if (["Control", "Shift", "Alt", "Meta"].includes(e.key)) return;
  const mods = [];
  if (e.ctrlKey) mods.push("ctrl");
  if (e.shiftKey) mods.push("shift");
  if (e.altKey) mods.push("alt");
  if (e.metaKey) mods.push("win");
  const key = keyFromEvent(e);
  if (!mods.length || !key) { toast("Use at least one modifier (Ctrl, Alt, Shift or Win) plus a key.", "warn"); return; }
  const name = S.capturing;
  S.capturing = null;
  const res = await api.set_hotkey(name, [...mods, key].join("+"));
  if (res.ok) toast("Shortcut saved", "success", 1800);
  else { toast(res.error, "error"); await api.end_hotkey_capture(); }
  renderSettings();
}, true);

/* ───────────────── Events from Python ───────────────── */

const WS = {
  receive(kind, payload) {
    switch (kind) {
      case "state": S.snap = payload; render(); break;
      case "level": onLevel(payload); break;
      case "history": S.history = payload; renderRecent(); if (S.page === "history") renderHistory(); break;
      case "sessions": S.sessions = payload; renderSessionList(); break;
      case "session":
        if (!payload) break;
        {
          const old = S.sessionCache[payload.id];
          const oldIds = new Set((old?.entries || []).map((e) => e.id));
          payload.entries.forEach((e) => { if (old && !oldIds.has(e.id)) S.freshNotes.add(e.id); });
          S.sessionCache[payload.id] = payload;
          if (payload.id === S.selectedSession && S.page === "sessions") patchSessionDetail(payload);
          setTimeout(() => S.freshNotes.clear(), 1800);
        }
        break;
      case "voices": S.voices = payload; renderVoiceSelect(); break;
      case "notice": toast(payload.msg, payload.tone, payload.duration); break;
      case "navigate": go(payload); break;
    }
  },
};
window.WS = WS;

// Update the open session without clobbering a note or the name being edited.
function patchSessionDetail(s) {
  const editing = document.activeElement;
  const editingNote = editing?.classList.contains("note-text") || editing?.id === "sd-name" || editing?.id === "note-input";
  if (!$("#notes") || !editingNote) { renderSessionDetail(); return; }
  const box = $("#notes");
  const have = new Set($$(".note[data-id]", box).map((n) => n.dataset.id));
  const want = new Set(s.entries.map((e) => e.id));
  $$(".note[data-id]", box).forEach((n) => { if (!want.has(n.dataset.id)) n.remove(); });
  s.entries.forEach((e) => {
    if (!have.has(e.id)) {
      $("#notes-empty")?.remove();
      $("#pending-slot").insertAdjacentHTML("beforebegin", noteHTML(e));
    }
  });
  $("#sd-count").textContent = plural(s.entries.length, "note");
}

/* ───────────────── Wiring ───────────────── */

function wire() {
  document.addEventListener("click", (e) => {
    const goEl = e.target.closest("[data-go]");
    if (goEl) { e.preventDefault(); go(goEl.dataset.go); }
  });
  $$("#nav button").forEach((b) => b.addEventListener("click", () => go(b.dataset.page)));

  // Dictate
  $("#mic-btn").onclick = () => {
    const st = S.snap.status;
    if (st === "recording") api.stop_recording();
    else if (st === "transcribing") api.cancel_transcription();
    else api.start_recording();
  };
  $$("#mode-seg button, #set-mode button").forEach((b) => b.onclick = () => api.set_setting("mode", b.dataset.v));
  $("#out-copy").onclick = () => copy(S.snap.last_transcription);
  $("#out-paste").onclick = () => api.paste_last();
  $("#out-clear").onclick = () => api.clear_transcription();

  // History (recent + full list share row markup)
  const onHistoryClick = async (e) => {
    const btn = e.target.closest("[data-act]");
    const row = e.target.closest(".list-item");
    if (!btn || !row) return;
    const i = +row.dataset.i, entry = S.history[i];
    if (btn.dataset.act === "copy") copy(entry.text);
    if (btn.dataset.act === "open") { await api.history_open(i); go("dictate"); }
    if (btn.dataset.act === "delete") api.history_delete(i);
  };
  $("#recent-list").onclick = onHistoryClick;
  $("#history-list").onclick = onHistoryClick;
  $("#history-search").oninput = renderHistory;
  $("#history-clear").onclick = async () => {
    if (await confirmModal("Clear history?", "All saved dictations will be removed. This can't be undone.", "Clear all")) api.history_clear();
  };

  // Sessions
  $("#session-new").onclick = async () => {
    const s = await api.session_new("", false);
    S.selectedSession = s.id;
    await loadSession(s.id);
    renderSessionList();
    setTimeout(() => { const n = $("#sd-name"); n?.focus(); n?.select(); }, 30);
  };
  $("#session-list").onclick = (e) => {
    const item = e.target.closest(".session-item");
    if (!item) return;
    S.selectedSession = item.dataset.id;
    renderSessionList();
    loadSession(item.dataset.id);
  };
  const detail = $("#session-detail");
  detail.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const id = S.selectedSession;
    switch (btn.dataset.act) {
      case "new-listen": {
        const s = await api.session_new("", true);
        S.selectedSession = s.id;
        await loadSession(s.id);
        break;
      }
      case "toggle-listen": api.session_toggle_listening(); break;
      case "end": api.session_end(); break;
      case "resume": await api.session_activate(id); api.session_toggle_listening(); break;
      case "copy-all": copy(await api.session_text(id, null), "Session copied — paste it into your agent"); break;
      case "export": api.session_export(id); break;
      case "delete-session":
        if (await confirmModal("Delete session?", `“${S.sessionCache[id]?.name}” and all its notes will be removed.`)) {
          await api.session_delete(id);
          delete S.sessionCache[id];
          S.selectedSession = S.sessions.find((x) => x.id !== id)?.id || null;
          loadSession(S.selectedSession);
        }
        break;
      case "del-note": api.session_delete_entry(id, btn.closest(".note").dataset.id); break;
    }
  });
  detail.addEventListener("focusout", (e) => {
    const id = S.selectedSession;
    if (e.target.id === "sd-name") {
      const v = e.target.value.trim();
      if (v && v !== S.sessionCache[id]?.name) api.session_rename(id, v);
    }
    if (e.target.classList.contains("note-text")) {
      const noteId = e.target.closest(".note").dataset.id;
      const old = S.sessionCache[id]?.entries.find((x) => x.id === noteId);
      const v = e.target.textContent.trim();
      if (old && v !== old.text) api.session_update_entry(id, noteId, v);
    }
  });
  detail.addEventListener("keydown", (e) => {
    if (e.target.id === "sd-name" && e.key === "Enter") e.target.blur();
    if (e.target.classList.contains("note-text") && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.target.blur(); }
    if (e.target.id === "note-input" && e.key === "Enter" && e.target.value.trim()) {
      api.session_add_text(S.selectedSession, e.target.value.trim());
      e.target.value = "";
    }
  });

  // Read Out
  $("#tts-read").onclick = () => api.tts_read_selection();
  $("#tts-stop").onclick = () => api.tts_stop();
  $("#tts-speak").onclick = () => api.tts_speak_text($("#tts-text").value);
  $("#tts-backend").onchange = (e) => api.tts_set_backend(e.target.value);
  $("#tts-search").oninput = renderVoiceSelect;
  $("#tts-main").onclick = () => api.tts_set_main_voice($("#tts-voice").value);
  $("#tts-add").onclick = () => api.tts_add_voice($("#tts-voice").value);
  $("#tts-cycle").onclick = () => api.tts_cycle_voice();
  $("#tts-rotation").onclick = (e) => {
    const chip = e.target.closest(".voice-chip");
    if (!chip) return;
    if (e.target.closest("[data-act=rm]")) api.tts_remove_voice(+chip.dataset.i);
    else api.tts_set_active_index(+chip.dataset.i);
  };
  const bindRange = (sel, labelSel, fmt, send) => {
    const el = $(sel);
    el.oninput = () => setRange(sel, el.value, fmt(el.value), labelSel);
    el.onchange = () => send(+el.value);
  };
  bindRange("#tts-rate", "#tts-rate-v", (v) => `${v}%`, (v) => api.tts_set_rate(v));
  bindRange("#tts-vol", "#tts-vol-v", (v) => `${v}`, (v) => api.tts_set_volume(v));

  // Settings
  $("#hotkey-rows").onclick = (e) => { const b = e.target.closest("[data-capture]"); if (b) startCapture(b.dataset.capture); };
  const segSetting = (sel, key) => $$(`${sel} button`).forEach((b) => b.onclick = () => api.set_setting(key, b.dataset.v));
  segSetting("#set-model", "model_size");
  segSetting("#set-lang", "local_language");
  segSetting("#set-sens", "session_sensitivity");
  segSetting("#set-copy", "session_copy_style");
  segSetting("#set-theme", "theme");
  $$("[data-setting]").forEach((el) => el.onchange = () => api.set_setting(el.dataset.setting, el.checked));
  bindRange("#set-pause", "#set-pause-v", (v) => `${Number(v).toFixed(1)} s`, (v) => api.set_setting("session_pause_s", v));
  $("#api-save").onclick = async () => {
    const v = $("#api-key").value.trim();
    if (!v) return toast("Paste your API key first.", "warn");
    if (await api.save_api_key(v)) { $("#api-key").value = ""; toast("API key saved", "success", 1800); }
  };
  $("#api-key").onkeydown = (e) => { if (e.key === "Enter") $("#api-save").click(); };
  $("#local-install").onclick = () => api.install_local_model();
  $("#mic").onchange = (e) => api.set_setting("input_device", e.target.value);
  $("#mic-refresh").onclick = () => api.refresh_devices();
  $("#quit").onclick = () => api.quit();

  window.addEventListener("focus", () => api.set_focused(true));
  window.addEventListener("blur", () => api.set_focused(false));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => S.snap && applyTheme(S.snap.theme));
  setInterval(() => { if (S.page === "sessions") renderSessionList(); }, 60000);
}

async function boot() {
  api = window.pywebview.api;
  wire();
  const init = await api.get_initial();
  S.snap = init.state;
  S.history = init.history;
  S.sessions = init.sessions;
  S.voices = init.voices;
  render();
  renderRecent();
  await api.ui_ready();
}

if (window.pywebview?.api) boot();
else window.addEventListener("pywebviewready", boot, { once: true });
