"use strict";
// herd recap view (#recap): what you left open, one topic per piece of work, from /api/recap.
// With a summary the recap skill saved for this window, topics are Claude's (a title, a short
// summary, next steps) drawn over herd's live data for their sessions; without one, each session
// is its own topic with herd's own notes. Every session line carries the one command that takes
// you back to it. The list's rules hold: grey and still unless something needs you, DOM nodes
// and textContent only (summaries, prompts and names are untrusted).
//
// Uses app.js's globals: h, svg, $, dur, clock, plural, objs, FETCH_TIMEOUT_MS, signedOut.

const recapView = (() => {
  const POLL = 30000;                 // the recap changes slowly; a summary run is polled faster
  const BUSY_POLL = 3000;
  const QUIET_KEYS = new Set(["now", "until"]);
  const STATE_WORDS = { blocked: "waiting for you", done: "finished its turn", working: "working", ended: "ended" };

  let on = false, timer = 0, tick = 0, data = null, sig = "", root = null, body = null, failed = "";
  const openDone = new Set();         // titles of done topics you opened

  function mount() {
    if (root) return;
    root = $("recap");
    body = h("div", { class: "rbody" }, h("p", { class: "note" }, "Reading your sessions…"));
    root.append(body);
  }

  // Also on every hash change while shown: #recap=friday is another window, fetched at once.
  function enter() {
    mount();
    if (!on) {
      on = true;
      if (data) draw(data, true);
    }
    poll();
  }

  function leave() {
    on = false;
    clearTimeout(timer);
    clearInterval(tick);
    timer = tick = 0;
  }

  // #recap=friday picks another window; the default is the server's (your last workday).
  const since = () => { const m = /^#recap=([\w-]+)$/.exec(location.hash); return m ? m[1] : ""; };
  const busy = (d) => !!(d && d.summarizing && d.summarizing.running);

  async function poll() {
    clearTimeout(timer);
    let again = true;
    const ctl = "AbortController" in window ? new AbortController() : null;
    const t = ctl ? setTimeout(() => ctl.abort(), FETCH_TIMEOUT_MS * 2) : 0;
    try {
      const q = since() ? "?since=" + encodeURIComponent(since()) : "";
      const r = await fetch("/api/recap" + q, { cache: "no-store", signal: ctl ? ctl.signal : undefined });
      if (r.status === 401) { again = false; signedOut(); }
      else if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        failed = e.error ? `Can't show this window: ${e.error}.` : `herd serve returned an error (${r.status}). Retrying…`;
        if (!data) note(failed);
        else if (on) draw(data, true);
      } else {
        failed = "";
        const d = await r.json();
        if (on) draw(d, false);
      }
    } catch (e) {
      if (!(e && e.name === "AbortError")) {
        console.error(e);
        if (!data) note("herd serve isn't answering on this port. Is it still running?");
      }
    } finally {
      clearTimeout(t);
    }
    if (again && on) timer = setTimeout(poll, busy(data) ? BUSY_POLL : POLL);
  }

  function note(text) {
    body.replaceChildren(h("p", { class: "note" }, text));
    sig = "";
  }

  async function summarize(btn) {
    btn.disabled = true;
    try {
      const r = await fetch("/api/recap/summarize", {
        method: "POST", cache: "no-store",
        headers: { "Content-Type": "application/json", "X-Herd": "1" },
        body: JSON.stringify({ since: since() }),
      });
      if (r.status === 401) { signedOut(); return; }
      const st = await r.json().catch(() => ({}));
      if (data) {
        data.summarizing = r.ok ? st : { running: false, error: st.error || `herd serve said ${r.status}` };
        draw(data, true);
      }
    } catch (e) {
      console.error(e);
      btn.disabled = false;
    }
    poll();
  }

  // --- the model: topics over live sessions ----------------------------------------------

  // A session a summary names, by full id or a prefix of 8 or more.
  function lookup(byId, rows, id) {
    return byId.get(id) || (id.length >= 8 ? rows.find((s) => s.session_id.startsWith(id)) : null) || null;
  }

  // A session as its own topic: herd's own notes.
  const own = (s) => ({ title: s.name, summary: s.ask || "", actions: s.todo || [], sessions: [s],
                        done: !s.open, mine: true });

  function model(d) {
    const rows = objs(d.sessions).filter((s) => s.session_id);
    const byId = new Map(rows.map((s) => [s.session_id, s]));
    const sm = d.summary && objs(d.summary.topics).length ? d.summary : null;
    const fits = sm && (sm.since == null || Math.abs(sm.since - d.since) < 1);
    let topics, fresh = [];
    if (fits) {
      const covered = new Set();
      topics = objs(sm.topics).map((t) => {
        const ss = (Array.isArray(t.sessions) ? t.sessions : []).map((id) => lookup(byId, rows, String(id))).filter(Boolean);
        ss.forEach((s) => covered.add(s.session_id));
        // Claude's "done" holds only while its sessions stay still: one that blocks, works or
        // moves on after the summary reopens the topic.
        const moved = ss.some((s) => s.state === "blocked" || s.state === "working" || s.last_at > (sm.generated_at || 0));
        return { title: t.title, summary: t.summary || "", actions: Array.isArray(t.actions) ? t.actions : [],
                 sessions: ss, done: t.state === "done" && !moved, mine: false };
      });
      fresh = rows.filter((s) => !covered.has(s.session_id)).map(own);
    } else topics = rows.map(own);
    const changed = fits ? rows.filter((s) => s.last_at > (sm.generated_at || 0)).length : 0;
    return { rows, sm: fits ? sm : null, other: sm && !fits ? sm : null, changed,
             open: topics.filter((t) => !t.done), done: topics.filter((t) => t.done),
             fresh: fresh.filter((t) => !t.done), freshDone: fresh.filter((t) => t.done) };
  }

  // A topic's mark: the most urgent of its sessions.
  function markOf(t) {
    const st = new Set(t.sessions.map((s) => s.state));
    if (st.has("blocked")) return ["blocked", "Needs you"];
    if (st.has("done")) return ["turn", "Your turn"];
    if (st.has("working")) return ["working", "Working"];
    return t.done ? ["completed", "Done"] : ["idle", "Open"];
  }

  // --- drawing ---------------------------------------------------------------------------

  function draw(d, force) {
    data = d;
    const s = JSON.stringify(d, (k, v) => (QUIET_KEYS.has(k) ? undefined : v));
    if (!force && s === sig) return;
    sig = s;
    const ae = document.activeElement, keep = ae && body.contains(ae) ? ae.dataset.k : null;
    const m = model(d);
    const kids = [top(d, m)];
    if (m.open.length) kids.push(group("Open", m.open.length, m.open.map(topic)));
    if (m.fresh.length) kids.push(group("New since the summary", m.fresh.length, m.fresh.map(topic)));
    if (!m.open.length && !m.fresh.length) {
      kids.push(h("p", { class: "note" }, m.rows.length ? "Nothing is left open." : "You typed no prompts in this window."));
    }
    const done = m.done.concat(m.freshDone);
    if (done.length) kids.push(group("Done", done.length, done.map(doneTopic)));
    const loose = objs(d.loose_ends);
    if (loose.length) kids.push(group("Loose ends", loose.length, loose.map(looseLine), "Shepherd tracks no session here covers"));
    const folded = objs(d.folded);
    if (folded.length) {
      kids.push(h("p", { class: "rfold" }, `+${plural(folded.length, "short session")} (one prompt, under 3 min): ` +
        folded.map((f) => f.name).join(", ")));
    }
    body.replaceChildren(...kids);
    clearInterval(tick);
    tick = busy(d) ? setInterval(ticking, 1000) : 0;
    ticking();
    if (keep) {
      const back = body.querySelector(`[data-k="${CSS.escape(keep)}"]`);
      if (back) back.focus({ preventScroll: true });
    }
  }

  function top(d, m) {
    const nOpen = m.open.length + m.fresh.length;
    const line = h("div", { class: "rline" }, "Since " + d.label,
      h("span", { class: "rcount" }, nOpen ? ` · ${nOpen} open` : " · nothing open",
        m.done.length + m.freshDone.length ? ` · ${m.done.length + m.freshDone.length} done` : ""));
    const kids = [line, state(d, m)];
    if (m.sm && m.sm.intro) kids.push(h("p", { class: "rintro" }, rich(m.sm.intro)));
    if (m.sm && m.sm.suggested) kids.push(h("p", { class: "rorder" }, h("span", { class: "label" }, "Suggested order"), rich(m.sm.suggested)));
    return h("section", { class: "rtop" }, kids);
  }

  // Where the summary stands, and the one button that changes it.
  function state(d, m) {
    const st = d.summarizing || {};
    const btn = (label) => h("button", { type: "button", class: "linkbtn", "data-k": "summarize",
                                         onclick: (e) => summarize(e.currentTarget) }, label);
    if (st.running) {
      return h("p", { class: "rstate" }, "Claude is summarizing these sessions… ",
        h("span", { class: "rtick", "data-from": String(st.started_at || 0) }));
    }
    const err = failed ? h("span", { class: "warn" }, failed + " ")
      : st.error ? h("span", { class: "warn" }, "The last summary failed: " + st.error + ". ") : null;
    if (m.sm) {
      return h("p", { class: "rstate" }, err, "Summarized by Claude at " + clock(m.sm.generated_at),
        m.changed ? ` · ${plural(m.changed, "session")} changed since` : "", " · ", btn("Summarize again"));
    }
    if (m.other) {
      return h("p", { class: "rstate" }, err, `The last summary covered another window (from ${clock(m.other.since)}). `,
        btn("Summarize this one"));
    }
    return h("p", { class: "rstate" }, err, "herd's own notes, one session per topic. ", btn("Summarize with Claude"),
      h("span", { class: "faint" }, " groups them into topics with next steps; it takes a minute or two."));
  }

  function ticking() {
    const el = body && body.querySelector(".rtick");
    if (el) el.textContent = dur(Date.now() / 1000 - Number(el.dataset.from || 0));
  }

  function group(title, n, items, hint) {
    return h("section", { class: "group" },
      h("h2", {}, title, h("span", { class: "n" }, String(n)), hint ? h("span", { class: "hint" }, hint) : null),
      h("ul", { class: "list rlist" }, items));
  }

  function topic(t, i) {
    const [mark, word] = markOf(t);
    const last = t.sessions.reduce((a, s) => Math.max(a, s.last_at || 0), 0);
    const when = t.sessions.find((s) => s.last_at === last);
    return h("li", { class: "topic", tabindex: "-1", "data-k": "t" + i + ":" + t.title },
      h("span", { class: "m", title: word }, svg(mark), h("span", { class: "sr" }, word + ". ")),
      h("span", { class: "tt" }, t.title),
      h("span", { class: "tw", title: last ? "Last activity " + clock(last) : null }, when ? when.when : ""),
      t.summary ? h("p", { class: "tsum" + (t.mine ? " mine" : ""), title: t.mine ? t.summary : null }, rich(t.summary)) : null,
      t.actions.length ? h("ul", { class: "acts", "aria-label": "Next steps" }, t.actions.map((a) => h("li", {}, rich(a)))) : null,
      t.sessions.length ? h("ul", { class: "tses", "aria-label": "Sessions" },
        t.sessions.map((s) => sessionLine(s, t.mine))) : null);
  }

  // A done topic folds to one line; one you opened stays open across redraws.
  function doneTopic(t) {
    const d = h("details", { open: openDone.has(t.title) },
      h("summary", {}, svg("completed"), h("span", { class: "tt" }, t.title),
        t.summary ? h("span", { class: "dsum" }, " — ", rich(t.summary)) : null),
      t.sessions.length ? h("ul", { class: "tses" }, t.sessions.map((s) => sessionLine(s, t.mine))) : null);
    d.addEventListener("toggle", () => { if (d.open) openDone.add(t.title); else openDone.delete(t.title); });
    return h("li", { class: "dtopic" }, d);
  }

  // A session under its topic; a topic that is the session itself doesn't repeat its name.
  function sessionLine(s, named) {
    const o = s.opener || {};
    const words = STATE_WORDS[s.state] || s.state;
    return h("li", { class: "ses" },
      named ? null : h("span", { class: "sn", title: s.cwd }, s.name),
      h("span", { class: "sst" }, s.state === "blocked" ? h("span", { class: "bad" }, words) : words,
        s.when && s.when !== "-" ? " · " + s.when : "", s.dir && s.dir !== "~" ? h("span", { class: "mono" }, " · " + s.dir) : null),
      o.cmd ? copyCmd(o.cmd, s) : h("span", { class: "noattach" }, o.note || ""));
  }

  function looseLine(t) {
    const where = t.track ? `${t.repo} · ${t.track}` : t.repo;
    return h("li", { class: "loose" }, h("span", { class: "sn" }, where),
      h("span", { class: "sst" }, (t.status ? t.status + " · " : "") + ((t.flags || []).join(" · ") || "clean")));
  }

  // `code` spans and **bold** markers from Claude's text, as nodes; everything else stays text.
  function rich(text) {
    return String(text || "").replace(/\*\*/g, "").split("`").map((part, i) => (i % 2 ? h("code", {}, part) : part));
  }

  function copyCmd(cmd, s) {
    const how = (s.opener || {}).how;
    const tip = how === "attach" ? `Attaches to its tmux pane ${s.tmux}` :
      `Starts claude --resume in a new tmux session in ${s.cwd} and attaches to it`;
    const label = h("span", { class: "lbl" }, "Copy");
    const code = h("code", {}, cmd);
    const btn = h("button", { type: "button", class: "copy", "data-k": "copy:" + s.session_id, title: tip,
                              onclick: () => copyText(btn, cmd) }, svg("copy"), label, code);
    return btn;
  }

  async function copyText(btn, cmd) {
    const label = btn.querySelector(".lbl"), code = btn.querySelector("code");
    let ok = false;
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("no clipboard");
      await navigator.clipboard.writeText(cmd);
      ok = true;
    } catch (e) {
      ok = false;
    }
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

  // j/k move from topic to topic, c copies the focused topic's first command.
  function key(e) {
    const topics = [...body.querySelectorAll(".topic")];
    const cur = document.activeElement && document.activeElement.closest ? document.activeElement.closest(".topic") : null;
    const at = cur ? topics.indexOf(cur) : -1;
    const k = e.key;
    if (k === "j" || k === "ArrowDown" || k === "k" || k === "ArrowUp") {
      if (!topics.length) return;
      const down = k === "j" || k === "ArrowDown";
      const i = at < 0 ? (down ? 0 : topics.length - 1) : Math.min(topics.length - 1, Math.max(0, at + (down ? 1 : -1)));
      topics[i].focus();
      topics[i].scrollIntoView({ block: "nearest" });
      e.preventDefault();
    } else if (k === "c" && cur) {
      const btn = cur.querySelector(".copy");
      if (btn) btn.click();
    }
  }

  return { enter, leave, key };
})();
