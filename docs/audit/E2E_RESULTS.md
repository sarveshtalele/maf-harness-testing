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

## 2. Run matrix

| Run | Target | Highlights | Outcome |
|---|---|---|---|
| reverse-1 … 5 | reverse-engineering-skill | each exposed a harness defect (skill never loaded; newline-stripped source; wrong pytest rootdir; collection errors aborting the session; guessed imports) | fixed, see [ERRORS.md](ERRORS.md) A3, B7, B8, C4–C6 |
| reverse-7 | reverse-engineering-skill | 3 heal rounds | FAIL 27/31 passed |
| **reverse-8** | reverse-engineering-skill | full current flow: environment stage → design policy → run → heal ×2 → ZIP | **FAIL 27 passed / 4 failed / 2 not executed (33 cases)** |
| petstore-2 … 5 | agoncal-application-petstore-ee7 (Java EE 7, live app at `http://127.0.0.1:8080/applicationPetstore`) | exposed Java gaps: undeclared library, truncated files, semantic compile errors | fixed, see [ERRORS.md](ERRORS.md) B11, B12, C9, F5, F6, F12 |
| **petstore-6** | agoncal-application-petstore-ee7 | current flow incl. compile-error quarantine and Java gates | 45 tests executed (7 pass / 6 fail / 32 error); stopped at the 2M token limit during heal — see §4 |

## 3. reverse-engineering-skill — run `live-reverse-8` (current harness)

Commit analyzed: `e6139c314e3c`. 71 tracked files, 15 Python source files, no existing tests.

| Stage | Result |
|---|---|
| Requirements | 20 requirements, 21 acceptance criteria, 4 validation rules; actors: CLI user, Python API consumer, Claude Code user, GitHub Copilot Chat user; 5 environment needs (Python, Git, `anthropic`, …) with evidence files |
| Environment plan (deterministic) | ecosystem `python`; `create-venv` → `install-pytest` → `install-requirements`; prerequisite `python3` present. Installed only after the tool-call approval; status `ready` |
| Design | 33 cases: 25 unit, 7 cli, 1 integration; 85% requirement coverage. The two IDE-skill actors have no case; the gaps are stated explicitly (their behavior is a SKILL.md delegating to the CLI). Earlier runs without the policy: 54% coverage, no stated reasons |
| Generation | 33 files, one per case, all passed static validation (syntax, import resolution, no placeholders/skips) |
| Execution (pytest in `.harness-venv`) | attempt 1: 25 pass / 6 fail / 2 error → attempt 2: 27 pass / 4 fail / 2 error → attempt 3: 27 / 4 / 2 (two heal rounds, human gate each) |
| Verdict | **FAIL** — 27 passed, 4 failed, 2 not executed; requirements: 15 verified, 1 failing, 1 not executed, 3 untested |
| Cost | 39 provider calls, 1,072,478 reported tokens (limit 2,000,000) |
| Evidence of MAF + Skills | `maf-harness verify-run` → 14/14 PASS: `create_harness_agent`, `SkillsProvider`, `load_skill('<assigned>')` succeeded via FunctionMiddleware trace for all four stages, skill packages unchanged |

**Files-to-update section of the QA report (excerpt).** Four generated tests assert scores the application does not produce
(e.g. `Expected total_score >= 80, got 36`); the report lists each generated test file with the failing line and classifies the failure as
"check the application first, then the expectation":

| File | Line | Case | Class |
|---|---|---|---|
| `tests/batch_08/test_eval_score_85_returns_high_confidence.py` | 49 | Evaluation score 85 returns HIGH confidence | check_application_then_expectation |
| `tests/batch_08/test_eval_score_45_returns_low_confidence.py` | 49 | Evaluation score 45 returns LOW confidence | check_application_then_expectation |
| `tests/batch_08/test_eval_score_60_returns_medium_confidence.py` | 49 | Evaluation score 60 returns MEDIUM confidence | check_application_then_expectation |
| `tests/batch_09/test_eval_score_30_returns_very_low_confidence.py` | 44 | Evaluation score 30 returns VERY LOW confidence | check_application_then_expectation |

and the requirements that still need a passing test (`engine/analyzer.py`, `engine/pipeline.py`, `README.md`).

**Deliverable.** `deliverables/reverse-engineering-skill-with-tests-live-reverse-8.zip`: 122 entries — the complete repository, all 33 generated tests
under `tests/`, and `harness-report/` (QA report, requirements, environment, plan, design, generation catalog, three execution attempts,
audit trail, `RUN_TESTS.md`).

**What this run is and is not.** It shows the harness executing real generated tests and reporting honestly. It does not show that the
application is defective: the four failures may be wrong model expectations or real scoring differences; the report says which files to check.
Two defects found earlier in this target by hand-written acceptance tests remain documented in [ERRORS.md §E](ERRORS.md).

## 4. Petstore (Java EE 7) — run `live-petstore-6`

Target: `agoncal-application-petstore-ee7` at `725839cad3c5`, Maven/JDK 11, live baseline application on WildFly 26 at
`http://127.0.0.1:8080/applicationPetstore` (`--app-url`). The run **stopped because it reached the 2,000,000-token workflow limit
(1,952,602 used, 59 provider calls)** during the first heal round — the budget guard refused the next call with a clear message and no
partial state was lost. Petstore is a large target (30 requirements, 47 cases in 16 generation batches), so a full heal cycle does not fit in
2M tokens with this model; the remaining options are a stronger/cheaper model, a smaller design, or a new run. No ZIP is produced for a stopped run.

| Stage | Result |
|---|---|
| Requirements | 30 requirements; actors: API consumer, Customer, Administrator, system; environment needs: JDK, Maven, application server, JUnit 4, Arquillian, EqualsVerifier (with evidence files) |
| Environment | ecosystem `maven`; step `resolve-dependencies` (isolated Maven repository under the harness cache dir); status `ready` after approval |
| Design | 47 cases: 29 api, 17 unit, 1 integration; 66.7% requirement coverage with stated gaps. UI/E2E cases were allowed (live app URL supplied) but the model chose API-level cases |
| Generation | 16 batches. The Java gates rejected undeclared `RestAssured` imports, truncated files, and paths that already existed in the repository (each was a real failure of an earlier run — [ERRORS.md](ERRORS.md) B11, B12, F12); batches were repaired with the host error and accepted |
| Execution attempt 1 (`mvn test`) | 45 tests executed: **7 passed, 6 failed, 32 errors**; 2 uncompilable generated files were **quarantined** so the other 45 tests still ran (before the quarantine, one bad file made every case "not executed") |

What the failures say (from the QA report's diagnostics, grouped):

| Count | Evidence | Reading |
|---|---|---|
| 17 | `no Bean Validation provider could be found` | model-authored unit tests need a validation provider on the test classpath; the repo scopes it `provided` → test-defect, or add the dependency |
| 13 | `DeploymentScenario contains a target (_DEFAULT_) not matching any defined Container` | Arquillian integration tests were generated but no container is configured for `mvn test` → needs the container profile; not a test-logic defect |
| 3 | `expected:<201> but was:<405>` | live API returned 405 for a create call → check the endpoint mapping first, then the expectation |
| 2 | `Provider org.glassfish.json.JsonProviderImpl not found` | missing JSON-P implementation on the test classpath |

These are exactly the "files to update" cases the report is designed to surface. **This run therefore does not prove the Petstore
application correct or defective**; it proves the harness can plan, generate, compile-triage, execute and diagnose a Java EE repository end to end,
and that its cost guard works. The two defects known in this application (unverified password on login; see [ERRORS.md §E](ERRORS.md)) were
found earlier by hand-written acceptance tests, not by this run.


## 5. Reproduce

```bash
export OPENAI_API_KEY=… OPENAI_BASE_URL=… OPENAI_MODEL=deepseek-v4-flash-global
uv run maf-harness run https://github.com/sarveshtalele/reverse-engineering-skill --auto-approve --auto-heal 2
# Petstore: WildFly 26 with the WAR deployed, JDK 11 on PATH/JAVA_HOME
uv run maf-harness run https://github.com/agoncal/agoncal-application-petstore-ee7 \
    --app-url http://127.0.0.1:8080/applicationPetstore --auto-approve --auto-heal 3
uv run maf-harness verify-run .harness_runs/<run>
```
