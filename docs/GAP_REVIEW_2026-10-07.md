# Harness gap review — 2026-10-07

This review was made by the Opus orchestrator after waves A–H. It is based on the code on `new-ui` and on evidence from run
`add278f4c106` (mcp-skills-registry, 191 requirements, 420 design cases).

## Fixed in this cycle

| # | Area | Error or gap (evidence) | Fix | Where |
|---|---|---|---|---|
| 1 | UI proxy | The page showed "This page couldn't load". Node 24 undici raised `assert(!this.paused)` while streaming the backend's HTTP/1.0 body. | The proxy reply is now buffered before it is returned. | `frontend/app/api/harness/[...path]/route.ts` |
| 2 | Launcher | A crashed UI from an older checkout on port 3000 was reused. | The launcher compares a `HARNESS_BUILD_ID` fingerprint and never reuses a stale build. | `scripts/start_ui.py`, `ui/settings_api.py` |
| 3 | Clone | Global `init.templateDir` hooks were copied into the target clone. | The clone runs with `--template=`. | `repo/analyzer.py` |
| 4 | Design | Path slashes counted as arithmetic and caused false retargets (`main` became `create_skill`). | Strict arithmetic cue. | `repo/testability.py` |
| 5 | Design and generation | Tests targeted unexported symbols (`mcpUrl`, `CLIENTS`) or copied the source into the test. | `non_public_targets` is fed to both prompts, plus a rule against copying code and a remedy. | `repo/testability.py`, `generation.py`, `qa/triage.py` |
| 6 | Generation | The model imported `skills.change_impact_analysis…`, a hyphenated directory, and failed 3 times. | `import_recipes` (sys.path root plus import line) is derived from the application's own `sys.path` inserts or from the repo's tests. Validator messages quote the recipe. | `repo/import_recipes.py`, `skills/contracts.py` |
| 7 | Generation | Retries at temperature 0.6 leaked JSON punctuation into `lines` (`unmatched ']'`). | Retry temperature is capped at 0.4, and a remedy covers invalid generated source. | `generation.py`, `qa/triage.py` |
| 8 | Tokens | The design prompt prefix broke at 2.7k characters because the schema embedded a per-batch ID enum. Each call also carried 38k characters of evidence. | The enum is removed from the prompt, sections are ordered stable-first, and design evidence is capped at 14k. Measured on a fixture, the shared prefix grew from 2.7k to 5.2k characters. | `planning.py`, `llm/prompts.py` |
| 9 | Tokens | Identical temperature-0 calls were billed again on rerun or resume. | A validated response cache is stored in `.maf-harness/cache/responses`, and each hit writes a `cache_hit` trace event. | `agent/runtime.py` |
| 10 | Progress | The UI showed only elapsed time, and the design stage looked stuck after 365 s. | Every stage now has a predicted and remaining time, computed as units × seconds per unit, using measured values, then history, then defaults. Gate waits are excluded. The run shows a finish estimate, and interrupted runs report busy time. | `ui/eta.py`, `workspace.tsx` |
| 11 | Validation | Design cases had no independent check. | A Chief Principal Architect reviewer, with expertise blocks for the detected stack, gives each case a confidence score and a verdict. A case rated below 60 gets one repair. | `workflow_steps/design_review.py`, `agent/personas.py` |
| 12 | New run | The optional fields had to be filled in by hand. | `/repo-preview` uses the read-only GitHub API (no clone) to fill the component, app URL, start-app option and repository knowledge. | `repo/preview.py`, `ui/api.py`, `workspace.tsx` |
| 13 | Docs | There was no current architecture map. | `scripts/gen_architecture.py` writes `docs/ARCHITECTURE_MAP.md` (mermaid diagrams plus a file table). The `docs-maintainer` agent keeps docs in sync, and a `--check` test guards the map. | `scripts/`, `docs/`, `.claude/agents/` |
| 14 | Launcher text | The Node 16 and Next.js version wording confused users. | Removed. | `scripts/start_ui.py`, docs |

## Open gaps (recommended next)

| # | Severity | Gap | Evidence | Recommendation |
|---|---|---|---|---|
| O1 | High | Design volume. 191 requirements produced 420 cases and 140 generation batches. At the measured 140 s per batch that is about 5.4 h. | ETA for `add278f4c106` | Add a case budget per requirement (for example ≤2 for each non-critical requirement). Prioritise P0/P1. Raise the batch size for small cases. |
| O2 | High | Live end-to-end runs were not run in this sandbox, because `.git/config` writes and port binding are blocked. | E2E attempts | Run mcp-skills-registry, mcp-server-market-v2, store-pos and petstore locally, then review `.maf-harness/runs/<id>`. |
| O3 | Fixed | `run.json` stayed `running` after an API restart. | `add278f4c106` | The API now reads a `running`/`stopping` run with no live job as `interrupted` (`ui/api.py`), so the UI offers Rerun. |
| O4 | Medium | The design reviewer adds one model call per design batch, about 37 for this repo, and re-primes the `test_design` agent. | design_review | Review only cases with low static confidence, or review several batches together. Make the review budget configurable. |
| O5 | Medium | Design-review case IDs are applied by ID after the final merge. A title-derived ID may differ, which leaves a case unmarked. | design_review report | Key reviews by a stable case hash that survives the merge. |
| O6 | Low | `run_tool_agent` and the generation reviewer are not response-cached. | agent/runtime | Acceptable: tool outputs are live state. Revisit only if the reviewer cost matters. |
| O7 | Fixed | `repo/analyzer.py` had no module docstring. | ARCHITECTURE_MAP | A docstring was added and the map regenerated. |
| O8 | Low | The Playwright and launcher smoke tests did not run here (no sockets). | sandbox | CI covers ubuntu, windows and macos. |
