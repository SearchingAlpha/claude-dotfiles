"use strict";
// herd dashboard: polls /api/sessions every 2 s and answers three questions in order — does anything
// need me, what finished, is the long-running work healthy. Sessions sit in four groups (Needs you,
// Your turn, Working, Idle); each row is one head line, an optional second line and one line per
// live workflow, with everything else behind a click.
//
// Rows are keyed by session id and rebuilt only when their signature changes, so focus, hover and
// text selection survive the poll. Everything is built with DOM nodes and textContent: prompts,
// labels and tool calls are untrusted. The only innerHTML is the constant SVG marks below.

const POLL_MS = 2000;
const FETCH_TIMEOUT_MS = 8000;
const STALE_S = 10;                   // no successful poll this long → say so in the header
const CAP = 8;                        // agent lines per phase in the detail
const DUP = 3;                        // this many agents doing the same thing share one line
const LIVE = new Set(["running", "stalled"]);
const GROUPS = [["need", "Needs you"], ["turn", "Your turn"], ["work", "Working"], ["idle", "Idle"]];
const SESSION_MARK = { need: "blocked", turn: "turn", work: "working", idle: "idle" };
const MARK_TITLE = { need: "Needs you", turn: "Your turn", work: "Working", idle: "Idle" };
const TIME_KEYS = new Set(["since", "elapsed", "updated_at", "last_activity", "now"]);
const DULL = /^(cont[a-z]*nue|go on|sigue|ok|okay|yes|si|sí)\b[\s.!]*$/i;
const $ = (id) => document.getElementById(id);

// --- marks: one authored set on a 16-unit grid, 1.5 stroke; meaning lives in text and titles ---

const NS = "http://www.w3.org/2000/svg";
const RING = (r) => `<circle cx="8" cy="8" r="${r}" fill="none" stroke="currentColor" stroke-width="1.5"/>`;
const ICON = {
  blocked: '<circle cx="8" cy="8" r="7" fill="currentColor"/><path class="ink" d="M8 4.2v4.4" stroke-width="1.5" stroke-linecap="round"/><circle class="ink" cx="8" cy="11.3" r="1"/>',
  turn: '<circle cx="8" cy="8" r="4" fill="currentColor"/>',
  working: '<circle cx="8" cy="8" r="3" fill="currentColor"/>',
  idle: RING(4),
  running: RING(5),
  stalled: RING(5.5) + '<path d="M6.6 5.9v4.2M9.4 5.9v4.2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
  failed: '<path d="M4.5 4.5l7 7M11.5 4.5l-7 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
  completed: '<path d="M3.5 8.4l3 3 6-6.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
  interrupted: RING(5.5) + '<path d="M4.4 11.6l7.2-7.2" stroke="currentColor" stroke-width="1.5"/>',
  pending: '<circle cx="8" cy="8" r="2" fill="currentColor"/>',
  chev: '<path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
  copy: '<rect x="5.5" y="5.5" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10.5 3.5v-.5a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h.5" fill="none" stroke="currentColor" stroke-width="1.5"/>',
};

function svg(key, extra) {
  const el = document.createElementNS(NS, "svg");
  el.setAttribute("viewBox", "0 0 16 16");
  el.setAttribute("aria-hidden", "true");
  el.setAttribute("class", `mk mk-${key}` + (extra ? " " + extra : ""));
  el.innerHTML = ICON[key] || ICON.pending;      // constants only, never data
  return el;
}

// --- small helpers ---------------------------------------------------------------------

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false || kid === "") continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

// "<1m", "32m", "2h 05m", "1d 03h": one format everywhere; the words around it say what it measures.
function dur(s) {
  s = Math.max(0, Math.floor(s || 0));
  if (s < 60) return "<1m";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m";
  const hr = Math.floor(m / 60);
  if (hr < 24) return hr + "h " + String(m % 60).padStart(2, "0") + "m";
  return Math.floor(hr / 24) + "d " + String(hr % 24).padStart(2, "0") + "h";
}
// Absolute local time, for titles only: "18:42" today, "Thu 18:42" otherwise.
function clock(t) {
  const d = new Date(t * 1000), today = new Date();
  const hm = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  return d.toDateString() === today.toDateString() ? hm
    : d.toLocaleDateString("en-US", { weekday: "short" }) + " " + hm;
}
const plural = (n, word) => n + " " + word + (n === 1 ? "" : "s");
const tokens = (n) => !n ? "" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M tokens"
  : n >= 1e3 ? Math.round(n / 1e3) + "k tokens" : n + " tokens";
const quoted = (t) => "“" + t + "”";
const prefix = (s) => String(s.session_id || "").slice(0, 8);
const shortHost = (host) => String(host || "").split(".")[0];

function informative(s) {
  const p = (s.prompt || "").trim();
  return p.split(/\s+/).length >= 4 && !DULL.test(p) && p !== s.name && p !== s.title ? p : "";
}

function store(area, key, def) {
  try { const v = JSON.parse(area.getItem(key)); return v == null ? def : v; } catch (e) { return def; }
}
function put(area, key, value) {
  try { area.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode or full: keep it in memory */ }
}

// --- view state, kept across polls and reloads -------------------------------------------

const open = new Set(store(sessionStorage, "herd.open", []));   // rows the user opened
const folds = new Set(store(sessionStorage, "herd.fold", []));  // finished folds the user opened
let seen = store(localStorage, "herd.seen", {});                 // "<sid>:<state_changed_at>" → true
const sticky = new Set();       // seen this visit, still held in Your turn until the user lets go
const rows = new Map();         // session_id → {li, sig, s, timers, group}
const sections = {};            // group → {section, ul, n}
let last = null;                // the latest good payload
let first = true;               // the first render places rows without the arrival fade
let blockedBefore = null;
const net = { ok: 0, failSince: 0, err: "", signedOut: false };

const saveOpen = () => { put(sessionStorage, "herd.open", [...open]); put(sessionStorage, "herd.fold", [...folds]); };
const changedAt = (s) => s.state_changed_at != null ? s.state_changed_at : Math.round(s.now - (s.since || 0));
const seenKey = (s) => s.session_id + ":" + changedAt(s);

// --- the model: what each session shows, derived only from server fields ---------------

function counts(p) { return (p && p.counts) || {}; }
// A list field as its object entries only: a stray null must not take the page down.
const objs = (v) => (Array.isArray(v) ? v.filter((y) => y && typeof y === "object") : []);

// One workflow: current phase, total, live counts and problems (5.2), each with a fallback
// for a server that doesn't send the field yet.
function wmodel(w) {
  const phases = objs(w.phases);
  let cur = w.current_phase;
  if (cur === undefined) {
    cur = phases.findIndex((p) => counts(p).running || counts(p).stalled);
    if (cur < 0) phases.forEach((p, i) => { if (Object.values(counts(p)).some(Boolean)) cur = i; });
    if (cur < 0) cur = null;
  }
  if (cur != null && !phases[cur]) cur = null;
  const live = LIVE.has(w.status) && !w.superseded_by;
  let probs = w.problems;
  if (!probs) {
    probs = { stalled: 0, failed: 0 };
    if (live && cur != null) phases.slice(cur).forEach((p) => {
      probs.stalled += counts(p).stalled || 0; probs.failed += counts(p).failed || 0;
    });
  }
  const sum = (k) => phases.reduce((n, p) => n + (counts(p)[k] || 0), 0);
  return { w, phases, cur, live, total: w.phase_total == null ? null : w.phase_total,
           running: sum("running"), failedAll: sum("failed"),
           stalled: live ? probs.stalled || 0 : 0, failed: live ? probs.failed || 0 : 0 };
}

// A phase's state for its mark and strip segment.
function phaseState(m, i) {
  const c = counts(m.phases[i]);
  const started = (c.running || 0) + (c.stalled || 0) + (c.done || 0) + (c.failed || 0) + (c.interrupted || 0);
  const problem = m.live && m.cur != null && i >= m.cur && (c.stalled || 0) + (c.failed || 0) > 0;
  if (problem) return c.stalled ? "stalled" : "failed";
  if (m.cur != null && i === m.cur) return "running";
  if (m.cur != null && i < m.cur) return "done";
  if (c.running) return "running";
  return started ? "done" : "pending";
}

function phaseWords(m, i, short) {
  const c = counts(m.phases[i]);
  const problem = m.live && m.cur != null && i >= m.cur;
  const out = [];
  for (const k of ["done", "running", "stalled", "failed", "interrupted", "retried"]) {
    if (!c[k]) continue;
    const word = c[k] + " " + k;
    out.push(problem && (k === "stalled" || k === "failed") ? h("span", { class: "warn" }, word) : word);
  }
  // A phase the run has moved past without agents of its own: the run it resumes did it, or the
  // script skipped it. Either way not "not started" next to a done mark.
  if (!out.length) return [m.cur != null && i < m.cur ? (m.w.resumed_from ? "done in the earlier run" : "skipped") : "not started"];
  if (short && out.length === 1 && c.done) return ["done"];
  return out.flatMap((p, j) => (j ? [", ", p] : [p]));
}

// Which group a session belongs to; the graph view marks its sessions the same way.
function groupOf(s) {
  if (s.state === "blocked") return "need";
  if (s.state === "done") return seen[seenKey(s)] && !sticky.has(s.session_id) ? "idle" : "turn";
  return "work";
}

function model(s, twins) {
  const wfs = objs(s.workflows).map(wmodel);
  const live = wfs.filter((m) => m.live);
  const finished = wfs.filter((m) => !m.live && !m.w.superseded_by)
    .sort((a, b) => (b.w.ended_at || 0) - (a.w.ended_at || 0));
  const agents = objs(s.agents);
  const liveAgents = agents.filter((a) => LIVE.has(a.status));
  const problem = live.some((m) => m.stalled + m.failed > 0) || liveAgents.some((a) => a.status === "stalled");
  return { s, live, finished, agents, liveAgents, problem, group: groupOf(s), twin: twins[s.name] > 1,
           known: ["blocked", "done", "working"].includes(s.state) };
}

function order(xs) {
  const by = {};
  for (const [g] of GROUPS) by[g] = xs.filter((x) => x.group === g);
  const ch = (x) => changedAt(x.s);
  const start = (x) => (x.s.started_at != null ? x.s.started_at : x.s.pid || 0);
  by.need.sort((a, b) => ch(a) - ch(b));
  by.turn.sort((a, b) => ch(a) - ch(b));
  by.work.sort((a, b) => (b.problem - a.problem) || start(a) - start(b) || (a.s.session_id < b.s.session_id ? -1 : 1));
  by.idle.sort((a, b) => ch(b) - ch(a));
  return by;
}

// --- timers: time text updated in place each poll, without a rebuild --------------------

let timers = null;                    // the list the row being built registers into
function timed(el, fn) { timers.push({ el, fn }); return el; }
function runTimers(entry, now) {
  for (const t of entry.timers) {
    const r = t.fn(entry.s, now) || {};
    if (r.text != null && t.el.dataset.text !== r.text) { t.el.dataset.text = r.text; setText(t.el, r.text); }
    if (r.title != null && t.el.title !== r.title) t.el.title = r.title;
    if (r.warn != null) t.el.classList.toggle("warn", r.warn);
  }
}
// Time cells may carry a chevron before the text: replace only the text node.
function setText(el, text) {
  const node = [...el.childNodes].find((n) => n.nodeType === 3 || (n.classList && n.classList.contains("tt")));
  if (node) node.textContent = text;
  else el.append(text);
}
const findWf = (s, id) => objs(s.workflows).find((w) => w.id === id);
const findAgent = (s, wid, aid) => {
  const w = wid ? findWf(s, wid) : null;
  const list = w ? objs(w.phases).flatMap((p) => objs(p.agents)) : objs(s.agents);
  return list.find((a) => a.id === aid);
};

// --- one row --------------------------------------------------------------------------

function sessionTime(x) {
  return (s, now) => {
    const at = changedAt(s), g = x.group;
    if (g === "need") return { text: dur(now - at), title: "Waiting for you since " + clock(at), warn: false };
    if (g === "turn" || g === "idle") {
      return { text: dur(now - at), title: "Finished at " + clock(at) + ", waiting for your next prompt", warn: false };
    }
    if (s.quiet && s.last_activity) {
      const q = dur(now - s.last_activity);
      return { text: "quiet " + q, warn: true,
               title: `No writes for ${q} and nothing running. Working since ${clock(at)}` };
    }
    return { text: dur(now - at), warn: false,
             title: "In this state since " + clock(at) + (s.last_activity ? ` · last activity ${dur(now - s.last_activity)} ago` : "") };
  };
}

function line2(x) {
  const s = x.s;
  if (x.group === "need") {
    return h("span", { class: "l2 need", title: s.asking ? `Waiting for ${s.waiting_for || "you"} — ${s.asking}` : null },
      h("span", { class: "bad" }, s.waiting_for ? "Waiting for " + s.waiting_for : "Waiting for you"),
      s.asking ? [" — ", h("span", { class: "ask" }, s.asking)] : null);
  }
  if (x.group === "turn") {
    // Open, the row shows the whole reply under "Last reply"; this line then hides (CSS).
    if (s.reply) return h("span", { class: "l2 reply", title: s.reply }, s.reply);
    const p = informative(s);
    if (p) return h("span", { class: "l2 quote", title: p }, quoted(p));
    const f = finishedSummary(x, true);
    return f ? h("span", { class: "l2 meta" }, f) : null;
  }
  if (x.group === "work") {
    if (!x.known) return h("span", { class: "l2 quote" }, `Status unknown (${s.state || "?"})`);
    if (x.live.length) return null;
    const p = informative(s);
    return p ? h("span", { class: "l2 quote", title: p }, quoted(p)) : null;
  }
  return null;
}
const promptOnLine2 = (x) => (x.group === "turn" && !x.s.reply) || (x.group === "work" && x.known && !x.live.length);

function finishedSummary(x, verbFirst) {
  const n = x.finished.length;
  if (!n) return "";
  const f = x.finished.reduce((k, m) => k + m.failedAll, 0);
  const runs = (st) => x.finished.filter((m) => m.w.status === st).length;
  const head = verbFirst ? plural(n, "workflow") + " finished" : n + " finished " + (n === 1 ? "workflow" : "workflows");
  // How runs ended, then agent failures: muted words, never colour.
  return [head, runs("failed") && runs("failed") + " failed", runs("killed") && runs("killed") + " killed",
    runs("interrupted") && runs("interrupted") + " interrupted", f && plural(f, "agent") + " failed"]
    .filter(Boolean).join(" · ");
}

function wfName(w) {
  return !w.name || w.name === w.id ? "Unnamed workflow" : w.name;
}

function readout(m) {
  const parts = [];
  if (m.cur == null) parts.push("Starting");
  else {
    const t = m.phases[m.cur].title || "Phase";
    // "Verify, 2 of 3" stays on one line; only a long phase title may wrap, and never inside "2 of 3".
    const n = m.total ? `${m.cur + 1} of ${m.total}` : `phase ${m.cur + 1}`;
    parts.push(t.length <= 24 ? h("span", { class: "nw" }, `${t}, ${n}`) : h("span", {}, t + ", ", h("span", { class: "nw" }, n)));
  }
  if (m.running) parts.push(m.running + " running");
  if (m.stalled) parts.push(h("span", { class: "warn" }, m.stalled + " stalled"));
  if (m.failed) parts.push(h("span", { class: "warn" }, m.failed + " failed"));
  return glued(parts);
}
// Facts joined by " · ", each separator glued to the fact after it, so a wrap never leaves a dot
// dangling at a line end.
const glued = (parts) => parts.filter((p) => p != null && p !== false && p !== "")
  .map((p, i) => (i ? h("span", { class: "nw" }, " · ", p) : p));

function wfMark(m) {
  if (m.failed) return "failed";
  return m.stalled || m.w.status === "stalled" ? "stalled" : "running";
}

function strip(m) {
  const fill = { done: "done", running: "cur", stalled: "warn", failed: "warn", pending: "" };
  const title = m.phases.map((p, i) => (p.title || "Phase " + (i + 1)) + ": " +
    phaseWords(m, i, true).map((n) => (typeof n === "string" ? n : n.textContent)).join("") + ".").join(" ");
  return h("span", { class: "strip", title: title || null },
    m.phases.map((p, i) => h("i", { class: fill[phaseState(m, i)] || null })));
}

function wline(m) {
  const w = m.w, id = w.id;
  const el = h("div", { class: "wline" },
    h("span", { class: "wl" },
      h("span", { class: "wn", title: wfName(w) === w.name ? null : w.id },
        svg(wfMark(m), m.failed ? "live" : ""), wfName(w), w.resumed_from ? h("span", { class: "res" }, ", resumed") : null),
      h("span", { class: "sep" }, " · "),
      h("span", { class: "ro" }, readout(m))),
    m.phases.length && m.total != null ? strip(m) : null);   // no declared total: a strip would read "1 of 1"
  return timed(el, (s, now) => {
    const cw = findWf(s, id) || w;
    const st = cw.started_at ? ", started " + clock(cw.started_at) : "";
    return { title: (cw.status === "stalled" ? "Stalled, running " : "Running ") + dur(cw.elapsed) + st };
  });
}

function subLine(x) {
  const a = x.liveAgents, stalled = a.filter((y) => y.status === "stalled").length;
  const running = a.length - stalled;
  const newest = a.reduce((b, y) => (!b || (y.started_at || 0) > (b.started_at || 0) ? y : b), null);
  return h("div", { class: "wline" },
    h("span", { class: "wl" },
      h("span", { class: "wn" }, svg(stalled ? "stalled" : "running"), "Subagents"),
      h("span", { class: "sep" }, " · "),
      h("span", { class: "ro" }, glued([running ? running + " running" : null,
        stalled ? h("span", { class: "warn" }, stalled + " stalled") : null]),
        // Its own separator, which the phone layout hides when the doing drops to its own line.
        newest && newest.doing ? [h("span", { class: "sep" }, " · "),
          h("span", { class: "doing", title: newest.doing }, newest.doing)] : null)));
}

function buildRow(entry, x) {
  const s = x.s, sid = s.session_id, isOpen = open.has(sid);
  timers = [];
  const timeEl = timed(h("span", { class: "time" }, svg("chev", "chev"), h("span", { class: "tt" })), sessionTime(x));
  const head = h("button", { type: "button", class: "head", "data-k": "head", "aria-expanded": String(isOpen),
                             "aria-controls": "d-" + sid, onclick: () => toggle(sid) },
    h("span", { class: "m", title: MARK_TITLE[x.group] }, svg(SESSION_MARK[x.group]),
      h("span", { class: "sr" }, MARK_TITLE[x.group] + ". ")),
    h("span", { class: "l1" },
      h("span", { class: "name", title: s.name }, s.name || "Unnamed session"),
      x.twin ? h("span", { class: "twin", title: s.cwd }, s.dir) : null,
      s.kind === "bg" ? h("span", { class: "bgtag" }, "Background") : null),
    timeEl,
    line2(x));
  const lines = x.live.map(wline);
  if (x.liveAgents.length) lines.push(subLine(x));
  // The workflow lines sit outside the head button; describe the head by them, so a screen
  // reader moving head to head hears each run's health too.
  lines.forEach((ln, i) => { ln.id = `w-${sid}-${i}`; ln.addEventListener("click", () => toggle(sid)); });
  if (lines.length) {
    lines[lines.length - 1].classList.add("last");
    head.setAttribute("aria-describedby", lines.map((ln) => ln.id).join(" "));
  }
  const det = isOpen ? detail(x) : h("div", { class: "detail", id: "d-" + sid, hidden: true });
  entry.li.className = "row" + (isOpen ? " open" : "") + (x.group === "idle" ? " idle" : "");
  entry.li.replaceChildren(head, ...lines, det);
  entry.timers = timers;
  timers = null;
}

// --- the expanded detail ----------------------------------------------------------------

function detail(x) {
  const s = x.s, kids = [];
  const ident = `pid ${s.pid} · session ${s.session_id}`;
  kids.push(h("div", { class: "meta" },
    s.dir && s.dir !== "~" ? h("span", { class: "mono", title: s.cwd }, s.dir) : h("span", { title: s.cwd }, "Home folder"),
    s.branch ? h("span", {}, "on ", h("span", { class: "mono" }, s.branch)) : null,
    s.kind === "bg" ? h("span", {}, "Background session, no terminal") : null,
    s.tmux ? copyButton(s, ident)
      : h("span", { class: "noattach end", title: `herd go can't attach. Its terminal is in ${s.cwd}.\n${ident}` }, "Not in tmux")));
  // The pending tool call is not repeated here: an open row unclamps line 2 instead (CSS).
  const p = informative(s);
  if (p && !promptOnLine2(x)) {
    kids.push(h("div", { class: "block" }, h("span", { class: "label" }, "Your last prompt"), h("p", { title: p }, p)));
  }
  if (s.state === "done" && s.reply) {
    kids.push(h("div", { class: "block" }, h("span", { class: "label" }, "Last reply"), h("p", { title: s.reply }, s.reply)));
  }
  for (const m of x.live) kids.push(liveBlock(m));
  if (x.agents.length) kids.push(subBlock(x));
  if (x.finished.length) kids.push(finishedFold(x));
  return h("div", { class: "detail", id: "d-" + s.session_id }, kids);
}

function liveBlock(m) {
  const w = m.w, id = w.id;
  const stalledRun = w.status === "stalled";
  const t = timed(h("span", { class: "t" + (stalledRun ? " warn" : ""), title: w.started_at ? "Started " + clock(w.started_at) : null }),
    (s, now) => {
      const cw = findWf(s, id) || w;
      return cw.status === "stalled"
        ? { text: "stalled " + dur(now - (cw.last_activity || cw.ended_at || now)) }
        : { text: "running " + dur(cw.elapsed) };
    });
  const kids = [h("div", { class: "wf-h" },
    h("span", { class: "wfn", title: wfName(w) === w.name ? null : w.id }, wfName(w),
      w.resumed_from ? h("span", { class: "res" }, ", resumed") : null), t)];
  if (w.description) kids.push(h("div", { class: "wf-d", title: w.description }, w.description));
  else if (w.started_at) kids.push(h("div", { class: "wf-d", title: w.id }, "Run started " + clock(w.started_at)));
  if (w.resumed_from) {
    kids.push(timed(h("div", { class: "wf-r" }), (s, now) => {
      const cw = findWf(s, id) || w;
      const why = earlierRun(findWf(s, w.resumed_from));
      return { text: `Resumed ${dur(now - (cw.started_at || now))} ago.` + (why ? " " + why : "") };
    }));
  }
  if (m.phases.length) kids.push(phaseGrid(m));
  return h("section", { class: "wf" }, kids);
}

// Why the run was resumed, from what became of the run it continues.
// Unknown (aged out, or still listed as live) says nothing rather than guess.
function earlierRun(o) {
  const st = o && o.status;
  if (st === "interrupted") return "The earlier run stopped when its session restarted.";
  if (st === "failed") {
    const err = String(o.error || "").replace(/\s+/g, " ").trim();
    return "The earlier run failed" + (err ? ": " + (err.length > 160 ? err.slice(0, 159) + "…" : err) : "") +
      (/[.!?…]$/.test(err) ? "" : ".");
  }
  if (st === "killed") return "The earlier run was stopped.";
  if (st === "completed") return "The earlier run finished.";
  return "";
}

const RANK = { stalled: 0, failed: 1, running: 2 };

function phaseGrid(m) {
  const grid = h("div", { class: "pgrid" });
  const wid = m.w.id;
  m.phases.forEach((p, i) => {
    const st = phaseState(m, i);
    const markKey = st === "done" ? "completed" : st;
    grid.append(
      h("span", { class: "c-m" }, svg(markKey, st === "failed" ? "live" : "")),
      h("span", { class: "c-t" + (i === m.cur ? " cur" : "") }, p.title || "Phase " + (i + 1)),
      h("span", { class: "c-i wrap" }, phaseWords(m, i, false)),
      h("span", { class: "c-s" }));
    const lines = agentLines(p);
    const shown = lines.slice(0, CAP);
    for (const ln of shown) grid.append(...agentCells(ln, wid, m.live));
    if (lines.length > CAP) {
      const total = lines.reduce((n, l) => n + l.n, 0), some = shown.reduce((n, l) => n + l.n, 0);
      grid.append(h("span", { class: "cap" }, `Showing ${some} of ${total} unfinished agents`));
    }
  });
  return grid;
}

// Unfinished agents of one phase as display lines: stalled, failed, running; three or more doing
// the same thing share a line; retried attempts come last, muted.
function agentLines(p) {
  const un = objs(p.agents).filter((a) => a.status !== "done");
  const rest = un.filter((a) => !a.superseded)
    .sort((a, b) => (RANK[a.status] == null ? 3 : RANK[a.status]) - (RANK[b.status] == null ? 3 : RANK[b.status]));
  const byDoing = new Map();
  for (const a of rest) {
    if (!a.doing) continue;
    const k = a.status + "\0" + a.doing;
    byDoing.set(k, (byDoing.get(k) || 0) + 1);
  }
  const lines = [], done = new Set();
  for (const a of rest) {
    const k = a.status + "\0" + a.doing;
    if (a.doing && byDoing.get(k) >= DUP) {
      if (done.has(k)) continue;
      done.add(k);
      lines.push({ kind: "group", n: byDoing.get(k), a });
    } else lines.push({ kind: "one", n: 1, a });
  }
  for (const a of un.filter((y) => y.superseded)) {
    const later = rest.find((y) => y.label === a.label && LIVE.has(y.status));
    lines.push({ kind: "retried", n: 1, a, later: later ? "running" : "finished" });
  }
  return lines;
}

function agentCells(ln, wid, liveRun) {
  const a = ln.a;
  if (ln.kind === "retried") {
    return [h("span", {}),
      h("span", { class: "c-l retried", title: `${a.label}\n${a.id}` }, a.label),
      h("span", { class: "c-i retried" }, `retried, later attempt ${ln.later}`),
      h("span", { class: "c-s" })];
  }
  const label = ln.kind === "group" ? `${ln.n} agents` : a.label;
  const status = statusCell(a, wid, liveRun && ln.kind === "one" ? a.id : null);
  return [h("span", {}),
    h("span", { class: "c-l", title: ln.kind === "group" ? null : `${a.label}\n${a.id}` }, label),
    h("span", { class: "c-i", title: a.doing || null }, a.doing || ""),
    status];
}

function statusCell(a, wid, aid) {
  const warn = a.status === "stalled" || a.status === "failed";
  const el = h("span", { class: "c-s" + (warn ? " warn" : "") });
  if (a.status === "stalled") {
    return timed(el, (s, now) => {
      const cur = (aid && findAgent(s, wid, aid)) || a;
      return { text: cur.updated_at ? "stalled " + dur(now - cur.updated_at) : "stalled" };
    });
  }
  if (a.status === "failed") el.textContent = "failed";
  else if (a.status === "interrupted" || a.status === "stopped") el.textContent = a.status;
  return el;
}

function subBlock(x) {
  const grid = h("div", { class: "pgrid" });
  for (const a of x.agents) {
    const live = LIVE.has(a.status);
    const key = a.status === "done" ? "completed" : a.status === "stopped" || a.status === "interrupted" ? "interrupted" : a.status;
    const el = h("span", { class: "c-s" + (a.status === "stalled" ? " warn" : "") });
    if (a.status === "stalled") {
      timed(el, (s, now) => {
        const cur = findAgent(s, null, a.id) || a;
        return { text: cur.updated_at ? "stalled " + dur(now - cur.updated_at) : "stalled" };
      });
    } else if (a.status === "failed") el.textContent = "failed";
    const info = live && a.doing ? a.doing : a.description || "";
    grid.append(
      h("span", { class: "c-m" }, svg(ICON[key] ? key : "pending")),
      h("span", { class: "c-l", title: `${a.type}\n${a.id}` }, a.type || "agent"),
      h("span", { class: "c-i" + (live && a.doing ? " mono" : ""), title: info || null }, info),
      el);
  }
  return h("section", { class: "wf" }, h("div", { class: "wf-h" }, h("span", { class: "wfn" }, "Subagents")), grid);
}

function finishedFold(x) {
  const sid = x.s.session_id, isOpen = folds.has(sid);
  const btn = h("button", { type: "button", class: "fold", "data-k": "fold", "aria-expanded": String(isOpen),
                            onclick: () => { isOpen ? folds.delete(sid) : folds.add(sid); saveOpen(); redraw(); } },
    svg("chev"), finishedSummary(x, false));
  if (!isOpen) return h("div", {}, btn);
  const grid = h("div", { class: "fin" });
  for (const m of x.finished) {
    const w = m.w, id = w.id;
    const key = w.status === "completed" ? "completed" : w.status === "failed" ? "failed" : "interrupted";
    const word = { completed: "", failed: "Failed", killed: "Killed", interrupted: "Interrupted" }[w.status];
    const tip = [w.description, tokens(w.tokens), w.error].filter(Boolean).join("\n");
    grid.append(
      h("span", { class: "c-m" }, svg(key)),
      h("span", { class: "c-n" }, h("span", { class: "nm", title: tip || (wfName(w) === w.name ? null : w.id) }, wfName(w)),
        word === "" ? null : h("span", { class: "st" }, word || "Interrupted")),
      h("span", { class: "fx" }, plural(w.agent_count || 0, "agent") + (m.failedAll ? `, ${m.failedAll} failed` : "")),
      h("span", { class: "fx" }, "took " + dur(w.elapsed)),
      timed(h("span", { class: "c-s" }), (s, now) => {
        const cw = findWf(s, id) || w;
        return { text: cw.ended_at ? dur(now - cw.ended_at) + " ago" : "" };
      }));
  }
  return h("div", {}, btn, grid);
}

function copyButton(s, ident) {
  const cmd = "herd go " + prefix(s);
  const label = h("span", { class: "lbl" }, "Copy");
  const code = h("code", {}, cmd);
  const btn = h("button", { type: "button", class: "copy end", "data-k": "copy",
                            title: `Attaches to tmux ${s.tmux}\n${ident}`,
                            onclick: () => copy(btn, s) }, svg("copy"), label, code);
  return btn;
}

async function copy(btn, s) {
  const cmd = "herd go " + prefix(s);
  const label = btn.querySelector(".lbl"), code = btn.querySelector("code");
  let ok = false;
  try {
    if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("no clipboard");
    await navigator.clipboard.writeText(cmd);
    ok = true;
  } catch (e) {
    ok = false;
  }
  if (s.state === "done") markSeen(s);
  clearTimeout(btn._t);
  if (ok) {
    label.textContent = "Copied";
    code.hidden = true;
    btn._t = setTimeout(() => { label.textContent = "Copy"; code.hidden = false; }, 1500);
  } else {
    label.textContent = "Copy failed — select the text";
    code.hidden = false;
    const sel = window.getSelection();
    if (sel) sel.selectAllChildren(code);
  }
}

// --- actions --------------------------------------------------------------------------

function markSeen(s) {
  const key = seenKey(s);
  if (seen[key]) return;
  seen[key] = true;
  sticky.add(s.session_id);
  put(localStorage, "herd.seen", seen);
}

function toggle(sid, want) {
  const was = open.has(sid), next = want == null ? !was : want;
  if (next === was) return;
  if (next) open.add(sid); else open.delete(sid);
  saveOpen();
  const e = rows.get(sid);
  if (next && e && e.s.state === "done" && e.group === "turn") markSeen(e.s);
  redraw();
}

function redraw() { if (last) render(last); }

// --- the page -------------------------------------------------------------------------

function setup() {
  const app = $("app");
  for (const [g, label] of GROUPS) {
    const n = h("span", { class: "n" });
    const ul = h("ul", { class: "list" });
    const section = h("section", { class: "group", hidden: true, "aria-label": label }, h("h2", {}, label, n), ul);
    app.append(section);
    sections[g] = { section, ul, n };
  }
  document.addEventListener("keydown", onKey);
  window.addEventListener("hashchange", route);
  $("view").addEventListener("click", () => setView(!graphShown()));
  setInterval(header, 1000);
  poll();
  route();
}

// Two views of the same sessions: the list (default) and #graph. The list keeps polling under the
// graph, so the header, title, favicon and notifications stay right and switching back is instant.
const graphShown = () => location.hash === "#graph";
function route() {
  const g = graphShown() && !net.signedOut;
  $("app").hidden = g;
  $("graph").hidden = !g;
  const v = $("view");
  v.setAttribute("aria-pressed", String(g));
  v.title = g ? "Back to the list (g)" : "Show as a graph (g)";
  if (g) graphView.enter();
  else { graphView.leave(); openFromHash(); }
}
function setView(graph) {
  if (graph) { if (graphShown()) route(); else location.hash = "graph"; }
  else { history.pushState(null, "", location.pathname + location.search); route(); }
}

const held = (li) => li.matches(":hover") || li.contains(document.activeElement);

function render(data) {
  last = data;
  const now = data.now;
  const list = objs(data.sessions).filter((s) => s.session_id);
  for (const s of list) s.now = now;
  const twins = {};
  for (const s of list) twins[s.name] = (twins[s.name] || 0) + 1;

  // Prune view state for sessions that are gone, and seen keys that no longer apply.
  const ids = new Set(list.map((s) => s.session_id));
  const keys = new Set(list.map(seenKey));
  for (const k of Object.keys(seen)) if (!keys.has(k)) delete seen[k];
  put(localStorage, "herd.seen", seen);
  for (const set of [open, folds, sticky]) for (const id of [...set]) if (!ids.has(id)) set.delete(id);
  saveOpen();
  // A row held in Your turn after being seen lets go once it is closed and neither hovered nor focused.
  for (const id of [...sticky]) {
    const e = rows.get(id);
    if (!open.has(id) && !(e && held(e.li))) sticky.delete(id);
  }

  const xs = list.map((s) => model(s, twins));
  const by = order(xs);

  // Rebuild the rows whose signature changed, keeping focus where it was.
  for (const x of xs) {
    const sid = x.s.session_id;
    let e = rows.get(sid);
    if (!e) { e = { li: h("li", { "data-sid": sid }), sig: "", timers: [] }; rows.set(sid, e); }
    const prevGroup = e.group;
    e.s = x.s;
    e.group = x.group;
    const sig = JSON.stringify([x.group, open.has(sid), folds.has(sid), x.twin, x.s],
      (k, v) => (TIME_KEYS.has(k) ? undefined : v));
    if (sig !== e.sig) {
      const ae = document.activeElement, had = e.li.contains(ae);
      const k = had ? ((ae.closest && ae.closest("[data-k]")) || {}).dataset : null;
      buildRow(e, x);
      e.sig = sig;
      if (had) {
        const back = (k && k.k && e.li.querySelector(`[data-k="${k.k}"]`)) || e.li.querySelector(".head");
        if (back) back.focus({ preventScroll: true });
      }
    }
    e.arrive = !first && x.group === "need" && prevGroup !== "need";
    runTimers(e, now);
  }
  for (const [sid, e] of rows) if (!ids.has(sid)) { e.li.remove(); rows.delete(sid); }

  // Place rows in order; a hovered or focused row keeps its place inside its group and the rest
  // flow around it. A row whose group changed moves at once: a session that just blocked must
  // not sit under Working while the pointer happens to rest on it.
  for (const [g] of GROUPS) {
    const ul = sections[g].ul;
    let ref = ul.firstChild;
    for (const x of by[g]) {
      const e = rows.get(x.s.session_id), li = e.li;
      while (ref && ref !== li && held(ref)) ref = ref.nextSibling;
      if (li.parentNode === ul && held(li)) { if (ref === li) ref = li.nextSibling; continue; }
      if (ref === li) { ref = li.nextSibling; continue; }
      const ae = document.activeElement, had = li.contains(ae);
      ul.insertBefore(li, ref);
      if (had && document.activeElement !== ae) ae.focus({ preventScroll: true });
      if (e.arrive) {
        li.classList.remove("arrived");
        void li.offsetWidth;
        li.classList.add("arrived");
        li.addEventListener("animationend", () => li.classList.remove("arrived"), { once: true });
      }
    }
  }
  for (const [g] of GROUPS) {
    const { section, ul, n } = sections[g];
    section.hidden = !ul.children.length;
    const c = String(by[g].length);
    if (n.textContent !== c) n.textContent = c;
  }

  const msg = $("msg");
  if (!list.length) {
    msg.replaceChildren("No Claude Code sessions are running on this machine.", h("br"),
      "Start one with ", h("code", {}, "herd new <name> <dir>"), ".");
    msg.hidden = false;
  } else msg.hidden = true;

  summary(by);
  footer(list);
  if (first) { first = false; openFromHash(); }
}

let tally = { need: 0, turn: 0, work: 0, idle: 0 };
function summary(by) {
  tally = { need: by.need.length, turn: by.turn.length, work: by.work.length, idle: by.idle.length };
  const { need, turn, work, idle } = tally;
  // The verdict is a live region: write it only when it changes, or a screen reader repeats it
  // on every poll.
  setVerdict(need ? need + (need === 1 ? " needs you" : " need you")
    : turn ? turn + " waiting for your reply" : "Nothing needs you", need > 0);
  setCounts([need && turn ? turn + " waiting for your reply" : "",
    work ? work + " working" : "", idle ? idle + " idle" : ""].filter(Boolean).join(" · "));
  const host = shortHost(last && last.host), n = need + turn;
  document.title = (n ? `(${n}) ` : "") + "herd" + (host ? " · " + host : "");
  const icon = $("icon"), want = need ? "/static/favicon-alert.svg" : "/static/favicon.svg";
  if (icon.getAttribute("href") !== want) icon.setAttribute("href", want);
  header();
}

function setVerdict(text, bad) {
  const v = $("verdict"), cls = "verdict" + (bad ? " bad" : "");
  if (v.className !== cls) v.className = cls;
  if (v.textContent !== text) v.textContent = text;
}
function setCounts(text) {
  const c = $("counts");
  if (c.textContent !== text) c.textContent = text;
}

// The right slot: the short host normally, a warning when the data is stale or the server is gone.
function header() {
  const el = $("host");
  const nowMs = Date.now();
  let text = last ? shortHost(last.host) : "", warn = false, title = last ? last.host : "";
  if (net.signedOut) { text = ""; title = ""; }
  else if (net.err === "net") {
    warn = true;
    text = !last ? "Can't reach herd serve"
      : nowMs - net.failSince >= STALE_S * 1000 ? `Can't reach herd serve. Last update ${dur((nowMs - net.ok) / 1000)} ago.`
      : "Can't reach herd serve. Retrying…";
  } else if (net.err === "render") {
    warn = true;
    text = "herd serve sent data this page can't show. Retrying…";
  } else if (net.err) {
    warn = true;
    text = `herd serve returned an error (${net.err}). Retrying…`;
  } else if (last && nowMs - net.ok >= STALE_S * 1000) {
    const age = Math.floor((nowMs - net.ok) / 1000);
    warn = true;
    text = "Not updated for " + (age < 60 ? Math.floor(age / 10) * 10 + "s" : dur(age));
  }
  if (el.textContent !== text) el.textContent = text;
  el.classList.toggle("warn", warn);
  if (el.title !== title) el.title = title;
}

let footSig = "";
function footer(list) {
  const noTmux = list.length > 0 && list.every((s) => !s.tmux);
  const ask = "Notification" in window && Notification.permission === "default";
  const sig = JSON.stringify([noTmux, ask]);
  if (sig === footSig) return;
  footSig = sig;
  const kids = [];
  if (noTmux) {
    kids.push(h("p", {}, "None of these sessions runs in tmux, so herd go can't attach to them. Sessions started with ",
      h("code", {}, "herd new"), " can."));
  }
  if (ask) {
    kids.push(h("p", {}, h("button", { type: "button", class: "linkbtn",
      onclick: () => Promise.resolve(Notification.requestPermission()).then(() => { footSig = ""; footer(last ? last.sessions : []); }) },
      "Notify me when a session needs you")));
  }
  $("foot").replaceChildren(...kids);
}

function notify(list) {
  const blocked = new Set(list.filter((s) => s.state === "blocked").map((s) => s.session_id));
  if (blockedBefore && "Notification" in window && Notification.permission === "granted") {
    for (const s of list) {
      if (!blocked.has(s.session_id) || blockedBefore.has(s.session_id)) continue;
      const n = new Notification(`${s.name} needs you`, {
        body: `Waiting for ${s.waiting_for || "you"}` + (s.asking ? ` — ${s.asking}` : ""), tag: s.session_id });
      n.onclick = () => { window.focus(); location.hash = "open=" + prefix(s); n.close(); };
    }
  }
  blockedBefore = blocked;
}

// #open=<session id prefix> opens that row (links from notifications).
function openFromHash() {
  const m = /^#open=([0-9a-f-]{4,})$/i.exec(location.hash);
  if (!m || !last) return;
  const s = (last.sessions || []).find((y) => String(y.session_id).startsWith(m[1]));
  if (!s) return;
  toggle(s.session_id, true);
  const e = rows.get(s.session_id);
  const head = e && e.li.querySelector(".head");
  if (head) { head.focus({ preventScroll: true }); head.scrollIntoView({ block: "nearest" }); }
}

function onKey(e) {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const t = e.target;
  if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
  if (e.key === "g" && !net.signedOut) { setView(!graphShown()); e.preventDefault(); return; }
  if (graphShown()) { graphView.key(e); return; }
  const heads = [...document.querySelectorAll("#app .head")];
  const li = document.activeElement && document.activeElement.closest ? document.activeElement.closest("li.row") : null;
  const at = li ? heads.indexOf(li.querySelector(".head")) : -1;
  const k = e.key;
  if (k === "ArrowDown" || k === "j" || k === "ArrowUp" || k === "k") {
    if (!heads.length) return;
    const down = k === "ArrowDown" || k === "j";
    const i = at < 0 ? (down ? 0 : heads.length - 1) : Math.min(heads.length - 1, Math.max(0, at + (down ? 1 : -1)));
    heads[i].focus();
    e.preventDefault();
  } else if (k === "Escape" && li) {
    toggle(li.dataset.sid, false);
    const head = li.querySelector(".head");
    if (head) head.focus();
  } else if (k === "c" && li) {
    const e2 = rows.get(li.dataset.sid);
    if (!e2 || !e2.s.tmux) return;
    // The Copy button carries the feedback (Copied / Copy failed and the selectable command),
    // so a collapsed row opens first. Focus stays on the head.
    if (!li.querySelector(".copy")) toggle(li.dataset.sid, true);
    const btn = li.querySelector(".copy");
    if (btn) copy(btn, e2.s);
  }
}

async function poll() {
  let again = true;
  const ctl = "AbortController" in window ? new AbortController() : null;
  const timer = ctl ? setTimeout(() => ctl.abort(), FETCH_TIMEOUT_MS) : 0;
  try {
    const r = await fetch("/api/sessions", { cache: "no-store", signal: ctl ? ctl.signal : undefined });
    if (r.status === 401) {
      net.signedOut = true;
      again = false;                  // no point retrying without the token
      signedOut();
    } else if (!r.ok) {
      net.err = String(r.status);
      if (!last) bodyNote(`herd serve returned an error (${r.status}). Retrying…`);
    } else {
      // A payload this page can't parse or draw is not an outage: say so, and leave a trace in
      // the console.
      try {
        const data = await r.json();
        net.ok = Date.now(); net.err = ""; net.failSince = 0;
        notify(objs(data.sessions));
        render(data);
      } catch (e) {
        if (e && e.name === "AbortError") throw e;    // timed out mid-body: staleness, as below
        console.error(e);
        net.err = "render";
        if (!rows.size) bodyNote("herd serve sent data this page can't show. Retrying…");
      }
    }
  } catch (e) {
    if (!(e && e.name === "AbortError")) {      // a slow answer is staleness, not an outage
      if (net.err !== "net") net.failSince = Date.now();
      net.err = "net";
      if (!last) bodyNote("herd serve isn't answering on this port. Is it still running?");
    }
  } finally {
    clearTimeout(timer);
  }
  header();
  if (again) setTimeout(poll, POLL_MS);
}

// The one body sentence shown before there is any data to draw.
function bodyNote(text) {
  const msg = $("msg");
  msg.hidden = false;
  if (msg.textContent !== text) msg.textContent = text;
}

function signedOut() {
  net.signedOut = true;
  graphView.leave();
  $("graph").hidden = true;
  $("app").hidden = false;
  setVerdict("Signed out", false);
  setCounts("");
  for (const [g] of GROUPS) sections[g].section.hidden = true;
  const msg = $("msg");
  msg.hidden = false;
  msg.textContent = "This page needs the access token. Open the URL that herd serve printed.";
  $("foot").replaceChildren();
  footSig = "";
  document.title = "herd";
}

// Deferred scripts all run before DOMContentLoaded, so graph.js is there when setup routes.
document.addEventListener("DOMContentLoaded", setup);
