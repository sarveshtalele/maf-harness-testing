# Agentic QE for Claude Code

Source-grounded E2E test planning and case generation from Jira stories, BRDs and bounded Git changes. This plugin generates a case catalog, traceability matrix and report. It does not run the target application's tests.

The design follows the artifact and stage-gate pattern in Anthropic's [code-modernization plugin](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-modernization). This implementation uses current Claude Code [plugin skills, agents and MCP components](https://code.claude.com/docs/en/plugins/components). The previous desktop application edits are preserved separately in the workspace archive `archive/qe-studio-app-2026-10-14/`; this plugin lives on `feature/agentic-qe-plugin` and leaves earlier releases unchanged.

## Install and run locally

Prerequisites: Claude Code 2.1.x, Python 3.11+, Git for brownfield, and `pypdf` only for PDF input. The helper uses Python's standard library for Markdown, TXT, CSV, DOCX, Jira REST, validation, dashboard and export. On macOS/Linux use `python3`; on Windows use `py -3` in the commands below. Install the PDF extra in your chosen Python environment with `python3 -m pip install -r requirements.txt` (Windows: `py -3 -m pip install -r requirements.txt`) if needed.

```sh
claude plugin validate --strict /absolute/path/to/Agentic-qe-plugin
claude --plugin-dir /absolute/path/to/Agentic-qe-plugin
```

In Claude Code, start with `/agentic-qe:qe-run <project and source selection>`. Use `/agentic-qe:qe-status <run-directory>` to see the in-Claude dashboard: seven stage rows, evidence counts, 95-point review blockers, recent memory and the next action. `/agentic-qe:qe-workflow` configures a named workflow using installed agents and MCP tools; `/agentic-qe:qe-export` validates and exports an approved run. Eleven adapted MAF skills retain their original names in [`skills/`](skills/) and are preloaded by the seven agents; see the [skill inventory](references/skill-inventory.md). The run's `dashboard.html` is a static companion view. Nothing opens a popup or starts a web server.

Atlassian MCP is configured in `.mcp.json` at the official `https://mcp.atlassian.com/v2/mcp` endpoint. Authenticate it in Claude Code with `/mcp`. The bundled Playwright MCP entry is available for optional, read-only observation and is **disabled in the default workflow**. `npx` and network access are needed only if you enable it. On a machine where the user's Claude Code version does not recognize an MCP entry, update Claude Code; the local file and Jira REST paths remain available. Other MCPs and plugins are installed through Claude Code and selected by workflow name; a workflow cannot install a server or grant tool permission.

## CLI journey

The skills invoke this helper, but the commands are also useful for local verification:

```sh
python3 scripts/qe.py init --project "Checkout" --mode greenfield
python3 scripts/qe.py ingest --run RUN requirements.md
python3 scripts/qe.py jira-import --run RUN --file jira-issues.json
python3 scripts/qe.py scope --run RUN --repo /path/to/repo --mode committed --base main --target HEAD
python3 scripts/qe.py status --run RUN
python3 scripts/qe.py validate --run RUN
python3 scripts/qe.py generate --run RUN
python3 scripts/qe.py export --run RUN --dest /path/to/output
```

For Jira REST fallback, set `JIRA_EMAIL` and `JIRA_API_TOKEN` in your environment and call `jira-rest --run RUN --site https://YOUR-SITE.atlassian.net --key PROJ-123` or `--jql 'project = PROJ'`. Credentials are never written into run artifacts. For brownfield working tree scope use `--mode working-tree` and optionally `--include-untracked`. The chosen scope records immutable commits or staged/unstaged file hashes; recapture if the working tree changes.

Run data defaults to `.agentic-qe/runs/<project>-<id>` in the **current working directory**. Keep that directory private when it contains Jira or BRD text. Each run has `memory.md` with BEFORE/AFTER stage, approval and export actions; agents add decision notes before and after artifact edits. See [`PLAN.md`](PLAN.md), [`references/skill-inventory.md`](references/skill-inventory.md), [`references/artifact-contract.md`](references/artifact-contract.md), [`references/source-policy.md`](references/source-policy.md) and [`references/workflow-guide.md`](references/workflow-guide.md) for the stage contract, skill mapping and policy.

## Gates and limitations

Seven stages: intake, scope, requirements, planning, design, independent review, generation. Human approval is required for source/story intake, brownfield scope, any below-95 repair, and final export. Review scores represent a four-part evidence rubric, not a calibrated accuracy probability. Items below 95 cannot be exported until a human approves a repair and the item is independently reviewed again; there are at most two repair rounds. The CLI records approver names and hashes but cannot authenticate the person typing a name, so enterprise identity enforcement requires the host's access controls and review process.

PDF extraction covers text PDFs. Scanned PDFs need OCR before intake. Jira MCP tool results must be normalized to issue JSON with `key` and `fields`; tool shapes can vary. Jira REST fallback supports Atlassian Cloud sites. This release is a local Claude Code plugin and does not provide shared tenant governance or a desktop application.
