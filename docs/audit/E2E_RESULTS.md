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
two defects found earlier by hand-written acceptance tests are in [ERRORS.md §E](ERRORS.md).

## 5. Daily token accounting

The ledger (`~/.maf-harness/usage`) counted the three runs of this session after it was introduced: 3,128,180 of the 5,000,000-token daily cap after
`final3-reverse` (stopped, 712,805), `final3-petstore` (1,434,870) and `final4-reverse` (980,505). Earlier runs of the day predate the ledger and are not counted.

## 6. Reproduce

```bash
export OPENAI_API_KEY=… OPENAI_BASE_URL=… OPENAI_MODEL=deepseek-v4-flash-global
uv run maf-harness run https://github.com/sarveshtalele/reverse-engineering-skill --auto-approve --auto-heal 2
# Petstore: WildFly 26 with the WAR deployed, JDK 11 on PATH/JAVA_HOME
uv run maf-harness run https://github.com/agoncal/agoncal-application-petstore-ee7 \
    --app-url http://127.0.0.1:8080/applicationPetstore --auto-approve --auto-heal 2
uv run maf-harness verify-run .harness_runs/<run>
```
