# Artifact contract

All files live under one run directory. Write JSON as UTF-8. Keep source text out of shell arguments and logs. `scripts/qe.py` validates links and gates; agents must not weaken its checks.

| File | Required shape |
| --- | --- |
| `sources.json` | `sources` with IDs and SHA256; `sections` with `source_id`, `locator`, `text`; `warnings` |
| `scope.json` | Brownfield only; captured by `qe.py scope`, then populated with approved story-to-changed-path impact entries |
| `stories.json` | `{"stories":[...]}`; each story has `id` (`STY-*`), `user_story`, `source_refs`, and nonempty `acceptance_criteria` |
| `requirements.json` | `{"requirements":[...]}`; each `FR-*` or `TR-*` has `type`, `description`, `story_ids`, `control_ids`, `source_refs` |
| `plan.json` | `{"items":[...]}`; each `PLN-*` has `objective`, `requirement_ids`, `control_ids`, `source_refs` |
| `cases.json` | `{"cases":[...]}`; each `TC-*` has `level:"e2e"`, `title`, `steps`, `expected_outcome`, `requirement_ids`, `control_ids`, `plan_ids`, `source_refs` |
| `reviews.json` | `{"reviews":[...]}`; independent records per story, requirement, plan item, case |

For brownfield, `scope.json.related_files` may contain an additional repository-relative path only with a specific reason and SHA256 of the current file: `{"path":"src/auth/helper.py","reason":"Imported by changed sign-in route","sha256":"..."}`. Changed paths remain the primary design scope; the helper verifies related file hashes before approval and review.

Brownfield `scope.json.impacted_stories` must contain at least one entry: `{"story_id":"STY-...","changed_paths":["src/auth/route.py"],"reason":"Changed route implements the approved sign-in flow"}`. Use `qe.py impact` to record it. Each story ID must occur in approved `stories.json`; each path must occur in the captured Git diff. Unmapped stories remain source context and are excluded from requirements, cases, independent review and final report. If the mapping or diff changes, scope approval becomes stale.

A `source_ref` is exactly locatable evidence: `{"source_id":"DOC-...","locator":"line:12","quote":"exact substring of that section"}`. Jira uses locator `description`. Each acceptance criterion has `id` (`AC-*`), `control_id` (`CTL-*`), `text`, and its own `source_refs`. Use `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/qe.py" id --kind STY --source SOURCE_ID --claim 'concise claim'` for stable IDs; repeat for `AC`, `CTL`, `FR`, `TR`, `PLN`, `TC`.

The story sentence must follow `As a [role], I want to [action], so that [benefit]`. A BRD-derived story is a proposed interpretation of cited text; mark uncertainty in an `open_questions` array and obtain story approval before deriving requirements. Do not invent unsupported acceptance criteria or promise implementation behavior that sources do not support. Keep functional behavior under `FR-*` and technical constraints under `TR-*`; both feed E2E cases.

Review records have `subject_id`, `subject_sha256`, `round` (0, 1 or 2), `scores` with `source_support` (0–40), `traceability` (0–30), `e2e_quality` (0–20), `clarity` (0–10), `verdict` (`approved`, `revise`, `reject`), `reason`, `reviewer`, `at`. Get current hashes from `qe.py subject-hashes --run RUN`. The total is an evidence rubric out of 100, not a probability of correctness. Never award points without checking the referenced source and linked artifacts. For a score below 95, record the finding and ask for explicit human approval of a named repair before `approve-repair`. Revise only that subject and its affected downstream links, then independently review the new hash. The two-round limit is enforced by the CLI.

Before final approval, run `qe.py validate --run RUN`, inspect the readable report and matrix, then run `qe.py generate --run RUN`. A changed approved artifact invalidates its approval hash; repeat the relevant gate. Do not use stale review records after edits.

Each run also contains `memory.md`. The helper records BEFORE/AFTER pairs for CLI state changes without copying source text or credentials. Before and after a model decision or direct artifact edit, call `qe.py note --run RUN --stage STAGE --phase before|after --summary 'brief reason/result'`. Summaries are 2–240 characters and must not include secrets, personal data or pasted document text. The dashboard shows recent entries; export includes the complete log.
