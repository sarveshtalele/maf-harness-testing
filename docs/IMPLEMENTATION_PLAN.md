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
| Determinism boundary | Host is deterministic (URLs, skill assignment + hashes, IDs, schemas, traceability, commands, verdict). Model output is not. `temperature=0` reduces variance, it does not create determinism. | Say what is and is not guaranteed. |
| Test execution | Separate host step, adapters per ecosystem, argv-only commands, new immutable result dir per run, sanitized environment, timeout kills the process group | Untrusted code executes only after explicit approval; evidence cannot be overwritten. |
| Isolation | Dependencies install into a venv / project `node_modules` / harness cache dirs after a tool-call approval; system tools are reported with install hints | Never mutate the host toolchain; approval before any network/installer side effect. |
| Token budget | 2,000,000 tokens per run is the only limit | Predictable cost ceiling without throttling individual calls. |
| Verdict | `PASS` needs fresh report evidence with ≥1 passing test and 0 failures; setup failure/blocked/zero tests → `INCONCLUSIVE`; skips → `PASS_WITH_SKIPS` | A green run must mean something. |

## 3. Architecture

| Module | Responsibility |
|---|---|
| `repo_analyzer.py` | URL policy, clone, inventory, framework detection, source-first evidence |
| `stack.py` | Ecosystem/runner/UI/service/OpenAPI/CI detection (deterministic) |
| `maf_runtime.py` | Harness agents, `SkillsProvider`, skill priming + verification, budget/provenance middleware |
| `skills_registry.py` | Agent Skills spec lint + package hashes (fail fast on invalid skills) |
| `personas.py`, `models.py`, `skill_contracts.py` | Stage roles, bounded schemas, ID assignment, traceability, static test validation |
| `workflow.py` | Checkpointed stages, approvals, heal loop, execution stage |
| `runners.py`, `execution.py` | Runner adapters (pytest, jest/vitest/playwright, maven, gradle, go, dotnet), result parsers, safe process execution |
| `qa_report.py` | requirement → case → test → result report and verdict |
| `cli.py`, `app.py` | Headless/CI driver and Streamlit UI over the same workflow |

## 4. Alignment with Microsoft Learn "Agent Skills"

| Requirement in the docs | Implementation | Verified by |
|---|---|---|
| Skill = directory, `SKILL.md` with `name` matching directory, `description` ≤ 1024, body < 500 lines | `skills_registry.lint_skill`; `require_valid` before every stage | `tests/test_skills_registry.py`, `maf-harness lint-skills` |
| Progressive disclosure: advertise → `load_skill` → `read_skill_resource` | `SkillsProvider(FilteringSkillsSource(FileSkillsSource))`, exactly the assigned skill advertised | `test_progressive_mode_loads_skill_through_maf_skills_provider` |
| Harness agents take a provider via `skills_provider` / `skills_paths` | `create_harness_agent(skills_provider=…)` | same |
| Skill tools need approval by default; auto-approve only trusted read-only tools | `auto_approval_rules=[SkillsProvider.read_only_tools_auto_approval_rule]`; anything else is rejected by the host | `test_script_execution_request_is_rejected_by_the_host` |
| Scripts need a runner and sandboxing; treat skills as third-party code | Script discovery disabled (`script_filter → False`), no `script_runner`; skill package hashed into provenance | runtime provenance test |
| Audit trail of loaded skills/resources | `state.skill_tool_trace`, `skill_provenance`, per-call `invocations`, `audit.jsonl` | UI *Audit* tab |
| Skills vs workflows: workflows for checkpointing and side effects | Skills only shape a stage's model turn; workflow owns sequencing, approvals, execution | architecture |
| **Doc/implementation gap**: docs say `run_skill_script` is advertised only when scripts exist; MAF 1.18 registers it unconditionally | Mitigated (approval required, never auto-approved, discovery off); asserted in tests so an upgrade is noticed | `test_progressive_mode_…` |

## 5. Delivery plan

### M0 — Baseline audit ✅
Reproduce reported failures, inventory files, error table (`docs/audit/ERRORS.md`).

### M1 — Real MAF Agent Skills ✅
`SkillsProvider`, two-phase priming (a tool-only turn loads the skill; the structured-output turn runs on the primed
session — found necessary because some gateways skip tool calls when structured output is requested), three priming
attempts, host-verified trace, provenance, explicit `preloaded` fallback, spec lint.
**Exit:** the 4 assigned skills lint clean; live run shows `skill_loaded_via_maf: true` for every stage.

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
`docs/audit/E2E_RESULTS.md`. Needs a funded gateway key for repeatable nightly runs.

### M7 — Isolation, design policy, deliverables ✅
* **Environment stage:** requirements list *environment needs*; the host adds a deterministic plan (prerequisites with versions and install
  hints, isolation strategy, exact commands). Dependencies are installed into a venv / project `node_modules` / cache-dir repositories only after a
  tool-call approval; system tools are reported, never installed.
* **Design policy:** unit-level cases required, every actor covered, every requirement covered or explained; generation cannot start before the design
  is approved (asserted in `tests/test_policies.py`).
* **Token policy:** one limit only — 2,000,000 tokens per workflow run.
* **Compiled-language resilience:** uncompilable generated files are quarantined so the rest still run.
* **Reports:** QA report lists the generated test files (with lines) and repository files (`file:line`) to update; ZIP of the complete repository with all
  generated and executed tests plus evidence.
* **Hygiene:** dead code/skills/scripts removed, strict ruff lint + format enforced in CI, full architecture documentation.

### M8 — Skills from the upstream catalog, configured end to end ✅
Six skills vendored from `shivswami/awesome-agent-skills` (pinned commit, per-file SHA-256 lock, offline security/quality audit, 0 findings), selected by
what the harness needs. Stage → skill sets are deterministic (`skills_config.skills_for`); every assigned skill must be loaded through MAF `load_skill`.
Scripts are governed by a reviewed allow-list with hash checks: one pure script is callable by the agent through MAF `run_skill_script`; the QA stage
runs the upstream `validate_traceability.py` and validates the coverage report against the upstream schema. Modified vendored files or scripts are refused.

### Next (⏭, in priority order)
1. **Sandboxed execution** (container/VM adapter behind the existing `execute_plan` boundary) — required before any
   unattended or multi-tenant use. Local-process execution is explicitly *not* a sandbox.
2. **Application lifecycle manager**: start/stop the system under test (compose/WildFly/npm start) with readiness
   probes, so UI/E2E cases run without a hand-started app.
3. **Deeper static gates**: compile Java/C#/TS generated sources against the repo classpath before approval.
4. **Evidence retrieval beyond bounded excerpts** (symbol index / embeddings) for large repos; OpenAPI extraction.
5. **Coverage and mutation feedback** into the heal loop (line coverage delta, mutation score).
6. **Multi-user hosting**: job queue, tamper-evident approvals, quotas, per-tenant run storage.
7. **Nightly qualification matrix** across model gateways with cost tracking.

## 6. Test strategy for the harness itself

| Layer | Content | Runs |
|---|---|---|
| Unit | validators, URL policy, ID stability, parsers, adapters, presenter | every push |
| Component (real MAF, scripted transport) | skill provider/priming/verification, budget, provenance, workflow gates, recovery | every push |
| Workflow e2e (real subprocess) | export → plan → run pytest → QA report → heal loop, tamper/skip paths | every push |
| Browser e2e (Playwright) | Streamlit UI validation and QA rendering (`HARNESS_BROWSER=1`) | CI job |
| Live model e2e | full CLI run against real repos through the gateway (`docs/audit/E2E_RESULTS.md`) | manual/nightly, key from env |

## 7. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Model ignores tools / hallucinates tool names | High on small models | Stage blocked | priming retries, host verification, retry gate, explicit `preloaded` fallback |
| Generated tests wrong (assert wrong behavior) | Medium | False failures/false confidence | human review of design and source; failure evidence loop; never-weaken rule; verdict never claims correctness |
| Executing untrusted repo code | Certain when enabled | Host compromise | separate approval with exact commands, sanitized env, timeout; **sandbox is a release gate for unattended use** |
| Token/cost blow-up | Medium | Cost | per-run token/call budgets, batch caching, fresh-session-from-snapshot |
| Gateway credential exposure | Medium | Account abuse | env-only, never persisted, never forwarded to test commands; rotate the credential that was previously embedded |
| MAF API drift (1.x) | Medium | Breakage | pinned versions, docs-gap assertion tests |

## 8. Definition of done for "production-grade"

- [x] Agent Skills used through MAF `SkillsProvider`, verified per stage, spec-linted
- [x] Every generated test traces to an approved case, requirement and actor
- [x] Tests are executed by the host with evidence-based verdicts; `INCONCLUSIVE` ≠ pass
- [x] Credentials env-only; not forwarded; secrets scan clean
- [x] CI: lint + unit + component + workflow e2e
- [x] Isolated dependency install behind a tool-call approval; ZIP deliverable; files-to-update report
- [ ] Sandbox adapter (blocks unattended/multi-tenant use)
- [ ] Nightly live qualification with budget alarms
