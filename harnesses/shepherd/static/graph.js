// shepherd dashboard — Graph tab: omnigraph stores (graphs/dev.omni, ontology/build, ...),
// drawn around one node, with a node browser and the ops the omnigraph mod saw.
// Loaded before app.js and uses its helpers (h, s, hue, api, clock, setStatus) at call time.
"use strict";

const GV_COL = 230, GV_ROW = 28, GV_HEAD = 22, GV_GAP = 110, GV_MID_W = 250, GV_MID_H = 58,
  GV_PAD = 16, GV_PER = 8, GV_LIST = 150, GV_REL = 40, GV_TRAIL = 30;

const GV = {
  timer: null, storeId: "", stores: [], graph: null, ix: null, listJson: "",
  center: null, selected: null, trail: [], touched: new Set(), seen: new Set(),
  follow: localStorage.getItem("shepherd.graph.follow") !== "0",
  query: "", label: "", queueOnly: false, expanded: new Set(), dom: null,
};

// replaceChildren would write a null kid out as the text "null"
function gvSet(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
}

function gvPush(map, key, value) {
  const list = map.get(key);
  if (list) list.push(value); else map.set(key, [value]);
}

function gvIndex(g) {
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  const out = new Map(), into = new Map();
  for (const [from, type, to] of g.edges) { gvPush(out, from, [type, to]); gvPush(into, to, [type, from]); }
  const degree = (id) => ((out.get(id) || []).length + (into.get(id) || []).length);
  const labels = new Map();
  for (const n of g.nodes) labels.set(n.label, (labels.get(n.label) || 0) + 1);
  const labelOrder = new Map([...labels.keys()].sort().map((l, i) => [l, i]));
  return { byId, out, into, degree, labels, labelOrder };
}

function gvNode(id) {
  return GV.ix.byId.get(id) || { id, label: "?", name: id, status: null, source: [], props: {} };
}

function gvByName(ids) {
  return ids.slice().sort((a, b) => (GV.touched.has(b) ? 1 : 0) - (GV.touched.has(a) ? 1 : 0)
    || gvNode(a).name.toLowerCase().localeCompare(gvNode(b).name.toLowerCase()) || (a < b ? -1 : a > b ? 1 : 0));
}

function gvBusiest() {
  let best = null, most = -1;
  for (const n of GV.graph.nodes) { const d = GV.ix.degree(n.id); if (d > most) { best = n.id; most = d; } }
  return best;
}

function gvOpKey(op) { return `${op.t}\u0000${op.op}\u0000${op.session || ""}`; }

function gvStore() { return GV.stores.find((x) => x.id === GV.storeId) || null; }

function gvDelta(op) {
  if (op.background) return "bg";
  if (op.dNodes === null || op.dNodes === undefined) return "·n ·e";
  const sign = (n) => (n >= 0 ? `+${n}` : String(n));
  return `${sign(op.dNodes)}n ${sign(op.dEdges || 0)}e${op.changed ? ` ~${op.changed}n` : ""}`;
}

function gvMs(ms) { return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`; }

function gvClock(t) {
  return new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// --- moving around ----------------------------------------------------------------------

// Move the drawing to `id`, keeping the node it leaves on the breadcrumb trail.
function gvRecentre(id) {
  if (id === GV.center) return;
  if (GV.center) GV.trail.push(GV.center);
  if (GV.trail.length > GV_TRAIL) GV.trail.splice(0, GV.trail.length - GV_TRAIL);
  GV.center = id;
}

function gvBrowse(id) {
  if (!GV.graph || !id) return;
  setFollow(false);
  gvRecentre(id);
  GV.selected = id;
  gvPaint();
}

function gvSelect(id) {
  GV.selected = id;
  gvPaintDetail();
  for (const el of GV.dom.canvas.querySelectorAll(".gv-node")) el.classList.toggle("sel", el.dataset.id === id);
}

function gvBack(i) {
  GV.center = GV.trail[i];
  GV.selected = GV.center;
  GV.trail = GV.trail.slice(0, i);
  setFollow(false);
  gvPaint();
}

function gvFocusOp(op, keepFollow) {
  if (!op.focus || !GV.ix || !GV.ix.byId.has(op.focus.center)) return false;
  GV.touched = new Set(op.touched || []);
  gvRecentre(op.focus.center);
  GV.selected = GV.ix.byId.has(op.focus.selected) ? op.focus.selected : op.focus.center;
  if (!keepFollow) setFollow(false);
  return true;
}

// The newest of `ops` with a node to show: an op that failed or only read has no focus.
function gvNewestFocus(ops) {
  for (let i = ops.length - 1; i >= 0; i--) {
    if (ops[i].focus && GV.ix.byId.has(ops[i].focus.center)) return ops[i];
  }
  return null;
}

// The feed's ops not seen at the last look. A set, not "after the last one": the feed is
// in start order, and a long op another session records later lands before the last.
function gvFresh(feed) {
  return (feed || []).filter((op) => !GV.seen.has(gvOpKey(op)));
}

function gvSeen(feed) {
  GV.seen = new Set((feed || []).map(gvOpKey));
}

function setFollow(on) {
  GV.follow = on;
  localStorage.setItem("shepherd.graph.follow", on ? "1" : "0");
  if (GV.dom) GV.dom.follow.checked = on;
}

// --- the drawing ------------------------------------------------------------------------

function gvGroups(id, dir) {
  const by = new Map();
  for (const [type, other] of ((dir === "out" ? GV.ix.out : GV.ix.into).get(id) || [])) gvPush(by, type, other);
  return [...by.entries()].map(([type, ids]) => ({ type, dir, ids: gvByName([...new Set(ids)]) }))
    .sort((a, b) => b.ids.length - a.ids.length || a.type.localeCompare(b.type));
}

// Column widths from the room the drawing has: the side columns and the gaps give way
// first, down to a floor; under that the drawing scrolls sideways.
function gvLayout() {
  const room = Math.max(0, (GV.dom.canvasWrap.clientWidth || 960) - 2);
  const mid = Math.min(GV_MID_W, Math.max(180, room * 0.26));
  const gap = Math.min(GV_GAP, Math.max(48, room * 0.09));
  const col = Math.min(GV_COL + 40, Math.max(150, (room - mid - 2 * gap - 2 * GV_PAD) / 2));
  return { col, gap, mid };
}

// A label's colour is its place among the store's labels, spread round the wheel.
function gvHue(label) {
  const i = GV.ix.labelOrder.get(label);
  return i === undefined ? 220 : Math.round((i * 137.508 + 210) % 360);
}

function gvSide(groups, x) {
  const items = [];
  let y = GV_PAD;
  for (const g of groups) {
    const key = `${GV.center}\u0000${g.dir}\u0000${g.type}`;
    const shown = GV.expanded.has(key) ? g.ids : g.ids.slice(0, GV_PER);
    items.push({ kind: "head", x, y, g });
    y += GV_HEAD;
    for (const id of shown) { items.push({ kind: "node", x, y, id, dir: g.dir }); y += GV_ROW; }
    if (shown.length < g.ids.length) { items.push({ kind: "more", x, y, key, n: g.ids.length - shown.length }); y += GV_ROW; }
    y += 10;
  }
  return { items, height: y };
}

function gvPill(id, x, y, w) {
  const n = gvNode(id);
  return h("button", { type: "button", class: `gv-node${id === GV.selected ? " sel" : ""}${GV.touched.has(id) ? " hit" : ""}`,
    hue: gvHue(n.label), "data-id": id, css: { left: `${x}px`, top: `${y}px`, width: `${w}px` },
    title: `${n.name}\n${n.label} · ${n.id}${n.status ? ` · ${n.status}` : ""}\nclick: draw the graph around it`,
    onclick: () => gvBrowse(id) },
  h("span", { class: "gv-dot" }), h("span", { class: "gv-name" }, n.name));
}

function gvCanvas() {
  const id = GV.center;
  const { col, gap, mid } = gvLayout();
  const left = gvSide(gvGroups(id, "in"), GV_PAD);
  const midX = GV_PAD + col + gap;
  const right = gvSide(gvGroups(id, "out"), midX + mid + gap);
  const width = midX + mid + gap + col + GV_PAD;
  const height = Math.max(left.height, right.height, GV_MID_H + GV_PAD * 2);
  const midY = Math.max(GV_PAD, Math.min(height / 2 - GV_MID_H / 2, height - GV_MID_H - GV_PAD));
  const canvas = h("div", { class: "gv-canvas", css: { width: `${width}px`, height: `${height}px` } });
  const svg = s("svg", { class: "gv-edges", width, height, viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" },
    s("defs", {}, s("marker", { id: "gv-arrow", viewBox: "0 0 8 8", refX: "7", refY: "4", markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse" },
      s("path", { d: "M0,0 L8,4 L0,8 z", class: "gv-arrow-head" }))));
  const kids = [];
  const cy = midY + GV_MID_H / 2;
  for (const it of [...left.items, ...right.items]) {
    if (it.kind === "head") {
      kids.push(h("div", { class: "gv-head", css: { left: `${it.x}px`, top: `${it.y}px`, width: `${col}px` } },
        it.g.dir === "in" ? `${it.g.type} →` : `→ ${it.g.type}`, h("span", { class: "gv-count" }, ` ×${it.g.ids.length}`)));
    } else if (it.kind === "more") {
      kids.push(h("button", { class: "gv-more", type: "button", css: { left: `${it.x}px`, top: `${it.y}px`, width: `${col}px` },
        onclick: () => { GV.expanded.add(it.key); gvPaintCanvas(); } }, `+${it.n} more`));
    } else {
      const y = it.y + GV_ROW / 2 - 1;
      const hit = GV.touched.has(it.id) ? " hit" : "";
      const d = it.dir === "in"
        ? `M${it.x + col},${y} C${it.x + col + gap / 2},${y} ${midX - gap / 2},${cy} ${midX},${cy}`
        : `M${midX + mid},${cy} C${midX + mid + gap / 2},${cy} ${it.x - gap / 2},${y} ${it.x},${y}`;
      svg.append(s("path", { class: `gv-edge${hit}`, d, "marker-end": "url(#gv-arrow)" }));
      kids.push(gvPill(it.id, it.x, it.y, col));
    }
  }
  const c = gvNode(id);
  kids.push(h("button", { type: "button", class: `gv-node gv-mid${id === GV.selected ? " sel" : ""}${GV.touched.has(id) ? " hit" : ""}`, hue: gvHue(c.label),
    "data-id": id, css: { left: `${midX}px`, top: `${midY}px`, width: `${mid}px` }, title: `${c.name}\n${c.label} · ${c.id}\nclick: show its details`,
    onclick: () => gvSelect(id) },
  h("div", { class: "gv-mid-top" }, h("span", { class: "gv-dot" }), h("span", { class: "gv-name" }, c.name)),
  h("div", { class: "gv-mid-sub" }, `${c.label} · ${GV.ix.degree(id)} edges${c.status ? ` · ${c.status}` : ""}`)));
  if (!left.items.length && !right.items.length) {
    kids.push(h("div", { class: "gv-alone", css: { left: `${midX}px`, top: `${midY + GV_MID_H + 8}px`, width: `${mid}px` } }, "No edges."));
  }
  canvas.append(svg, ...kids);
  const shown = new Set([id, ...[...left.items, ...right.items].filter((it) => it.kind === "node").map((it) => it.id)]);
  const labels = [...new Set([...shown].map((x) => gvNode(x).label))].sort();
  const legend = h("div", { class: "gv-legend" }, labels.map((l) => h("span", { class: "gv-key", hue: gvHue(l) }, h("span", { class: "gv-dot" }), l)));
  return [canvas, legend];
}

function gvCrumbs() {
  const items = GV.trail.map((id, i) => [h("a", { href: "#", onclick: (e) => { e.preventDefault(); gvBack(i); } }, gvNode(id).name), h("span", { class: "gv-sep" }, " › ")]);
  return [items, h("strong", {}, gvNode(GV.center).name), GV.follow ? h("span", { class: "gv-live" }, " ● following ops") : null];
}

// --- the browser and the detail ------------------------------------------------------------

function gvListItems() {
  const q = GV.query.trim().toLowerCase();
  const queue = new Set(GV.graph.queue_status || []);
  let nodes = GV.graph.nodes;
  if (GV.label) nodes = nodes.filter((n) => n.label === GV.label);
  if (GV.queueOnly) nodes = nodes.filter((n) => queue.has(n.status));
  if (q) nodes = nodes.filter((n) => n.name.toLowerCase().includes(q) || n.id.toLowerCase().includes(q));
  const sorted = q
    ? nodes.slice().sort((a, b) => (a.name.toLowerCase().startsWith(q) ? 0 : 1) - (b.name.toLowerCase().startsWith(q) ? 0 : 1) || a.name.localeCompare(b.name))
    : nodes.slice().sort((a, b) => GV.ix.degree(b.id) - GV.ix.degree(a.id) || a.name.localeCompare(b.name));
  return { total: nodes.length, shown: sorted.slice(0, GV_LIST) };
}

function gvPaintList() {
  const { total, shown } = gvListItems();
  GV.dom.list.replaceChildren(
    h("div", { class: "gv-list-count" }, total > shown.length ? `${shown.length} of ${total}` : `${total} node${total === 1 ? "" : "s"}`,
      GV.query ? "" : " · busiest first"),
    h("ul", { class: "gv-list" }, shown.map((n) => h("li", {},
      h("button", { type: "button", class: n.id === GV.center ? "on" : "", hue: gvHue(n.label), title: `${n.label} · ${n.id}`, onclick: () => gvBrowse(n.id) },
        h("span", { class: "gv-dot" }), h("span", { class: "gv-name" }, n.name), h("span", { class: "gv-deg" }, String(GV.ix.degree(n.id))))))));
}

function gvValue(v) {
  const text = typeof v === "string" ? v : JSON.stringify(v);
  return text.length > 400 ? `${text.slice(0, 400)}…` : text;
}

function gvRelations(id, dir) {
  const groups = gvGroups(id, dir);
  if (!groups.length) return null;
  return h("div", { class: "gv-rel" }, h("h4", {}, dir === "out" ? "Outgoing" : "Incoming"),
    groups.map((g) => h("div", { class: "gv-rel-group" },
      h("div", { class: "gv-rel-type" }, dir === "out" ? `→ ${g.type}` : `${g.type} →`, h("span", { class: "gv-count" }, ` ×${g.ids.length}`)),
      h("ul", { class: "gv-rel-list" }, g.ids.slice(0, GV_REL).map((x) => h("li", {},
        h("a", { href: "#", hue: gvHue(gvNode(x).label), title: `${gvNode(x).label} · ${x}`, onclick: (e) => { e.preventDefault(); gvBrowse(x); } },
          h("span", { class: "gv-dot" }), gvNode(x).name))),
      g.ids.length > GV_REL ? h("li", { class: "gv-faint" }, `+${g.ids.length - GV_REL} more`) : null))));
}

// `path:line` sources, one row per file with its lines after it
function gvSources(sources) {
  const files = new Map();
  for (const src of sources) {
    const m = /^(.*?):(\d+)$/.exec(src);
    gvPush(files, m ? m[1] : src, m ? m[2] : null);
  }
  return h("ul", { class: "gv-src" }, [...files.entries()].map(([path, lines]) => {
    const nums = lines.filter(Boolean).map(Number).sort((a, b) => a - b);
    return h("li", { title: sources.filter((x) => x === path || x.startsWith(`${path}:`)).join("\n") },
      h("code", {}, path), nums.length ? h("span", { class: "gv-lines" }, `:${nums.join(", ")}`) : null);
  }));
}

function gvPaintDetail() {
  const id = GV.selected || GV.center;
  const n = gvNode(id);
  const queue = new Set(GV.graph.queue_status || []);
  const skip = new Set(["name", "title", "status", "source"]);
  const props = Object.entries(n.props || {}).filter(([k]) => !skip.has(k));
  gvSet(GV.dom.detail,
    h("div", { class: "gv-detail-head", hue: gvHue(n.label) },
      h("span", { class: "gv-label" }, h("span", { class: "gv-dot" }), n.label),
      n.status ? h("span", { class: `chip${queue.has(n.status) ? " warn" : ""}` }, n.status) : null,
      GV.touched.has(id) ? h("span", { class: "chip good" }, "touched by the last op") : null),
    h("div", { class: "gv-title" }, n.name),
    h("div", { class: "mono gv-id" }, n.id),
    id !== GV.center ? h("button", { type: "button", class: "gv-btn", onclick: () => gvBrowse(id) }, "Draw the graph around it") : null,
    (n.source || []).length ? h("div", { class: "gv-sources" }, h("h4", {}, "Source"), gvSources(n.source)) : null,
    props.length ? h("dl", { class: "kv gv-props" }, props.map(([k, v]) => [h("dt", {}, k), h("dd", {}, gvValue(v))])) : null,
    gvRelations(id, "out"), gvRelations(id, "in"));
}

// --- the feed and the header ------------------------------------------------------------

function gvPaintFeed(st) {
  const ops = (st.feed || []).slice().reverse();
  GV.dom.feed.replaceChildren(h("h3", {}, `Ops (${ops.length})`), ops.length
    ? h("ul", { class: "list gv-feed" }, ops.map((op) => h("li", { class: op.focus ? "gv-op go" : "gv-op",
      title: op.focus ? "click: draw the graph around the node this op touched" : "",
      onclick: op.focus ? () => { if (gvFocusOp(op)) gvPaint(); } : null },
      h("span", { class: "when" }, gvClock(op.t)),
      h("span", { class: "gv-who" }, op.who || ""),
      h("code", { class: "gv-opname" }, op.op),
      h("span", { class: "gv-delta" }, gvDelta(op)),
      h("span", { class: "gv-ms" }, gvMs(op.ms)),
      h("span", { class: op.ok ? "gv-ok" : "gv-bad" }, op.background ? "…" : op.ok ? "✓" : "✗"),
      op.error ? h("span", { class: "gv-err" }, op.error) : null)))
    : h("div", { class: "empty" }, "No ops yet. Ops appear here when Claude runs a graph command in a session that loads the omnigraph mod."));
}

function gvShortRoot(root) {
  const parts = String(root).split("/").filter(Boolean);
  return parts[parts.length - 1] || root;
}

function gvPaintHead(st) {
  const g = GV.graph;
  const pick = GV.stores.length > 1
    ? (() => {
        const sel = h("select", { class: "gv-pick", "aria-label": "Store" }, GV.stores.map((x) =>
          h("option", { value: x.id, selected: x.id === GV.storeId || null }, `${x.name} — ${gvShortRoot(x.root)}${x.branch ? ` (${x.branch})` : ""}`)));
        sel.addEventListener("change", () => { location.hash = `#/graph/${encodeURIComponent(sel.value)}`; });
        return sel;
      })()
    : h("strong", {}, st.name);
  gvSet(GV.dom.head, pick,
    h("span", { class: "gv-meta", title: st.dir },
      [st.branch, `${st.nodes} nodes`, `${st.edges} edges`, `${st.labels} labels`, st.version ? `v ${st.version}` : null].filter(Boolean).join(" · ")),
    st.preset ? h("span", { class: "chip", title: `found with the omnigraph preset ${st.preset}` }, `preset ${st.preset}`) : null,
    st.queued ? h("span", { class: "chip warn", title: `nodes with status ${(g && g.queue_status || []).join(" or ")}` }, `${st.queued} ${(g && g.queue_status || ["queued"])[0]}`) : null,
    st.conflicts ? h("span", { class: "chip bad", title: g ? g.conflicts.join("\n") : "" }, `${st.conflicts} open conflict${st.conflicts === 1 ? "" : "s"}`) : null,
    st.problem ? h("span", { class: "chip warn" }, st.problem) : null);
}

// --- the view ---------------------------------------------------------------------------

function gvMount() {
  const follow = h("input", { type: "checkbox", id: "gv-follow", checked: GV.follow || null });
  follow.addEventListener("change", () => {
    setFollow(follow.checked);
    const st = gvStore();
    const op = follow.checked && st && GV.ix ? gvNewestFocus(st.feed || []) : null;
    if (op && gvFocusOp(op, true)) gvPaint();
    else gvPaintCrumbs();
  });
  const input = h("input", { type: "search", class: "gv-search", placeholder: "Find a node by name or id", "aria-label": "Find a node" });
  input.value = GV.query;
  input.addEventListener("input", () => { GV.query = input.value; gvPaintList(); });
  const labelSel = h("select", { class: "gv-labels", "aria-label": "Label" });
  labelSel.addEventListener("change", () => { GV.label = labelSel.value; gvPaintList(); });
  const queue = h("input", { type: "checkbox", id: "gv-queue", checked: GV.queueOnly || null });
  queue.addEventListener("change", () => { GV.queueOnly = queue.checked; gvPaintList(); });
  const dom = {
    head: h("div", { class: "gv-headline" }), follow, input, labelSel, queue,
    list: h("div", {}), crumbs: h("div", { class: "gv-crumbs" }), canvasWrap: h("section", { class: "gv-wrap" }),
    canvas: h("div", {}), detail: h("section", { class: "panel gv-detail" }), feed: h("section", { class: "panel gv-feedpanel" }),
  };
  dom.root = h("div", { class: "gv" },
    h("div", { class: "gv-bar" }, dom.head, h("label", { class: "toggle", for: "gv-follow", title: "jump to each node the next ops touch" }, follow, "Follow ops")),
    h("div", { class: "gv-grid" },
      h("aside", { class: "panel gv-side" }, input, labelSel,
        h("label", { class: "toggle gv-queue", for: "gv-queue" }, queue, h("span", { class: "gv-queue-label" }, "Review queue only")), dom.list),
      h("div", { class: "gv-center" }, dom.crumbs, dom.canvasWrap, dom.feed),
      dom.detail));
  GV.dom = dom;
  app.replaceChildren(dom.root);
}

function gvPaintCrumbs() { gvSet(GV.dom.crumbs, gvCrumbs()); }

function gvPaintCanvas() {
  const [canvas, legend] = gvCanvas();
  GV.dom.canvas = canvas;
  GV.dom.canvasWrap.replaceChildren(canvas, legend);
}

function gvPaintLabels() {
  // a filter the store no longer has a use for would hide every node with no control to clear it
  if (GV.label && !GV.ix.labels.has(GV.label)) GV.label = "";
  if (!GV.graph.queued) GV.queueOnly = false;
  GV.dom.queue.checked = GV.queueOnly;
  const opts = [h("option", { value: "" }, `All labels (${GV.graph.nodes.length})`),
    ...[...GV.ix.labels.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([l, n]) => h("option", { value: l, selected: l === GV.label || null }, `${l} (${n})`))];
  GV.dom.labelSel.replaceChildren(...opts);
  GV.dom.queue.closest("label").hidden = !GV.graph.queued;
  GV.dom.queue.closest("label").querySelector(".gv-queue-label").textContent = `Review queue only (${GV.graph.queued})`;
}

function gvPaint() {
  if (!GV.dom || !GV.graph) return;
  if (!GV.center) {
    GV.dom.crumbs.replaceChildren();
    gvSet(GV.dom.canvasWrap, h("div", { class: "empty gv-nothing" }, "This store has no nodes yet."));
    gvSet(GV.dom.detail, h("div", { class: "empty" }, "Nothing to show."));
    gvPaintList();
    return;
  }
  gvPaintCrumbs();
  gvPaintCanvas();
  gvPaintDetail();
  gvPaintList();
}

function gvEmpty() {
  GV.dom = null;
  app.replaceChildren(h("div", { class: "notice gv-none" },
    h("h3", {}, "No omnigraph store found"),
    h("p", {}, "The board looks for a store (", h("code", {}, "manifest.json"), ", ", h("code", {}, "nodes.jsonl"), ", ", h("code", {}, "edges.jsonl"),
      ") under the folders omnigraph saw ops in, enrolled repos and their tracks, and the folders of running sessions: at the omnigraph ",
      h("code", {}, "store"), " option (", h("code", {}, "graphs/dev.omni"), " by default) and at each preset's store (", h("code", {}, "ontology/build"), ")."),
    h("p", {}, "To try it: ", h("code", {}, "omnigraph init"), " in a folder with the demo CLI on PATH, then start ", h("code", {}, "claude"),
      " there (", h("code", {}, "mods/omnigraph/README.md"), ", Try it in 60 seconds).")));
}

async function gvLoadGraph(st) {
  const g = await api(`/api/graph?id=${encodeURIComponent(st.id)}`);
  if (currentRoute().view !== "graph" || GV.storeId !== st.id) return false;
  const first = !GV.graph || GV.graph.id !== g.id;
  GV.graph = g;
  GV.ix = gvIndex(g);
  if (first) {
    GV.trail = []; GV.expanded = new Set(); GV.touched = new Set(); GV.label = ""; GV.queueOnly = false;
    GV.center = null; GV.selected = null;
  }
  GV.trail = GV.trail.filter((id) => GV.ix.byId.has(id));
  if (GV.center && !GV.ix.byId.has(GV.center)) GV.center = null;
  const fresh = first ? st.feed || [] : gvFresh(st.feed);
  const op = first || GV.follow ? gvNewestFocus(fresh) : null;
  if (op) gvFocusOp(op, true);
  gvSeen(st.feed);
  if (!GV.center) GV.center = gvBusiest();
  if (!GV.selected || !GV.ix.byId.has(GV.selected)) GV.selected = GV.center;
  gvPaintLabels();
  return true;
}

async function loadGraphs() {
  let data;
  try {
    data = await api("/api/graphs");
    lastOk = Date.now();
  } catch (e) {
    if (currentRoute().view !== "graph") return;
    if (authFailed) return showAuth();
    GV.listJson = "";
    if (GV.dom && app.contains(GV.dom.root)) gvSet(GV.dom.head, h("span", { class: "chip warn" }, `Could not load graphs: ${e.message}`));
    else app.replaceChildren(h("div", { class: "notice" }, `Could not load graphs: ${e.message}`));
    return;
  }
  if (currentRoute().view !== "graph") return;
  GV.stores = data.stores || [];
  setStatus();
  if (!GV.stores.length) { GV.graph = null; GV.listJson = ""; return gvEmpty(); }
  const wanted = currentRoute().id || localStorage.getItem("shepherd.graph.store") || "";
  const st = GV.stores.find((x) => x.id === wanted) || GV.stores[0];
  if (GV.storeId !== st.id) { GV.storeId = st.id; GV.graph = null; GV.listJson = ""; }
  localStorage.setItem("shepherd.graph.store", st.id);
  if (!GV.dom || !app.contains(GV.dom.root)) {
    // back from another tab (or a failed poll): the store in hand is drawn again at once
    gvMount();
    GV.listJson = "";
    if (GV.graph && GV.ix) { gvPaintLabels(); gvPaint(); }
  }
  const json = JSON.stringify(GV.stores);
  if (json === GV.listJson && GV.graph) return;
  GV.listJson = json;
  gvPaintFeed(st);
  if (!GV.graph || GV.graph.rev !== st.rev || GV.graph.id !== st.id) {
    try {
      if (!(await gvLoadGraph(st))) return;
    } catch (e) {
      if (authFailed) return showAuth();
      GV.listJson = "";
      GV.dom.canvasWrap.replaceChildren(h("div", { class: "notice" }, `Could not load the store: ${e.message}`));
      return;
    }
    gvPaint();
  } else {
    // ops that changed nothing (queries): follow them without reading the store again
    const op = GV.follow ? gvNewestFocus(gvFresh(st.feed)) : null;
    gvSeen(st.feed);
    if (op && gvFocusOp(op, true)) gvPaint();
  }
  gvPaintHead(st);
}

function startGraph() {
  stopGraph();
  GV.listJson = "";
  loadGraphs();
  GV.timer = setInterval(() => document.visibilityState === "visible" && loadGraphs(), POLL_MS);
}

function stopGraph() {
  clearInterval(GV.timer);
  GV.timer = null;
}

let gvResized = null;
window.addEventListener("resize", () => {
  clearTimeout(gvResized);
  gvResized = setTimeout(() => { if (GV.timer && GV.dom && GV.graph && GV.center) gvPaintCanvas(); }, 150);
});
