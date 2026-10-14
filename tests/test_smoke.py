"""Smoke test for the engagement-MCP template.

Loads the example ontology, checks that all tools register, that gate_check
reports the sample gate's blockers, and that shared audience hides
internal-marked nodes. Runs standalone: `python tests/test_smoke.py`.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "ontology" / "ontology.example.json"

EXPECTED_TOOLS = {
    "job_overview", "get_node", "find", "open_items", "gate_check",
    "read_doc", "intake_status", "run_check",
}


def _engagement_root(tmp: Path) -> Path:
    """Materialize a throwaway engagement root from the example ontology."""
    root = tmp / "engagement"
    ont_dir = root / "plan" / "ontology"
    ont_dir.mkdir(parents=True)
    (ont_dir / "ontology.json").write_text(
        EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return root


def _load_server(root: Path, audience: str):
    os.environ["ENGAGEMENT_ROOT"] = str(root)
    os.environ["ENGAGEMENT_AUDIENCE"] = audience
    os.environ.pop("ENGAGEMENT_ALLOW_WRITE", None)
    sys.path.insert(0, str(REPO))
    import engagement_mcp
    importlib.reload(engagement_mcp)
    return engagement_mcp


def _tool_names(mod) -> set[str]:
    import anyio
    names = set()

    async def _gather():
        for t in await mod.mcp.list_tools():
            names.add(t.name)

    anyio.run(_gather)
    return names


def test_all_tools_register():
    with tempfile.TemporaryDirectory() as td:
        mod = _load_server(_engagement_root(Path(td)), "internal")
        names = _tool_names(mod)
        missing = EXPECTED_TOOLS - names
        assert not missing, f"missing tools: {missing}"
        print(f"tools registered ({len(names)}): {sorted(names)}")


def test_gate_check_reports_sample_gate_blockers():
    with tempfile.TemporaryDirectory() as td:
        mod = _load_server(_engagement_root(Path(td)), "internal")
        res = mod.gate_check("D-01")
        assert res.get("blocked") is True, res
        gates = {b["gate"] for b in res["blockers"]}
        assert "G-01" in gates, res
        print(f"gate_check(D-01) blocked=True blockers={res['blockers']}")
        # transitive: D-02 depends on D-01, so G-01 blocks it too
        res2 = mod.gate_check("D-02")
        assert res2.get("blocked") is True and "G-01" in {b.get("gate") for b in res2["blockers"]}, res2
        print("gate_check(D-02) transitively blocked by G-01: OK")


def test_shared_audience_hides_internal_nodes():
    with tempfile.TemporaryDirectory() as td:
        root = _engagement_root(Path(td))
        internal_ids = {
            n["id"] for n in json.loads(EXAMPLE.read_text(encoding="utf-8"))["nodes"]
            if n.get("audience") != "shared"
        }
        shared_ids = {
            n["id"] for n in json.loads(EXAMPLE.read_text(encoding="utf-8"))["nodes"]
            if n.get("audience") == "shared"
        }
        assert internal_ids, "example ontology must contain internal nodes"
        mod = _load_server(root, "shared")
        d = mod._load()
        visible = set(d["idx"])
        leaked = internal_ids & visible
        assert not leaked, f"internal nodes leaked in shared mode: {leaked}"
        assert shared_ids <= visible, f"shared nodes missing: {shared_ids - visible}"
        # get_node on an internal id must error in shared mode
        one_internal = sorted(internal_ids)[0]
        assert "error" in mod.get_node(one_internal)
        print(f"shared mode hides {len(internal_ids)} internal nodes "
              f"(checked {one_internal}); {len(shared_ids)} shared nodes visible")


def test_write_tool_disabled_by_default():
    with tempfile.TemporaryDirectory() as td:
        mod = _load_server(_engagement_root(Path(td)), "internal")
        res = mod.log_document("S-01", str(mod.ROOT / "plan" / "x.txt"))
        assert "error" in res and "write disabled" in res["error"], res
        print("log_document disabled without ENGAGEMENT_ALLOW_WRITE=1: OK")


if __name__ == "__main__":
    for fn in (test_all_tools_register,
               test_gate_check_reports_sample_gate_blockers,
               test_shared_audience_hides_internal_nodes,
               test_write_tool_disabled_by_default):
        fn()
        print(f"PASS {fn.__name__}\n")
    print("ALL SMOKE TESTS PASSED")
