# Harness audit and qualification — 2026-09-19 (first pass)

> **Superseded in part.** This is the first-pass audit. Since then the harness gained real MAF `SkillsProvider` usage,
> an execution stage with QA report, and many fixes — see [ERRORS.md](ERRORS.md) (current defect register),
> [E2E_RESULTS.md](E2E_RESULTS.md) (live runs) and [../IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md). Statements below about
> skills being "preloaded instructions" and the workflow never executing tests describe the *previous* behavior.

## Outcome and scope

The reported harness failures are reproduced and repaired. Real target acceptance tests now exist and can be run through a separate host execution tool. This is a tested repair, not a claim that arbitrary repositories automatically become production-ready.

The inspected application is `.`. The initially selected `maf-harness` workspace was empty apart from Git/Serena metadata. No original saved run was replayed or rewritten. `.harness_runs/` is the actual run directory; no separate `.harness` directory was found in the application inventory.

## Original runs

| Run | What succeeded | Failure | Evidence |
|---|---|---|---|
| `18c289c11cdf` | Approved GitHub clone and static analysis | First provider call failed with connection error; consumed repo approval remained pending | `run-18c289c11cdf.json` |
| `b24b651d3a56` | Analysis, requirements, planning revisions, design | Generation batch 1/7 failed three times; last two responses contained imports only | `run-b24b651d3a56.json` |

Second run charged seven provider calls / 82,408 reported tokens. Analysis correctly counted 71 tracked files and 15 Python files. Persisted model evidence was only 17,689 characters of README/dependency text across five files. All Python was excluded by documentation priority and an early budget break. Requirements therefore described README examples as invariants: the proposed 292-file cap disagrees with the actual `FILE_CAP = 300` and 20 “other” slots.

The generation validator correctly rejected imports-only responses. Weak model output was compounded by missing source evidence and repair attempts without sufficient implementation context. An exception entering the downstream MAF executor escaped local error handling. Since the previous approval was already consumed, the UI reported an interrupted run at Test Design instead of a retryable Test Generation error. Earlier raw generation attempts were not durably retained.

## Architecture and file review

`file-inventory.json` inventories every first-party application/test/script file and bundled skill asset (path, size, SHA-256; Python syntax, symbols, imports). This is a per-file static inventory, not a claim of line-by-line semantic verification of every vendored helper.

| File/component | Responsibility | Findings and disposition |
|---|---|---|
| `app.py` | Streamlit lifecycle, asynchronous dispatch, approvals, artifacts | Wrong stage shown after interruption; use recorded active stage. Show MAF/skill provenance. Browser-tested invalid URL rejection before provider call. |
| `workflow.py` | Native MAF graph, checkpoints, review transitions, generation | Catch executor-entry failures; retain approvals; issue new retry request; clear stale error state. Include source in design and requirements/plan in generation. One case per batch, persist raw attempts and accepted batches keyed by input signature. |
| `repo_analyzer.py` | URL validation, clone, inventory, evidence, framework detection | README starvation fixed; source paths, line-numbered excerpts, Python symbol/constant outline. Reject nonstandard ports/dot paths. Detect Java EE/JSF/Arquillian; avoid classifying `<springmvc>false>` as Spring. |
| `models.py` | Bounded Pydantic stage schemas | Add explicit source file inventory. Existing requirements schema still lacks authored formal API contracts; see remaining work. |
| `maf_runtime.py` | Native harness agents, clients, sessions, budget middleware | Real MAF verified. Add exact skill-file hashes, MAF package versions, prompt/response hashes and call outcomes. Host preloads instructions. |
| `skill_contracts.py` | Stable IDs, schema/traceability validation, source validation | Count collectable Python tests using AST; reject nested/string declarations, padded empty bodies, `assert True`, constant-only assertions, duplicate case mappings and noncanonical paths. Other languages still use best-effort declaration patterns. |
| `execution.py` (new) | Reviewed export and explicit argv execution | New workspace only; original files cannot be overwritten; credentials not inherited; timeout kills process group; immutable per-run logs/status; fresh JUnit testcase outcomes distinguish passed/failed/skipped/no evidence. Local execution is not a security sandbox. |
| `settings.py` | Gateway configuration | Removed embedded credential and hardcoded gateway precedence. Environment configuration required. Previously embedded credential should be rotated. |
| `runtime_service.py` | Dedicated event loop/thread for MAF clients | Existing lifecycle tests pass. Not a distributed job queue or crash-recovery service. |
| `personas.py` | Stage instructions | Existing stack-neutral roles preserved. Instruction compliance is not guaranteed by model temperature. |
| `artifact_presenter.py` | JSON/Markdown/UI presentation | Add expandable full row details so truncated tables do not hide scenarios/evidence. |
| `__init__.py` | Public model exports | No behavior change. |
| `scripts/json_to_markdown.py` | Artifact export | Existing formatter tests retained. |
| `scripts/audit_project.py` (new) | Non-executing file inventory | Reproducible static inventory of application and bundled skill files. |
| `tests/` | Harness regression/MAF tests | New starvation, retry checkpoint, attempt persistence/cache, validation, export/execution and provenance cases. Correct pre-existing test-helper bug on `pending=None`. |
| `tests/targets/` | Real application qualification | Separate opt-in CLI/pipeline, API/browser and Streamlit browser suites; no fabricated generated-test claims. |
| `pyproject.toml`, `uv.lock` | Dependencies | Add optional Playwright test extra; lock updated. |
| `.gitignore`, `.env.example` | Local configuration hygiene | Ignore target/build/browser caches; environment example retained without real credentials. |

## Does it really use MAF and Agent Skills?

Yes: `MAFHarnessRuntime._create_agent()` calls `agent_framework.create_harness_agent`; `Harness` builds native `Executor` stages with `WorkflowBuilder` and `FileCheckpointStorage`. The native runtime regression injects a controlled chat transport but executes the actual harness/session/middleware, verifies the skill text reaches the provider boundary, and checks budget accounting and hashes. This proves integration, not a successful live commercial-provider run.

Active deterministic assignments:

| Stage | Loaded skill |
|---|---|
| requirements | `repository-requirements/SKILL.md` |
| test_planning | `test-planning/SKILL.md` |
| test_design | `test-design-generator/SKILL.md` plus host-loaded YAML output schema |
| test_generation | `generic-test-generator/SKILL.md` |

`requirement-analysis`, `test-design`, `junit-generator`, and `playwright-test-generator` are bundled but not selected by the active stage mapping. Their scripts are not run by the model. Some bundled generator examples intentionally contain TODOs and have incompatible input contracts; automatically wiring them in would violate the harness's no-placeholder contract. The generic generator recommends native frameworks/Playwright from evidence. Framework choice itself remains model-authored, not a fully deterministic adapter registry.

Deterministic: URL normalization, static selection, host skill assignment, hashes, case IDs for identical normalized content, schema validation, approval replay guards, artifact outcomes. Nondeterministic: model requirement interpretation, plan/case/source text, provider behavior, external services and timing. Temperature zero does not change this distinction.

## Target results and defects

### Reverse-engineering skill

Commit: `e6139c314e3c89d2e84efe690a8bdfbce3dc17d2`.

Baseline: 5 native acceptance checks passed; dashboard browser check failed with `Unexpected token 'export'`. The generated HTML loads `@hpcc-js/wasm/dist/index.min.js` as a classic script, but it contains ESM exports. Its renderer no longer uses that dependency. Isolated fix removes the unused script; patch is `../../patches/reverse-dashboard-unused-esm.patch`.

Patched qualification: 6/6 passed. Checks cover help/missing arguments, heuristic/AI/interactive CLI dispatch (pipeline boundary replaced only for dispatch), full real heuristic pipeline with actual Git clone of a local fixture, seven nonempty artifacts/JSON, and all six dashboard navigation sections without page errors. AI provider behavior is not exercised.

Original failed evidence: `reverse-e2e.xml`. Patched: `reverse-patched-e2e.xml`. Source checkout used for patch: `.targets/reverse-engineering-skill`; original run checkout untouched.

### Petstore Java EE 7

Commit: `725839cad3c52515b9702dec2edb3984df58d04f`.

Actual Homebrew JDK 11 and Maven build, WildFly 26.1.3.Final, seeded H2 database, localhost-only deployment. Nine existing JUnit tests passed, WAR built. Original API/catalog/cart/unknown-user checks passed. Added known-user/wrong-password browser check failed: `AccountBean.doLogin()` selected the customer by login and skipped password authentication.

Isolated fix verifies the submitted password using the application's existing digest convention before setting the session customer, clears failed authentication state and reports a generic error. Patch: `../../patches/petstore-verify-password.patch`. Patched native package tests: 9/9 passed; patched API/browser suite: 8/8 passed. Checks cover four catalog APIs, anonymous category→product→item navigation, unknown-user rejection, known-user/wrong-password rejection, valid login and add/remove cart.

This is a focused bypass fix, not a complete security redesign. The existing unsalted SHA-256 password convention and broad admin/API authorization need separate hardening. No public remote service was tested or modified; changes exist only in local qualification checkout/patches.

Original negative regression: `petstore-baseline-e2e.xml`. Patched: `petstore-patched-e2e.xml`; Maven logs in this directory. Full Arquillian integration profiles, checkout/order placement, registration, load and accessibility are not qualified by these smoke/acceptance suites.

### Combined execution proof

`execution-targets/result.json` records 15 tests passed, zero failures/errors/skips through the new actual execution tool: 6 reverse-engineering, 8 Petstore, 1 Streamlit browser check. This result applies to the local patched target copies. `target-qualification.xml` and log hashes support the result. Screenshots are under `screenshots/`.

## Remaining release gates

1. Configure an environment gateway key and run a new live MAF workflow for each repo. No live LLM generation was completed during this repair; controlled MAF tests and authored acceptance tests must not be confused with generated tests passing.
2. Old requirements/designs were based on insufficient evidence. Do not reuse them as a trustworthy test oracle; start new runs.
3. Add per-language runner adapters, actual collection/compilation gates, dependency setup plans and secure container/VM execution before offering unattended arbitrary-repository execution. Current runner is explicit/local and POSIX-oriented.
4. Add evidence retrieval beyond bounded excerpts, formal OpenAPI extraction and test-level metadata before claiming complete large-repo or API-contract coverage. Inventory is complete within supported tracked-file rules; prompt evidence is intentionally partial.
5. Python AST checks detect structural defects, not semantic coverage or every fake assertion. Other languages use regex preflight. Runtime execution is still required.
6. Strengthen checkpoint/start concurrency, tamper-resistant approval storage, log-size quotas and deployment lifecycle management for multi-user production hosting.
7. Upstream target patches require ordinary review and integration; nothing was pushed or published.

These boundaries are explicit so a passing harness regression suite is not mistaken for universal production readiness.
