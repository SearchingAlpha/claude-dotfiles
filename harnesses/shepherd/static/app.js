// shepherd dashboard — vanilla JS, read-only. DOM is built with createElement/textContent only.
"use strict";

const POLL_MS = 3000;
const DETAIL_MS = 10000;
const app = document.getElementById("app");
const statusEl = document.getElementById("status");
let board = null, boardJson = "", lastOk = 0, authFailed = false;
const openFiles = new Set();
let detailTimer = null, activityTimer = null, agentsTimer = null;
let showFinished = localStorage.getItem("shepherd.showFinished") !== "0";

// --- helpers -----------------------------------------------------------------------

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "hue") el.style.setProperty("--h", String(v));
    else if (k === "x") el.style.setProperty("--x", v);
    else if (k === "css") Object.assign(el.style, v);   // CSSOM: the page's CSP forbids style attributes
    else if (k === "onclick") el.addEventListener("click", v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, String(v));
  for (const kid of kids.flat(Infinity)) if (kid) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}

function hue(id) {
  if (!id) return 220;
  let x = 0;
  for (const c of String(id)) x = (x * 31 + c.charCodeAt(0)) >>> 0;
  return x % 360;
}

function ago(ts) {
  if (!ts) return "—";
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

async function api(path) {
  const r = await fetch(path, { credentials: "same-origin", cache: "no-store" });
  if (r.status === 401) { authFailed = true; throw new Error("unauthorized"); }
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

function setStatus() {
  if (authFailed) { statusEl.textContent = "not signed in"; statusEl.className = "status stale"; return; }
  const stale = lastOk && Date.now() - lastOk > POLL_MS * 3;
  statusEl.className = "status" + (stale || !lastOk ? " stale" : "");
  if (!lastOk) { statusEl.textContent = "connecting…"; return; }
  statusEl.replaceChildren(h("span", { class: "status-label" }, "updated "), clock(lastOk / 1000), stale ? " · reconnecting…" : "");
}

function showAuth() {
  app.replaceChildren(h("div", { class: "notice" },
    h("h3", {}, "Not signed in"),
    h("p", {}, "Open the URL printed by ", h("code", {}, "shepherd serve"),
      " (or ", h("code", {}, "/shepherd"), "): it carries the access token. ",
      "Run ", h("code", {}, "shepherd serve --daemon"), " again to see it.")));
}

function sessionLabel(agent) {
  const a = agent || {};
  let cls = "ended", text = "unclaimed";
  if (a.session_id) {
    text = a.name || a.session_id.slice(0, 8);
    cls = !a.live ? "ended" : (a.status === "busy" ? "busy" : "idle");
  }
  const state = a.session_id ? (a.live ? (a.status || "live") : "ended") : "";
  return h("span", { class: "who", title: a.session_id ? `session ${a.session_id} · ${state}` : "no owner" },
    h("span", { class: `dot ${cls}` }), h("span", { class: "who-name" }, text, state && state !== "busy" ? ` · ${state}` : ""));
}

const CHECK_ICON = { passing: "✓", failing: "✗", pending: "…", none: "", unknown: "" };
const STATE_CLASS = { merged: "good", approved: "good", changes_requested: "bad", closed: "bad",
  draft: "", open: "", unknown: "" };

function reviewChip(rv) {
  if (!rv || !rv.id) return null;
  const id = /^\d+$/.test(String(rv.id)) ? `#${rv.id}` : String(rv.id);
  const label = `${id} ${String(rv.state || "").replace("_", " ")} ${CHECK_ICON[rv.checks] || ""}`.trim();
  const cls = `chip ${rv.error || rv.stale ? "warn" : STATE_CLASS[rv.state] || ""}`;
  const title = [rv.title, rv.raw_state, rv.error, rv.hint].filter(Boolean).join(" — ");
  const chip = rv.url
    ? h("a", { class: cls, href: rv.url, target: "_blank", rel: "noopener noreferrer", title }, label)
    : h("span", { class: cls, title }, label);
  chip.addEventListener("click", (e) => e.stopPropagation());
  return chip;
}

// what each agent status means, in the words the board shows
const AGENT_STATUS = {
  running: "running", done: "done", stalled: "stalled — no output for 15 min",
  interrupted: "interrupted — its session ended first", stopped: "stopped by the user",
};

function subagentSummary(sum) {
  const order = ["running", "stalled", "interrupted", "stopped", "done"];
  const parts = order.filter((k) => sum && sum[k]);
  if (!parts.length) return null;
  return h("div", { class: "subs", title: "subagents this session spawned (last 24 h)" },
    h("span", { class: "subs-label" }, "subagents"),
    parts.map((k) => h("span", { class: `astat ${k}` }, h("span", { class: `adot ${k}` }), `${sum[k]} ${k}`)));
}

function warnEl(text) {
  const severe = /gone|missing|review:|conflict/i.test(text);
  return h("div", { class: severe ? "w bad" : "w" }, text);
}

// --- fleet --------------------------------------------------------------------------

const COLUMNS = [["active", "Active"], ["review", "In review"], ["merged", "Merged — needs finish"], ["paused", "Paused"]];

function trackHref(repo, name) { return `#/track/${encodeURIComponent(repo.common)}/${encodeURIComponent(name)}`; }

function card(repo, c) {
  const a = c.agent || {};
  const lc = c.last_commit;
  const el = h("a", { class: "card", href: trackHref(repo, c.name), hue: hue(a.session_id || c.name) },
    h("div", { class: "card-top" }, h("span", { class: "tname" }, c.name), sessionLabel(a)),
    h("div", { class: "goal" }, c.goal || "(no goal)"),
    a.title && a.title !== c.goal ? h("div", { class: "title" }, a.title) : null,
    h("div", { class: "meta" },
      h("span", { class: "mono", title: c.worktree || "" }, c.branch),
      h("span", { title: "ahead / behind main" }, `↑${c.ahead} ↓${c.behind}`),
      c.changed_count ? h("span", { title: (c.changed || []).join("\n") }, `${c.changed_count} file${c.changed_count === 1 ? "" : "s"}`) : null,
      c.dirty_count ? h("span", { title: (c.dirty || []).join("\n") }, `${c.dirty_count} uncommitted`) : null),
    lc ? h("div", { class: "last", title: `${lc.short} ${lc.subject}` }, `${lc.short} ${lc.subject} · ${ago(lc.at)}`) : null,
    c.last_activity ? h("div", { class: "last" }, `edited ${c.last_file || ""} · ${ago(c.last_activity)}`) : null,
    c.note ? h("div", { class: "last" }, `note: ${c.note}`) : null,
    subagentSummary(c.subagents),
    c.review && c.review.id ? h("div", { class: "review" }, reviewChip(c.review)) : null,
    c.warnings && c.warnings.length ? h("div", { class: "warns" }, c.warnings.map(warnEl)) : null);
  return el;
}

function lane(repo) {
  const head = h("div", { class: "lane-head" },
    h("h2", { title: repo.primary }, repo.name),
    h("span", { class: `chip ${repo.mode}`, title: repo.mode === "enforce" ? "edits/commits on protected branches are blocked" : "warnings only" }, repo.mode),
    h("span", { class: "chip", title: `base ${repo.base}` }, `protected: ${(repo.protected || [repo.main]).join(", ")}`),
    repo.unpushed ? h("span", { class: "chip warn" }, `${repo.unpushed} unpushed on ${repo.main}`) : null,
    repo.main_dirty ? h("span", { class: "chip bad" }, `${repo.main_dirty} uncommitted on ${repo.main}`) : null,
    (repo.on_main || []).length ? h("span", { class: "chip warn", title: repo.on_main.map((s) => s.name).join(", ") },
      `${repo.on_main.length} session(s) in main checkout`) : null,
    repo.remote ? h("span", { class: "chip", title: `base ${repo.base}` }, repo.remote) : null);
  const kids = [head];
  // the session count is already a chip in the header
  for (const w of (repo.warnings || []).filter((w) => !/session\(s\) working in the main checkout/.test(w))) {
    kids.push(h("div", { class: "lane-warn" }, w));
  }
  if (repo.error) kids.push(h("div", { class: "lane-warn" }, repo.error));
  const cols = h("div", { class: "cols" });
  for (const [status, label] of COLUMNS) {
    const items = (repo.tracks || []).filter((t) => t.status === status);
    cols.append(h("div", { class: items.length ? "col" : "col vacant" }, h("h3", {}, h("span", {}, label), h("span", {}, String(items.length))),
      items.length ? items.map((t) => card(repo, t)) : h("div", { class: "col-empty" }, "—")));
  }
  kids.push(cols);
  const foot = [];
  if ((repo.untracked || []).length) {
    foot.push(h("div", {}, "Worktrees not on a track:",
      h("ul", {}, repo.untracked.map((u) => h("li", {}, h("code", {}, u.path), ` (${u.branch || "detached"}${u.dirty_count ? `, ${u.dirty_count} uncommitted` : ""}) — `, h("code", {}, `shepherd adopt --path ${u.path}`))))));
  }
  if ((repo.overlaps || []).length) {
    foot.push(h("div", {}, "Overlaps:",
      h("ul", {}, repo.overlaps.map((o) => h("li", {}, h("code", {}, o.a), " ↔ ", h("code", {}, o.b), `: ${o.shared.length} shared file${o.shared.length === 1 ? "" : "s"} `,
        o.conflicts && o.conflicts.length ? h("span", { class: "chip bad" }, `conflicts: ${o.conflicts.join(", ")}`) : null)))));
  }
  if (foot.length) kids.push(h("div", { class: "lane-foot" }, foot));
  return h("section", { class: "lane" }, kids);
}

function renderFleet() {
  if (!board) return;
  const kids = [];
  if (!board.repos.length) {
    kids.push(h("div", { class: "notice" }, "No enrolled repos yet. In a repo's checkout run ", h("code", {}, "shepherd init"), "."));
  }
  for (const r of board.repos) kids.push(lane(r));
  if ((board.unmanaged || []).length) {
    kids.push(h("section", { class: "lane" },
      h("div", { class: "lane-head" }, h("h2", {}, "Sessions not on a track"), h("span", { class: "chip" }, String(board.unmanaged.length))),
      h("div", { class: "unmanaged" }, board.unmanaged.map((s) => h("div", { class: "sess", hue: hue(s.session_id) },
        h("div", {}, sessionLabel({ session_id: s.session_id, name: s.name, live: true, status: s.status })),
        s.title ? h("div", { class: "title" }, s.title) : null,
        h("div", { class: "cwd" }, s.cwd || ""))))));
  }
  app.replaceChildren(...kids);
}

// --- track detail -------------------------------------------------------------------

function parseDiff(text) {
  const files = [];
  let cur = null;
  for (const line of (text || "").split("\n")) {
    if (line.startsWith("diff --git ")) {
      const m = line.match(/ b\/(.*)$/);
      cur = { name: m ? m[1] : line.slice(11), lines: [], add: 0, del: 0 };
      files.push(cur);
    } else if (cur && line !== "") {
      if (line.startsWith("+") && !line.startsWith("+++")) cur.add++;
      else if (line.startsWith("-") && !line.startsWith("---")) cur.del++;
      cur.lines.push(line);
    }
  }
  return files;
}

function diffView(text, truncated, key) {
  const files = parseDiff(text);
  if (!files.length) return h("div", { class: "empty" }, "No changes.");
  const out = files.map((f) => {
    const id = `${key}:${f.name}`;
    const det = h("details", { class: "file", open: openFiles.has(id) || files.length <= 3 },
      h("summary", {}, h("span", {}, f.name), h("span", { class: "num" }, h("span", { class: "a" }, `+${f.add}`), " ", h("span", { class: "d" }, `−${f.del}`))));
    det.addEventListener("toggle", () => { det.open ? openFiles.add(id) : openFiles.delete(id); });
    const pre = h("pre", { class: "diff" });
    const shown = f.lines.slice(0, 3000);
    for (const l of shown) {
      if (l.startsWith("index ") || l.startsWith("--- ") || l.startsWith("+++ ")) continue;
      const cls = l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
      pre.append(h("span", { class: cls }, l || " "));
    }
    det.append(pre);
    if (f.lines.length > shown.length) det.append(h("div", { class: "trunc" }, `… ${f.lines.length - shown.length} more lines`));
    return det;
  });
  if (truncated) out.push(h("div", { class: "trunc" }, "Diff truncated at 200 KB — open the branch locally for the rest."));
  return out;
}

function kv(pairs) {
  return h("dl", { class: "kv" }, pairs.filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

function renderDetail(d, common) {
  const t = d.track, a = d.agent || {}, rv = t.review;
  const repo = (board && board.repos.find((r) => r.common === common)) || {};
  const headEl = h("div", { class: "detail-head", hue: hue(a.session_id || t.name) },
    h("div", { class: "card-top" }, h("span", { class: "tname" }, `${repo.name || ""} / ${t.name}`), sessionLabel(a)),
    h("h2", {}, t.goal || "(no goal)"),
    a.title ? h("div", { class: "title" }, a.title) : null,
    h("div", { class: "meta" }, h("span", { class: "chip" }, t.status), h("span", { class: "mono" }, t.branch),
      h("span", {}, `from ${d.base}`), h("span", { class: "mono" }, t.worktree || "(no worktree)"), reviewChip(rv)),
    (() => {
      const c = (repo.tracks || []).find((x) => x.name === t.name);
      const ws = (c && c.warnings) || [];
      const ov = (repo.overlaps || []).filter((o) => o.a === t.name || o.b === t.name)
        .map((o) => `overlaps ${o.a === t.name ? o.b : o.a} on ${o.shared.length} file(s)${o.conflicts && o.conflicts.length ? `; predicted conflicts in ${o.conflicts.join(", ")}` : ""}`);
      return ws.length || ov.length ? h("div", { class: "warns" }, [...ws, ...ov].map(warnEl)) : null;
    })());
  const reviewPanel = h("div", { class: "panel" }, h("h3", {}, "Review"), rv && (rv.id || rv.error)
    ? kv([["state", rv.state], ["checks", rv.checks], ["comments", String(rv.comments ?? "")],
      ["reviewers", (rv.reviewers || []).join(", ")], ["link", rv.url ? h("a", { href: rv.url, target: "_blank", rel: "noopener noreferrer" }, rv.url) : null],
      ["checked", rv.checked_at ? ago(rv.checked_at) : null], ["error", rv.error], ["hint", rv.hint]])
    : h("div", { class: "empty" }, "No review yet — the agent opens one with ", h("code", {}, "shepherd pr"), "."));
  const claims = h("div", { class: "panel" }, h("h3", {}, "Claims"),
    kv([["owns", (t.owns || []).join(", ") || "—"], ["avoids", (t.avoid || []).join(", ")], ["live", (t.live || []).join(", ")],
      ["session", a.session_id ? `${a.name || ""} (${a.session_id.slice(0, 8)})` : "unclaimed"], ["started", ago(t.created_at)], ["note", t.note]]));
  const commits = h("div", { class: "panel" }, h("h3", {}, `Commits (${d.commits.length})`),
    d.commits.length ? h("ul", { class: "list" }, d.commits.map((c) => h("li", {}, h("span", { class: "when" }, ago(c.at)), h("code", {}, c.short), h("span", {}, c.subject)))) : h("div", { class: "empty" }, "None yet."));
  const files = h("div", { class: "panel" }, h("h3", {}, `Files (${d.numstat.length})`),
    d.numstat.length ? h("ul", { class: "list" }, d.numstat.map((f) => h("li", {}, h("span", { class: "num" }, h("span", { class: "a" }, `+${f.added}`), " ", h("span", { class: "d" }, `−${f.deleted}`)), h("code", {}, f.file)))) : h("div", { class: "empty" }, "None yet."));
  const diff = h("div", { class: "panel" }, h("h3", {}, "Committed diff vs main"), diffView(d.diff, d.diff_truncated, "c"));
  const nd = (d.dirty || []).length;
  const wip = h("div", { class: "panel" }, h("h3", {}, `Uncommitted (${nd} file${nd === 1 ? "" : "s"})`),
    (d.dirty || []).length && !d.wip ? h("ul", { class: "list" }, d.dirty.map((f) => h("li", {}, h("code", {}, f), " (untracked)"))) : diffView(d.wip, d.wip_truncated, "w"));
  const acts = (d.activity || []).slice(-60).reverse();
  const activity = h("div", { class: "panel" }, h("h3", {}, "Edit activity"),
    acts.length ? h("ul", { class: "list" }, acts.map((e) => h("li", {}, h("span", { class: "when" }, clock(e.at)), h("span", {}, e.tool || ""), h("code", {}, e.file)))) : h("div", { class: "empty" }, "No edits recorded."));
  const subs = d.subagents || [];
  const byId = new Map(subs.map((x) => [x.id, x]));
  const depthOf = (x) => { let n = 0; for (let p = x; p && p.parent && n < 8; p = byId.get(p.parent)) n++; return n; };
  const subPanel = h("div", { class: "panel" }, h("h3", {}, `Subagents (${subs.length})`),
    subs.length ? h("ul", { class: "list" }, subs.slice().reverse().map((x) => h("li", { title: AGENT_STATUS[x.status] || x.status },
      h("span", { class: "when" }, ago(x.started_at)),
      h("span", { class: "sub-row", css: { paddingLeft: `${depthOf(x) * 14}px` } },
        h("span", { class: `astat ${x.status}` }, h("span", { class: `adot ${x.status}` }), x.status),
        h("span", { class: "sub-type" }, x.type), " ", x.description,
        x.doing ? h("span", { class: "doing" }, x.doing) : null))))
      : h("div", { class: "empty" }, "This session hasn't spawned subagents in the last 24 hours."));
  const log = h("div", { class: "panel" }, h("h3", {}, "Coordination log"),
    (d.log || []).length ? h("ul", { class: "list" }, d.log.slice().reverse().map((e) => h("li", {}, h("span", { class: "when" }, ago(e.at)),
      h("span", {}, h("span", { class: "chip" }, e.type), " ", e.to ? `${e.from} → ${e.to}: ` : `${e.from}: `, e.text)))) : h("div", { class: "empty" }, "Nothing logged."));
  app.replaceChildren(h("a", { class: "back", href: "#/" }, "← Fleet"), headEl,
    h("div", { class: "grid2" }, h("div", {}, commits, files, diff, wip), h("div", {}, reviewPanel, claims, subPanel, log, activity)));
}

async function loadDetail(common, name) {
  try {
    const d = await api(`/api/track?repo=${encodeURIComponent(common)}&track=${encodeURIComponent(name)}`);
    if (currentRoute().view !== "track") return;
    lastOk = Date.now();
    renderDetail(d, common);
  } catch (e) {
    if (authFailed) return showAuth();
    app.replaceChildren(h("a", { class: "back", href: "#/" }, "← Fleet"), h("div", { class: "notice" }, `Could not load track: ${e.message}`));
  }
  setStatus();
}

// --- overlaps -----------------------------------------------------------------------

function renderOverlaps() {
  if (!board) return;
  const kids = [];
  for (const r of board.repos) {
    const work = (r.tracks || []).filter((t) => t.status === "active" || t.status === "review");
    const pairs = new Map((r.overlaps || []).map((o) => [[o.a, o.b].sort().join("\u0000"), o]));
    const detailBox = h("ul", { class: "pairs" });
    const panel = h("section", { class: "panel" }, h("h3", {}, r.name));
    if (work.length < 2) {
      panel.append(h("div", { class: "empty" }, "Fewer than two working tracks — nothing can overlap."));
      kids.push(panel);
      continue;
    }
    const table = h("table", { class: "matrix" },
      h("tr", {}, h("th", {}, ""), work.map((t) => h("th", { title: t.goal || "" }, t.name))),
      work.map((a) => h("tr", {}, h("th", { title: a.goal || "" }, a.name), work.map((b) => {
        if (a.name === b.name) return h("td", { class: "self" }, "·");
        const o = pairs.get([a.name, b.name].sort().join("\u0000"));
        if (!o) return h("td", {}, "");
        const conflict = o.conflicts && o.conflicts.length;
        const tip = `${o.shared.join("\n")}${conflict ? `\n\npredicted conflicts:\n${o.conflicts.join("\n")}` : ""}`;
        return h("td", { class: conflict ? "conflict" : "some", title: tip, onclick: () => {
          detailBox.replaceChildren(h("li", {}, h("strong", {}, `${o.a} ↔ ${o.b}`), conflict ? ` — predicted conflicts in ${o.conflicts.join(", ")}` : " — no predicted conflicts"),
            ...o.shared.map((f) => h("li", {}, h("code", {}, f))));
        } }, conflict ? `${o.shared.length} ✗` : String(o.shared.length));
      }))));
    panel.append(table, h("p", { class: "title" }, "Cells count files both tracks touch (committed or uncommitted); red means git predicts a merge conflict between their branches. Click a cell for the files."), detailBox);
    kids.push(panel);
  }
  if (!kids.length) kids.push(h("div", { class: "notice" }, "No enrolled repos."));
  app.replaceChildren(...kids);
}

// --- agents ------------------------------------------------------------------------
// Sessions on the left, each fanning out to its subagents (and theirs) to the right.
// Arcs in the left gutter join sessions whose tracks messaged each other or share files.

const NODE_W = 248, NODE_H = 68, COL_GAP = 52, SLOT = 78, BLOCK_GAP = 20, GUTTER = 72, PAD = 14;

function layoutAgents(sessions) {
  const placed = [];
  let slot = 0, maxDepth = 0;
  sessions.forEach((sess, bi) => {
    const visible = sess.agents.filter((a) => showFinished || a.status !== "done");
    const ids = new Set(visible.map((a) => a.id));
    // keep a finished parent on screen while one of its children is shown
    for (const a of visible) for (let p = a.parent; p && !ids.has(p);) {
      const pa = sess.agents.find((x) => x.id === p);
      if (!pa) break;
      visible.push(pa); ids.add(p); p = pa.parent;
    }
    const kids = new Map();
    for (const a of visible) {
      const k = a.parent && ids.has(a.parent) ? a.parent : "";
      if (!kids.has(k)) kids.set(k, []);
      kids.get(k).push(a);
    }
    const top = (n) => PAD + n * SLOT + bi * BLOCK_GAP;
    const place = (item, depth, parentNode) => {
      maxDepth = Math.max(maxDepth, depth);
      const node = { item, depth, parent: parentNode, sess, kind: depth ? "agent" : "session" };
      const ch = kids.get(depth ? item.id : "") || [];
      if (!ch.length) node.y = top(slot++);
      else {
        const ys = ch.map((c) => place(c, depth + 1, node).y);
        node.y = (ys[0] + ys[ys.length - 1]) / 2;
      }
      node.x = GUTTER + depth * (NODE_W + COL_GAP);
      placed.push(node);
      return node;
    };
    place(sess, 0, null);
  });
  const height = PAD * 2 + Math.max(1, slot) * SLOT + Math.max(0, sessions.length - 1) * BLOCK_GAP - (SLOT - NODE_H);
  return { placed, width: GUTTER + (maxDepth + 1) * (NODE_W + COL_GAP) - COL_GAP + PAD, height };
}

function sessionNode(n) {
  const sess = n.item;
  const state = sess.live ? (sess.status === "busy" ? "busy" : "idle") : "ended";
  const tracks = sess.tracks || [];
  const first = tracks[0];
  const tag = first ? "a" : "div";
  return h(tag, { class: `node session ${state}`, hue: hue(sess.session_id || sess.name),
    href: first ? trackHref({ common: first.common }, first.name) : null,
    css: { left: `${n.x}px`, top: `${n.y}px` }, title: [sess.title, sess.cwd].filter(Boolean).join("\n") || null },
    h("div", { class: "node-top" }, h("span", { class: `dot ${state}` }), h("span", { class: "node-name" }, sess.name || "session"),
      h("span", { class: `node-state ${state}` }, state === "busy" ? "working" : state)),
    h("div", { class: "node-sub", title: tracks.map((t) => `${t.repo}/${t.name}`).join("\n") || null }, first
      ? [h("span", { class: "tname" }, `${first.repo}/${first.name}`), tracks.length > 1 ? h("span", { class: "more" }, `+${tracks.length - 1}`) : null]
      : h("span", {}, "not on a track")),
    sess.doing ? h("div", { class: "node-doing", title: sess.doing }, sess.doing) : null);
}

function agentNode(n) {
  const a = n.item;
  const tip = [`${a.type}${a.fork ? " (fork)" : ""}${a.background ? " · background" : ""} · ${AGENT_STATUS[a.status] || a.status}`, a.description,
    a.doing ? `now: ${a.doing}` : null, `started ${ago(a.started_at)} · last output ${ago(a.updated_at)}`,
    a.worktree_branch ? `worktree branch ${a.worktree_branch}` : null].filter(Boolean).join("\n");
  return h("div", { class: `node agent ${a.status}`, css: { left: `${n.x}px`, top: `${n.y}px` }, title: tip, tabindex: "0" },
    h("div", { class: "node-top" }, h("span", { class: `adot ${a.status}` }), h("span", { class: "node-name" }, a.description || a.type),
      h("span", { class: `node-state ${a.status}` }, a.status)),
    h("div", { class: "node-sub" }, h("span", { class: "sub-type" }, a.type),
      h("span", {}, a.status === "running" ? `started ${ago(a.started_at)}` : `last output ${ago(a.updated_at)}`)),
    a.doing ? h("div", { class: "node-doing" }, a.doing) : null);
}

function renderAgents(data) {
  const sessions = (data.sessions || []).slice().sort((a, b) =>
    (b.tracks.length ? 1 : 0) - (a.tracks.length ? 1 : 0) || (b.live ? 1 : 0) - (a.live ? 1 : 0));
  const toggle = h("input", { type: "checkbox", id: "show-finished", checked: showFinished || null });
  toggle.addEventListener("change", () => {
    showFinished = toggle.checked;
    localStorage.setItem("shepherd.showFinished", showFinished ? "1" : "0");
    renderAgents(data);
  });
  const counts = {};
  for (const sx of sessions) for (const a of sx.agents) counts[a.status] = (counts[a.status] || 0) + 1;
  const bar = h("div", { class: "agents-bar" },
    h("div", { class: "legend" },
      h("span", { class: "astat" }, h("span", { class: "dot busy" }), "session working"),
      h("span", { class: "astat" }, h("span", { class: "dot idle" }), "idle"),
      h("span", { class: "astat" }, h("span", { class: "dot ended" }), "ended"),
      ["running", "done", "stalled", "interrupted", "stopped"].map((k) =>
        h("span", { class: `astat ${k}`, title: AGENT_STATUS[k] }, h("span", { class: `adot ${k}` }), `${k}${counts[k] ? ` ${counts[k]}` : ""}`)),
      h("span", { class: "astat" }, h("span", { class: "key-line message" }), "message"),
      h("span", { class: "astat" }, h("span", { class: "key-line overlap" }), "shared files")),
    h("label", { class: "toggle", for: "show-finished" }, toggle, "Show finished subagents"));
  if (!sessions.length) {
    app.replaceChildren(bar, h("div", { class: "notice" }, "No Claude Code sessions are running, and no tracks are claimed."));
    return;
  }
  const { placed, width, height } = layoutAgents(sessions);
  const canvas = h("div", { class: "graph", css: { width: `${width}px`, height: `${height}px` } });
  const svg = s("svg", { class: "edges", width, height, viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" },
    s("defs", {}, s("marker", { id: "arrow", viewBox: "0 0 8 8", refX: "7", refY: "4", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" },
      s("path", { d: "M0,0 L8,4 L0,8 z", class: "arrow-head" }))));
  const mid = NODE_H / 2;
  for (const n of placed) {
    if (!n.parent) continue;
    const x1 = n.parent.x + NODE_W, y1 = n.parent.y + mid, x2 = n.x, y2 = n.y + mid, c = COL_GAP / 2;
    svg.append(s("path", { class: `edge ${n.item.status}`, d: `M${x1},${y1} C${x1 + c},${y1} ${x2 - c},${y2} ${x2},${y2}` }));
  }
  // track name -> session node, for the arcs between tracks
  const byTrack = new Map();
  for (const n of placed) if (n.kind === "session") for (const t of n.item.tracks) byTrack.set(`${t.common}\u0000${t.name}`, n);
  const linkRows = [];
  const pairs = new Map();
  (data.links || []).forEach((l) => {
    const a = byTrack.get(`${l.common}\u0000${l.from}`), b = byTrack.get(`${l.common}\u0000${l.to}`);
    const what = l.kind === "message" ? `${l.type}${l.accepted ? " (accepted)" : ""}: ${l.text}`
      : `${l.shared.length} shared file(s)${l.conflicts.length ? `, predicted conflicts in ${l.conflicts.join(", ")}` : ""}`;
    linkRows.push(h("li", {}, h("span", { class: "when" }, l.at ? ago(l.at) : ""),
      h("span", {}, h("span", { class: `key-line ${l.kind}${l.conflicts && l.conflicts.length ? " conflict" : ""}` }),
        h("code", {}, l.from), l.kind === "message" ? " → " : " ↔ ", h("code", {}, l.to), ` · ${what}`)));
    if (!a || !b || a === b) return;
    const key = [a.y, b.y].sort().join(":") + l.kind;
    const i = pairs.get(key) || 0;
    pairs.set(key, i + 1);
    const bulge = Math.min(GUTTER - 8, 22 + Math.abs(a.y - b.y) / 14 + i * 7);
    const x = GUTTER;
    const p = s("path", { class: `link ${l.kind}${l.conflicts && l.conflicts.length ? " conflict" : ""}`,
      d: `M${x},${a.y + mid} C${x - bulge},${a.y + mid} ${x - bulge},${b.y + mid} ${x},${b.y + mid}`,
      "marker-end": l.kind === "message" ? "url(#arrow)" : null }, s("title", {}, `${l.from} → ${l.to}: ${what}`));
    svg.append(p);
  });
  canvas.append(svg, ...placed.map((n) => n.kind === "session" ? sessionNode(n) : agentNode(n)));
  const shownAgents = placed.filter((n) => n.kind === "agent").length;
  const hidden = sessions.reduce((k, sx) => k + sx.agents.length, 0) - shownAgents;
  app.replaceChildren(...[bar, h("section", { class: "graph-wrap" }, canvas),
    hidden > 0 ? h("p", { class: "title" }, `${hidden} finished subagent${hidden === 1 ? "" : "s"} hidden.`) : null,
    h("section", { class: "panel links" }, h("h3", {}, `Links between tracks (${linkRows.length})`),
      linkRows.length ? h("ul", { class: "list" }, linkRows)
        : h("div", { class: "empty" }, "No track has messaged another or touched the same files in the last 24 hours.")),
    h("p", { class: "title" }, "Subagents from the last 24 hours, read from each session's transcripts. Hover a node for details; click a session to open its track.")].filter(Boolean));
}

let agentsJson = "";
async function loadAgents() {
  let data;
  try {
    data = await api("/api/agents");
    lastOk = Date.now();
  } catch (e) {
    if (authFailed) return showAuth();
    app.replaceChildren(h("div", { class: "notice" }, `Could not load agents: ${e.message}`));
    return;
  }
  if (currentRoute().view !== "agents") return;
  // redraw only on change, so hover and focus survive the poll
  const json = JSON.stringify(data.sessions) + JSON.stringify(data.links);
  if (json !== agentsJson || !app.querySelector(".graph")) { agentsJson = json; renderAgents(data); }
  setStatus();
}

// --- activity -----------------------------------------------------------------------

async function loadActivity() {
  const windowS = 7200, now = Math.floor(Date.now() / 1000), since = now - windowS;
  let events;
  try {
    events = (await api(`/api/activity?since=${since}`)).events || [];
    lastOk = Date.now();
  } catch (e) {
    if (authFailed) return showAuth();
    app.replaceChildren(h("div", { class: "notice" }, `Could not load activity: ${e.message}`));
    return;
  }
  if (currentRoute().view !== "activity") return;
  const lanes = new Map();
  for (const e of events) {
    const k = e.sid || "unknown";
    if (!lanes.has(k)) lanes.set(k, { who: e.who || k.slice(0, 8), events: [], repos: new Set() });
    const l = lanes.get(k);
    l.events.push(e);
    l.repos.add(e.repo);
  }
  const pct = (t) => `${Math.min(100, Math.max(0, ((t - since) / windowS) * 100)).toFixed(2)}%`;
  const ticks = [];
  for (let t = Math.ceil(since / 900) * 900; t <= now; t += 900) ticks.push(t);
  const grid = () => ticks.map((t) => h("div", { class: "gridline", x: pct(t) }));
  const rows = [...lanes.entries()].map(([sid, l]) => h("div", { class: "row" },
    h("div", { class: "label", title: `${sid}\n${[...l.repos].join(", ")}` }, h("span", { class: "swatch", hue: hue(sid) }), `${l.who} · ${l.events.length}`),
    h("div", { class: "track-area" }, grid(), l.events.map((e) => h("div", { class: "ev", hue: hue(sid), x: pct(e.at),
      title: `${clock(e.at)} ${e.repo}${e.track ? " / " + e.track : ""}\n${e.tool || ""} ${e.file}` })))));
  app.replaceChildren(h("section", { class: "timeline" },
    h("div", { class: "axis" }, h("div", { class: "label" }, "Last 2 hours"), h("div", { class: "track-area" }, ticks.map((t) =>
      // a label centred on either edge would spill outside the axis
      (t - since) / windowS < 0.07 || (now - t) / windowS < 0.04 ? null
        : h("span", { class: t % 3600 === 0 ? "tick" : t % 1800 === 0 ? "tick half" : "tick minor", x: pct(t) }, clock(t))))),
    rows.length ? rows : h("div", { class: "empty" }, "No edits in the last 2 hours in enrolled repos."),
    h("p", { class: "title" }, "One row per Claude Code session; each dot is a file edit. Hover a dot for the file.")));
  setStatus();
}

// --- routing & polling ----------------------------------------------------------------

function currentRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  if (parts[0] === "track" && parts.length >= 3) return { view: "track", common: decodeURIComponent(parts[1]), name: decodeURIComponent(parts.slice(2).join("/")) };
  if (parts[0] === "overlaps") return { view: "overlaps" };
  if (parts[0] === "activity") return { view: "activity" };
  if (parts[0] === "agents") return { view: "agents" };
  if (parts[0] === "graph") return { view: "graph", id: decodeURIComponent(parts[1] || "") };
  return { view: "fleet" };
}

function route() {
  const r = currentRoute();
  for (const a of document.querySelectorAll(".tabs a")) a.classList.toggle("on", a.dataset.view === r.view || (r.view === "track" && a.dataset.view === "fleet"));
  clearInterval(detailTimer); clearInterval(activityTimer); clearInterval(agentsTimer); stopGraph();
  if (authFailed) return showAuth();
  if (r.view === "track") {
    loadDetail(r.common, r.name);
    detailTimer = setInterval(() => document.visibilityState === "visible" && loadDetail(r.common, r.name), DETAIL_MS);
  } else if (r.view === "agents") {
    agentsJson = "";
    loadAgents();
    agentsTimer = setInterval(() => document.visibilityState === "visible" && loadAgents(), POLL_MS);
  } else if (r.view === "graph") {
    startGraph();
  } else if (r.view === "activity") {
    loadActivity();
    activityTimer = setInterval(() => document.visibilityState === "visible" && loadActivity(), DETAIL_MS);
  } else {
    render();
  }
}

function render() {
  const v = currentRoute().view;
  if (v === "fleet") renderFleet();
  else if (v === "overlaps") renderOverlaps();
}

async function poll(force) {
  if (!force && document.visibilityState !== "visible") return;
  try {
    const data = await api("/api/board");
    lastOk = Date.now();
    const json = JSON.stringify(data.repos) + JSON.stringify(data.unmanaged);
    board = data;
    if (json !== boardJson) { boardJson = json; render(); }
  } catch (e) {
    if (authFailed) showAuth();
  }
  setStatus();
}

window.addEventListener("hashchange", route);
document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && poll(true));
setInterval(poll, POLL_MS);
setInterval(setStatus, 1000);
poll(true).then(route);
