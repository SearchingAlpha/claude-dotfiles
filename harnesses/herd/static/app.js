"use strict";
// herd dashboard: polls /api/sessions and draws every live session, most urgent first,
// with its workflows (phase bar, agents per phase) and its own subagents underneath.
// Everything is built with DOM nodes and textContent: prompts and labels are untrusted.

const POLL_MS = 2000;
const SECTIONS = [["blocked", "Needs you"], ["done", "Done — your turn"], ["working", "Working"]];
const LIVE = new Set(["running", "stalled"]);
const opened = new Map();             // workflow id → open, so a redraw keeps the user's choice
const $ = (id) => document.getElementById(id);
let blockedSeen = null;
let now = Date.now() / 1000;

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") for (const [p, val] of Object.entries(v)) el.style.setProperty(p, val);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false || kid === "") continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

function ago(s) {
  for (const [u, n] of [["d", 86400], ["h", 3600], ["m", 60]]) if (s >= n) return Math.floor(s / n) + u;
  return Math.max(0, Math.floor(s)) + "s";
}
function tokens(n) {
  return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(n);
}
function hue(id) {
  let x = 0;
  for (const c of id) x = (x * 31 + c.charCodeAt(0)) >>> 0;
  return x % 360;
}
function tone(status) {
  return { blocked: "bad", failed: "bad", killed: "bad", interrupted: "bad", stopped: "bad",
           done: "good", completed: "good", running: "good", stalled: "warn" }[status] || "";
}
function total(phases) {
  const c = { running: 0, stalled: 0, done: 0, failed: 0, interrupted: 0 };
  for (const p of phases) for (const k in c) c[k] += p.counts[k] || 0;
  return c;
}
function busy(s) {
  return s.state === "blocked" || s.workflows.some((w) => LIVE.has(w.status)) || s.agents.some((a) => LIVE.has(a.status));
}

// --- one session --------------------------------------------------------------------

function sessionCard(s) {
  return h("article", { class: `sess ${s.state}`, style: { "--h": hue(s.session_id) } },
    h("div", { class: "sess-head" },
      h("span", { class: `dot ${s.state}`, title: s.state }),
      h("h3", { class: "sname" }, s.name),
      h("span", { class: `chip ${tone(s.state)}` }, `${s.state} · ${ago(s.since)}`),
      s.kind === "bg" && h("span", { class: "chip" }, "background"),
      h("span", { class: "sess-meta" },
        h("span", { class: "chip mono", title: s.cwd }, s.dir),
        s.branch && h("span", { class: "chip mono", title: "git branch" }, "branch " + s.branch),
        s.tmux ? h("span", { class: "chip mono", title: `herd go ${s.tmux.split(":")[0]}` }, "tmux " + s.tmux)
               : h("span", { class: "chip", title: "not in tmux: herd go can't attach" }, "no tmux"),
        h("span", { class: "chip mono", title: s.session_id }, "pid " + s.pid))),
    s.state === "blocked" && h("div", { class: "waiting" }, "Waiting for you" + (s.waiting_for ? ": " + s.waiting_for : "")),
    s.prompt && h("div", { class: "prompt", title: s.prompt }, s.prompt),
    sessionBody(s));
}

function sessionBody(s) {
  const order = (w) => (LIVE.has(w.status) ? 0 : 1);
  const wfs = [...s.workflows].sort((a, b) => order(a) - order(b) || b.started_at - a.started_at);
  if (!wfs.length && !s.agents.length) {
    return h("div", { class: "sess-body" }, h("span", { class: "nothing" }, "No workflows or subagents in the last 24 h"));
  }
  return h("div", { class: "sess-body" },
    wfs.length > 0 && h("div", { class: "sub-h" }, `Workflows · ${wfs.length}`),
    wfs.map(workflow),
    s.agents.length > 0 && h("div", { class: "sub-h" }, `Subagents · ${s.agents.length}`),
    s.agents.map(subagent));
}

// --- workflows ----------------------------------------------------------------------

function workflow(w) {
  const c = total(w.phases);
  const det = h("details", { class: `wf ${w.status}`, open: opened.has(w.id) ? opened.get(w.id) : LIVE.has(w.status) },
    h("summary", {},
      h("div", { class: "wf-top" },
        h("span", { class: `adot ${w.status}` }),
        h("span", { class: "wf-name" }, w.name),
        h("span", { class: `chip ${tone(w.status)}` }, w.status),
        w.description && h("span", { class: "wf-desc", title: w.description }, w.description),
        h("span", { class: "wf-nums" },
          h("span", {}, `${c.done}/${w.agent_count} agents`),
          c.running > 0 && h("span", { class: "run" }, `${c.running} running`),
          c.stalled > 0 && h("span", { class: "stl" }, `${c.stalled} stalled`),
          c.failed > 0 && h("span", { class: "bad" }, `${c.failed} failed`),
          c.interrupted > 0 && h("span", { class: "bad" }, `${c.interrupted} interrupted`),
          h("span", { title: LIVE.has(w.status) ? "running for" : "took" }, ago(w.elapsed)),
          w.tokens > 0 && h("span", { title: "tokens" }, tokens(w.tokens) + " tok"))),
      w.error && h("div", { class: "wf-error", title: w.error }, w.error),
      w.phases.length > 0 && phaseBar(w)),
    agentsByPhase(w));
  det.addEventListener("toggle", () => opened.set(w.id, det.open));
  return det;
}

function phaseBar(w) {
  const live = LIVE.has(w.status);
  const current = live ? w.phases.findIndex((p) => p.counts.running || p.counts.stalled) : -1;
  return h("div", { class: "phases" }, w.phases.map((p, i) => {
    const n = p.agents.length;
    const pct = (x) => (n ? (100 * x) / n : 0) + "%";
    return h("div", { class: "ph" + (i === current ? " current" : ""),
                      style: { "--w": Math.max(1, Math.sqrt(n)).toFixed(2) },
                      title: `${p.title}: ${p.counts.done}/${n} done` },
      h("div", { class: "ph-bar", style: { "--done": pct(p.counts.done), "--run": pct(p.counts.running + p.counts.stalled),
                                           "--bad": pct(p.counts.failed + p.counts.interrupted) } },
        h("span", { class: "ph-done" }), h("span", { class: "ph-run" }), h("span", { class: "ph-bad" })),
      h("div", { class: "ph-label" }, h("span", {}, p.title), h("span", { class: "n" }, n ? `${p.counts.done}/${n}` : "–")));
  }));
}

function agentsByPhase(w) {
  const phases = w.phases.filter((p) => p.agents.length);
  if (!phases.length) return h("div", { class: "wf-agents" }, h("span", { class: "nothing" }, "No agents started yet"));
  return h("div", { class: "wf-agents" }, phases.map((p) => {
    const active = p.agents.filter((a) => a.status !== "done");
    const done = p.agents.filter((a) => a.status === "done");
    return h("div", { class: "wf-phase" },
      h("h4", {}, p.title, h("span", { class: "n" }, `${p.counts.done}/${p.agents.length} done`)),
      active.map((a) => h("div", { class: "arow" },
        h("span", { class: `adot ${a.status}` }),
        h("span", { class: "alabel" }, a.label),
        h("span", { class: `astat ${a.status}` }, a.status),
        a.doing && h("span", { class: "adoing", title: a.doing }, a.doing))),
      done.length > 0 && h("div", { class: "donegrid" },
        done.slice(0, 160).map((a) => h("span", { class: "adot done", title: a.label })),
        done.length > 160 && h("span", { class: "more" }, `+${done.length - 160}`),
        done.length <= 6 && h("span", { class: "more" }, done.map((a) => a.label).join(" · "))));
  }));
}

function subagent(a) {
  return h("div", { class: "arow" },
    h("span", { class: `adot ${a.status}` }),
    h("span", { class: "alabel" }, a.type),
    h("span", {}, a.description),
    a.background && h("span", { class: "chip" }, "bg"),
    h("span", { class: `astat ${a.status}` }, `${a.status} · ${ago(now - a.updated_at)}`),
    a.doing && h("span", { class: "adoing", title: a.doing }, a.doing));
}

// --- page ---------------------------------------------------------------------------

function render(data) {
  now = data.now;
  const all = data.sessions;
  const shown = $("quiet").checked ? all.filter(busy) : all;
  const n = (st) => all.filter((s) => s.state === st).length;
  $("counts").replaceChildren(
    h("span", { class: "chip bad" }, `${n("blocked")} need you`),
    h("span", { class: "chip good" }, `${n("done")} done`),
    h("span", { class: "chip" }, `${n("working")} working`));
  document.title = n("blocked") ? `(${n("blocked")}) herd` : "herd";
  notify(all);
  const sections = SECTIONS.map(([key, label]) => {
    const rows = shown.filter((s) => s.state === key);
    return rows.length && h("section", { class: `section ${key}` },
      h("h2", {}, label, h("span", {}, String(rows.length))), rows.map(sessionCard));
  }).filter(Boolean);
  const other = shown.filter((s) => !SECTIONS.some(([key]) => key === s.state));
  if (other.length) sections.push(h("section", { class: "section" }, h("h2", {}, "Other"), other.map(sessionCard)));
  $("app").replaceChildren(...(sections.length ? sections
    : [h("p", { class: "empty" }, all.length ? "Nothing running — untick the filter to see every session." : "No live Claude Code sessions.")]));
}

function notify(all) {
  const blocked = new Set(all.filter((s) => s.state === "blocked").map((s) => s.session_id));
  if (blockedSeen && "Notification" in window && Notification.permission === "granted") {
    for (const s of all) {
      if (blocked.has(s.session_id) && !blockedSeen.has(s.session_id)) {
        new Notification(`${s.name} needs you`, { body: s.waiting_for || s.prompt || "", tag: s.session_id });
      }
    }
  }
  blockedSeen = blocked;
}

async function poll() {
  const status = $("status");
  try {
    const r = await fetch("/api/sessions", { cache: "no-store" });
    if (r.status === 401) {
      status.textContent = "signed out — open the URL printed by herd serve";
      status.classList.add("stale");
      return;                           // no point retrying without the token
    }
    if (!r.ok) throw new Error(String(r.status));
    const data = await r.json();
    poll.last = data;
    render(data);
    status.classList.remove("stale");
    status.textContent = `${data.host} · updated ${new Date(data.now * 1000).toLocaleTimeString()}`;
  } catch (e) {
    status.classList.add("stale");
    status.textContent = "can't reach herd serve — retrying";
  }
  setTimeout(poll, POLL_MS);
}

function setup() {
  const quiet = $("quiet");
  quiet.checked = localStorage.getItem("herd.quiet") === "1";
  quiet.addEventListener("change", () => {
    localStorage.setItem("herd.quiet", quiet.checked ? "1" : "0");
    poll.last && render(poll.last);
  });
  const btn = $("notify");
  if ("Notification" in window && Notification.permission === "default") {
    btn.hidden = false;
    btn.addEventListener("click", () => Notification.requestPermission().then(() => { btn.hidden = true; }));
  }
  poll();
}

setup();
