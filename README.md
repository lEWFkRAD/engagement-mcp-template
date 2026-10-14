# Engagement MCP Template

A blank, reusable template for the **engagement-MCP pattern**: one engagement,
one folder, one MCP server that serves the job's shared structured language to
an agent client â€” read-only by default, local only.

## The pattern

**Topology.** One engagement = one root folder holding an ontology (JSON),
plan documents (Markdown), and an intake ledger (JSONL). A single FastMCP
stdio server sits in front of it. Every client â€” a Hermes agent, a cron job,
a partner-facing view â€” connects to the same engagement through that one MCP
interface. The server has no network listener or outbound integration. The connected
client may forward tool results to its configured model; choose that client deliberately.

**Ontology.** The job's shared structured language: nodes with ids, classes,
and typed links. The template ships with generic classes â€” `client`,
`engagement`, `deliverable`, `document_slot`, `gate`, `open_item` â€” and the
server code carries zero engagement-specific content. Instantiating for a new
job means swapping the ontology and docs, not editing the server.

**Gates.** Conditions that must clear before work runs. `gate_check` walks
`gated_by` / `depends_on` and reports what blocks an action â€” it never
approves anything. Approval stays human.

**Audience filtering.** Nodes and docs are marked `shared` or `internal`.
Running with `ENGAGEMENT_AUDIENCE=shared` hides everything internal, so the
same server can back a partner view without leaking firm-only items.

**Writes.** Read-only by default. Exactly one write tool (`log_document`)
appends to the local intake ledger, and it is disabled unless
`ENGAGEMENT_ALLOW_WRITE=1`.

## Quickstart

Use Python 3.11+ in an isolated environment. The package deliberately pins the
[official SDK v1 FastMCP interface](https://py.sdk.modelcontextprotocol.io/v1/)
to `mcp<2`; do not install it into an existing agent runtime.

```bash
python -m venv .venv
. .venv/bin/activate
# Windows PowerShell instead: .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Create a private instance outside this repository, then initialize it:

```bash
mkdir -p /absolute/path/to/example-job/plan/ontology
cp ontology/ontology.example.json /absolute/path/to/example-job/plan/ontology/ontology.json
cp docs/workplan.md docs/document-request-list.md /absolute/path/to/example-job/plan/

export ENGAGEMENT_ROOT=/absolute/path/to/example-job
export ENGAGEMENT_AUDIENCE=internal   # or: shared
# export ENGAGEMENT_ALLOW_WRITE=1     # only if you want the intake write tool

python engagement_mcp.py              # stdio MCP server
```

Run the smoke test (uses the example ontology, no engagement root needed):

```bash
python -m pytest tests/test_smoke.py    # or: python tests/test_smoke.py
```

## Instantiating for a new job

1. Create the engagement root folder; under it make `plan/ontology/` and put
   your `ontology.json` there (start from `ontology/ontology.example.json`).
2. Copy the `docs/` stubs into `plan/` and fill them in.
3. Point `ENGAGEMENT_ROOT` at the root; launch the server from your MCP
   client config (below).
4. Use `run_check` for built-in reference validation; it never executes job scripts.
5. Keep the rules: gates stay unmet until a human marks them met; every
   incoming document maps to a `document_slot`; nothing outward-facing
   happens without an explicit human go.

## Hermes `mcp_servers` config snippet

Placeholder â€” adjust paths and the venv interpreter:

```yaml
mcp_servers:
  engagement:
    command: "C:/path/to/template/.venv/Scripts/python.exe"
    args: ["C:/path/to/template/engagement_mcp.py"]
    env:
      ENGAGEMENT_ROOT: "C:/path/to/engagements/example"
      ENGAGEMENT_AUDIENCE: "internal"
      # ENGAGEMENT_ALLOW_WRITE: "1"
```

## Tools and boundaries

Eight read tools are registered by default: `job_overview`, `get_node`, `find`,
`open_items`, `gate_check`, `read_doc`, `intake_status`, and `run_check`.
`log_document` is registered only for an internal server with the write flag set
exactly to `1`. Configuration is fixed when the process starts. Unknown audience
values fail closed. `.env.example` is documentation; the server does not load it.

On PowerShell, set `$env:ENGAGEMENT_ROOT = 'C:/path/to/example-job'` and use the
same `$env:` syntax for audience and the write flag. The installed
`engagement-mcp` command is equivalent to `python engagement_mcp.py`.

Shared mode removes hidden relationship IDs and lists only whitelisted document
names. Unavailable prerequisites block conservatively without revealing their IDs.
A gate marked `met` still does not grant approval: all gate results include
`approved: false`. Intake receipts do not automatically change a gate's status.
`read_doc` returns at most 12,000 characters and rejects files larger than 2 MB.

Host filesystem permissions protect this local server. Anyone who can edit the
ontology or change its environment controls what is shared. Mark complete nodes
appropriately; do not place private prose inside a shared node. Keep documents as
untrusted data, never instructions for the client. Use a separate process for each
audience. Do not publish real job directories or intake ledgers.

Writes use an exclusive intake lock. If a writer crashes, verify no writer remains
before manually removing a stale lock. File containment resolves symlinks and
requires real directory boundaries. This is not a sandbox against a malicious
local user who can replace files concurrently.

## Sanitization

Before publishing a customized template, audit every tracked file and Git history
for private names, absolute host paths, credentials, and engagement content.
Keep your private search patterns outside the public repository:

```bash
git grep -n -i -f /private/audit-patterns.txt
```

Expected: no matches in any tracked file, including this README. Audit the
commit history before pushing as well.

## Acknowledgments

This pattern runs alongside ecosystem tools we build with:

- **OpenAccountants**: Michael Cutajar and team, [openaccountants.com](https://openaccountants.com).
- **SlowBooks Pro**: **VonHoltenCodes**, creator and maintainer.
- **Hermes**: by **Nous Research**.

These are ecosystem credits, not bundled integrations or claims of endorsement.

## License

MIT. Copyright Onyx Intelligence Systems. See LICENSE.
