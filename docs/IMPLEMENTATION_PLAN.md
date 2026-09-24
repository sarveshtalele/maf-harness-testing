# Implementation plan — production-grade MAF test-engineering harness

Status legend: ✅ delivered and verified in this repository · 🟡 delivered, partially verified · ⏭ planned.

## 1. Goal and non-goals

**Goal.** Given a public GitHub repository, autonomously produce, under human review, a traceable and *executed*
test suite for any technology stack, with an evidence-backed QA verdict.

```
repo URL → clone/analyze → requirements → environment → test plan → test design → test generation → run → QA report + ZIP
              (host)        (agent+skill)   (host, tool   (agent+skill)  (agent+skill)   (agent+skill)   (host,   (host)
                                             approval)                                                     tool approval)
                └─ every arrow is a human gate; installing and running code each need their own tool-call approval ─┘
```

**Non-goals.** Replacing human judgement of requirements; proving program correctness; unattended execution of
untrusted code without a sandbox; supporting private repositories (needs credential design).

## 2. Design decisions

| Decision | Choice | Why |
|---|---|---|
| Repository input | **GitHub page URL or clone URL**, normalized to one HTTPS clone URL; optional `/tree/<ref>` pins a ref | Users paste page URLs; SSH/other hosts/credentials add attack surface. One canonical clone form makes runs reproducible (`repo_url`, `commit_sha` recorded). |
| Orchestration | MAF `WorkflowBuilder` + `FileCheckpointStorage`, one `Executor` per stage, human gates as `request_info` | Documented MAF guidance: *use workflows when you must guarantee which steps run, checkpoint, and gate side effects*. |
| Agents | `create_harness_agent` per stage | Same factory the MAF Harness docs use; budget, tools and approvals stay framework-native. |
| Agent Skills | **`SkillsProvider` (progressive disclosure)**, one skill per stage, `load_skill` verified by the host | Matches MS Learn "Agent Skills". A skill is never trusted to have been used: the host reads a `FunctionMiddleware` trace and rejects output otherwise. |
| Skill fallback | `HARNESS_SKILL_MODE=preloaded` injects `SKILL.md` into instructions; recorded as `uses_maf_skills_provider: false` | Some models cannot call tools. The fallback is explicit, never silent. |
| Determinism boundary | Host is deterministic (URLs, skill assignment + hashes, IDs, schemas, traceability, commands, verdict, import resolution, signatures, routes, compile results). Model output is not. `temperature=0` reduces variance, it does not create determinism, and a deterministic model repeats a rejected answer, so retries raise the temperature. | Say what is and is not guaranteed. |
| Facts vs. guesses | Anything the host can compute exactly is a **central tool** (`code_intel`, `autofix`, `build_gate`, exposed to agents through `agent_tools`) and is also applied by the host itself on every candidate | A model call costs ~35k tokens; a dictionary lookup costs nothing. Correctness must not depend on the agent choosing to call a tool (measured: it did not). |
| Test execution | Separate host step, adapters per ecosystem, argv-only commands, new immutable result dir per run, sanitized environment, timeout kills the process group | Untrusted code executes only after explicit approval; evidence cannot be overwritten. |
| Isolation | Dependencies install into a venv / project `node_modules` / harness cache dirs after a tool-call approval; system tools are reported with install hints | Never mutate the host toolchain; approval before any network/installer side effect. |
| Token budget | **50,000,000 tokens per local day across all runs** (env `HARNESS_DAILY_TOKEN_LIMIT`) is the only harness limit (file ledger with exclusive lock; reserve-before-call, settle to reported usage; failed calls refunded) | A predictable daily spend ceiling without throttling any single run, and safe under concurrent runs. The gateway may have its own quota; that is reported, never worked around. |
| Verdict | `PASS` needs fresh report evidence with ≥1 passing test and 0 failures; setup failure/blocked/zero tests → `INCONCLUSIVE`; skips → `PASS_WITH_SKIPS` | A green run must mean something. |

## 3. Architecture

| Module | Responsibility |
|---|---|
| `repo_analyzer.py` | URL policy, clone, inventory, framework detection, source-first evidence, target source and collaborator signatures |
| `stack.py` | Ecosystem/runner/UI/service/OpenAPI/CI detection (deterministic) |
| `maf_runtime.py` | Harness agents, `SkillsProvider`, skill priming + verification, budget/provenance middleware |
| `skills_registry.py` | Agent Skills spec lint + package hashes (fail fast on invalid skills) |
| `personas.py`, `models.py`, `skill_contracts.py` | Stage roles, bounded schemas, ID assignment, traceability, static test validation |
| `workflow.py` | Checkpointed stages, approvals, heal loop, execution stage |
| `runners.py`, `execution.py` | Runner adapters (pytest, jest/vitest/playwright, maven, gradle, go, dotnet), result parsers, safe process execution |
| `code_intel.py`, `autofix.py`, `build_gate.py`, `agent_tools.py`, `routes.py` | Central deterministic tools: import resolution, API/symbol lookup, static gates, mechanical repairs, compile/collect gate, route inventory; MAF function-tool wrappers |
| `qa_report.py` | requirement → case → test → result report and verdict |
| `cli.py`, `app.py` | Headless/CI driver and Streamlit UI over the same workflow |

## 4. Alignment with Microsoft Learn "Agent Skills"

| Requirement in the docs | Implementation | Verified by |
|---|---|---|
| Skill = directory, `SKILL.md` with `name` matching directory, `description` ≤ 1024, body < 500 lines | `skills_registry.lint_skill`; `require_valid` before every stage | `tests/test_skills_registry.py`, `maf-harness lint-skills` |
| Progressive disclosure: advertise → `load_skill` → `read_skill_resource` | `SkillsProvider(FilteringSkillsSource(FileSkillsSource))`, exactly the assigned skill advertised | `test_progressive_mode_loads_skill_through_maf_skills_provider` |
| Harness agents take a provider via `skills_provider` / `skills_paths` | `create_harness_agent(skills_provider=…)` | same |
| Skill tools need approval by default; auto-approve only trusted read-only tools | `auto_approval_rules=[SkillsProvider.read_only_tools_auto_approval_rule, script_approval_rule]`; anything else is rejected by the host. The central harness tools are read-only or scratch-only and declared `approval_mode="never_require"` | `test_script_execution_request_is_rejected_by_the_host`, `test_central_tools.py` |
| Scripts need a runner and sandboxing; treat skills as third-party code | Vendored skills are locked by per-file SHA-256; only reviewed scripts run (`HarnessScriptRunner`: allow-list + hash check, argv-only, temp dir, sanitized env); model-tier scripts may be called through `run_skill_script`, host-tier scripts only by the harness | `test_skills_registry.py`, `test_native_maf.py` |
| Audit trail of loaded skills/resources | `state.skill_tool_trace`, `skill_provenance`, per-call `invocations`, `audit.jsonl` | UI *Audit* tab |
| Skills vs workflows: workflows for checkpointing and side effects | Skills only shape a stage's model turn; workflow owns sequencing, approvals, execution | architecture |
| **Doc/implementation gap**: docs say `run_skill_script` is advertised only when scripts exist; MAF 1.18 registers it unconditionally | Mitigated (scripts refused unless on the reviewed allow-list); asserted in tests so an upgrade is noticed | `test_native_maf.py` |

## 5. Delivery plan

### M0 — Baseline audit ✅
Reproduce reported failures, inventory files, error table (`docs/audit/ERRORS.md`).

### M1 — Real MAF Agent Skills ✅
`SkillsProvider`, two-phase priming (a tool-only turn loads the skill; the structured-output turn runs on the primed
session — found necessary because some gateways skip tool calls when structured output is requested), five priming
attempts, host-verified trace, provenance, explicit `preloaded` fallback, spec lint.
**Exit:** the assigned skills lint clean; live run shows `skill_loaded_via_maf: true` for every stage.

### M2 — Accurate test design for any stack and persona ✅
Requirements now carry **actors**, categories, acceptance criteria, validation rules and explicit API contracts.
Design cases carry **level, actor, target, preconditions, data**. Host rules: UI/E2E/accessibility only when a
running app URL is supplied; unknown actors/levels rejected; contract tests only for explicit contracts.
**Exit:** every case traces to requirement/criterion/rule/contract IDs; coverage matrix rebuilt by the host.

### M3 — Stack profile, runner adapters, execution ✅ (🟡 Java/.NET/Go verified by unit tests only)
`stack.py`, `runners.py`, execution stage with plan approval (plan hash), sanitized env, fresh-report evidence,
timeouts, `BASE_URL` injection. **Exit:** real subprocess pytest e2e test; secrets-not-forwarded test.

### M4 — QA report and heal loop ✅
Deterministic report (case/requirement states, failures, provenance, limitations). Human-approved regeneration of
only failing cases (≤3 rounds) with failure evidence; assertions must not be weakened.

### M5 — Delivery surface ✅
`maf-harness` CLI (interactive, `--auto-approve`, `--auto-heal`, exit codes for CI), Streamlit UI (QA tab,
execution approval, app URL, provenance), GitHub Actions CI.

### M6 — Qualification against real targets 🟡
Reverse-engineering-skill (Python CLI + generated HTML dashboard) and Petstore (Java EE 7) — see
`docs/audit/E2E_RESULTS.md`. Reverse completes end to end on the current code (FAIL verdict with authentic, analyzed test defects). Petstore
completed on an earlier revision; on the current code three attempts stopped in generation (two model-behavior issues, fixed; one provider quota
limit). Needs gateway quota headroom for repeatable nightly runs.

### M7 — Isolation, design policy, deliverables ✅
* **Environment stage:** requirements list *environment needs*; the host adds a deterministic plan (prerequisites with versions and install
  hints, isolation strategy, exact commands). Dependencies are installed into a venv / project `node_modules` / cache-dir repositories only after a
  tool-call approval; system tools are reported, never installed.
* **Design policy:** unit-level cases required, every actor covered, every requirement covered or explained; generation cannot start before the design
  is approved (asserted in `tests/test_policies.py`).
* **Token policy:** one limit only — a 50,000,000-token daily cap shared by all runs (`usage.py`); the earlier per-run cap was removed.
* **Compiled-language resilience:** uncompilable generated files are quarantined so the rest still run.
* **Reports:** QA report lists the generated test files (with lines) and repository files (`file:line`) to update; ZIP of the complete repository with all
  generated and executed tests plus evidence.
* **Hygiene:** dead code/skills/scripts removed, strict ruff lint + format enforced in CI, full architecture documentation.

### M8 — Skills from the upstream catalog, configured end to end ✅
Six skills vendored from `shivswami/awesome-agent-skills` (pinned commit, per-file SHA-256 lock, offline security/quality audit, 0 findings), selected by
what the harness needs. Stage → skill sets are deterministic (`skills_config.skills_for`); every assigned skill must be loaded through MAF `load_skill`.
Scripts are governed by a reviewed allow-list with hash checks: one pure script is callable by the agent through MAF `run_skill_script`; the QA stage
runs the upstream `validate_traceability.py` and validates the coverage report against the upstream schema. Modified vendored files or scripts are refused.

### M9 — Central deterministic tools ✅ (🟡 live pass on Petstore pending gateway quota)
Import errors and build errors are facts, so the host answers them before asking a model again:
* **Tools** (`code_intel`, `agent_tools`): `resolve_import`, `module_api`, `find_symbol`, `read_source`, `check_test`, `list_routes`, `installed_packages`,
  `probe_tools`, `try_build`, exposed to stage agents as MAF function tools (small per-stage selection).
* **Auto-fix** (`autofix`): unambiguous missing imports (stdlib, mock, JDK, unique repository class, JUnit) and module-path corrections; audited.
* **Static gates**: undefined names, mocked `sys.exit`, non-existent patch targets, unused setup, URL paths that are not routes, Arquillian, undeclared libraries.
* **Build gate** (`build_gate`): `pytest --collect-only` / `mvn test-compile` after the approved environment install; `file:line` diagnostics.
* **Prompt facts**: routes and the real `BASE_URL`, collaborator signatures, declared and forbidden libraries, nested requirements and evidence-backed packages installed.
* **Retry policy**: temperature rises per attempt and per round; remedies name the fix; extra test methods are listed.
**Measured:** on reverse the agents called none of the tools; the host layer did the work (25 build checks, 0 build failures, replayed gates flag 6 of 34
generated files, each a real defect). Whether an agent-side call ever pays for its extra round trip is still open (`HARNESS_AGENT_TOOLS=off` allows A/B).

### M10 — Observability and traceability ✅
Per-run Observability tab and `maf-harness trace`, built on MAF middleware and the existing audit/state (design and guide: [OBSERVABILITY.md](OBSERVABILITY.md)):
* `trace.jsonl` (model runs, provider calls with tokens and duration, tool calls with category, summarised arguments and duration);
* report: stages, skills (origin, loads, scripts), tools (including never-used harness tools), timeline, requirement → case → file → result chain with the skills
  that produced each link, integrity checks (assigned skills loaded, no unexpected tools, trace tokens = ledger tokens), Markdown/JSON export;
* older runs render at summary level; optional OpenTelemetry export (`HARNESS_OTEL`), message content never captured.
**Exit:** rendered from the real `final5-reverse` run and from a real-MAF scripted run in the browser; tests in `test_observability.py` and the UI browser suite.

### M11 — Windows and macOS ✅
Every OS-specific decision lives in `envs/platform_compat.py` (venv layout, `.cmd`/`.exe` resolution, process-tree kill, fake-`HOME`, cache paths); `filelock` replaced `fcntl`.
Found only by live runs on Windows and fixed: a bare `python3` opening the Python installer, a relative `argv[0]` resolved against the caller's directory, an unbuildable package
aborting a whole `pip install -r` (now retried package by package), declared-but-uninstalled packages (`envs/env_check.py`), and the application under test never started for Playwright
cases (`envs/app_launcher.py`, `--start-app`). CI has a Windows leg.

### M12 — Error taxonomy and recovery playbook ✅
`errors/`: 38 codes with category (environment / infra / test defect / app defect), autonomy (auto / auto_once / ask / manual) and recipes; classifiers for Python, Java, .NET and common
failures; 56 recorded real logs as fixtures (`tests/error_cases.py`); wired into install, the build gate, the QA report and triage so an environment failure never costs generation retries.
[ERROR_PLAYBOOK.md](ERROR_PLAYBOOK.md) is generated from the tables.

### M13 — Toolchain provisioning ✅ (🟡 no live JDK download)
`envs/spec.py` detects what a repository needs (Python, JDK, Maven/Gradle, .NET SDK); `envs/toolchains.py` finds it or, after the environment approval, downloads it into
`~/.maf-harness/toolchains` (Temurin, Apache Maven, Gradle, Microsoft .NET, Python via `uv`) with checksum verification, path-escape refusal and a file lock. Verified offline against a local
mirror and live for Maven; a 190 MB JDK download was not exercised. Not built: scaffolding a Java or .NET *test project* for generated tests. See [ENVIRONMENTS.md](ENVIRONMENTS.md).

### M14 — Package structure ✅
Modules grouped into `repo/ envs/ skills/ tools/ agent/ llm/ qa/ monitoring/ ui/ errors/ hooks/`. The old flat import paths were
bridged by compatibility shims for the migration, then removed once every caller (all internal, in `tests/`) moved to the
subpackage path — a private repo with no external consumers has no reason to carry that indirection permanently.

### M15 — Hooks and typed tools ✅
`hooks/`: eight events, verdicts allow/deny/transform/ask, fail-closed on the enforcing ones, evaluate-only mode, command and Python hooks, built-ins (secret redaction, write scope,
download consent, stage budget, audit), `.harness/hooks.json` with strict validation; wired into `Harness` (gates and failures) and the MAF agent loop. `tools/registry.py`: schema-validated
tools with one result envelope. See [HOOKS.md](HOOKS.md).

### M16 — Bounded recovery, diagnosis agent, prompt assembly ✅ (🟡 not measured on a large repository)
`envs/recovery.py` applies allow-listed recipes to failed setup steps within fixed bounds; an opt-in diagnosis agent writes an advisory note after what recovery could not fix;
stage prompts are assembled stable-first for the provider's prefix cache (60.7 % to 82.2 % shared prefix on the fixture repository, but only 8.3 % in a live run on a real repository); reviewed skill validators are available to the design
stage as an opt-in tool. The agent loops are verified with a real MAF pipeline and a scripted transport. See [AUTOMATION.md](AUTOMATION.md).

### M17 — AGENT-HOOKS backend and the Automation view ✅ (🟡 SDK is alpha)
`HARNESS_HOOKS_BACKEND=sdk` enforces the same hook engine through MAF's AGENT-HOOKS-0.1 middleware (optional `agent-hooks-sdk`); the Observability report, its Markdown and the UI gain an
*Automation* section (hook decisions, typed tool calls, recoveries including those left to a human, diagnosis notes).

### Next (⏭, in priority order)
1. **Sandboxed execution** (container/VM adapter behind the existing `execute_plan` boundary) — required before any
   unattended or multi-tenant use. Local-process execution is explicitly *not* a sandbox.
2. **Scaffold a Java/.NET test project** for generated tests (Maven/Gradle module, `.csproj` with the test SDK and runner) and verify a live JDK download.
3. **Application lifecycle manager beyond Python/npm apps**: compose/WildFly, with readiness probes (Streamlit, FastAPI, Flask and npm apps are started today with `--start-app`).
4. **More automatic recipes**: `prefer-binary`, `relax-pin`, `install-browser` and the `provision-*` recipes under an explicit approval, plus execution-phase recovery (a hung or port-blocked test run).
5. **Measure the token effect** of stable-first prompts and the diagnosis agent on real, large repositories; only then consider session reuse or compaction.
6. **Build gate for more ecosystems**: Java and Python are covered; add C#/TS/Go compile checks, and Gradle.
7. **Evidence retrieval beyond bounded excerpts** (symbol index / embeddings) for large repos; OpenAPI extraction.
8. **Coverage and mutation feedback** into the heal loop (line coverage delta, mutation score).
9. **Multi-user hosting**: job queue, tamper-evident approvals, quotas, per-tenant run storage.
10. **Nightly qualification matrix** across model gateways with cost tracking.

## 6. Test strategy for the harness itself

| Layer | Content | Runs |
|---|---|---|
| Unit | validators, URL policy, ID stability, parsers, adapters, presenter | every push |
| Component (real MAF, scripted transport) | skill provider/priming/verification, budget, provenance, workflow gates, recovery | every push |
| Workflow e2e (real subprocess) | export → plan → run pytest → QA report → heal loop, tamper/skip paths | every push |
| Browser e2e (Playwright) | Streamlit UI validation and QA rendering (`HARNESS_BROWSER=1`) | CI job |
| Live model e2e | full CLI run against real repos through the gateway (`docs/audit/E2E_RESULTS.md`) | manual/nightly, key from env |
| Real-subprocess recovery / hooks | recovery with real timeouts and venv rewinds, command hooks as real processes, hooks in a real MAF loop | every push |

## 7. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Model ignores tools / hallucinates tool names | High on small models | Stage blocked | priming retries, host verification, retry gate, explicit `preloaded` fallback |
| Generated tests wrong (assert wrong behavior) | Medium | False failures/false confidence | human review of design and source; failure evidence loop; never-weaken rule; verdict never claims correctness |
| Executing untrusted repo code | Certain when enabled | Host compromise | separate approval with exact commands, sanitized env, timeout; **sandbox is a release gate for unattended use** |
| Token/cost blow-up | Medium | Cost | daily ledger, batch caching, fresh-session-from-snapshot, retry only what failed, mechanical repairs instead of model retries |
| Provider quota exhausted mid-run | Medium | Run stops, tokens already spent | reported as the provider's limit; artifacts retained; choose *Retry* at the error gate after the reset (a *Stop* decision cannot be resumed, so `--auto-approve` runs must be relaunched) |
| Model repeats a rejected answer | High at temperature 0 | Wasted attempts (1.6M tokens in one session) | retry temperature schedule, forbidden-library list, actionable feedback |
| Gateway credential exposure | Medium | Account abuse | env-only, never persisted, never forwarded to test commands; rotate the credential that was previously embedded |
| MAF API drift (1.x) | Medium | Breakage | pinned versions, docs-gap assertion tests |
| Trace and MAF spans diverge | Low | Misleading tab | the trace is written from MAF middleware; integrity check compares its tokens with the ledger; OpenTelemetry export is separate and optional |

## 8. Definition of done for "production-grade"

- [x] Agent Skills used through MAF `SkillsProvider`, verified per stage, spec-linted
- [x] Every generated test traces to an approved case, requirement and actor
- [x] Tests are executed by the host with evidence-based verdicts; `INCONCLUSIVE` ≠ pass
- [x] Credentials env-only; not forwarded; secrets scan clean
- [x] CI: lint + unit + component + workflow e2e
- [x] Isolated dependency install behind a tool-call approval; ZIP deliverable; files-to-update report
- [x] Deterministic import/build checks before any model retry (auto-fix, static gates, build gate)
- [x] Windows, macOS and Linux (CI matrix includes Windows); every OS decision in one module
- [x] **Strict guardrail**: nothing after environment setup starts with a missing or conflicting requirement; a fallback Python is proposed and approved as a download
- [x] Every environment/execution failure classified; environment failures never burn generation retries; safe recovery bounded and audited
- [x] Fail-closed hooks around tools, model calls and gates; secrets redacted; agent writes confined to the workspace
- [ ] Sandbox adapter (blocks unattended/multi-tenant use)
- [ ] Live pass of the current code on both targets (Petstore blocked by gateway quota)
- [ ] Nightly live qualification with budget alarms
