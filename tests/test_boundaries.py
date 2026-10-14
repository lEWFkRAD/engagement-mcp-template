"""Privacy, filesystem, approval, and real stdio protocol regressions."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
import sysconfig

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def job(tmp_path, monkeypatch):
    root = tmp_path / "job"
    (root / "plan/ontology").mkdir(parents=True)
    data = json.loads((REPO / "ontology/ontology.example.json").read_text(encoding="utf-8"))
    (root / "plan/ontology/ontology.json").write_text(json.dumps(data), encoding="utf-8")
    (root / "plan/workplan.md").write_text("Sample text " * 50, encoding="utf-8")
    (root / "plan/private-notes.md").write_text("Private example content", encoding="utf-8")
    monkeypatch.setenv("ENGAGEMENT_ROOT", str(root))
    monkeypatch.setenv("ENGAGEMENT_AUDIENCE", "internal")
    monkeypatch.delenv("ENGAGEMENT_ALLOW_WRITE", raising=False)
    sys.path.insert(0, str(REPO))
    import engagement_mcp
    mod = importlib.reload(engagement_mcp)
    yield mod, root, data
    sys.path.remove(str(REPO))


def save(root, data):
    (root / "plan/ontology/ontology.json").write_text(json.dumps(data), encoding="utf-8")


def tools(mod):
    return {t.name for t in asyncio.run(mod.mcp.list_tools())}


def test_default_write_tool_not_advertised(job):
    mod, root, _ = job
    assert "log_document" not in tools(mod)
    assert "error" in mod.log_document("S-01", "plan/workplan.md")
    assert not (root / "plan/intake").exists()


@pytest.mark.parametrize("flag", ["0", "true", "yes", "2", ""])
def test_write_requires_exact_flag(job, monkeypatch, flag):
    mod, _, _ = job
    monkeypatch.setenv("ENGAGEMENT_ALLOW_WRITE", flag)
    mod = importlib.reload(mod)
    assert "log_document" not in tools(mod)


def test_shared_never_writes_even_with_flag(job, monkeypatch):
    mod, root, _ = job
    monkeypatch.setenv("ENGAGEMENT_ALLOW_WRITE", "1")
    monkeypatch.setenv("ENGAGEMENT_AUDIENCE", "shared")
    mod = importlib.reload(mod)
    assert "log_document" not in tools(mod)
    assert "error" in mod.log_document("S-01", "plan/workplan.md")
    assert not (root / "plan/intake").exists()


def test_shared_names_and_references_do_not_leak(job, monkeypatch):
    mod, root, data = job
    shared = next(n for n in data["nodes"] if n["id"] == "D-01")
    shared["depends_on"] = ["D-02"]
    shared["gated_by"].append("O-01")
    shared["owner"] = "O-01"
    save(root, data)
    monkeypatch.setenv("ENGAGEMENT_AUDIENCE", "shared")
    mod = importlib.reload(mod)
    outputs = [mod.get_node("D-01"), mod.read_doc("missing"), mod.read_doc("private-notes"),
               mod.gate_check("D-01"), mod.run_check(), mod.job_overview(), mod.find("Sample")]
    text = json.dumps(outputs)
    for secret in ("D-02", "O-01", "private-notes", "Private example content"):
        assert secret not in text
    assert mod.gate_check("D-01")["blocked"]


@pytest.mark.parametrize("key,value", [("ENGAGEMENT_ROOT", ""), ("ENGAGEMENT_AUDIENCE", "public")])
def test_invalid_configuration_fails_closed(job, monkeypatch, key, value):
    mod, _, _ = job
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        importlib.reload(mod)


def test_direct_gate_unmet_and_met_never_approves(job):
    mod, root, data = job
    assert mod.gate_check("G-01")["blocked"]
    next(n for n in data["nodes"] if n["id"] == "G-01")["status"] = "met"
    save(root, data)
    answer = mod.gate_check("G-01")
    assert not answer["blocked"]
    assert answer["approved"] is False


def test_missing_dependency_and_cycle_block(job):
    mod, root, data = job
    node = next(n for n in data["nodes"] if n["id"] == "D-01")
    node["depends_on"] = ["missing-input"]
    save(root, data)
    assert mod.gate_check("D-01")["blocked"]
    assert not mod.run_check()["valid"]
    node["depends_on"] = ["D-02"]
    save(root, data)
    assert any(b["status"] == "dependency_cycle" for b in mod.gate_check("D-01")["blockers"])


def test_run_check_cannot_execute_job_code(job):
    mod, root, _ = job
    marker = root / "executed"
    checker = root / "plan/ontology/check.py"
    checker.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('unsafe')", encoding="utf-8")
    assert mod.run_check()["valid"]
    assert not marker.exists()


@pytest.mark.parametrize("offset,limit", [(-1, 10), (0, 0), (0, 12001), (1000001, 10)])
def test_document_window_bounds(job, offset, limit):
    mod, _, _ = job
    assert "error" in mod.read_doc("workplan", offset, limit)


def test_document_read_window(job):
    mod, _, _ = job
    answer = mod.read_doc("workplan", 7, 4)
    assert answer["text"] == "text"
    assert answer["more"]


def test_write_receipt_and_reject_sibling_prefix(job, monkeypatch):
    mod, root, _ = job
    monkeypatch.setenv("ENGAGEMENT_ALLOW_WRITE", "1")
    mod = importlib.reload(mod)
    assert "log_document" in tools(mod)
    outside = root.parent / "job-other"
    outside.mkdir()
    (outside / "x.txt").write_text("outside", encoding="utf-8")
    assert "error" in mod.log_document("S-01", str(outside / "x.txt"))
    assert "error" in mod.log_document("S-01", "../job-other/x.txt")
    assert "error" in mod.log_document("S-01", "plan")
    answer = mod.log_document("S-01", "plan/workplan.md", "Sample receipt")
    assert answer["logged"]["path"] == "plan/workplan.md"
    assert answer["approved"] is False
    assert mod.intake_status()["filled"] == {"S-01": 1}
    assert mod.gate_check("D-01")["blocked"]


def test_ledger_lock_prevents_concurrent_append(job, monkeypatch):
    mod, root, _ = job
    monkeypatch.setenv("ENGAGEMENT_ALLOW_WRITE", "1")
    mod = importlib.reload(mod)
    (root / "plan/intake").mkdir()
    (root / "plan/intake/ledger.lock").write_text("occupied", encoding="utf-8")
    assert "error" in mod.log_document("S-01", "plan/workplan.md")
    assert not mod.LEDGER.exists()


def test_symlink_escape_is_rejected(job, tmp_path):
    mod, root, _ = job
    outside = tmp_path / "outside.txt"
    outside.write_text("outside secret", encoding="utf-8")
    link = root / "plan/workplan.md"
    link.unlink()
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation privilege is unavailable")
    assert "error" in mod.read_doc("workplan")


def test_duplicate_ids_fail_validation(job):
    mod, root, data = job
    data["nodes"].append(data["nodes"][0])
    save(root, data)
    with pytest.raises(ValueError, match="Invalid or unavailable"):
        mod.run_check()


def test_installed_entry_point_over_real_stdio(job):
    _, root, _ = job
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    command = Path(sysconfig.get_path("scripts")) / ("engagement-mcp.exe" if os.name == "nt" else "engagement-mcp")
    async def exercise():
        parameters = StdioServerParameters(command=str(command), args=[], env={
            "ENGAGEMENT_ROOT": str(root), "ENGAGEMENT_AUDIENCE": "shared", "ENGAGEMENT_ALLOW_WRITE": "1"})
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = {t.name for t in listed.tools}
                assert len(names) == 8
                assert "log_document" not in names
                result = await session.call_tool("gate_check", {"node_id": "G-01"})
                assert not result.isError
                payload = result.structuredContent
                if payload is None:
                    payload = json.loads(result.content[0].text)
                assert payload["blocked"] and payload["approved"] is False
    asyncio.run(asyncio.wait_for(exercise(), timeout=30))


def test_wrong_gate_type_cannot_clear_gate(job):
    mod, root, data = job
    next(n for n in data["nodes"] if n["id"] == "D-01")["gated_by"] = ["C-01"]
    save(root, data)
    assert not mod.run_check()["valid"]
    assert mod.gate_check("D-01")["blocked"]
