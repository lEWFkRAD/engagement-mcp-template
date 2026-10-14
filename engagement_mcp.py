"""Engagement MCP server (stdio, local only) â€” generalized template.

Pattern
-------
One engagement = one folder (the "root") holding an ontology (JSON), plan
documents (Markdown), and an intake ledger (JSONL). This server exposes that
engagement's shared structured language to an MCP client over a single stdio
interface. This server has no network listener or outbound integrations. The client
may forward returned data to its configured model.

Configuration is env-only:
  ENGAGEMENT_ROOT        absolute path to the engagement folder (required)
  ENGAGEMENT_AUDIENCE    internal (default) | shared
                         shared hides every node not marked audience=shared
                         and every doc not whitelisted as shareable
  ENGAGEMENT_ALLOW_WRITE set to 1 to enable the single write tool (log_document)

Safety model
------------
- Read-only by default. The one write tool appends a line to a local intake
  ledger and is disabled unless ENGAGEMENT_ALLOW_WRITE=1.
- Audience filtering: with ENGAGEMENT_AUDIENCE=shared, internal-marked nodes
  and non-whitelisted docs are hidden, so the same server can back a partner
  view without leaking firm-only items.
- Gates live in the ontology. gate_check() walks gated_by/depends_on and
  reports what blocks an action; it NEVER approves anything. Approval stays
  human.

Instantiate for a new job: copy this file, point ENGAGEMENT_ROOT at the new
engagement folder, swap in that job's ontology and docs. The code carries no
engagement-specific content.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

_root_env = os.environ.get("ENGAGEMENT_ROOT")
if not _root_env:
    raise ValueError("ENGAGEMENT_ROOT is required")
ROOT = Path(_root_env).resolve(strict=True)
PLAN = ROOT / "plan"
ONT = PLAN / "ontology" / "ontology.json"
LEDGER = PLAN / "intake" / "ledger.jsonl"
AUDIENCE = os.environ.get("ENGAGEMENT_AUDIENCE", "internal").lower()
if AUDIENCE not in ("internal", "shared"):
    raise ValueError("ENGAGEMENT_AUDIENCE must be internal or shared")
ALLOW_WRITE = os.environ.get("ENGAGEMENT_ALLOW_WRITE") == "1" and AUDIENCE == "internal"

# Docs a shared audience may read (by file name). Everything else is internal.
SHARED_DOCS = {"workplan.md", "document-request-list.md"}

# Doc names for read_doc are discovered from the plan folder at load time,
# plus any extra names listed here (name -> relative path under ROOT).
EXTRA_DOCS: dict[str, str] = {}

mcp = FastMCP("engagement")


# ---------- loading ----------
def _inside(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("Path must stay inside the engagement root")
    return resolved


def _all_nodes() -> list[dict]:
    try:
        path = _inside(ONT)
        if path.stat().st_size > 2_000_000:
            raise ValueError("Ontology too large")
        nodes = json.loads(path.read_text(encoding="utf-8"))["nodes"]
        if not isinstance(nodes, list):
            raise ValueError("Invalid nodes")
        ids = set()
        for n in nodes:
            if not isinstance(n, dict) or any(not isinstance(n.get(k), str) or not n[k] for k in ("id", "cls", "label")):
                raise ValueError("Invalid node")
            if n["cls"] not in {"client", "engagement", "deliverable", "document_slot", "gate", "open_item"}:
                raise ValueError("Unknown class")
            if n["id"] in ids or n.get("audience") not in ("internal", "shared"):
                raise ValueError("Invalid ID or audience")
            ids.add(n["id"])
            for key in ("gated_by", "depends_on", "feeds"):
                if key in n and (not isinstance(n[key], list) or any(not isinstance(v, str) for v in n[key])):
                    raise ValueError("Invalid relationship")
        return nodes
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Invalid or unavailable ontology; inspect it locally") from None


def _load() -> dict:
    nodes = _all_nodes()
    if AUDIENCE == "shared":
        nodes = [n for n in nodes if n["audience"] == "shared"]
    return {"nodes": nodes, "idx": {n["id"]: n for n in nodes}}


def _public_node(n: dict, visible: set[str]) -> dict:
    out = _short(n)
    for key in ("gated_by", "depends_on", "feeds"):
        if key in n:
            out[key] = [v for v in n[key] if v in visible]
    if AUDIENCE == "shared":
        hidden = {x["id"] for x in _all_nodes()} - visible
        if out.get("owner") in hidden:
            out["owner"] = "restricted"
    return out


def _docs() -> dict[str, Path]:
    """Plan-folder Markdown files, keyed by stem, plus EXTRA_DOCS."""
    docs = {p.stem: p for p in PLAN.glob("*.md")}
    for name, rel in EXTRA_DOCS.items():
        docs[name] = ROOT / rel
    return docs


def _short(n: dict) -> dict:
    keep = ("id", "cls", "label", "owner", "status", "severity", "due",
            "audience", "note")
    out = {k: n[k] for k in keep if k in n and n[k] not in (None, [], "")}
    if AUDIENCE == "shared":
        hidden = {x["id"] for x in _all_nodes() if x["audience"] != "shared"}
        if out.get("owner") in hidden:
            out["owner"] = "restricted"
    return out


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9\-]+", s.lower()) if len(w) > 2}


def _doc_allowed(name: str, docs: dict[str, Path]) -> bool:
    return AUDIENCE != "shared" or docs[name].name in SHARED_DOCS


# ---------- tools ----------
@mcp.tool()
def job_overview() -> dict:
    """One-screen status of the engagement: node counts by class, gate states,
    HIGH-severity items, and open decisions. Read-only."""
    d = _load()
    by: dict[str, int] = {}
    for n in d["nodes"]:
        by[n["cls"]] = by.get(n["cls"], 0) + 1
    return {
        "audience": AUDIENCE,
        "counts": by,
        "gates": [_short(n) for n in d["nodes"] if n["cls"] == "gate"],
        "high_items": [_short(n) for n in d["nodes"]
                       if n.get("severity") == "HIGH"],
        "open_items": [_short(n) for n in d["nodes"]
                       if n["cls"] == "open_item"
                       and not str(n.get("status", "")).startswith("closed")],
        "note": "Gates report blockers only; approval is always human.",
    }


@mcp.tool()
def get_node(node_id: str) -> dict:
    """Return a visible node and backlinks, omitting restricted relationship IDs."""
    d = _load()
    n = d["idx"].get(node_id)
    if n is None:
        return {"error": "unknown or unavailable node"}
    back = []
    for other in d["nodes"]:
        for key in ("gated_by", "depends_on", "feeds"):
            if node_id in other.get(key, []):
                back.append({"from": other["id"], "via": key, "label": other["label"]})
    return {"node": _public_node(n, set(d["idx"])), "linked_from": back}


@mcp.tool()
def find(query: str, cls: str | None = None, limit: int = 15) -> list[dict]:
    """Keyword search over node ids, labels, and notes. Optional class filter
    (client, engagement, deliverable, document_slot, gate, open_item, ...)."""
    if not 1 <= limit <= 100 or len(query) > 500:
        return []
    d = _load()
    q = _words(query)
    out = []
    for n in d["nodes"]:
        if cls and n["cls"].lower() != cls.lower():
            continue
        text = " ".join(str(n.get(k, "")) for k in ("id", "label", "note"))
        score = len(q & _words(text))
        if query.lower() in text.lower():
            score += 3
        if score:
            out.append((score, _short(n)))
    return [x for _, x in sorted(out, key=lambda t: -t[0])[:limit]]


@mcp.tool()
def open_items() -> dict:
    """Every open item in the ontology: open_items, unmet gates, and
    HIGH-severity nodes."""
    d = _load()
    return {
        "open_items": [_short(n) for n in d["nodes"]
                       if n["cls"] == "open_item"
                       and not str(n.get("status", "")).startswith("closed")],
        "unmet_gates": [_short(n) for n in d["nodes"]
                        if n["cls"] == "gate" and n.get("status") != "met"],
        "high": [_short(n) for n in d["nodes"] if n.get("severity") == "HIGH"],
    }


@mcp.tool()
def gate_check(node_id: str) -> dict:
    """Report gates and incomplete dependencies, conservatively hiding restricted prerequisites.

    Cycles and unresolved references are blockers. No result grants human approval.
    """
    d = _load()
    if node_id not in d["idx"]:
        return {"error": "unknown or unavailable node", "approved": False}
    blockers, completed, visiting = [], set(), set()

    def blocked(key: str, via: str, relation: str) -> None:
        if key not in d["idx"]:
            entry = {"status": "restricted_or_missing", "note": "A prerequisite requires internal review"}
            if entry not in blockers:
                blockers.append(entry)
            return
        node = d["idx"][key]
        if relation == "gated_by" and node["cls"] != "gate":
            blockers.append({"status": "invalid_gate_reference", "via": via})
            return
        status = node.get("status", "pending")
        if node["cls"] == "gate" and status != "met":
            blockers.append({"gate": key, "label": node["label"], "status": status, "via": via})
        elif relation == "depends_on" and node["cls"] != "gate" and status not in ("complete", "completed", "done", "received", "closed"):
            blockers.append({"dependency": key, "label": node["label"], "status": status, "via": via})
        if key in visiting:
            blockers.append({"status": "dependency_cycle", "via": key})
            return
        if key in completed:
            return
        visiting.add(key)
        for gate in node.get("gated_by", []):
            blocked(gate, key, "gated_by")
        for dependency in node.get("depends_on", []):
            blocked(dependency, key, "depends_on")
        visiting.remove(key)
        completed.add(key)

    blocked(node_id, node_id, "root")
    return {"node": node_id, "blocked": bool(blockers), "blockers": blockers,
            "approved": False, "note": "A human must separately approve the exact final action"}


@mcp.tool()
def read_doc(name: str, offset: int = 0, max_chars: int = 12000) -> dict:
    """Read bounded UTF-8 text by document stem; shared mode lists only shareable documents."""
    docs = _docs()
    allowed = {k: v for k, v in docs.items() if _doc_allowed(k, docs)}
    if name not in allowed:
        return {"error": "unknown or unavailable document", "names": sorted(allowed)}
    if not 0 <= offset <= 1_000_000 or not 1 <= max_chars <= 12000:
        return {"error": "invalid text window"}
    try:
        path = _inside(allowed[name])
        if not path.is_file() or path.stat().st_size > 2_000_000:
            return {"error": "document unavailable or too large"}
        with path.open(encoding="utf-8") as stream:
            text = stream.read(offset + max_chars + 1)[offset:]
    except (OSError, UnicodeError, ValueError):
        return {"error": "document unavailable"}
    return {"name": name, "offset": offset, "text": text[:max_chars], "more": len(text) > max_chars}


@mcp.tool()
def run_check() -> dict:
    """Validate graph links and visible approval gates without running any local program."""
    d = _load()
    issues = []
    for n in d["nodes"]:
        if n["cls"] == "open_item" and not n.get("owner"):
            issues.append({"node": n["id"], "problem": "missing owner"})
        for relation in ("gated_by", "depends_on", "feeds"):
            for target in n.get(relation, []):
                if target not in d["idx"]:
                    issues.append({"node": n["id"], "problem": "restricted or missing relationship"})
                elif relation == "gated_by" and d["idx"][target]["cls"] != "gate":
                    issues.append({"node": n["id"], "problem": "gate reference is not a gate"})
    return {"valid": not issues, "issues": issues, "approved": False,
            "gates": [gate_check(n["id"]) for n in d["nodes"] if n["cls"] == "gate"]}


def log_document(slot_id: str, local_path: str, note: str = "") -> dict:
    """Append a local receipt only when enabled for an internal audience; never approve it."""
    if not ALLOW_WRITE or AUDIENCE == "shared":
        return {"error": "write disabled"}
    d = _load()
    if slot_id not in d["idx"] or d["idx"][slot_id]["cls"] != "document_slot":
        return {"error": "unknown or unavailable document slot"}
    if len(note) > 1000:
        return {"error": "note exceeds 1000 characters"}
    try:
        supplied = Path(local_path)
        path = _inside(supplied if supplied.is_absolute() else ROOT / supplied)
        if not path.is_file():
            return {"error": "document file not found"}
        ledger = _inside(LEDGER)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        lock = _inside(ledger.with_suffix(".lock"))
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return {"error": "another writer holds the intake lock"}
    except (OSError, ValueError):
        return {"error": "document and ledger must stay inside the engagement root"}
    try:
        os.close(descriptor)
        rec = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
               "slot": slot_id, "path": path.relative_to(ROOT).as_posix(),
               "bytes": path.stat().st_size, "note": note}
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(rec, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {"logged": rec, "approved": False}
    finally:
        lock.unlink(missing_ok=True)


@mcp.tool()
def intake_status() -> dict:
    """Which document slots have something logged in the intake ledger, and
    which are still empty."""
    d = _load()
    slots = [n for n in d["nodes"] if n["cls"] == "document_slot"]
    got: dict[str, int] = {}
    if _inside(LEDGER).exists() and AUDIENCE != "shared":
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                got[r["slot"]] = got.get(r["slot"], 0) + 1
            except Exception:
                pass
    got = {key: count for key, count in got.items() if key in {s["id"] for s in slots}}
    return {"filled": got,
            "empty": [s["id"] for s in slots if s["id"] not in got],
            "total_slots": len(slots)}


if ALLOW_WRITE:
    mcp.tool()(log_document)


def main() -> None:
    """Run the configured server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
