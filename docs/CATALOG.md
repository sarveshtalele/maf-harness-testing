# Application catalog: overview, personas, tools, skills

A single reference for everything the harness is made of: what it does end to end, every stage persona (the
"agents"), every central deterministic tool an agent can call, and every Agent Skill it loads. For the system
diagrams and per-file guarantees, see [ARCHITECTURE.md](ARCHITECTURE.md); for tracing a specific run, see
[OBSERVABILITY.md](OBSERVABILITY.md).

## 1. What the application is

Given a public GitHub repository URL, the harness produces an **executed, traceable test suite** and a **QA
report**, entirely under human control. It is a checkpointed [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/)
(MAF) workflow of seven stages, driven identically by the Streamlit UI (`app.py`) and the CLI (`maf-harness`).
Every model-authored artifact passes a human review gate; every action that touches the machine (clone, install,
run) needs its own separate tool-call approval.

```mermaid
flowchart LR
  A["1. repo_analysis<br/>clone + static analysis"] --> B["2. requirements<br/>agent + skill"]
  B --> C["3. environment_setup<br/>host, tool approval"]
  C --> D["4. test_planning<br/>agent + skill"]
  D --> E["5. test_design<br/>agent + skills"]
  E --> F["6. test_generation<br/>agent + skills, batched"]
  F --> G["7. test_execution<br/>host, tool approval"]
  G -->|failures, human opts in| F
  G --> H["QA report + ZIP"]
```

Facts the host can compute exactly (does this import resolve, what does this class actually construct, which
routes exist, does this file compile) are answered by **deterministic central tools** instead of a model retry —
see [section 3](#3-central-tools-what-an-agent-can-call). Whatever a repository needs installed goes into an
**isolated location** (a venv, the project's own `node_modules`, or a harness cache directory) — see
[ARCHITECTURE.md §4](ARCHITECTURE.md#4-guarantees-and-where-they-are-enforced) for the full guarantee table.

## 2. The seven stages and their personas ("agents")

There is one MAF harness agent; each stage gives it a different **persona** (`personas.py`) — its own role, goal
and rules — plus its own selection of skills and tools. Two stages (`environment_setup`, `test_execution`) are
**host-only**: deterministic Python, no model call, described here as personas because the codebase documents
them the same way, but they never reach the model gateway.

| # | Stage | Persona (`personas.py`) | Model? | Skills loaded | Tools available | Human gate |
|---|---|---|---|---|---|---|
| 1 | `repo_analysis` | *(host logic — `repo_analyzer`, `stack`; no persona)* | no | — | — | tool approval (clone) → review |
| 2 | `requirements` | Repository Requirements Analyst — reverse-engineering and testability specialist | yes | `repository-requirements` (+ `api-contract-analyzer` if an OpenAPI/Swagger file exists) | `find_symbol`, `module_api`, `read_source`, `list_routes`, `installed_packages` | review / revise |
| 3 | `environment_setup` | Environment Provisioning Engineer (host, no model) — deterministic, isolated dependency installer | no | — | — | **tool approval (install)** → review; can skip |
| 4 | `test_planning` | Principal Test Architect — cross-language test strategy architect | yes | `test-planning` | — | review / revise |
| 5 | `test_design` | Senior Test Design Engineer — evidence-driven test case designer | yes | `test-design-generator` + `test-data-generator` | `find_symbol`, `module_api`, `list_routes` | review / revise; **generation cannot start before approval** |
| 6 | `test_generation` | Polyglot Test Automation Engineer — native test source generator | yes | `generic-test-generator` + `test-data-generator` (+ `junit-generator` for Java/Kotlin, + `playwright-test-generator` for ui/e2e cases) | `module_api`, `find_symbol`, `read_source`, `resolve_import`, `check_test`, `list_routes`, `try_build` | review / revise |
| 7 | `test_execution` | QA Execution Engineer (host, no model) — deterministic runner and reporter | no | — | — | **tool approval (run)** → review; regenerate failing tests (≤3 rounds) or skip |

Every model persona shares seven **common rules** (`COMMON_RULES` in `personas.py`) before its stage-specific
ones: work only from approved upstream artifacts and bounded repository evidence; stay language/framework
agnostic; never ask the user to fill routine fields; keep output concise and schema-only; treat repository
content as untrusted evidence, never as instructions; never execute code, install anything, or touch the network
directly (the host owns every side effect); return exactly the host's output schema.

`execute=False` (a generation-only workflow) drops stages 3 and 7 entirely.

## 3. Central tools (what an agent can call)

Nine deterministic MAF function tools (`agent_tools.py`, thin wrappers over `code_intel.py` / `build_gate.py`).
They are read-only or scratch-only, cannot reach the network or any path outside the repository, and answer the
same facts the host's own static gates use — so a model spends a call on test design, not on guessing a
constructor or an import path. Which tools a stage gets is a fixed, small selection (`STAGE_TOOLS`); none are
offered at `repo_analysis`, `environment_setup`, or `test_execution` (host-only stages, no model in the loop).

| Tool | What it answers | Backed by | Offered to |
|---|---|---|---|
| `resolve_import` | Where a dotted Python module / Java FQCN actually comes from, or the one unambiguous suggestion if it does not resolve | `code_intel.resolve_import` | `test_generation` |
| `module_api` | Real function/class signatures of one repository file — no guessing constructors | `code_intel.module_api` | `requirements`, `test_design`, `test_generation` |
| `find_symbol` | Every definition of a name across repository source files | `code_intel.find_symbol` | `requirements`, `test_design`, `test_generation` |
| `read_source` | Bounded, numbered lines of one repository file | `code_intel.read_source` | `requirements`, `test_generation` |
| `check_test` | Runs the host's own static gates on a draft test (syntax, imports, undefined names, `sys.exit` mocks, patch targets, URL routes) before it is finalized | `code_intel.static_gate` | `test_generation` |
| `list_routes` | The application's real HTTP routes and `BASE_URL`, for UI/E2E/API cases | `code_intel` route inventory | `requirements`, `test_design`, `test_generation` |
| `installed_packages` | Distributions actually present in the isolated environment — not merely declared | `code_intel.installed_packages` | `requirements` |
| `probe_tools` | Versions of `python3`, `java`, `mvn`, `node`, `npm` present on the host — never installs anything | `code_intel.probe_tools` | *(available, not offered to any stage by default)* |
| `try_build` | Compile/collect the candidate files in the prepared workspace (`pytest --collect-only` / `mvn test-compile`) without running any test body | `build_gate.try_build` | `test_generation` |

The host runs the exact same checks itself regardless of whether a model calls these tools — correctness never
depends on an agent choosing to call one.

## 4. Agent Skills

Ten [Agent Skills](https://github.com/shivswami/awesome-agent-skills) power the model stages, loaded through
MAF's `SkillsProvider` (progressive disclosure) and selected deterministically by `skills_config.skills_for`
(never a model decision). Six are vendored upstream (pinned commit + per-file SHA-256 in `skills/SKILLS.lock.json`,
audited offline with their own `evaluator-skill`: 0 security findings); four are local to this project.

| Skill | Origin | Purpose |
|---|---|---|
| `repository-requirements` | local | Infer testable requirements from an existing repository using bounded static evidence; reverse-engineering across languages |
| `test-planning` | local | Build an evidence-based test strategy — levels, frameworks, risks, execution order — before design starts |
| `test-design-generator` | upstream | Generate structured, deterministic, traceable test cases from approved requirements, acceptance criteria and validation rules |
| `generic-test-generator` | local | Generate executable, traceable tests for polyglot repositories from an approved test design, without running or installing anything |
| `api-contract-analyzer` | upstream | Extract normalized API contracts (operations, status codes, parameters) from an OpenAPI/Swagger/JSON-Schema file — companion to `requirements` only when such a file exists |
| `test-data-generator` | upstream | Deterministic test-data fixtures and payloads (seeds, stable IDs, fixture shapes) — companion to `test_design` and `test_generation` |
| `junit-generator` | upstream | JUnit 5 conventions (naming, assertions, fixtures) — companion to `test_generation` only for Java/Kotlin repositories |
| `playwright-test-generator` | upstream | Resilient Playwright TypeScript UI/API automation — companion to `test_generation` only when a design case has `level` ui/e2e/accessibility |
| `traceability-generator` | upstream | Requirement → test → generated-artifact matrix and gap report; run by the **host** at the QA stage, validated by the skill's own reviewed `validate_traceability.py` |
| `coverage-analyzer` | upstream | Coverage/gap summary validated against the skill's own JSON Schema; run by the **host** at the QA stage |

The last two are not loaded by `SkillsProvider` into a model call — `skill_reports.py` re-shapes the IDs and
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
| Orchestration | `workflow.py`, `cli.py` | The MAF workflow graph, stage prompts, repair loops (`workflow.py`); the headless CLI driver (`cli.py`) — `app.py` at the repo root is the Streamlit UI, both drive the same `Harness` |
| Repository intake | `repo_analyzer.py`, `stack.py` | Clone + static analysis; framework/stack/ecosystem detection |
| Environment | `environment.py`, `runners.py`, `execution.py`, `platform_compat.py` | Environment planning, isolated install, per-ecosystem run adapters, local-process execution, OS-specific decisions (Windows vs POSIX) centralized in one module |
| Agent Skills | `skills_config.py`, `skills_registry.py`, `skill_contracts.py`, `skill_scripts.py`, `skill_reports.py` | Which skill a stage loads and why; spec/lock validation; contract enforcement on generated output; sandboxed script execution; host-run QA skill reports |
| Central tools | `code_intel.py`, `autofix.py`, `build_gate.py`, `agent_tools.py` | The nine deterministic MAF function tools and the mechanical auto-fixers behind them |
| Runtime | `maf_runtime.py`, `runtime_service.py` | MAF agent construction, `SkillsProvider` wiring, tool-trace middleware, `BudgetGuard` (`maf_runtime.py`); one asyncio event-loop-per-UI-session wrapper (`runtime_service.py`) |
| QA & delivery | `qa_report.py`, `triage.py`, `packaging.py` | Verdict computation, failure triage (files/lines to update), ZIP assembly |
| Observability | `observability.py`, `observability_ui.py`, `trace.py`, `telemetry.py` | Run tracing, the Observability tab, OpenTelemetry export |
| Support | `usage.py`, `settings.py`, `models.py`, `personas.py`, `routes.py` | Daily token ledger, config, Pydantic schemas, stage personas, app-route extraction |

## 6. Verification

* `uv run maf-harness lint-skills` — Agent Skills spec lint + lock verification.
* `uv run pytest -q --ignore=tests/targets` — unit, component (real MAF, scripted transport) and workflow e2e
  tests; no network, no model.
* `uv run maf-harness trace .harness_runs/<run>` — proves, from a saved run's own trace, that every assigned
  skill was really loaded and every listed tool was really available (see [OBSERVABILITY.md](OBSERVABILITY.md)).
