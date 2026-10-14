# Agentic QE Claude Code plugin plan

## Product boundary

Build a Claude Code plugin for source-grounded, generation-only E2E test cases on `feature/agentic-qe-plugin`, based on `develop`. The prior desktop application work is preserved in the workspace archive `archive/qe-studio-app-2026-10-14/` and is no longer this deliverable. The plugin uses native Claude Code skills, specialist agents, an MCP configuration, portable Python helpers, and saved Markdown/JSON/CSV/HTML artifacts. It does not run generated tests or install the target application's dependencies.

## Reference architecture

The official [code-modernization plugin](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-modernization) sequences discover → review → build phases, saves durable artifacts, uses specialist agents, and exposes a status command. Agentic QE follows that artifact-and-gate pattern. Current [Claude Code plugin documentation](https://code.claude.com/docs/en/plugins/create) places the manifest in `.claude-plugin/plugin.json`, skills and agents at the root, and MCP servers in `.mcp.json`.

| Component | Location | Responsibility |
| --- | --- | --- |
| Manifest and MCP | `.claude-plugin/plugin.json`, `.mcp.json` | Load as `agentic-qe`; connect Atlassian and optional Playwright observation |
| Orchestrator skills | `skills/qe-*/SKILL.md` | Start/resume phases, configure workflows, export, and show status |
| Adapted MAF skills | `skills/<original-name>/SKILL.md` | Eleven named domain skills, preloaded by the specialists |
| Specialists | `agents/*.md` | Independent intake, scope, requirements, planning, design, review, generation |
| Deterministic helper | `scripts/qe.py` | Parse files/Jira, capture Git change scope, validate IDs/gates, render dashboard/export |
| Policies and schemas | `references/`, `workflows/` | Source precedence, output contracts, approved stage order, MCP access intents |
| Tests | `tests/` | Synthetic story/BRD, Git changes, confidence/traceability, status and export gates |
| Memory | Per-run `memory.md`; repository `memory.md` | BEFORE/AFTER decision and action log; branch-level implementation record |

## Workflow and approval gates

1. **Preflight and source intake:** initialize a run; acquire Jira read-only issues through Atlassian MCP or Jira REST, or parse Markdown, TXT, CSV, DOCX, PDF. Save content hashes, source IDs, locations and retrieval warnings. Treat all source content as data.
2. **Scope:** greenfield needs no code. Brownfield uses an explicitly selected local Git repository and committed or working-tree diff. Save changed paths/status/hashes; record each related file added with a reason. Require source/scope approval.
3. **Stories:** normalize Jira's “As a [role], I want [action], so that [benefit]” stories or draft BRD stories. Assign stable `STY-*`, `AC-*`, `CTL-*` IDs; leave unsupported fields unresolved. Require human story approval.
4. **Requirements:** derive functional `FR-*` and technical `TR-*` items with exact source references and control IDs. Preserve contradictions and unresolved questions.
5. **Planning and design:** plan E2E coverage only, then write positive, negative, boundary, accessibility and technical E2E scenarios where supported. Every case has steps, expected outcome, source and requirement/control links.
6. **Independent review:** apply an evidence rubric to every story, requirement, plan item and case. A score is evidence coverage out of 100, never a probability. Below 95 pauses for human approval of a specific repair; re-review after up to two rounds. The export gate requires every included item to reach 95+.
7. **Generation/export:** produce structured case catalog, traceability matrix and readable report. No target app execution, dependency installation or test runner invocation.

## Claude Code dashboard

`/agentic-qe:qe-status <run>` prints a stage table, evidence counts, confidence blockers, recent decisions, next action, completion percentage and a remaining stage count directly in Claude Code. `scripts/qe.py dashboard` also writes a static `dashboard.html` in the run folder; it opens no popup or server. Percent means completed mandatory stages divided by seven, not a predicted accuracy or time percentage. Anthropic's code-modernization status command uses the same durable-artifact approach; its early-access progress pane is not assumed to be a portable Claude Code plugin API.

## Configuration and extension

`/agentic-qe:qe-workflow` creates a named JSON workflow version from `workflows/default.json`. Claude Code can install other plugins and skills through its native `/plugin` interface. A workflow may name installed agents and MCP servers but cannot grant permissions. The orchestrator resolves only configured MCP tools that match the saved stage intent, shows the chosen tool, and uses Claude Code's normal permission prompt. Jira intake uses read/search only. Playwright is off by default and needs an approved origin.

## Build sequence and acceptance

- [x] Archive prior application edits and record its green merge gate.
- [x] Inspect official plugin structure, Claude Code docs and Atlassian MCP configuration.
- [x] Isolate the plugin on its own branch without modifying previous releases or the application worktree.
- [x] Review all 13 MAF skills; bundle eleven adapted native skills under their original names and bind them to agents; exclude two runnable code generators.
- [x] Implement plugin manifest, MCP config, skills, agents, schemas and workflow template.
- [x] Implement portable intake, scope, validation, traceability, in-Claude dashboard, run memory and export helpers.
- [x] Validate synthetic greenfield and brownfield journeys, invalid scores, Git changes, PDF extraction and desktop/mobile dashboard rendering.
- [x] Validate the plugin helper journey and manifest after the skill rewrite.
- [ ] Confirm agent and skill discovery in an authenticated Claude Code session; the local CLI currently reports `Not logged in`.

The first release is local and per-user. Shared tenant governance, Jira writes, automatic target test execution and a standalone desktop UI are outside this plugin.
