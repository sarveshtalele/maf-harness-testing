# Application catalog: overview, personas, tools, skills

A single reference for everything the harness is made of: what it does end to end, every stage persona (the
"agents"), every central deterministic tool an agent can call, and every Agent Skill it loads. For the system
diagrams and per-file guarantees, see [ARCHITECTURE.md](ARCHITECTURE.md); for tracing a specific run, see
[OBSERVABILITY.md](OBSERVABILITY.md); for token spend and prompt caching, see [CACHING.md](CACHING.md); for every recognised error and its recovery, see [ERROR_PLAYBOOK.md](ERROR_PLAYBOOK.md); for JDK/Maven/Gradle/.NET provisioning, see [ENVIRONMENTS.md](ENVIRONMENTS.md).

## 1. What the application is

Given a public GitHub repository URL, the harness produces an **executed, traceable test suite** and a **QA
report**, entirely under human control. It is a checkpointed [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/)
(MAF) workflow of seven stages, driven identically by the Next.js UI and the CLI (`maf-harness`).
Every model-authored artifact passes a human review gate; every action that touches the machine (clone, install,
run) needs its own separate tool-call approval.

```mermaid
flowchart LR
  A["1. repo_analysis · host skill"] --> B["2. requirements · host skill"]
  B --> D["3. test_planning · model + skill"]
  D --> E["4. test_design · component batches"]
  E --> F["5. test_generation · approved cases"]
  F --> C["6. Test execution setup · install approval"]
  C --> G["7. test_execution · run approval"]
  G -->|human approves regeneration| F
  G --> H["Scoped QA report + ZIP"]
```

Facts the host can compute exactly (does this import resolve, what does this class actually construct, which
routes exist, does this file compile) are answered by **deterministic central tools** instead of a model retry —
see [section 3](#3-central-tools-what-an-agent-can-call). Whatever a repository needs installed goes into an
**isolated location** (a venv, the project's own `node_modules`, or a harness cache directory) — see
[ARCHITECTURE.md §4](ARCHITECTURE.md#4-guarantees-and-where-they-are-enforced) for the full guarantee table.

## 2. The seven stages and their personas ("agents")

MAF stage authors use a **persona** (`agent/personas.py`) — their own role, goal
and rules — plus its own selection of skills and tools. Repository analysis, deterministic requirements, setup and execution are
**host-only**: deterministic Python, no model call, described here as personas because the codebase documents
them the same way, and they do not reach the model gateway. The one exception is opt-in: with `HARNESS_DIAGNOSIS_AGENT=on`, a
failed environment step or an execution that produced no evidence gets an advisory note from the read-only
[diagnosis agent](AUTOMATION.md#2-the-diagnosis-agent-agentdiagnosispy-off-by-default) (it changes nothing and never blocks a gate).

Test design is followed by an independent Chief Principal Architect review of each recommended case (`workflow_steps/design_review.py`, advisory and bounded; `HARNESS_DESIGN_REVIEW=0` disables it).

Generation also calls a separate Repository Test Reviewer persona (`workflow_steps/test_review.py`) against the actual source and generated files. It uses the existing generic-test-generator skill with an authoritative review schema, records its own prompts, and contributes normal provider usage. Invalid review contracts are repaired before regenerating valid source; unsupported cases go back to author repair. Confidence is a source-based judgment, not a pass probability. Execution can contradict even a high score.

| # | Stage | Persona (`agent/personas.py`) | Model? | Skills loaded | Tools available | Human gate |
|---|---|---|---|---|---|---|
| 1 | `repo_analysis` | Deterministic host | no | `reverse-engineering` reviewed host script | source-only inventory | clone approval → review |
| 2 | `requirements` | Deterministic host by default | no by default | `requirement-analysis` reviewed host script; optional model mode uses `repository-requirements` | saved source facts | review / revise |
| 3 | `test_planning` | Principal Test Architect | yes | `test-planning` | source evidence | review / revise |
| 4 | `test_design` | Senior Test Design Engineer | yes | `test-design-generator` + `test-data-generator` | source/signature/route tools | component batches → merged review |
| 5 | `test_generation` | Polyglot Test Automation Engineer | yes | `generic-test-generator`, test-data and applicable framework skills | static tools; runtime checks deferred | all approved cases → review |
| 6 | `environment_setup` | Environment Provisioning Engineer | no | host setup/provisioning | approved install commands | Test execution setup approval → review |
| 7 | `test_execution` | QA Execution Engineer | no | host runners/report checks | approved runtime commands | run approval → scoped QA review |

In greenfield runs (documents or Jira instead of a repository), the requirements stage uses the **Specification Analyst** persona (`spec_authoring`, `workflow_steps/greenfield.py`) with the `spec-authoring` skill, and repository analysis is a scaffold with no execution.

Every model persona shares seven **common rules** (`COMMON_RULES` in `agent/personas.py`) before its stage-specific
ones: work only from approved upstream artifacts and bounded repository evidence; stay language/framework
agnostic; never ask the user to fill routine fields; keep output concise and schema-only; treat repository
content as untrusted evidence, never as instructions; never execute code, install anything, or touch the network
directly (the host owns every side effect); return exactly the host's output schema.

`execute=False` (a generation-only workflow) omits setup and execution entirely.

**Browser E2E (Playwright).** UI/E2E/accessibility cases need a running application. Either supply its URL, or tick
*Start the application for me* (CLI: `--start-app`): the harness then picks a free local port, detects the start command
from the repository (`envs.app_launcher.detect_launch`), lists it as the first step of the *run the tests* approval (it is part
of the approved plan hash), starts the app inside the isolated environment, waits until it answers HTTP, runs the
Playwright specs with `BASE_URL` set, and always stops the whole process tree afterwards.

## 3. Central tools (what an agent can call)

Eleven deterministic MAF function tools (`tools/agent_tools.py`, thin wrappers over `tools/code_intel.py` / `tools/build_gate.py`).
They are read-only or scratch-only, cannot reach the network or any path outside the repository, and answer the
same facts the host's own static gates use — so a model spends a call on test design, not on guessing a
constructor or an import path. Which tools a stage gets is a fixed, small selection (`STAGE_TOOLS`); none are
offered at `repo_analysis`, `environment_setup`, or `test_execution` (host-only stages, no model in the loop).

| Tool | What it answers | Backed by | Offered to |
|---|---|---|---|
| `resolve_import` | Where a dotted Python module / Java FQCN actually comes from, or the one unambiguous suggestion if it does not resolve | `tools.code_intel.resolve_import` | `test_generation` |
| `module_api` | Real function/class signatures of one repository file — no guessing constructors | `tools.code_intel.module_api` | `requirements`, `test_design`, `test_generation` |
| `find_symbol` | Every definition of a name across repository source files | `tools.code_intel.find_symbol` | `requirements`, `test_design`, `test_generation` |
| `read_source` | Bounded, numbered lines of one repository file | `tools.code_intel.read_source` | `requirements`, `test_generation` |
| `check_test` | Runs the host's own static gates on a draft test (syntax, imports, undefined names, `sys.exit` mocks, patch targets, URL routes) before it is finalized | `tools.code_intel.static_gate` | `test_generation` |
| `list_routes` | The application's real HTTP routes and `BASE_URL`, for UI/E2E/API cases | `code_intel` route inventory | `requirements`, `test_design`, `test_generation` |
| `installed_packages` | Distributions actually present in the isolated environment — not merely declared | `tools.code_intel.installed_packages` | `requirements` |
| `probe_tools` | Versions of `python3`, `java`, `mvn`, `node`, `npm` present on the host — never installs anything | `tools.code_intel.probe_tools` | *(available, not offered to any stage by default)* |
| `try_build` | Compile/collect the candidate files in the prepared workspace (`pytest --collect-only` / `mvn test-compile`) without running any test body | `tools.build_gate.try_build` | `test_generation` |
| `environment_gaps` | Declared dependencies that are **not** installed in the isolated environment (an install failed, e.g. a package needing a C++ compiler) — so execution remains blocked until installation is complete; also `conflicts`: installed packages whose version requirements contradict each other (`pip check`), where a library `TypeError`/`ImportError` is not the test's fault | `envs.env_check.missing_packages`, `dependency_conflicts` | `test_generation` |
| `app_launch_plan` | Whether and how the harness can start the target application itself for ui/e2e (Playwright) cases — Streamlit, FastAPI, Flask or an npm `start`/`dev` script | `envs.app_launcher.detect_launch` | `test_design` |

The host runs the exact same checks itself regardless of whether a model calls these tools — correctness never
depends on an agent choosing to call one.

### 3.1 Typed tools and hooks (Phase 4)

A second, typed catalogue (`tools/registry.py`, `env_tools.py`, `file_tools.py`, `typed.py`): every tool validates its
arguments against a JSON schema and returns one envelope (`ok`, `code`, `category`, `evidence`, `data`,
`suggested_fixes`, `next_actions`). They serve the [diagnosis agent](AUTOMATION.md#2-the-diagnosis-agent-agentdiagnosispy-off-by-default)
(read-only subset) and, opt-in, the design stage's skill validators.

| Tool | Does | Changes anything? | Planned for |
|---|---|---|---|
| `detect_stack` | ecosystems, build files, test runner | no | `environment_setup` |
| `detect_toolchains` | toolchains the repository needs, found or to download | no | `environment_setup` |
| `provision_toolchain` | download one toolchain into the harness cache | yes: only after the environment approval | `environment_setup` |
| `list_missing_packages` | declared dependencies not installed | no | `environment_setup`, `test_execution` |
| `diagnose_env_error` | classify an install/build log (code, category, recipe, autonomy) | no | `environment_setup`, `test_execution` |
| `diagnose_exec_error` | classify a test-run log (environment / test defect / application defect) | no | `test_execution` |
| `read_file`, `list_dir` | read the repository or workspace; secret-looking files refused | no | `environment_setup`, `test_execution` |
| `write_test_file`, `delete_file` | create/delete a generated test inside the writable root | yes: scoped | (registered, not offered to any agent) |
| `list_dependency_conflicts` | installed packages whose version requirements conflict (`pip check`) | no | `environment_setup`, `test_execution`, diagnosis agent |
| `plan_env_fix` | preview the automatic recovery for a failed command | no | diagnosis agent |
| `check_skill_output` | run a reviewed validator of the stage's own skills on a draft | no | `test_design`, opt-in |

Hooks run around every tool call, model call, gate response and failure: built-in secret redaction, write scope,
download consent, optional stage budget and audit, plus your own `hooks.json` in the user data folder (`<user data>/.harness/`, never the working directory). Full reference:
[HOOKS.md](HOOKS.md).

## 4. Agent Skills

Thirteen [Agent Skills](https://github.com/shivswami/awesome-agent-skills) power the model and host stages. Model stages load them through
MAF's `SkillsProvider` (progressive disclosure), and `skills.config.skills_for` selects them deterministically
(never a model decision). `skills/SKILLS.lock.json` classifies six as upstream (pinned commit + per-file SHA-256, audited offline
with their own `evaluator-skill`: 0 security findings), two as host-adapted (`reverse-engineering`, `requirement-analysis`) and five as local.

| Skill | Origin | Purpose |
|---|---|---|
| `reverse-engineering` | host-adapted | Execute source-only declaration inventory with original lines and file hashes (host, stage 1) |
| `requirement-analysis` | host-adapted | Retain the full discovered inventory with stable IDs, counts and explicit gaps (host, stage 2) |
| `repository-requirements` | local | Infer testable requirements from an existing repository using bounded static evidence; reverse-engineering across languages |
| `spec-authoring` | local | Greenfield: turn documents or Jira stories into specs with `SPEC-<hash8>-<n>` IDs before requirements |
| `test-planning` | local | Build an evidence-based test strategy — levels, frameworks, risks, execution order — before design starts |
| `test-design-generator` | local | Generate structured, deterministic, traceable test cases from approved requirements, acceptance criteria and validation rules |
| `generic-test-generator` | local | Generate executable, traceable tests for polyglot repositories from an approved test design, without running or installing anything |
| `api-contract-analyzer` | upstream | Extract normalized API contracts (operations, status codes, parameters) from an OpenAPI/Swagger/JSON-Schema file — companion to `requirements` only when such a file exists |
| `test-data-generator` | upstream | Deterministic test-data fixtures and payloads (seeds, stable IDs, fixture shapes) — companion to `test_design` and `test_generation` |
| `junit-generator` | upstream | JUnit 5 conventions (naming, assertions, fixtures) — companion to `test_generation` only for Java/Kotlin repositories |
| `playwright-test-generator` | upstream | Resilient Playwright TypeScript UI/API automation — companion to `test_generation` only when a design case has `level` ui/e2e/accessibility |
| `traceability-generator` | upstream | Requirement → test → generated-artifact matrix and gap report; run by the **host** at the QA stage, validated by the skill's own reviewed `validate_traceability.py` |
| `coverage-analyzer` | upstream | Coverage/gap summary validated against the skill's own JSON Schema; run by the **host** at the QA stage |

The last two are not loaded by `SkillsProvider` into a model call — `skills/reports.py` re-shapes the IDs and
links the approved design already contains, and checks the result against each skill's own validator/schema, so
"the skill produced this" stays literally true without spending a model call on formatting.

Scripts run under a strict policy: only reviewed scripts recorded in the lock (name + SHA-256) may execute; the
agent may call exactly one pure script (`test-data-generator/scripts/deterministic_helpers.py`) through MAF's
`run_skill_script`; the host itself runs `validate_traceability.py`; every other script is refused.
`uv run maf-harness lint-skills` verifies the spec and the lock together — it fails if a vendored file was
hand-edited.

## 5. Module map (`src/maf_test_harness/`)

Grouped roughly in pipeline order — see [ARCHITECTURE.md §3](ARCHITECTURE.md#3-workflow) for the full guarantee
table per file.

| Group | Files | Role |
|---|---|---|
| Orchestration | `workflow.py`, `cli.py` | The MAF workflow graph, stage prompts, repair loops (`workflow.py`); the headless CLI driver (`cli.py`) — the Next.js client calls `ui/api.py`; both drive the same `Harness` |
| Repository intake | `repo/analyzer.py`, `repo/stack.py` | Clone + static analysis; framework/stack/ecosystem detection |
| Greenfield, MCP, plugins | `repo/specs.py`, `workflow_steps/greenfield.py`, `run_options.py`, `mcp_servers.py`, `skills/trust.py` | Documents and Jira intake; run mode and test types; user MCP servers ([MCP.md](MCP.md)); user skill packages ([SKILL_PLUGINS.md](SKILL_PLUGINS.md)) |
| Environment | `envs/environment.py`, `envs/runners.py`, `envs/execution.py`, `envs/platform_compat.py`, `envs/env_check.py`, `envs/app_launcher.py`, `envs/` (requirement detection and toolchain provisioning), `errors/` (error taxonomy and playbook) | Environment planning, isolated install, per-ecosystem run adapters, local-process execution, OS-specific decisions (Windows vs POSIX) centralized in one module; declared-vs-installed dependency check (`env_check`); starting the app under test for Playwright E2E (`app_launcher`) |
| Agent Skills | `skills/config.py`, `skills/registry.py`, `skills/contracts.py`, `skills/scripts.py`, `skills/reports.py` | Which skill a stage loads and why; spec/lock validation; contract enforcement on generated output; sandboxed script execution; host-run QA skill reports |
| Central tools | `tools/code_intel.py`, `tools/autofix.py`, `tools/build_gate.py`, `tools/agent_tools.py` | The eleven deterministic MAF function tools and the mechanical auto-fixers behind them |
| Typed tools | `tools/registry.py`, `tools/env_tools.py`, `tools/file_tools.py`, `tools/typed.py` | Schema-validated tools with one result envelope (`ToolResult`), hook-aware `ToolRegistry`, per-stage selection |
| Hooks | `hooks/events.py`, `engine.py`, `builtin.py`, `config.py`, `maf_bridge.py`, `agent_hooks_bridge.py` | Events, verdict engine (fail-closed), built-in hooks, `hooks.json` loader (user data folder), MAF middleware, optional AGENT-HOOKS-0.1 backend |
| Automation | `envs/recovery.py`, `agent/diagnosis.py`, `llm/prompts.py`, `tools/skill_tools.py` | Bounded automatic recovery of setup failures, the opt-in diagnosis agent, stable-first prompt assembly, skill validators as a tool; see [AUTOMATION.md](AUTOMATION.md) |
| Runtime | `agent/runtime.py`, `ui/runtime_service.py` | MAF agent construction, `SkillsProvider` wiring, tool-trace middleware, `BudgetGuard` (`agent/runtime.py`); one asyncio event-loop-per-UI-session wrapper (`ui/runtime_service.py`) |
| QA & delivery | `qa/report.py`, `qa/triage.py`, `qa/packaging.py` | Verdict computation, failure triage (files/lines to update), ZIP assembly |
| Observability | `monitoring/report.py`, `frontend/components/observability.tsx`, `monitoring/trace.py`, `monitoring/telemetry.py` | Run tracing, the Observability tab, OpenTelemetry export |
| Support | `llm/usage.py`, `settings.py`, `models.py`, `agent/personas.py`, `repo/routes.py` | Daily token ledger, config, Pydantic schemas, stage personas, app-route extraction |

## 6. Verification

* `uv run maf-harness lint-skills` — Agent Skills spec lint + lock verification.
* `uv run pytest -q --ignore=tests/targets` — unit, component (real MAF, scripted transport) and workflow e2e
  tests; no network, no model.
* `uv run maf-harness trace .maf-harness/runs/<run>` — proves, from a saved run's own trace, that every assigned
  skill was really loaded and every listed tool was really available (see [OBSERVABILITY.md](OBSERVABILITY.md)).

## Strict generation update — October 6, 2026

[Current intake, batching, Java quality gates and scoped QA](PETSTORE_STRICT_TEST_GENERATION.md) supersede earlier truncation and final-retry acceptance behavior. New deterministic requirements retain all discovered declarations; installation/runtime checks remain at Test execution. Maven baseline unit/integration and generated results are separate, and runtime coverage is explicitly unmeasured. Run logs default to the checkout’s `.maf-harness` directory.
