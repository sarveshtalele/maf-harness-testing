# Strict generation and reviewer verification — October 6, 2026

Branch: `new-ui`. Product: Agentic QE Platform. This report separates harness regression checks, focused live evidence and unfinished full-repository cycles.

## Changes and causes

- Requirements previously retained only 30 declaration facts and further filtered them through sampled model excerpts. The deterministic inventory now retains every discovered declaration, with bounded component batches for validation and design.
- Unexplained design omissions remained acceptable on the last repair attempt. They now stay blocked; every requirement needs a case or an explicit exclusion.
- Java constructor-only checks, invalid positive `@Size` literals and exact duplicate existing test bodies could look like broader coverage. Conservative shared source gates now reject these patterns; they are static witnesses, not complete Java semantic analysis.
- Maven generated-only execution hid baseline regression scope. Unit/integration baselines are separate approved commands and QA scopes. Missing fresh evidence for any expected baseline class remains inconclusive; runtime line/branch/mutation coverage is unmeasured.
- In `mcp-skills-registry`, `skills/text-statistics/scripts/main.py` exists; `skills.text_statistics.scripts.main` does not. Shared import validation now supplies verified path guidance without inventing aliases or changing the target repository.
- Generated cases receive an independent reviewer persona, full recorded prompts, source citations and descending confidence judgments. Invalid reviewer schemas/IDs/citations receive reviewer-only repair. Rejected cases remain visible. Scores do not establish execution success.
- Failed-case healing now supplies the exact previous failed generated source alongside execution differences, rather than asking the author to reconstruct the case. Passing files and accepted reviews remain unchanged.
- Active Stop run cancels model work and signals owned clone/setup/test processes; saved analysis and logs survive reload.

## Local verification

Host: macOS arm64; Python 3.12; Node.js 24.21.0; Next.js 16.3.8.

| Check | Result |
|---|---|
| Complete backend suite, `pytest tests --ignore=tests/targets -q` | 795 passed, 1 skipped |
| Focused source-aware healing/reviewer/prompt checks | 36 passed |
| Chromium workflow journeys | 9 passed |
| TypeScript and optimized production build | Passed |
| Ruff lint/format and diff whitespace | Passed |
| Skill registry/lock validation | All 12 packages and lock passed |
| Restarted UI and authenticated backend proxy | HTTP 200, expected branding/configuration |

The browser suite covers workflow approvals, historical evidence, editable prompts, confidence tables, dark-mode contrast, QA scope, reports/downloads, responsive layouts, mouse resizing, run deletion, clone progress, active Stop and saved analysis after stopping. HTML/JUnit reports and screenshots are generated under `frontend/playwright-report` and `frontend/test-results`. The browser workflow uses a scripted model transport and tiny repository fixture; it does not qualify the live registry or Petstore application.

## Live registry reproduction

Target commit: `8326a52cdd36fa350f97dc4f35ca3ee9854efebb`.

The focused reproduction used the real configured gateway, generation/static gates, separate reviewer, actual source and the shared failed-case healing method. It installed pytest into a separate execution environment only after generation. The first import resolved, but execution failed because the model asserted character count 30 for a 28-character string. A reviewer assigned 95 despite that error. Both failure and judgment were retained; no PASS was fabricated.

Reviewer citation repair and source-aware healing were then exercised. The final focused attempt executed **1 test, 1 passed**, retaining all five metric assertions and the actual `run(inputs)` call. Its generated-case verdict is PASS, while its overall QA verdict remains **INCONCLUSIVE** because the remaining repository requirements were not exercised by this focused proof. Confidence, including the final reviewer score of 100, remains an uncalibrated judgment.

Local retained evidence:

- `.maf-harness/runs/registry-import-probe-20261006/`: initial generated source, review and failed JUnit/logs.
- `.maf-harness/runs/registry-import-healed-20261006/`: intermediate failed repair evidence.
- `.maf-harness/runs/registry-import-verified-20261006/`: passing focused test, JUnit, QA JSON/Markdown and review prompts.
- `.maf-harness/runs/registry-strict-20261006/`: separate full-repository cycle, 191 discovered declaration requirements. This larger cycle is continuing through the UI API using the final code and retained component batches; this report does not claim completion or full application coverage.

## Petstore and platform limits

The strict Petstore cycle retained 463 declaration candidates from 68 production Java files. Its gateway returned HTTP 499 and timed out during design; full runtime qualification was not completed. Its managed WildFly profile also has a repository unpack-directory/home mismatch, which the harness reports rather than silently editing the target POM.

The new code uses the existing native Windows/macOS/Linux process and path helpers. Local testing here is macOS. The repository's compatibility workflow runs Windows, macOS and Ubuntu, Node 24, launcher smoke checks, real public GitHub clones and browser journeys; consult the specific GitHub Actions result before claiming native compatibility for a release. Enterprise proxies, endpoint protection and target-specific prerequisites still require their actual logs/configuration.

## Markdown documentation audit

Active setup, workflow, catalog, observability, troubleshooting and environment guides were aligned with source-first intake, late runtime setup, strict generation, scoped QA, reviewer confidence and active stopping. Contributor notes were updated. Retired migration/backend/dead-code audits are explicitly historical and link to current behavior; their original findings/measurements were preserved. JUnit skill instructions no longer ask for TODO/scaffold assertions or force JUnit 5. Vendored resource examples and Azure proposal material remain reference material, not new platform setup or UI features. No existing project file was deleted.
