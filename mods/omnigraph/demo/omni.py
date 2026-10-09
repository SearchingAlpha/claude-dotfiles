#!/usr/bin/env python3
"""omnigraph demo CLI: a small issue-tracker graph in the omnigraph store format.

    omni.py init     [--store DIR] [--force]
    omni.py schema   [--store DIR]
    omni.py mutate   OP --params JSON [--store DIR] [--query FILE]
    omni.py query    NAME [--params JSON] [--store DIR] [--query FILE]
    omni.py status   [--store DIR]

The store is a directory of three files the omnigraph mod reads (README.md,
"Store format"): nodes.jsonl, edges.jsonl and manifest.json, plus ops.jsonl, a
log of every mutate and query. Every write replaces whole files, the manifest
last, so a reader never sees a half-written store. Synthetic data only.

Python 3.9, standard library only.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time

try:
    import fcntl
except ImportError:  # Windows: no lock, one writer at a time is on the caller
    fcntl = None

DEFAULT_STORE = os.environ.get("OMNIGRAPH_STORE", "graphs/dev.omni")
QUERIES_FILE = "queries/queries.gq"

# The seed graph: (id, label, name, extra props), then (from, type, to).
SEED_NODES = [
    ("epc-tenant-isolation", "Epic", "Hard tenant isolation", {}),
    ("epc-offline-sync", "Epic", "Offline editing & sync", {}),
    ("epc-sync-observability", "Epic", "Sync observability baseline", {}),
    ("epc-billing-v2", "Epic", "Usage-based billing v2", {}),
    ("iss-offline-queue", "Issue", "Persist the offline edit queue", {"status": "open"}),
    ("iss-offline-merge", "Issue", "Three-way merge for list fields", {"status": "open"}),
    ("iss-offline-presence", "Issue", "Presence dots after reconnect", {"status": "closed"}),
    ("iss-offline-retry", "Issue", "Backoff for sync retries", {"status": "open"}),
    ("iss-offline-tombstones", "Issue", "Collect CRDT tombstones", {"status": "open"}),
    ("iss-tenant-rls", "Issue", "Row-level security on shared tables", {"status": "open"}),
    ("iss-tenant-keys", "Issue", "Per-tenant encryption keys", {"status": "open"}),
    ("iss-tenant-audit", "Issue", "Cross-tenant access audit log", {"status": "closed"}),
    ("iss-obs-lag", "Issue", "Sync lag histogram", {"status": "open"}),
    ("iss-obs-trace", "Issue", "Trace one merge end to end", {"status": "open"}),
    ("iss-bill-meter", "Issue", "Usage meter for API calls", {"status": "open"}),
    ("iss-bill-proration", "Issue", "Prorated invoices on plan change", {"status": "proposed"}),
    ("act-nadia", "Actor", "Nadia Okonkwo", {}),
    ("act-tomas", "Actor", "Tomas Ferreira", {}),
    ("act-priya", "Actor", "Priya Raman", {}),
    ("act-jun", "Actor", "Jun Park", {}),
    ("lbl-crdt", "Label", "crdt", {}),
    ("lbl-sync-core", "Label", "sync-core", {}),
    ("lbl-security", "Label", "security", {}),
    ("lbl-perf", "Label", "perf", {}),
    ("lbl-billing", "Label", "billing", {}),
    ("rel-v1-5-0", "Release", "v1.5.0", {}),
    ("rel-v1-6-0", "Release", "v1.6.0", {}),
    ("rfc-offline", "RFC", "RFC: Offline editing & conflict resolution", {"status": "proposed"}),
    ("rfc-tenancy", "RFC", "RFC: Tenant isolation model", {"status": "accepted"}),
    ("cmt-offline-epic-1", "Commit", "cmt-offline-epic-1", {}),
    ("cmt-tenant-rls-1", "Commit", "cmt-tenant-rls-1", {}),
]

SEED_EDGES = [
    ("epc-offline-sync", "contains", "iss-offline-queue"),
    ("epc-offline-sync", "contains", "iss-offline-merge"),
    ("epc-offline-sync", "contains", "iss-offline-presence"),
    ("epc-offline-sync", "contains", "iss-offline-retry"),
    ("epc-offline-sync", "contains", "iss-offline-tombstones"),
    ("epc-tenant-isolation", "contains", "iss-tenant-rls"),
    ("epc-tenant-isolation", "contains", "iss-tenant-keys"),
    ("epc-tenant-isolation", "contains", "iss-tenant-audit"),
    ("epc-sync-observability", "contains", "iss-obs-lag"),
    ("epc-sync-observability", "contains", "iss-obs-trace"),
    ("epc-billing-v2", "contains", "iss-bill-meter"),
    ("epc-billing-v2", "contains", "iss-bill-proration"),
    ("epc-offline-sync", "owned_by", "act-nadia"),
    ("epc-tenant-isolation", "owned_by", "act-priya"),
    ("epc-sync-observability", "owned_by", "act-jun"),
    ("epc-billing-v2", "owned_by", "act-tomas"),
    ("iss-offline-queue", "owned_by", "act-nadia"),
    ("iss-offline-merge", "owned_by", "act-tomas"),
    ("iss-tenant-rls", "owned_by", "act-priya"),
    ("iss-obs-lag", "owned_by", "act-jun"),
    ("epc-offline-sync", "tagged", "lbl-crdt"),
    ("epc-offline-sync", "tagged", "lbl-sync-core"),
    ("iss-offline-merge", "tagged", "lbl-crdt"),
    ("iss-offline-tombstones", "tagged", "lbl-crdt"),
    ("iss-offline-retry", "tagged", "lbl-perf"),
    ("iss-obs-lag", "tagged", "lbl-sync-core"),
    ("epc-tenant-isolation", "tagged", "lbl-security"),
    ("epc-billing-v2", "tagged", "lbl-billing"),
    ("epc-offline-sync", "targets", "rel-v1-5-0"),
    ("epc-sync-observability", "targets", "rel-v1-5-0"),
    ("epc-tenant-isolation", "targets", "rel-v1-6-0"),
    ("epc-billing-v2", "targets", "rel-v1-6-0"),
    ("rfc-offline", "specifies", "epc-offline-sync"),
    ("rfc-tenancy", "specifies", "epc-tenant-isolation"),
    ("rfc-offline", "authored_by", "act-nadia"),
    ("rfc-tenancy", "authored_by", "act-priya"),
    ("cmt-offline-epic-1", "implements", "iss-offline-queue"),
    ("cmt-offline-epic-1", "authored_by", "act-nadia"),
    ("cmt-tenant-rls-1", "implements", "iss-tenant-rls"),
    ("iss-offline-queue", "blocks", "iss-offline-merge"),
    ("iss-obs-lag", "blocks", "iss-obs-trace"),
]

QUERIES_TEXT = """\
# The operations the omnigraph demo CLI accepts: `omni.py mutate|query <name>
# --query queries/queries.gq` refuses a name this file does not declare.

mutation create_issue(title, id?, status?, epic?, owner?, labels?)
mutation add_issue_to_epic(epic, issue)
mutation assign_issue_owner(issue, owner)
mutation link_blocks(blocker, blocked)
mutation tag_issue(issue, label)
mutation close_issue(issue)

query epic_open_issues(epic)
query blocked()
query neighbors(id)
query list_nodes(label)
"""


class OmniError(Exception):
    """A request the graph refuses: unknown id, wrong label, duplicate edge."""


# ------------------------------------------------------------------ the store


class Store:
    """The graph on disk: nodes by id, edges as (from, type, to) triples."""

    def __init__(self, path):
        self.path = path
        self.nodes = {}
        self.edges = []

    def file(self, name):
        return os.path.join(self.path, name)

    def exists(self):
        return os.path.isfile(self.file("nodes.jsonl"))

    def load(self):
        if not self.exists():
            raise OmniError(f"no store at {self.path}: run `omni.py init` first")
        self.nodes = {}
        for record in read_jsonl(self.file("nodes.jsonl")):
            self.nodes[record["id"]] = record
        self.edges = [(r["from"], r["type"], r["to"]) for r in read_jsonl(self.file("edges.jsonl"))]
        return self

    def save(self):
        os.makedirs(self.path, exist_ok=True)
        nodes = "".join(json.dumps(self.nodes[k], sort_keys=True) + "\n" for k in sorted(self.nodes))
        edges = "".join(
            json.dumps({"from": a, "type": t, "to": b, "props": {}}, sort_keys=True) + "\n"
            for a, t, b in sorted(set(self.edges))
        )
        write_atomic(self.file("nodes.jsonl"), nodes)
        write_atomic(self.file("edges.jsonl"), edges)
        # The manifest goes last: a reader that sees a new one sees the files it counts.
        write_atomic(self.file("manifest.json"), json.dumps(self.manifest(nodes + edges), indent=1, sort_keys=True) + "\n")

    def manifest(self, text):
        labels, types, status = {}, {}, {}
        for node in self.nodes.values():
            labels[node["label"]] = labels.get(node["label"], 0) + 1
            state = node["props"].get("status")
            if state:
                status[state] = status.get(state, 0) + 1
        for _, edge_type, _ in set(self.edges):
            types[edge_type] = types.get(edge_type, 0) + 1
        return {
            "format": "omnigraph/1",
            "name": os.path.basename(os.path.normpath(self.path)),
            "version": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
            "nodes": len(self.nodes),
            "edges": len(set(self.edges)),
            "labels": labels,
            "edge_types": types,
            "status": status,
        }

    # -- reads

    def node(self, node_id, label=None):
        node = self.nodes.get(node_id)
        if node is None:
            raise OmniError(f"no node {node_id!r}")
        if label is not None and node["label"] != label:
            raise OmniError(f"{node_id!r} is a {node['label']}, not a {label}")
        return node

    def out(self, node_id, edge_type):
        return [b for a, t, b in self.edges if a == node_id and t == edge_type]

    def into(self, node_id, edge_type):
        return [a for a, t, b in self.edges if b == node_id and t == edge_type]

    def name(self, node_id):
        return self.nodes[node_id]["props"].get("name", node_id)

    # -- writes

    def add_node(self, node_id, label, name, **props):
        if node_id in self.nodes:
            raise OmniError(f"{node_id!r} exists already")
        self.nodes[node_id] = {"id": node_id, "label": label, "props": {"name": name, **props}}

    def add_edge(self, a, edge_type, b):
        if (a, edge_type, b) in self.edges:
            raise OmniError(f"{a} -{edge_type}-> {b} exists already")
        self.edges.append((a, edge_type, b))


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_atomic(path, text):
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


class Lock:
    """An exclusive lock on the store for one command (none where fcntl is missing)."""

    def __init__(self, path):
        self.path = path
        self.fh = None

    def __enter__(self):
        os.makedirs(self.path, exist_ok=True)
        self.fh = open(os.path.join(self.path, ".lock"), "w")
        if fcntl is not None:
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if fcntl is not None:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


# --------------------------------------------------------------- operations


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "untitled"


def need(params, *keys):
    missing = [k for k in keys if not isinstance(params.get(k), str) or not params.get(k)]
    if missing:
        raise OmniError(f"missing params: {', '.join(missing)}")
    return [params[k] for k in keys]


def optional(params, key):
    """An optional string param: None when absent or empty, OmniError when it is not a string."""
    value = params.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise OmniError(f"param {key} must be a string, not {type(value).__name__}")
    return value


def string_list(params, key):
    """An optional list of strings; one string reads as a list of one."""
    value = params.get(key)
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise OmniError(f"param {key} must be a string or a list of strings")
    return value


def create_issue(store, params):
    (title,) = need(params, "title")
    issue = optional(params, "id") or "iss-" + slug(title)
    epic, owner = optional(params, "epic"), optional(params, "owner")
    labels = string_list(params, "labels")
    store.add_node(issue, "Issue", title, status=optional(params, "status") or "open")
    if epic:
        store.node(epic, "Epic")
        store.add_edge(epic, "contains", issue)
    if owner:
        store.node(owner, "Actor")
        store.add_edge(issue, "owned_by", owner)
    for label in labels:
        store.node(label, "Label")
        store.add_edge(issue, "tagged", label)
    return {"id": issue}


def add_issue_to_epic(store, params):
    epic, issue = need(params, "epic", "issue")
    store.node(epic, "Epic")
    store.node(issue, "Issue")
    for other in store.into(issue, "contains"):
        raise OmniError(f"{issue} is in {other} already")
    store.add_edge(epic, "contains", issue)
    return {"epic": epic, "issue": issue}


def assign_issue_owner(store, params):
    issue, owner = need(params, "issue", "owner")
    store.node(issue, "Issue")
    store.node(owner, "Actor")
    if owner in store.out(issue, "owned_by"):
        raise OmniError(f"{issue} is owned by {owner} already")
    store.edges = [e for e in store.edges if not (e[0] == issue and e[1] == "owned_by")]
    store.add_edge(issue, "owned_by", owner)
    return {"issue": issue, "owner": owner}


def link_blocks(store, params):
    blocker, blocked = need(params, "blocker", "blocked")
    if blocker == blocked:
        raise OmniError("an issue cannot block itself")
    store.node(blocker, "Issue")
    store.node(blocked, "Issue")
    store.add_edge(blocker, "blocks", blocked)
    return {"blocker": blocker, "blocked": blocked}


def tag_issue(store, params):
    issue, label = need(params, "issue", "label")
    store.node(issue, "Issue")
    store.node(label, "Label")
    store.add_edge(issue, "tagged", label)
    return {"issue": issue, "label": label}


def close_issue(store, params):
    (issue,) = need(params, "issue")
    node = store.node(issue, "Issue")
    if node["props"].get("status") == "closed":
        raise OmniError(f"{issue} is closed already")
    node["props"]["status"] = "closed"
    return {"issue": issue}


MUTATIONS = {
    "create_issue": create_issue,
    "add_issue_to_epic": add_issue_to_epic,
    "assign_issue_owner": assign_issue_owner,
    "link_blocks": link_blocks,
    "tag_issue": tag_issue,
    "close_issue": close_issue,
}


def issue_row(store, issue):
    owners = ", ".join(store.name(o) for o in store.out(issue, "owned_by")) or "-"
    blockers = [b for b in store.into(issue, "blocks") if store.nodes[b]["props"].get("status") != "closed"]
    blocked = f"  blocked by {', '.join(blockers)}" if blockers else ""
    return f"{issue}  {store.name(issue)}  ({owners}){blocked}"


def epic_open_issues(store, params):
    (epic,) = need(params, "epic")
    store.node(epic, "Epic")
    issues = [i for i in store.out(epic, "contains") if store.nodes[i]["props"].get("status") == "open"]
    return [issue_row(store, i) for i in sorted(issues)]


def blocked(store, params):
    rows = []
    for issue in sorted(n for n, r in store.nodes.items() if r["label"] == "Issue"):
        if store.nodes[issue]["props"].get("status") == "closed":
            continue
        if any(store.nodes[b]["props"].get("status") != "closed" for b in store.into(issue, "blocks")):
            rows.append(issue_row(store, issue))
    return rows


def neighbors(store, params):
    (node_id,) = need(params, "id")
    store.node(node_id)
    rows = [f"-> {t:<12} {b}  {store.name(b)}" for a, t, b in sorted(store.edges) if a == node_id]
    rows += [f"<- {t:<12} {a}  {store.name(a)}" for a, t, b in sorted(store.edges) if b == node_id]
    return rows


def list_nodes(store, params):
    (label,) = need(params, "label")
    ids = sorted(n for n, r in store.nodes.items() if r["label"] == label)
    if not ids:
        raise OmniError(f"no {label} nodes; labels: {', '.join(sorted({r['label'] for r in store.nodes.values()}))}")
    return [f"{i}  {store.name(i)}" for i in ids]


QUERIES = {"epic_open_issues": epic_open_issues, "blocked": blocked, "neighbors": neighbors, "list_nodes": list_nodes}


def declared(path):
    """The operation names a queries file declares (`mutation name(...)`, `query name(...)`)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return set(re.findall(r"^\s*(?:mutation|query)\s+(\w+)", fh.read(), re.M))
    except OSError as exc:
        raise OmniError(f"cannot read {path}: {exc.strerror}")


# ---------------------------------------------------------------------- CLI


def process_started():
    """When this process started, seconds since the epoch: the op's latency then
    counts the interpreter's start, as the caller waits for it. Linux /proc; the
    current time elsewhere."""
    try:
        with open("/proc/self/stat", encoding="ascii") as fh:
            start_ticks = int(fh.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/uptime", encoding="ascii") as fh:
            uptime = float(fh.read().split()[0])
        return min(time.time(), time.time() - (uptime - start_ticks / os.sysconf("SC_CLK_TCK")))
    except (OSError, ValueError, IndexError):
        return time.time()


def log_op(store, record):
    with open(store.file("ops.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def seed(store):
    store.nodes, store.edges = {}, []
    for node_id, label, name, props in SEED_NODES:
        store.add_node(node_id, label, name, **props)
    for a, edge_type, b in SEED_EDGES:
        store.add_edge(a, edge_type, b)


def cmd_init(args):
    store = Store(args.store)
    if store.exists() and not args.force:
        print(f"{args.store} exists; --force reseeds it", file=sys.stderr)
        return 1
    with Lock(args.store):
        seed(store)
        store.save()
        if os.path.exists(store.file("ops.jsonl")):
            os.remove(store.file("ops.jsonl"))
    if not os.path.exists(QUERIES_FILE):
        os.makedirs(os.path.dirname(QUERIES_FILE), exist_ok=True)
        write_atomic(QUERIES_FILE, QUERIES_TEXT)
    manifest = json.load(open(store.file("manifest.json"), encoding="utf-8"))
    print(json.dumps({"ok": True, "op": "init", "store": args.store, "nodes": manifest["nodes"], "edges": manifest["edges"]}))
    return 0


def params_of(text):
    """--params as a dict; OmniError when it is not a JSON object."""
    try:
        params = json.loads(text) if text else {}
    except ValueError as exc:
        raise OmniError(f"--params is not JSON: {exc}")
    if not isinstance(params, dict):
        raise OmniError("--params must be a JSON object")
    return params


def run(args, kind, table):
    started = process_started()
    name = args.name
    store = Store(args.store)
    try:
        params = params_of(args.params)
    except OmniError as exc:
        # Logged too when there is a store, so a reader of the log sees the failure.
        print(f"omnigraph: {kind} {name}: {exc}", file=sys.stderr)
        if store.exists():
            with Lock(args.store):
                log_op(store, {"t": int(started * 1000), "kind": kind, "op": name, "ok": False, "error": str(exc),
                               "dNodes": 0, "dEdges": 0, "ms": int((time.time() - started) * 1000)})
        return 2
    record = {"t": int(started * 1000), "kind": kind, "op": name, "params": params}
    if not store.exists():
        print(f"omnigraph: no store at {args.store}: run `omnigraph init` first", file=sys.stderr)
        return 1
    with Lock(args.store):
        try:
            if args.query and name not in declared(args.query):
                raise OmniError(f"{args.query} declares no {name}")
            if name not in table:
                raise OmniError(f"unknown {kind} {name!r}; known: {', '.join(sorted(table))}")
            store.load()
            before = (len(store.nodes), len(set(store.edges)))
            out = table[name](store, params)
            if kind == "mutate":
                store.save()
            record.update(ok=True, dNodes=len(store.nodes) - before[0], dEdges=len(set(store.edges)) - before[1])
        except OmniError as exc:
            record.update(ok=False, error=str(exc), dNodes=0, dEdges=0)
            out = None
        except (TypeError, KeyError, ValueError, AttributeError) as exc:
            # A param of a shape no check foresaw, or a store line hand-edited
            # wrong: a failed op with a one-line reason, not a traceback.
            record.update(ok=False, error=f"{type(exc).__name__}: {exc}", dNodes=0, dEdges=0)
            out = None
        record["ms"] = int((time.time() - started) * 1000)
        if store.exists():
            log_op(store, record)
    if not record["ok"]:
        print(f"omnigraph: {kind} {name}: {record['error']}", file=sys.stderr)
        return 1
    if kind == "query":
        for row in out:
            print(row)
        print(f"({len(out)} rows)")
    else:
        print(json.dumps({"ok": True, "op": name, "dNodes": record["dNodes"], "dEdges": record["dEdges"], **out}))
    return 0


def cmd_schema(args):
    """What an agent needs to drive the CLI: the ops, the labels and some ids."""
    print(QUERIES_TEXT, end="")
    store = Store(args.store)
    if store.exists():
        store.load()
        print("\n# labels and ids in", args.store)
        for label in sorted({r["label"] for r in store.nodes.values()}):
            ids = sorted(n for n, r in store.nodes.items() if r["label"] == label)
            print(f"{label}: {', '.join(ids)}")
    print('\n# example: omnigraph mutate add_issue_to_epic --store graphs/dev.omni '
          '--params \'{"epic":"epc-offline-sync","issue":"iss-offline-queue"}\'')
    return 0


def cmd_status(args):
    store = Store(args.store)
    if not store.exists():
        print(f"no store at {args.store}", file=sys.stderr)
        return 1
    print(open(store.file("manifest.json"), encoding="utf-8").read(), end="")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="omnigraph", description="omnigraph demo CLI (synthetic issue tracker)")
    sub = parser.add_subparsers(dest="cmd")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", default=DEFAULT_STORE, help="store directory (default %(default)s)")
    init = sub.add_parser("init", parents=[common], help="seed the demo graph")
    init.add_argument("--force", action="store_true", help="reseed an existing store")
    for kind in ("mutate", "query"):
        p = sub.add_parser(kind, parents=[common], help=f"run a named {kind}")
        p.add_argument("name")
        p.add_argument("--params", default="", help="a JSON object")
        p.add_argument("--query", default="", help="a queries file the name must be declared in")
    sub.add_parser("status", parents=[common], help="print the manifest")
    sub.add_parser("schema", parents=[common], help="list the ops, labels and ids")
    args = parser.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "mutate":
        return run(args, "mutate", MUTATIONS)
    if args.cmd == "query":
        return run(args, "query", QUERIES)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "schema":
        return cmd_schema(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
