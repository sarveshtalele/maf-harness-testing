# Live end-to-end results

All runs below used the real harness (`maf-harness run … --auto-approve`) against a real model gateway
(`deepseek-v4-flash-global`, progressive skill mode) and real repositories. Nothing in these runs was scripted except the human clicks
(`--auto-approve`, `--auto-heal N`). Credentials came from the environment; none are stored in the repository.

## 1. Where the original failures came from

Two runs from the original report were analyzed first (`.harness_runs/`):

| Run | What succeeded | Failure | Root cause | Fixed by |
|---|---|---|---|---|
| `18c289c11cdf` | approved clone, static analysis | first provider call failed (connection error); the consumed repository approval stayed "pending" | downstream exception escaped the stage handler, so no retry gate was issued | retry/stop gate for every stage failure (`tests/test_recovery.py`) |
| `b24b651d3a56` | analysis, requirements, plans, design | test generation batch 1/7 failed 3× ("no recognized concrete test declarations", imports only) | (1) README-heavy evidence consumed the prompt budget so no source reached the model, (2) no reason was fed back on repair, (3) the model (via this gateway) returned source with **newlines stripped** | source-first evidence, repair loop with host errors, `lines[]` output |

## 2. Run matrix (what each run taught)

| Run | Target | What it exposed | Fixed in |
|---|---|---|---|
| reverse-1 … 5 | reverse-engineering-skill | skill never loaded; newline-stripped source; wrong pytest rootdir; collection errors aborting the session; guessed imports | [ERRORS.md](ERRORS.md) A3, B7, B8, C4–C6 |
| reverse-7, 8 | reverse-engineering-skill | 4 evaluator tests built blind (truncated 34 KB source); `_layer` imported although it is a local; collection errors reported without a reason | F13–F15 |
| reverse-9 | reverse-engineering-skill | generation deadlocked on a module in a non-package directory | F16 |
| petstore-2 … 6 | agoncal-application-petstore-ee7 | undeclared RestAssured, truncated files, path collisions, container-only tests, 2M per-run cap hit twice | B11, B12, F5, F12, F19 |
| final (1) reverse / petstore | both | heal made results *worse* (23 → 19 passed); transposed hash suffix in IDs; 21 nested `with` blocks passing `ast.parse`; 17 of 43 generation calls were repairs | F20–F25 |
| final2 | both | cost and repair churn measured; per-run cap stopped Petstore at 1.96M | F19, F22 |
| final3-reverse | reverse-engineering-skill | 20+ nested `with patch(...)` rejected 9 times by the new gate | auto-flatten to `ExitStack` |
| **final4-reverse** | reverse-engineering-skill | **complete, 0 stage errors** | §3 |
| **final3-petstore** | agoncal-application-petstore-ee7 | **complete, 0 stage errors** | §4 |
| **final5-reverse** | reverse-engineering-skill | **complete, 0 stage errors** on the central-tools code; agents never called the tools | §4c |
| final5/6/7-petstore | agoncal-application-petstore-ee7 | two model-behavior loops (two `@Test` methods; RestAssured ×9) and a gateway quota stop | F35, F36, §4c |

"Harness error" below means a stage error, retry/stop gate, interrupted run, or budget stop. A `FAIL` verdict means generated tests failed against the
application; that is a result, not a harness error.

## 3. reverse-engineering-skill — run `final4-reverse` (final code)

Commit analyzed: `e6139c314e3c`. Workflow **complete**: 0 stage errors, 0 retry gates, 41 provider calls, 980,505 tokens.

| Stage | Result |
|---|---|
| Requirements | 21 requirements, 10 acceptance criteria, 3 validation rules; actors: CLI user, Claude Code user, GitHub Copilot Chat user, Python API consumer; 5 environment needs. Skills loaded via MAF: `repository-requirements` |
| Environment | `python`: `create-venv` → `install-pytest` → `install-requirements`; status `ready` (isolated `.harness-venv`) |
| Design | 36 unit cases, 80.95% requirement coverage; gaps stated per actor. Skills: `test-design-generator` + `test-data-generator` |
| Generation | 36 files; 3 repairs (import gate / syntax), 0 gate failures |
| Execution | attempt 1: 27 pass / 9 fail → attempt 2: 28 / 8 → attempt 3: **29 / 7** (monotonic: heal regenerates only failing cases) |
| QA | **FAIL: 29 passed, 7 failed of 36**; requirements: 12 verified, 5 failing, 4 untested. `traceability-generator` validator **PASS**, `coverage-analyzer` contract **PASS** |
| Evidence | `maf-harness verify-run`: all checks PASS (harness factory, every assigned skill loaded through MAF, packages unchanged, lock intact) |
| Deliverable | ZIP with 127 entries: repository + 36 generated tests + `harness-report/` (QA report, traceability matrix, coverage summary, three execution attempts, audit trail, `RUN_TESTS.md`) |

**Why the 7 tests still fail — all are test-authoring defects, none is an application defect:**

| Failing tests | Root cause (from the traceback and the app source) |
|---|---|
| `CLI exits with code 1 when no URL…` (`'NoneType' has no attribute 'rstrip'`) | the test replaces `sys.exit` with a mock, so `main()` continues past its `sys.exit(1)` and calls `run_pipeline(None)`; it should use `pytest.raises(SystemExit)` |
| `--help`, `-h` (`reading from stdin while output is captured`) | the tests do not patch `input()` although `main()` calls `_ask_mode()` |
| `no args prints help…` (`name 'unittest' is not defined`) | a missing import |
| `_call_claude catches API exception` (`module has no attribute 'anthropic'`) | patches a name the module imports lazily inside the function |
| `AI mode falls back…` (`ImportError: anthropic package is required`) | expectation ignores that AI mode raises when the package is absent |
| `Java parser extracts … Spring routes` | assertion about parser output that the code does not produce |

The harness now attaches a concrete remedy to each of these message patterns (mocked `sys.exit`, stdin, missing import, lazily imported attribute) for the
regeneration feedback and the QA report. Those remedies were added after this run; the run above used the code before the last two.

## 4. Petstore (Java EE 7) — run `final3-petstore` (final code)

Commit analyzed: `725839cad3c5`; Maven/JDK 11; live baseline application on WildFly 26 (`--app-url`). Workflow **complete**: 0 stage errors, 0 retry gates,
49 provider calls, 1,434,870 tokens (the earlier run of this target used 1.96M and was stopped by the old per-run cap).

| Stage | Result |
|---|---|
| Requirements | 26 requirements, 9 acceptance criteria, 18 validation rules, **25 API contracts**; actors: anonymous_visitor, customer, administrator, api_consumer; 9 environment needs. Skills loaded via MAF: `repository-requirements` **+ `api-contract-analyzer`** (the repo ships an OpenAPI/Swagger file) |
| Environment | `maven`: `resolve-dependencies` into an isolated Maven repository; status `ready` |
| Design | 33 cases: 16 api, 12 unit, 5 integration; 61.5% requirement coverage with stated gaps |
| Generation | skills `generic-test-generator` + `test-data-generator` + **`junit-generator`**; 11 repairs (undeclared libraries, truncation) instead of 17 in the earlier run; the declared-dependency list in the policy removed most RestAssured churn |
| Execution (`mvn test`) | attempt 1: 32 tests, 9 pass / 6 fail / 17 error → attempt 2: 30 tests, **24 pass** / 6 fail (3 uncompilable files quarantined) → attempt 3: 24 / 5 / 1 |
| QA | **FAIL: 24 passed, 5 failed, 4 error of 33**; requirements: 10 verified, 6 failing, 10 untested. Skill-contract checks PASS |
| Evidence | `verify-run`: 15 PASS, 0 FAIL |
| Deliverable | ZIP with 238 entries (full repository + generated tests + `harness-report/`) |

The heal loop moved Petstore from 9 to 24 passing tests by removing container-only tests (`no Bean Validation provider`, `Arquillian DeploymentScenario`).
What remains: `expected:<201> but was:<405>` for POST on items/products and `404`/`400` on category content negotiation — **check the application first**: these
may be real behavior of the live app (or wrong assumptions in the tests), and the report lists the exact test files. Three tests do not compile against the
production classes (private access, constructor shape) and were quarantined so the rest could run. This run does not prove Petstore correct or defective; the
two defects found earlier by hand-written acceptance tests are in [ERRORS.md §G](ERRORS.md).

## 4b. Remaining failures of `final4-reverse` / `final3-petstore` → fixes

Every remaining failure was traced to a test-authoring or environment defect the harness can detect (defects F28–F31 in
[ERRORS.md](ERRORS.md)); none was an application bug except the reverse skill's Java-parser wildcard-import assertion,
which is left failing on purpose (a FAIL is a result, not a harness error).

| Failure (run) | Root cause | Fix now in the harness |
|---|---|---|
| 3 CLI tests (reverse) | `mocker.patch.object(sys, "exit")` | sys.exit-mock gate |
| 1 test (reverse) | missing `import unittest` | undefined-name gate |
| 1 test (reverse) | `patch("engine.ai_analysis.anthropic")`, package imported lazily | patch-target gate |
| `anthropic` not installed (reverse) | declared only in nested `skill-packages/*/requirements.txt` and the README | nested requirements + evidence-backed documented packages in the approved plan |
| 8 API tests (Petstore) | guessed `/petstore/rest/...` path while `BASE_URL` = `http://127.0.0.1:8080/applicationPetstore` | route inventory + `check_route_literals` + 404/405 remedy |
| 3 quarantined Java tests (Petstore) | guessed constructors/methods of collaborators | `collaborator_signatures` in the prompt |

Replaying the new static gates on the previously generated tests flagged 4 of 36 reverse files and 5 of 33 Petstore files, each one a test that
failed at run time. The live reruns are in §4c: reverse ran to completion; Petstore did not (see there).

## 4c. Central-tools runs (`final5-reverse`, Petstore `final5`/`final6`/`final7`)

Code under test: central tools, auto-fix, build gate, route/hygiene gates (see [ERRORS.md](ERRORS.md) F28–F39). Run through the CLI with
`--auto-approve --auto-heal 2`; the daily cap was raised for the session with `HARNESS_DAILY_TOKEN_LIMIT` (the default is now 50,000,000).

**reverse-engineering-skill (`final5-reverse`)** — complete, 0 stage errors: verdict **FAIL**, 25 passed / 9 failed, 44 provider calls,
1.15M tokens, 25 build-gate checks (0 build failures), 1 auto-fix. The 9 failures are authentic requirement-derived tests that were
badly written; none proves an application defect:

| Failures | Cause | Now caught before the run? |
|---|---|---|
| 2 CLI tests | mocked `sys.exit` (`patch("reverse_engineer_skill.sys.exit")`), so `main()` ran on into `input()` / `repo_name_from_url(None)` | yes (gate now also matches module-qualified `sys.exit`) |
| 3 evaluation-boundary tests (60/40/39) | `sections = [...]` built and never passed to the function under test | yes (unused-setup gate) |
| 1 evaluation-boundary test (80) | `sections` only used for a self-check sum | no (needs semantic judgement) |
| 2 `--ai` fallback tests | drive `_ask_mode(["--ai"])`, which returns `"ai"` before any key/package check; the fallback exists only on interactive choice 2 | no |
| 1 `_layer` test | `_layer` is a closure that runs only above 300 files; the test uses 6 and patches `os.path.join` globally | no |

Replaying the new gates on the generated tests flags 6 of 34 files (5 of the 9 failures plus one test that passed by luck).
Honest finding: **the agents never called the harness tools** (`harness_tool_calls` is empty for every stage). The gains come from the host
layer (auto-fix, gates, build gate), which does not depend on the model choosing to call anything.

**Petstore** — no completed run on this code; three attempts stopped in test generation, 2.0M tokens in total:

| Run | Stopped at | Cause | Response |
|---|---|---|---|
| `final5-petstore` | batch 6/11 after 5 accepted batches (5 build gates OK, 61 auto-fixes of missing imports) | model wrote 2 `@Test` methods (and Arquillian) for a one-case file, verbatim on every retry | feedback now names the methods; Arquillian gate |
| `final6-petstore` | batch 1/12 | model re-imported undeclared RestAssured on 9 straight attempts (temperature 0 repeats a rejected answer) | retry temperature rises per attempt and per round; `forbidden_imports` in the generation policy |
| `final7-petstore` | batch 1/11 | **gateway quota**: `daily token limit exceeded (25,003,793/25,000,000)` (the provider's own cap, separate from the harness's ledger) | none possible; not circumvented |

The Petstore fixes above are covered by unit tests and replays, not by a live pass. The next full run should start after the gateway quota resets.

## 4d. Browser review of the Streamlit UI

Driven through the browser pane against the reverse-engineering repository: start form → clone approval (tool, URL, destination shown) → repository analysis
(commit `e6139c31`, 71 files, 15 source files, downloads) → approval of the analysis → requirements model call. The call failed with the gateway's 429, which
exposed two UI defects (raw exception shown twice; no hint that retrying is pointless), fixed as F38 and re-verified in the browser. A second pass, started
without the raised cap, showed the harness's own daily-cap message correctly. A complete model-driven UI run needs gateway quota.

## 4e. Observability tab on real data

`maf-harness trace .harness_runs/final5-reverse` and the Observability tab (opened in a browser) reproduce the run's own numbers: 44 provider calls,
1,154,828 tokens, 36 model runs, 6 skill loads (`repository-requirements`, `test-planning`, `test-design-generator` + `test-data-generator`,
`generic-test-generator` + `test-data-generator`), 0 harness-tool calls, 16 human gates, 1 auto-fix / 25 build checks, 701 s wall time, and the
requirement → case → file → result chain (25 passed / 9 failed, 22 requirements). This run predates `trace.jsonl`, so the tab reports *summary*
detail. A full trace was checked on a scripted real-MAF run (skill load + harness tool call + four provider calls at 20 tokens each = 80 tokens = the
accounted tokens); a partial trace merged into the real run's state correctly failed the *trace tokens match ledger* check (80 vs 1,154,828).
The review also found the green FAIL banner (F41).

## 5. Token accounting

* **Harness ledger** (`~/.maf-harness/usage`, default cap 50,000,000 per local day): 3,128,180 after `final3-reverse` (stopped, 712,805), `final3-petstore`
  (1,434,870) and `final4-reverse` (980,505); 6,319,094 after the `final5-*` runs (`final5-reverse` 1,154,828; Petstore attempts 890,002 / 667,633 / 478,451).
  Earlier runs of the day predate the ledger and are not counted.
* **Gateway quota** is separate and provider-owned: it returned `daily token limit exceeded (25,003,793/25,000,000)` on 2026-09-19. It cannot be raised
  from the harness, and the harness does not try.

## 6. Reproduce

```bash
export OPENAI_API_KEY=… OPENAI_BASE_URL=… OPENAI_MODEL=deepseek-v4-flash-global   # LLM_MODEL is accepted as an alias
# optional: HARNESS_DAILY_TOKEN_LIMIT=… (default 50,000,000), HARNESS_AGENT_TOOLS/AUTOFIX/BUILD_GATE=off for A/B runs
uv run maf-harness run https://github.com/sarveshtalele/reverse-engineering-skill --auto-approve --auto-heal 2
# Petstore: WildFly 26 with the WAR deployed, JDK 11 on PATH/JAVA_HOME
uv run maf-harness run https://github.com/agoncal/agoncal-application-petstore-ee7 \
    --app-url http://127.0.0.1:8080/applicationPetstore --auto-approve --auto-heal 2
uv run maf-harness verify-run .harness_runs/<run>
# an existing --run-dir resumes at its pending gate (a run ended by a Stop decision cannot be resumed)
```

## 8. requirement-analysis-skill (stdlib-only Python, `scripts/engine/` package, no tests), Windows

Two real CLI runs (`maf-harness run <url> --auto-approve --auto-heal 2`, gateway model `deepseek-v4-flash-global`). Both were stopped by the operator, so neither is a completed run; what they showed is what counts.

| Run | Outcome | What it exposed |
|---|---|---|
| `ras-e2e` (before the fix) | stopped at an error gate in test generation, batch 2 of 12, after 22 provider calls / 438k tokens | every generated test imported `from engine.requirement_parser import ...` (the real module path is `scripts.engine.requirement_parser`). The import gate should have said so but accepted it: it substring-matched "engine" against the free text of `assets/requirements.txt` ("The engine is pure Python standard library"). The build gate caught it (`ModuleNotFoundError: No module named 'engine'`) only after three model attempts per batch. Defect H15, fixed in `tools/code_intel.py`. |
| `ras-e2e2` (with the fix) | reached the test-execution review: **FAIL, 16 passed / 3 failed of 19 cases**, 22 provider calls / 443k tokens; heal round 1 had started when the run was stopped | **no generation repair and no stage error**; the design stage needed two repairs (an actor without a case, then one uncovered requirement), as before. The 3 failures are test-authoring defects and a test asserting on a temp-path string, shown in the QA report with `file:line`. |

Environment on this run: venv created, `pytest` installed, `assets/requirements.txt` (comments only) handled; no toolchain download and no recovery were needed. Hooks were active (StageGate recorded 8 times, 0 denies).

Prompt cache on these runs (the report's *Prompt cache* table): provider-reported cache hit 27.2 % of input tokens in `ras-e2e` and 16.2 % in `ras-e2e2`; in `ras-e2e2` the generation stage repeated only **8.3 %** of each request's prefix (57.1 % in `ras-e2e`, where repair retries re-send nearly the same prompt). Reading: the stable-first ordering guarantees the schema and rules are in the prefix, but on a real repository the per-batch source and evidence are most of each prompt, so the saving from ordering alone is small. The fixture-repository figure in [../AUTOMATION.md](../AUTOMATION.md) (60.7 % to 82.2 %) does not carry over. Next lever: make the per-batch evidence and source smaller or shared across batches ([../IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) *Next* 5).
