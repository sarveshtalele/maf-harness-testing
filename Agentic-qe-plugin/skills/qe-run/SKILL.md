---
description: Start or resume Agentic QE from Jira stories, a BRD, documents or a Git change set and generate source-traced E2E test cases.
argument-hint: "[project and source files/Jira keys; optional run path]"
---

# Agentic QE run

Read `${CLAUDE_PLUGIN_ROOT}/references/source-policy.md`, `artifact-contract.md`, `workflow-guide.md`, and `skill-inventory.md` before acting. Treat `$ARGUMENTS` as the user's project/source selection, never as executable shell text. Use the host's Python 3.11+ launcher for deterministic state and gates: `python3` on macOS/Linux or `py -3` on Windows, passing the quoted path to `${CLAUDE_PLUGIN_ROOT}/scripts/qe.py`. For PDF intake install the optional dependency in `requirements.txt` into that Python environment if needed.

If the user supplies a run directory, call `status --run RUN` and resume at its next pending stage. Otherwise choose greenfield or brownfield from the user's request, initialize with `init --project NAME --mode MODE [--workflow FILE]`, and tell the user the run path. Never overwrite an older run.

Use the saved `workflow.json` to determine stage agent, prompt, MCP servers and intended tool actions. Check an agent is loaded before delegation; if a custom one is absent, pause and report it. Before and after each material decision or agent artifact edit, write a short `qe.py note` entry to the run's `memory.md`, naming stage, reason and result without source text or secrets. Make progress visible after every stage with `status --run RUN` and show the seven-stage table, completed percentage, blockers, and next action in Claude Code. The CLI also saves `dashboard.html`; do not open a window.

1. **Intake:** use read/search only with Atlassian MCP, save the issue JSON (`key`, `fields.summary`, `fields.description`, `fields.updated`) to a local file and run `jira-import --file FILE`, or use `jira-rest` / `ingest` for local data. Delegate story normalization to `story-intake-analyst`. Draft role/action/benefit stories with control IDs in `stories.json`. Show proposed stories and source references; obtain a real user's explicit approval before `approve --gate sources`, `approve --gate stories`, and `stage-complete --stage intake`. Do not infer approval from silence or a generated agent response.
2. **Scope:** greenfield completes directly. For brownfield ask for repo root and exact base/target or working-tree choice, run `scope`, have `change-scope-analyst` inspect only changed files and justified dependencies, show scope, obtain human scope approval, then complete.
3. **Requirements:** delegate to `requirements-analyst`; write functional and technical items with controls and evidence. Complete stage only with valid artifacts.
4. **Planning:** delegate to `e2e-test-planner`; cover all controls and both requirement classes where sourced. Complete stage.
5. **Design:** delegate to `e2e-scenario-designer`; write only E2E cases. Use optional Playwright observation only if the saved workflow enables it and the user approves the origin. Complete stage.
6. **Review:** delegate to the independent `confidence-reviewer`, which must inspect evidence itself and write `reviews.json`. Run `validate`; if any item is below 95 or stale, display the exact blockers. For a specific repair, obtain explicit human approval, run `approve-repair`, revise affected artifacts, re-review, and validate again. Complete stage only after the gate passes.
7. **Generation:** delegate to `test-artifact-generator`, run `generate`, inspect the report/matrix, and show the result. Obtain explicit human final approval, record `approve --gate final`, then `export --dest PATH` only if requested. Do not run target tests.

Never fabricate a positive review or claim 95 as model certainty. On interruption, keep saved artifacts and report next command. If a tool or evidence source fails, report the exact missing capability and continue independent stages only when their source gates are valid.
