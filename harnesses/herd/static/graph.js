"use strict";
// herd graph view: each session as the root of what it started (subagents, nested ones, forks,
// workflow runs with their phases and agents), and who talked to whom, from /api/graph. The list's
// rules hold here too: grey and still unless something needs you; nodes keyed by id and rebuilt only
// when their signature changes; the tree's shape changes only while the pointer is off its rows;
// DOM nodes and textContent only, since descriptions, labels and summaries are untrusted.
//
// Spawn edges are the outline itself (CSS hairlines). Session ↔ session messages and hand-offs to a
// background job are arcs in the left gutter, one per pair, arrowheads at the receiving end.
// Messages between a session and its own subagents are a count on the subagent's row.
//
// Uses app.js's globals: h, svg, NS, $, dur, clock, plural, prefix, objs, LIVE, POLL_MS,
// FETCH_TIMEOUT_MS, net, signedOut, store, put, groupOf, SESSION_MARK, MARK_TITLE, wmodel, readout,
// phaseState, phaseWords, wfMark, wfName, glued.

const graphView = (() => {
  const ROW_MID = 10;                 // half a row (20px): where connectors and arcs meet a row
  const LANE = 7;                     // px between arc lanes in the gutter
  const HOLD = 3;                     // polls a shape change may wait for the pointer to leave
  const QUIET_KEYS = new Set(["updated_at", "last_activity", "elapsed", "now"]);
  const AGENT_MARK = { running: "running", stalled: "stalled", done: "completed", failed: "failed",
                       stopped: "interrupted", interrupted: "interrupted" };
  const ENDED_WORD = { failed: "Failed", killed: "Killed", interrupted: "Interrupted", stopped: "Stopped" };

  const openRuns = new Set(store(sessionStorage, "herd.gopen", []));
  const entries = new Map();          // node id → {li, gn, kids, sig, times, n}
  let on = false, timer = 0, data = null, shape = "", held = 0, arcSig = "", focusId = null;
  let byId = new Map(), childIds = new Map(), ctx = null;
  let root, wrap, tree, arcs, msg, empty, bar;

  // --- mounting and polling ---------------------------------------------------------------

  function mount() {
    if (root) return;
    root = $("graph");
    tree = h("ul", { class: "gtree", role: "tree", "aria-label": "Sessions and what they started" });
    arcs = document.createElementNS(NS, "svg");
    arcs.setAttribute("class", "arcs");
    arcs.setAttribute("aria-hidden", "true");
    wrap = h("div", { class: "gwrap", hidden: true }, arcs, tree);
    msg = h("p", { class: "note" }, "Reading the graph…");
    empty = h("p", { class: "note gempty", hidden: true },
      "No subagents, workflows or messages between sessions in the last 24 hours.");
    bar = h("div", { class: "gdetail", id: "gdetail", hidden: true });
    root.append(msg, wrap, empty, bar);
    tree.addEventListener("focusin", (e) => {
      const li = e.target.closest && e.target.closest("li[data-id]");
      if (li) { rove(li.dataset.id); showDetail(); }
    });
    tree.addEventListener("focusout", () => setTimeout(() => {
      if (!tree.contains(document.activeElement)) showDetail();
    }, 0));
    if ("ResizeObserver" in window) new ResizeObserver(() => drawArcs()).observe(wrap);
  }

  function enter() {
    mount();
    if (on) return;
    on = true;
    if (data) render(data);
    poll();
  }

  function leave() {
    on = false;
    clearTimeout(timer);
    timer = 0;
  }

  async function poll() {
    clearTimeout(timer);
    let again = true;
    const ctl = "AbortController" in window ? new AbortController() : null;
    const t = ctl ? setTimeout(() => ctl.abort(), FETCH_TIMEOUT_MS) : 0;
    try {
      const r = await fetch("/api/graph", { cache: "no-store", signal: ctl ? ctl.signal : undefined });
      if (r.status === 401) { again = false; signedOut(); }
      else if (!r.ok) { if (!data) note(`herd serve returned an error (${r.status}). Retrying…`); }
      else {
        const d = await r.json();
        if (on) render(d);
      }
    } catch (e) {
      if (!(e && e.name === "AbortError")) {
        console.error(e);
        if (!data) note("herd serve isn't answering on this port. Is it still running?");
      }
    } finally {
      clearTimeout(t);
    }
    if (again && on) timer = setTimeout(poll, POLL_MS);
  }

  function note(text) {
    msg.hidden = false;
    if (msg.textContent !== text) msg.textContent = text;
  }

  // --- the model: visible nodes and what each row needs from its neighbours ---------------

  function render(d) {
    data = d;
    const nodes = objs(d.nodes).filter((n) => n.id);
    byId = new Map(nodes.map((n) => [n.id, n]));
    for (const id of [...openRuns]) if (!byId.has(id)) openRuns.delete(id);
    put(sessionStorage, "herd.gopen", [...openRuns]);

    // A collapsed run hides everything under it; a node whose parent isn't drawn isn't either.
    const all = new Map(), vis = [], visible = new Set();
    for (const n of nodes) {
      if (n.parent) all.set(n.parent, (all.get(n.parent) || 0) + 1);
      const p = n.parent ? byId.get(n.parent) : null;
      if (n.parent && (!visible.has(n.parent) || (p.kind === "workflow" && !openRuns.has(p.id)))) continue;
      visible.add(n.id);
      vis.push(n);
    }
    childIds = new Map();
    for (const n of vis) {
      const k = n.parent || "";
      if (!childIds.has(k)) childIds.set(k, []);
      childIds.get(k).push(n.id);
    }
    ctx = context(nodes, objs(d.edges), all);

    const sessions = vis.filter((n) => n.kind === "session");
    if (!sessions.length) {
      note("No Claude Code sessions are running on this machine.");
      msg.append(h("br"), "Start one with ", h("code", {}, "herd new <name> <dir>"), ".");
      wrap.hidden = true;
      empty.hidden = true;
      return;
    }
    msg.hidden = true;
    wrap.hidden = false;
    empty.hidden = nodes.some((n) => n.kind !== "session") || objs(d.edges).length > 0;

    // The tree's shape: applied at once unless the pointer rests on a row (then it waits a few polls).
    const want = vis.map((n) => n.id + "<" + (n.parent || "")).join("|");
    if (want !== shape) {
      if (shape && tree.querySelector(".gn:hover") && held < HOLD) held++;
      else { reshape(vis); shape = want; held = 0; }
    }
    for (const n of vis) {
      const e = entries.get(n.id);
      if (!e) continue;
      e.n = n;
      const sig = JSON.stringify([n, rowContext(n)], (k, v) => (QUIET_KEYS.has(k) ? undefined : v));
      if (sig !== e.sig) { build(e, n); e.sig = sig; }
      for (const t of e.times) {
        const text = t.fn(e.n, d.now);
        if (t.el.textContent !== text) t.el.textContent = text;
      }
    }
    if (!entries.has(focusId)) rove(null);
    showDetail();
    drawArcs();
  }

  // Edges folded into what each row shows: messages per session, hand-backs and coordinator
  // messages per subagent, the workflow model behind each run and phase row, twin names.
  function context(nodes, edges, all) {
    const names = {}, sessionOf = new Map(), runOf = new Map(), models = new Map();
    for (const n of nodes) {
      if (n.kind === "session") { names[n.name] = (names[n.name] || 0) + 1; sessionOf.set(n.id, n.id); }
      else if (n.parent) sessionOf.set(n.id, sessionOf.get(n.parent));
      if (n.kind === "workflow") { models.set(n.id, wmodel(asRun(n))); runOf.set(n.id, n.id); }
      else if (n.parent && runOf.has(n.parent)) runOf.set(n.id, runOf.get(n.parent));
    }
    const talk = new Map(), msgs = new Map();
    for (const e of edges) {
      if (e.kind === "handback" || e.kind === "coordinator") {
        const aid = e.kind === "handback" ? e.from : e.to;
        const t = talk.get(aid) || { h: 0, k: 0, last: 0, summary: "" };
        if (e.kind === "handback") t.h += e.count || 0; else { t.k += e.count || 0; t.summary = e.summary || t.summary; }
        t.last = Math.max(t.last, e.last_at || 0);
        talk.set(aid, t);
      } else {
        for (const end of [e.from, e.to]) {
          if (!msgs.has(end)) msgs.set(end, []);
          msgs.get(end).push(e);
        }
      }
    }
    return { names, sessionOf, runOf, models, talk, msgs, all };
  }

  // A workflow node in the shape app.js's workflow helpers expect.
  function asRun(n) {
    return Object.assign({}, n, { id: n.id.replace(/^w:/, ""), superseded_by: "" });
  }

  function rowContext(n) {
    const c = ctx;
    if (n.kind === "session") {
      return [n.live ? groupOf(n) : "ended", c.names[n.name] > 1, c.all.has(n.id),
              (c.msgs.get(n.id) || []).map((e) => [e.id, e.count, e.last_at])];
    }
    if (n.kind === "workflow") return [openRuns.has(n.id), c.all.has(n.id)];
    if (n.kind === "phase") {
      const m = c.models.get(c.runOf.get(n.id));
      return m ? [m.cur, m.live, m.stalled, m.failed, m.total] : null;
    }
    if (n.kind === "wf-agent") { const m = c.models.get(c.runOf.get(n.id)); return m ? m.live : null; }
    if (n.kind === "subagent" || n.kind === "fork") return c.talk.get(n.id) || null;
    return null;
  }

  // --- structure ---------------------------------------------------------------------------

  function reshape(vis) {
    const ae = document.activeElement, hadFocus = tree.contains(ae);
    const was = new Map([...entries].map(([id, e]) => [id, e.n.parent]));   // parents before this poll
    const want = new Set(vis.map((n) => n.id));
    for (const [id, e] of entries) if (!want.has(id)) { e.li.remove(); entries.delete(id); }
    for (const n of vis) if (!entries.has(n.id)) entries.set(n.id, makeEntry(n));
    place(tree, childIds.get("") || []);
    if (hadFocus) {
      // Focus stays on its node; if the node went away, on the nearest ancestor still drawn.
      let id = focusId;
      while (id && !entries.has(id)) id = was.get(id) || (byId.get(id) || {}).parent || null;
      const e = entries.get(id) || entries.get((childIds.get("") || [])[0]);
      if (e && document.activeElement !== e.li) e.li.focus({ preventScroll: true });
    }
  }

  function place(ul, ids) {
    let ref = ul.firstChild;
    for (const id of ids) {
      const e = entries.get(id);
      if (ref === e.li) ref = ref.nextSibling;
      else ul.insertBefore(e.li, ref);
      const kids = childIds.get(id) || [];
      if (kids.length) {
        if (!e.kids) { e.kids = h("ul", { class: "gk", role: "group" }); e.li.append(e.kids); }
        place(e.kids, kids);
      } else if (e.kids) { e.kids.remove(); e.kids = null; }
    }
    while (ref) { const next = ref.nextSibling; ref.remove(); ref = next; }
  }

  function makeEntry(n) {
    const gid = "g-" + n.id.replace(/[^A-Za-z0-9_-]/g, "_");
    const gn = h("div", { class: "gn", id: gid });
    const li = h("li", { role: "treeitem", "data-id": n.id, tabindex: "-1", "aria-labelledby": gid,
                         "aria-describedby": "gdetail" }, gn);
    gn.addEventListener("click", () => { rove(n.id); li.focus({ preventScroll: true }); activate(n.id, true); });
    return { li, gn, kids: null, sig: "", times: [], n };
  }

  // --- rows -----------------------------------------------------------------------------------

  function build(e, n) {
    e.times = [];
    e.li.setAttribute("aria-level", String((n.depth || 0) + 1));
    const timed = (el, fn) => { e.times.push({ el, fn }); el.textContent = fn(n, data.now); return el; };
    let kids;
    if (n.kind === "session") kids = sessionRow(e, n, timed);
    else if (n.kind === "workflow") kids = runRow(e, n, timed);
    else if (n.kind === "phase") kids = phaseRow(n);
    else if (n.kind === "wf-agent") kids = wfAgentRow(e, n, timed);
    else if (n.kind === "more") kids = moreRow(e, n);
    else kids = agentRow(e, n, timed);
    if (n.kind === "workflow" && ctx.all.has(n.id)) e.li.setAttribute("aria-expanded", String(openRuns.has(n.id)));
    else e.li.removeAttribute("aria-expanded");
    e.gn.replaceChildren(...kids.filter(Boolean));
  }

  function sessionRow(e, n, timed) {
    const g = n.live ? groupOf(n) : "ended";
    e.li.className = "gs" + (g === "idle" || g === "ended" ? " quiet" : "");
    e.gn.className = "gn";
    const word = n.live ? MARK_TITLE[g] : n.handed_off ? "Moved to background" : "Ended";
    const l1 = h("div", { class: "sl1" },
      h("span", { class: "m", title: word }, svg(n.live ? SESSION_MARK[g] : "idle"), h("span", { class: "sr" }, word + ". ")),
      h("span", { class: "gname", title: n.name }, n.name || "Unnamed session"),
      ctx.all.has(n.id) ? h("span", { class: "lead" }) : null);
    const state = h("span", { class: g === "need" ? "bad" : null });
    if (g === "need") state.textContent = n.waiting_for ? "Waiting for " + n.waiting_for : "Needs you";
    else if (g === "ended") {
      timed(state, (c, now) => (c.handed_off ? "Moved to background " : "Ended ") + dur(now - (c.ended_at || now)) + " ago");
    } else state.textContent = word;
    // Line 2 also carries "Background" and, for two sessions with one name, the folder that tells them
    // apart: the first column is narrow, and the name keeps its room.
    const twin = ctx.names[n.name] > 1 && n.dir ? h("span", { class: "twin", title: n.cwd }, n.dir) : null;
    return [l1, h("div", { class: "sl2" }, glued([state, n.bg ? "Background" : null, twin]))];
  }

  function agentRow(e, n, timed) {
    const live = LIVE.has(n.status);
    e.gn.className = "gn" + (live ? "" : " gfin");
    const out = [svg(AGENT_MARK[n.status] || "pending"),
      h("span", { class: "ty" }, n.kind === "fork" ? "fork" : n.type || "agent"),
      h("span", { class: "ds", title: n.description || null }, n.description || "")];
    if (n.status === "stalled") {
      out.push(timed(h("span", { class: "mt warn" }), (c, now) => "stalled " + dur(now - (c.updated_at || now))));
    }
    const t = ctx.talk.get(n.id);
    if (t && (t.k >= 1 || t.h >= 2)) {
      out.push(h("span", { class: "mt", title: talkTitle(n, t) }, " · " + plural(t.k + t.h, "message")));
    }
    return out;
  }

  function runRow(e, n, timed) {
    const m = ctx.models.get(n.id);
    e.gn.className = "gn grun" + (m.live ? "" : " gfin");
    const run = asRun(n);
    const key = m.live ? wfMark(m) : n.status === "completed" ? "completed" : n.status === "failed" ? "failed" : "interrupted";
    let ro;
    if (m.live) ro = readout(m);
    else {
      const when = timed(h("span", {}), (c, now) => "ended " + dur(now - (c.ended_at || now)) + " ago");
      ro = glued([ENDED_WORD[n.status] || null, plural(n.agent_count || 0, "agent"), when]);
    }
    return [svg(key, m.live && m.failed ? "live" : ""),
      h("span", { class: "wn", title: wfName(run) === n.name ? n.description || null : run.id },
        wfName(run), n.resumed_from ? h("span", { class: "res" }, ", resumed") : null),
      h("span", { class: "ro" }, ro),
      ctx.all.has(n.id) ? svg("chev", "chev") : null];
  }

  function phaseRow(n) {
    const m = ctx.models.get(ctx.runOf.get(n.id));
    if (!m || !m.phases[n.index]) return [svg("pending"), h("span", { class: "ph" }, n.title || "Phase")];
    const st = phaseState(m, n.index);
    return [svg(st === "done" ? "completed" : st, st === "failed" ? "live" : ""),
      h("span", { class: "ph" + (n.index === m.cur ? " cur" : "") }, n.title || "Phase " + (n.index + 1)),
      h("span", { class: "ro" }, phaseWords(m, n.index, false))];
  }

  function wfAgentRow(e, n, timed) {
    const m = ctx.models.get(ctx.runOf.get(n.id));
    const live = LIVE.has(n.status);
    e.gn.className = "gn" + (live ? "" : " gfin") + (n.superseded ? " sup" : "");
    const warn = m && m.live && !n.superseded && (n.status === "failed" || n.status === "stalled");
    const out = [svg(AGENT_MARK[n.status] || "pending", warn && n.status === "failed" ? "live" : ""),
      h("span", { class: "lb", title: n.label }, n.label)];
    if (n.superseded) out.push(h("span", { class: "mt" }, "retried"));
    else if (n.status === "stalled" && m && m.live) {
      out.push(timed(h("span", { class: "mt warn" }), (c, now) => "stalled " + dur(now - (c.updated_at || now))));
    }
    if (live && n.doing) out.push(h("span", { class: "dg", title: n.doing }, n.doing));
    return out;
  }

  function moreRow(e, n) {
    e.gn.className = "gn gfin";
    const parts = Object.entries(n.counts || {}).filter(([, v]) => v).map(([k, v]) => v + " " + k);
    return [h("span", { class: "sp" }), h("span", { class: "more", title: parts.join(", ") || null }, n.count + " more")];
  }

  function nameOf(id) {
    const n = byId.get(id);
    if (!n) return "a session";
    return n.kind === "session" ? n.name || "Unnamed session" : n.description || n.type || n.label || "an agent";
  }

  function talkTitle(n, t) {
    const parent = nameOf(n.parent);
    const bits = [];
    if (t.k) bits.push(`${parent} sent it ${plural(t.k, "message")}` + (t.summary ? ` (latest: “${t.summary}”)` : ""));
    if (t.h) bits.push(`it reported back ${t.h === 1 ? "once" : t.h + " times"}`);
    return bits.join("; ") + (t.last ? `, last ${dur(data.now - t.last)} ago` : "");
  }

  // --- actions and keys -------------------------------------------------------------------

  function rove(id) {
    if (id && !entries.has(id)) id = null;
    if (!id) id = (childIds.get("") || [])[0] || null;
    if (focusId && focusId !== id && entries.has(focusId)) entries.get(focusId).li.tabIndex = -1;
    focusId = id;
    if (id) entries.get(id).li.tabIndex = 0;
  }

  function focusNode(id) {
    const e = entries.get(id);
    if (!e) return;
    rove(id);
    e.li.focus({ preventScroll: true });
    e.gn.scrollIntoView({ block: "nearest" });
  }

  function toggleRun(id, want) {
    const next = want == null ? !openRuns.has(id) : want;
    if (next) openRuns.add(id); else openRuns.delete(id);
    put(sessionStorage, "herd.gopen", [...openRuns]);
    shape = "";                       // the user asked for it: no waiting for the pointer
    if (data) render(data);
    focusNode(id);
  }

  // Enter, Space or a click: a run opens or closes; a session (or a fold under it) opens in the
  // list, where its prompt, reply and tmux attach live.
  function activate(id, byClick) {
    const n = byId.get(id);
    if (!n) return;
    if (n.kind === "workflow") { if (ctx.all.has(id)) toggleRun(id); return; }
    const sid = ctx.sessionOf.get(id);
    const s = sid && byId.get(sid);
    if (n.kind === "session" || n.kind === "more") {
      if (s && s.live) location.hash = "open=" + prefix(s);
      return;
    }
    if (!byClick) showDetail();
  }

  function key(e) {
    const k = e.key;
    const order = [...tree.querySelectorAll("li[data-id]")].map((li) => li.dataset.id);
    if (!order.length) return;
    const cur = tree.contains(document.activeElement) ? focusId : null;
    const i = cur ? order.indexOf(cur) : -1;
    const go = (id) => { if (id) focusNode(id); e.preventDefault(); };
    if (k === "ArrowDown" || k === "j") return go(order[i < 0 ? 0 : Math.min(order.length - 1, i + 1)]);
    if (k === "ArrowUp" || k === "k") return go(order[i < 0 ? order.length - 1 : Math.max(0, i - 1)]);
    if (k === "Home") return go(order[0]);
    if (k === "End") return go(order[order.length - 1]);
    if (!cur) return;
    const n = byId.get(cur);
    if (k === "ArrowRight") {
      e.preventDefault();
      if (n.kind === "workflow" && ctx.all.has(cur) && !openRuns.has(cur)) return toggleRun(cur, true);
      const first = (childIds.get(cur) || [])[0];
      if (first) focusNode(first);
    } else if (k === "ArrowLeft") {
      e.preventDefault();
      if (n.kind === "workflow" && openRuns.has(cur)) return toggleRun(cur, false);
      if (n.parent) focusNode(n.parent);
    } else if (k === "Enter" || k === " ") {
      e.preventDefault();
      activate(cur, false);
    } else if (k === "Escape") {
      for (let p = cur; p; p = (byId.get(p) || {}).parent) {
        if (openRuns.has(p)) { e.preventDefault(); return toggleRun(p, false); }
      }
    }
  }

  // --- the detail bar: what a focused node can't fit on its row --------------------------------

  function showDetail() {
    if (!bar) return;
    const n = focusId && tree.contains(document.activeElement) ? byId.get(focusId) : null;
    if (!n) { bar.hidden = true; bar.replaceChildren(); return; }
    const lines = detailLines(n, data.now).filter(Boolean);
    bar.hidden = !lines.length;
    bar.replaceChildren(...lines.map((ln, i) => h("div", { class: i ? "dl" : "dl first" }, ln)));
  }

  function detailLines(n, now) {
    if (n.kind === "session") {
      const where = n.dir && n.dir !== "~" ? h("span", { class: "mono", title: n.cwd }, n.dir) : "Home folder";
      const since = n.live ? (n.state === "blocked" ? "waiting since " : n.state === "done" ? "finished at " : "working since ")
        + clock(n.state_changed_at) : (n.handed_off ? "moved to background at " : "ended at ") + clock(n.ended_at);
      const talk = (ctx.msgs.get(n.id) || []).map((e) => {
        if (e.kind === "continued") {
          return e.from === n.id ? `Moved its work to ${nameOf(e.to)}` : `Took over the work of ${nameOf(e.from)}`;
        }
        const out = e.from === n.id;
        return (out ? `Sent ${plural(e.count, "message")} to ${nameOf(e.to)}` : `Received ${plural(e.count, "message")} from ${nameOf(e.from)}`)
          + `, last ${dur(now - e.last_at)} ago` + (e.summary ? ` (“${e.summary}”)` : "");
      });
      return [glued([where, since, n.live ? "Enter opens it in the list" : null]), talk.length ? talk.join(" · ") : null];
    }
    if (n.kind === "workflow") {
      return [glued([n.description || wfName(asRun(n)), n.started_at ? "started " + clock(n.started_at) : null,
        n.tokens ? Math.round(n.tokens / 1000) + "k tokens" : null]), n.error || null];
    }
    if (n.kind === "phase" || n.kind === "more") return [];
    if (n.kind === "wf-agent") {
      return [glued([h("span", { class: "mono" }, n.label), n.doing ? h("span", { class: "mono" }, n.doing) : null,
        n.updated_at ? "last write " + dur(now - n.updated_at) + " ago" : null])];
    }
    const t = ctx.talk.get(n.id);
    return [glued([n.kind === "fork" ? "fork" : n.type, n.description, n.model && n.model !== "inherit" ? n.model : null,
                   n.worktree_branch ? h("span", {}, "on ", h("span", { class: "mono" }, n.worktree_branch)) : null]),
      glued([n.doing ? h("span", { class: "mono" }, n.doing) : null, n.started_at ? "started " + clock(n.started_at) : null,
             n.ended_at ? "finished " + clock(n.ended_at) : n.updated_at ? "last write " + dur(now - n.updated_at) + " ago" : null,
             t ? talkTitle(n, t) : null])];
  }

  // --- arcs: session ↔ session, in the gutter ---------------------------------------------------

  function drawArcs() {
    if (!on || !data || wrap.hidden) return;
    const pairs = new Map();
    for (const e of objs(data.edges)) {
      if ((e.kind !== "message" && e.kind !== "continued") || !entries.has(e.from) || !entries.has(e.to)) continue;
      const key = [e.from, e.to].sort().join("|");
      if (!pairs.has(key)) pairs.set(key, { a: e.from, b: e.to, ends: new Set(), items: [] });
      const p = pairs.get(key);
      p.ends.add(e.to);
      p.items.push(e);
    }
    const gutter = parseFloat(getComputedStyle(wrap).paddingLeft) || 40;
    const top = wrap.getBoundingClientRect().top;
    const y = (id) => Math.round(entries.get(id).gn.getBoundingClientRect().top - top) + ROW_MID + 0.5;
    const x0 = gutter - 6;
    const lanes = Math.max(1, Math.min(4, Math.floor((x0 - 10) / LANE) + 1));
    const list = [...pairs.values()].map((p) => {
      const ya = y(p.a), yb = y(p.b);
      return Object.assign(p, { lo: Math.min(ya, yb), hi: Math.max(ya, yb) });
    }).sort((p, q) => (p.hi - p.lo) - (q.hi - q.lo));
    const used = [];
    for (const p of list) {
      let l = 0;
      while (l < lanes - 1 && (used[l] || []).some(([lo, hi]) => p.lo < hi + 4 && p.hi > lo - 4)) l++;
      (used[l] = used[l] || []).push([p.lo, p.hi]);
      p.lane = l;
    }
    const height = wrap.offsetHeight;
    const sig = JSON.stringify([gutter, height, list.map((p) => [p.a, p.b, p.lane, p.lo, p.hi, [...p.ends].sort(),
      p.items.map((e) => [e.kind, e.count, e.last_at || e.at])])]);
    if (sig === arcSig) return;
    arcSig = sig;
    arcs.setAttribute("width", String(gutter));
    arcs.setAttribute("height", String(height));
    arcs.setAttribute("viewBox", `0 0 ${gutter} ${height}`);
    const out = [];
    for (const p of list) {
      const lx = x0 - 10 - LANE * p.lane + 0.5, r = 5;
      const d = `M${x0} ${p.lo}H${lx + r}Q${lx} ${p.lo} ${lx} ${p.lo + r}V${p.hi - r}Q${lx} ${p.hi} ${lx + r} ${p.hi}H${x0}`;
      out.push(path(d, "arc"));
      for (const end of p.ends) {
        const ye = y(end);
        out.push(path(`M${x0 - 4} ${ye - 3.5}L${x0} ${ye}L${x0 - 4} ${ye + 3.5}`, "tip"));
      }
      const hit = path(d, "hit");
      const t = document.createElementNS(NS, "title");
      t.textContent = p.items.map(arcTitle).join("\n");
      hit.append(t);
      out.push(hit);
    }
    arcs.replaceChildren(...out);
  }

  function path(d, cls) {
    const el = document.createElementNS(NS, "path");
    el.setAttribute("d", d);
    el.setAttribute("class", cls);
    return el;
  }

  function arcTitle(e) {
    if (e.kind === "continued") return `${nameOf(e.from)} moved its work to the background job ${nameOf(e.to)}, ${dur(data.now - e.at)} ago`;
    return `${nameOf(e.from)} → ${nameOf(e.to)}: ${plural(e.count, "message")}, last ${dur(data.now - e.last_at)} ago`
      + (e.summary ? ` — “${e.summary}”` : "");
  }

  return { enter, leave, key };
})();
