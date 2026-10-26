# Plan: Agentic QE design platform on MAF (2026-10-26)

Status: **approved by the owner on 2026-10-26** with decisions D1 remove, D2 web UI only, D3 rename to `agentic_qe`, D4 reviewer same as author, D5 removal list approved (section 8, extended by D2).

## 1. Goal and scope

Build the agentic-qe plugin's use case (github.com/sarveshtalele/agentic-qe, branch `agentic-qe-plugin`, v1.1.0) end to end on Microsoft Agent Framework (MAF) in this repository. The pipeline is the same for brownfield (a repository) and greenfield (BA notes, PRD or specs).

In scope: intake, discovery, user stories, requirements, test plan, test design, optional test data, traceability, offline report and sign-off.

Out of scope: test code generation, environment setup, execution and healing. The platform stops after design. Every output follows the plugin's document formats and has complete traceability.

```text
Brownfield: intake -> discovery -> requirements -> plan -> design -> [data] -> trace -> report -> sign-off
Greenfield: intake -> discovery -> stories -> requirements -> plan -> design -> [data] -> trace -> report -> sign-off
```

Each arrow is a human approval gate (MAF `request_info`). Each model stage has an independent confidence review that must reach 95 before its gate opens.

## 2. Sources checked

| Source | Version / state | How it was checked |
|---|---|---|
| Plugin | agentic-qe 1.1.0, commit 215f808 | Cloned and read all skills, agents, commands, scripts and both example runs |
| Harness | integ-v2 at f32c943 (develop 85a2bbf plus the verify-run merge and fix; not pushed) | Code read; gate re-run; E2E run on 2026-10-13 |
| MAF | pinned agent-framework-core 1.20.0, agent-framework-openai 1.14.3. Latest on PyPI: 1.23.0 and 1.17.0 | PyPI JSON; Microsoft Learn "Python 2026 significant changes" (updated 2026-10-15); structured-outputs page |
| Live run | requirement-analysis-skill at 3eae93a, model deepseek-v4-flash-global | `.runs/ras-e2e-2026-10-13` (evidence stays outside the repo) |

Context7 is not connected in this session. MAF facts come from Microsoft Learn and PyPI through Firecrawl.

## 3. Plugin to MAF mapping

| Plugin element | Plugin form | MAF form in this platform |
|---|---|---|
| `/qe-start` intake, Gate 0 | command + userConfig | `IntakeExecutor` (host), `request_info` gate, writes `00_intake_scope.md` and `00_intake_manifest.json` |
| `/qe-discover`, `analyzing-repositories`, `scan-repo.ts`, `repo-intelligence-analyst` | skill + script + haiku agent | `DiscoveryExecutor`: deterministic host scan (existing `repo/analyzer.py`, `stack.py`, `testability.py`). No model needed. Writes `01_discovery_architecture.json` and `01_discovery_summary.md` |
| `/qe-stories`, `authoring-user-stories`, `export-cases.ts` | skill + script | `StoriesExecutor`: `stories-author` agent with `response_format=StoriesDraft`. A host validator ports every `export-cases.ts` rule. Writes `01_user_stories.md`, `01_test_cases.csv`, `01_jira_import.csv` plus `.meta.json` sidecars |
| `/qe-requirements`, `extracting-requirements`, `requirements-analyst`, Gate 1 | skill + sonnet agent | `RequirementsExecutor`: `requirements-analyst` agent (structured output) plus host evidence anchoring (section 6.1). Writes `01_discovery_requirements.json` and `.md` |
| `/qe-plan`, `planning-tests`, `principal-test-architect`, Gate 2 | skill + opus agent | `PlanExecutor`: `test-architect` agent plus the `confidence-reviewer` agent (advisor). Writes `02_planning_test_strategy.md` and `02_planning_test_plan.json` |
| `/qe-design`, `designing-test-cases`, `test-design-architect`, Gate 3 | skill + sonnet agent | `DesignExecutor`: `test-designer` agent, one requirement batch per call, with host multiplicity and oracle validators and the reviewer. Writes `03_design_test_cases.json` and `.md` plus the 11-column CSV |
| `/qe-data`, `generating-test-data` | skill | Optional `DataExecutor`: model draft plus a host schema check. Writes `03_design_test_data_fixtures.json` |
| `/qe-trace`, `tracing-coverage`, `build-matrix.ts` | skill + script | `TraceExecutor`: deterministic host code (existing `qa/controls.py`, extended). Writes `07_traceability_matrix.json` and `.csv` |
| `/qe-dashboard`, `build-dashboard.ts` | skill + script | `ReportExecutor`: deterministic, one offline `REPORT.html` with no external scripts or fonts, plus `dashboard-metrics.json` |
| `/qe-sign` | command | `request_info` sign-off gate in the UI, named signer, writes `08_release_signoff.md`. The platform never signs for a person |
| `confidence-reviewer` agent | opus agent | `ReviewerAgent` built per stage with `response_format`. Its model is set separately from the author model (advisor pattern, section 6.4). Scores are host-computed and written to `confidence.jsonl` |
| Hooks `guard-write`, `guard-bash`, `ledger-hook`, `stamp-metadata` | Claude Code hooks | MAF function middleware (existing `hooks/maf_bridge.py`) plus a host `DocumentWriter`. The writer stamps `_meta` and appends a hash-chained `ledger.jsonl` entry for every document |
| `/qe-status`, `/qe-panel` | commands | Existing UI run page and progress API |
| `/qe-generate`, `/qe-execute`, `/qe-heal` | commands | Not built (out of scope) |

## 4. Validated errors and gaps

### 4.1 Plugin (each one checked in the clone)

| ID | Defect | Evidence |
|---|---|---|
| P1 | The priority scale contradicts itself. Stories, design and export use P1-P3. Planning, tracing, the architect agent and `generate-cases.ts` use P0-P2, so the "every P0/P1 maps" trace gate checks a priority that design never emits. | `planning-tests/SKILL.md:19`, `tracing-coverage/SKILL.md:19`, `designing-test-cases/scripts/generate-cases.ts:10`, `export-cases.ts:36` |
| P2 | Design cases have no structured oracle and no `level`. The oracle lives only as free text in `remarks`, so no tool can verify it. | Both example `03_design_test_cases.json` files have only the 11 column keys |
| P3 | The traceability matrix contains 6 cases that have no design spec (TC-PFM-029..034), yet it reports 100% coverage and 0 orphans. Cause: orphan detection only checks the requirement link, not the design link. | Checked: tests minus design = 029-034; matrix has 34 cases |
| P4 | `build-matrix.ts` counts a requirement as covered when any case links to it. The skill promises "at least 1 verified test case" and "line/branch coverage"; neither is computed. With zero requirements it reports 100%. | `build-matrix.ts:13-27`, `tracing-coverage/SKILL.md` |
| P5 | Level names disagree. Skills require Unit, Integration and E2E. The plan example uses `api`. The level guide merges Integration and API. | Example `02_planning_test_plan.json`, `LEVEL_GUIDELINES.md` |
| P6 | Brownfield requirements are not produced as a document. Neither example contains `01_discovery_requirements.json`; brownfield runs use `US-*` story IDs directly, so Gate 1 has no artifact. | Example folders |
| P7 | Requirement and oracle quotes are copied by the model. `validate-evidence.ts` exists, but no command or skill runs it. | `scripts/validate-evidence.ts`; `extracting-requirements` has no script |
| P8 | The plan JSON has no area IDs, risk scores or confidence fields, so the plan cannot be traced to cases or to its review. | Example plan: `story_id, level, framework, rationale` only |
| P9 | The multiplicity rule is stated three ways. The design skill says 2-3 Negative plus separate boundary cases. The template and export count boundary cases as Negative and require 2. | `designing-test-cases/SKILL.md`, `TEMPLATE.md`, `export-cases.ts:51-53` |
| P10 | The `derived` oracle ("derived from source code logic") can conflict with the rule "reject assertions that copy target implementation logic". There is no `characterization` kind, so current-but-unconfirmed behaviour is reported as fact. | `ORACLE_TYPES.md`, `test-design-architect.md` rule 4 |

### 4.2 Harness (checked in code and in the 2026-10-13 live run)

| ID | Defect | Evidence |
|---|---|---|
| H1 | **The live E2E run stopped at design.** All 12 cases were below the 95 gate, then two oracle retries failed and the run stopped. No design document was produced. | `.runs/ras-e2e-2026-10-13.log`: "12 of 12 cases are below the 95 confidence gate"; exit 2 |
| H2 | **Root cause of H1: quotes are copied by the model.** The model joined 4 source lines into one and changed `"` to `'`. The verbatim check then failed and `expected_derivable` scored 0, giving 75 points. Checked: `quote_in_text` returns False for that quote even after quote normalisation. | `confidence.jsonl` design record for `FormattingAnalyzer.analyze` |
| H3 | Verdict and points disagree. The reviewer said "approved" and "all dimensions score full marks", but the host gave 75. The gate shows a contradiction instead of a repair instruction. | Same record: `verdict: approved`, `points: 75` |
| H4 | 13 cases were capped at 60 by `numeric_expected_unverified`, which follows from H2. | `confidence.jsonl` |
| H5 | Case IDs are long hashes (`TC-POS-FORMATTINGANALYZER-...-4C3CD11D`), not the plugin's `TC-<APP>-NNN`. Priority is P0-P2 and the type taxonomy is positive/negative/boundary/validation/contract instead of Positive / Negative / Negative (Boundary) / Exception. | `models.py:347`, `TestDesignDraft` |
| H6 | There are no plugin documents: no 11-column table, no Jira CSV, no Given/When/Then stories, no `_meta` header, no `source_commit`, no CSV sidecar and no hash-chained ledger. | `qa/templates/`, `qa/step_reports.py` |
| H7 | About 10,000 lines of source and the matching tests serve generation and execution, which are now out of scope. They add risk and maintenance without serving the design goal. | Section 8 |
| H8 | `docs/` holds 70 tracked files, many of them dated reviews, audits, screenshots and an Azure proposal the owner removed from the product. | `git ls-files docs` |
| H9 | MAF is 3 minor versions behind (1.20.0, latest 1.23.0). Version 1.23 changes tool-approval sessions and middleware sequences, so the upgrade needs a check before the pins move. Pins are never relaxed; they move to exact new versions. | PyPI; Microsoft Learn changes guide |
| H10 | Integration branch `integ-v2` (verify-run merge, oracle-shape fix, seeded benchmark) is not pushed, and its gate re-run did not finish. | `git log`; STATUS.md |

## 5. Target architecture (MAF)

One MAF workflow per run: `WorkflowBuilder` with stable executor IDs, `FileCheckpointStorage`, and `request_info`/`@response_handler` for every gate. The state is JSON-only, so checkpoints hold no pickled custom types.

```text
Intake -> Discovery -> [Stories] -> Requirements -> Plan -> Design -> [Data] -> Trace -> Report -> SignOff
            host        agent+review   agent+review   agent+review  agent+review  agent    host     host     gate
```

Each agent stage runs this loop:

1. **Author agent** (fresh `AgentSession`, `response_format=<Pydantic model>`) drafts one batch.
2. **Host validators** run: schema, IDs, evidence anchoring, multiplicity and oracle shape. Every failure becomes one specific repair line, and the batch goes back to the author (bounded retries).
3. **Reviewer agent** (advisor, separate model, fresh session) scores against the rubric. The host computes the confidence from points and caps and appends it to the ledger.
4. If every item reaches 95 or more, the gate opens for the human. Below 95, the item goes back to the author with the reviewer's concrete fix; after the retry budget, it goes to the human with the reason.

Deterministic work (scan, IDs, quotes, trace, rendering) is host code and never a model call.

## 6. Accuracy design (the changes that fix H1-H4 and P2-P10)

### 6.1 Host-anchored evidence

The model never types a quote. It cites `{file, line_start, line_end}` and the host reads those lines from the clone and stores the exact text. The citation is rejected when the file is not tracked, the range is outside the file, or the range is more than 12 lines. This removes the H2 failure class.

The verbatim check stays only for documents the user supplied, and it gets one rule: a quote may be an ordered subsequence of whole lines.

### 6.2 Coherent confidence

- The reviewer returns points per rubric dimension plus the evidence for each. The host recomputes the verdict from the points: approved means full marks.
- When the reviewer's words and points disagree (H3), that is a reviewer defect. The host asks the reviewer once to restate; it never shows the contradiction as a gate failure.
- Rubrics come from the plugin: plan `controls_covered 40 / level_fit 30 / scenarios_derivable 30`; design `source_proven 30 / correct_target 25 / expected_derivable 25 / fixture_validity 20`.
- The 95 gate stays for plan and design.
- The ledger records every round: author model, reviewer model, prompt hashes, cited lines and caps.

### 6.3 Plugin-exact documents with complete traceability

- One ID scheme, assigned by the host only:
  - `US-<APP>-NNN`: story (greenfield)
  - `REQ-<APP>-NNN`: requirement
  - `AC-<APP>-NNN-n`: acceptance criterion
  - `PLN-<APP>-NN`: plan area
  - `TC-<APP>-NNN`: case
- Priority is **P1-P3 everywhere** (fixes P1).
- Type is Positive / Negative / Negative (Boundary) / Exception (fixes H5).
- Level is exactly Unit / Integration / E2E (fixes P5).
- Multiplicity rule, one definition (fixes P9): per requirement at least 1 Positive, 2 Negative (boundary counts) and 1 Exception. A requirement can be exempted only with a written reason that the reviewer accepts.
- The design case keeps the plugin's 11 columns and adds structured JSON fields: `level`, `requirement_id`, `ac_ids`, `plan_area`, `oracle {kind, source{file,line_start,line_end,text}, relation}`, `confidence`, `review_round` (fixes P2). Oracle kinds: specified, derived, metamorphic, golden, property, characterization. Characterization is never counted as verified (fixes P10).
- The trace matrix is two-way: requirement to AC to plan area to case, plus case to requirement. It reports:
  - orphans in both directions (cases without a requirement, and design-less items, fixing P3);
  - uncovered P1/P2 requirements;
  - coverage as "designed", never "tested" (fixes P4);
  - 0% when there are no requirements.
- `DocumentWriter` writes every document in `docs/qe/<app>/` with the plugin's `_meta` header: YAML front matter for Markdown, `_meta` for JSON, a comment block for HTML and a `.meta.json` sidecar for CSV. It also appends a SHA-256 chained ledger line. The writer refuses paths outside the run folder.

### 6.4 Advisor pattern

Building: Opus advises, plans, reviews and merges. Sonnet and Haiku implement in worktrees and consult the advisor at fixed checkpoints: after reading the brief, before the first edit, and before reporting done.

Product: each stage has an author model and an advisor (reviewer) model, configured in the existing `HARNESS_STAGE_MODELS`. Default: the current gateway model for both; the owner can set a stronger reviewer. The reviewer never sees the author's session (fresh agent and session, as the MAF guidance on independent runs says).

### 6.5 Batching for accuracy

Design runs one requirement (with its ACs) per call, with that requirement's cited source lines only, so the context stays small and the tool count per agent stays low (MAF guidance on tool selection). A failed batch is marked and the run continues; it does not abort the whole run.

## 7. Production project structure (target)

```text
src/agentic_qe/                  # renamed from maf_test_harness (D3)
  domain/        ids.py, schemas.py (Pydantic drafts and stored models), taxonomy.py (priority, type, level, oracle kinds)
  pipeline/      workflow.py (builder, checkpoints, gates), executors/{intake,discovery,stories,requirements,plan,design,data,trace,report,signoff}.py
  agents/        factory.py (fresh agent + session, response_format), personas/ (author and reviewer prompts, editable in UI), runtime.py
  validators/    evidence.py (host anchoring), stories.py (export-cases rules), design.py (multiplicity, oracle), plan.py, confidence.py
  documents/     writer.py (_meta, sidecars, ledger), render_md.py, render_csv.py (11 columns, Jira), render_html.py (offline report)
  traceability/  matrix.py, ledger.py (confidence.jsonl, ledger.jsonl hash chain)
  repo/          analyzer.py, stack.py, testability.py, git_transport.py, specs.py (greenfield inputs)
  skills/        registry.py, trust.py (lock and approval kept)
  hooks/         maf_bridge.py, engine.py (fail-closed middleware kept)
  ui/            api.py and services (loopback API, session cookie, CSP kept)
  cli.py, settings.py, paths.py, storage.py
skills/          stories-author, requirements-analyst, test-architect, test-designer, test-data, confidence-reviewer (SKILL.md + references/, hash-locked)
frontend/        Next.js 16 pages: intake, discovery, stories, requirements, plan, design, trace, report, sign-off, settings, MCP
tests/           unit/ (validators, ids, writer), pipeline/ (scripted model end to end), golden/ (plugin example parity), ui/ (Playwright)
docs/            README, USER_GUIDE, DEVELOPER_GUIDE, ARCHITECTURE (+ generated map), DOCUMENT_FORMATS, SECURITY, RELEASING, DESKTOP, MCP, SKILL_PLUGINS
```

## 8. Files to remove (needs your approval; nothing is deleted before that)

**Out-of-scope code** (generation, environment, execution, healing):
- `src/maf_test_harness/envs/` (all 14 files)
- `workflow_steps/{generation,test_review,execution,rerun}.py`
- `qa/{verification,coverage,triage,derivation,packaging}.py`
- `tools/{autofix,build_gate,mock_pitfalls,python_contracts,env_tools}.py`
- `errors/` (execution taxonomy)
- `agent/diagnosis.py`
- `repo/import_recipes.py`

**Out-of-scope skills:** `generic-test-generator`, `junit-generator`, `playwright-test-generator`, `test-reviewer`, `coverage-analyzer`.

**Out-of-scope benchmarks and scripts:** `benchmarks/generation/`, `benchmarks/seeded/`, `scripts/benchmark_generation.py`, `scripts/benchmark_seeded.py` (all of them need execution), plus the tests that cover the removed modules.

**Frontend:** the environment, execution, heal and generated-test views and their Playwright specs (exact list produced in wave 1 from imports).

**Docs (stale or dated):**
- `AZURE_DEPLOYMENT_PROPOSAL.md`, `Azure-deployment.md`, `azure-plan-desktop.md`
- `BACKEND_FIX_PLAN_2026-10-02.md`, `BACKEND_REVIEW_2026-10-01.md`
- `DEAD_CODE_REVIEW_2026-10-01.md`, `DEAD_CODE_REVIEW_2026-10-07.md`
- `GAP_REVIEW_2026-10-07.md`, `DETERMINISTIC_SKILL_REVIEW.md`, `RUN_FAILURE_AND_SKILL_EXECUTION_REVIEW.md`
- `WINDOWS_RUN_E9681C3_REVIEW.md`, `PETSTORE_STRICT_TEST_GENERATION.md`
- `MODULAR_WORKFLOW_IMPLEMENTATION.md`, `WORKFLOW_SERVICE_MIGRATION_PLAN.md`, `IMPLEMENTATION_PLAN.md`
- `GENERATION_BENCHMARK.md`, `ENVIRONMENTS.md`, `ERROR_PLAYBOOK.md`
- `CACHING.md`, `CATALOG.md`, `AUTOMATION.md`, `UI_VERIFICATION.md`, `NEXTJS_UI.md` (the last three merged into DEVELOPER_GUIDE)
- `docs/audit/` (25 files), `docs/ui/` (12 screenshots)
- `docs/plans/PLAN_2026-10-10_*`

The plans and reviews stay in git history.

**Root:** `AGENTS.md` merged into `CLAUDE.md`; `CONTRIBUTING.md` merged into DEVELOPER_GUIDE.

**Kept:** security model (session cookie, CSP, proxy, report tokens), skill lock and approval, MCP management, hooks, desktop shell, cross-OS launchers, `memory.md`, CI on 3 OSes.

## 9. Implementation waves (after approval)

Building roles: Opus is the advisor (plans, consults, reviews, merges); Sonnet and Haiku implement in `.wt/` worktrees. Every wave ends with the merge gate (`scripts/check_all.py`) on 3 OSes and an Opus review.

| Wave | Work | Model | Done when |
|---|---|---|---|
| 0 | Push `integ-v2` to develop with CI green (H10). Tag the last full-pipeline version `v1.0.8-full` as a fallback. | Opus | CI green on 3 OSes |
| 1 | Removal (section 8) and the new package layout (section 7), behaviour-preserving for the design stages. Regenerate the architecture map. | Sonnet (Haiku for the doc deletions) | Gate green; design-stage tests unchanged and passing |
| 2 | `domain/` IDs and taxonomy, and `validators/evidence.py` host anchoring (6.1). Schemas switch to line citations. | Sonnet | The H2 record from the live run passes; out-of-range, untracked and over-long citations are rejected |
| 3 | Confidence coherence (6.2), plugin rubrics, reviewer restate loop, ledger fields | Sonnet | The H3 record is resolved by a restate; tests for the full-marks verdict |
| 4 | Stories stage plus `validators/stories.py` (every `export-cases.ts` rule) and the design multiplicity and oracle validators (6.3) | Sonnet | Golden parity: plugin example stories and cases pass, and seeded rule breaks fail with the exact messages |
| 5 | `documents/` writer, `_meta`, sidecars, hash-chained ledger, MD/CSV/Jira/HTML renderers | Haiku (Sonnet review) | Byte-stable output for a fixed run; ledger tamper test fails `verify` |
| 6 | Two-way traceability matrix (fixes P3, P4) | Sonnet | The plugin brownfield example reproduces P3: 6 design-less cases are flagged |
| 7 | MAF workflow wiring with the new executors, advisor models per stage, per-requirement design batches, fail-soft batches | Sonnet, Opus review | Scripted-model end-to-end run, brownfield and greenfield, gates and resume after a checkpoint |
| 8 | UI pages for each document (template pages, CSV and HTML download, no raw Markdown), sign-off page | Sonnet | Playwright at 1366, 1024 and 390 px, light and dark |
| 9 | MAF upgrade to exact latest pins (1.23.0 / 1.17.0) after a changelog check (H9) | Sonnet | Gate green; approval-session tests pass |
| 10 | Docs: DEVELOPER_GUIDE, USER_GUIDE, DOCUMENT_FORMATS, CLAUDE.md files, memory.md | Haiku, Opus review | Every command and path in the docs exists |
| 11 | Live accuracy runs (section 10) and the release | Opus | Section 10 targets met |

## 10. Accuracy verification

Live runs with the real gateway, evidence stored outside the repo:
- **Brownfield:** requirement-analysis-skill (3eae93a), and personal-finance-mcp (the plugin's example target), compared against the plugin's recorded output.
- **Greenfield:** the plugin's web-calculator BA notes.

Targets, measured by host scripts:
- **Evidence:** 100% of cited lines resolve to tracked files. 0 model-typed quotes.
- **Multiplicity:** 100% of requirements meet 1P/2N/1E or have an accepted exemption.
- **Traceability:** 0 orphans in both directions; every P1 requirement has at least 1 designed case.
- **Confidence:** every approved item is at 95 or more, and the ledger has one record per round. No verdict and points contradiction reaches a gate.
- **Precision:** the owner labels 20 requirements and 40 cases per brownfield run as correct, partly correct or wrong. Target: 90% or more correct, and 0 cases that assert behaviour the source contradicts.
- **Completion:** the run reaches sign-off with no manual repair. Two runs on the same commit give the same IDs and structure.

## 11. Decisions for the owner

Decided on 2026-10-26:
- **D1 remove:** generation, execution and healing are removed completely. The last full-pipeline version is tagged `v1.0.8-full` first as a fallback.
- **D2 web UI only:** `desktop/`, `packaging/`, `src/.../desktop_backend.py`, `scripts/build_desktop.py`, `scripts/desktop_smoke.py`, their tests, `docs/DESKTOP.md` and the desktop installer workflows are removed. The launchers (`start-*.cmd/.command/.sh`) and `scripts/start_ui.py` stay.
- **D3 rename:** the package becomes `agentic_qe`, the CLI `agentic-qe` (the old `maf-harness` entry point is dropped), and the import root `src/agentic_qe/`.
- **D4 reviewer:** same model as the author by default; configurable per stage.
- **D5:** the section 8 removal list is approved, plus the D2 items.
