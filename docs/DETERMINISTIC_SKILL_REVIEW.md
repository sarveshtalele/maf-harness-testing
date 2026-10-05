# Deterministic repository analysis and run navigation

New runs use approved clone → static repository analysis → deterministic requirements → model-assisted planning, design and generation → approved execution setup → test execution. Target installation and runtime checks remain deferred until execution. Saved older runs retain their original mode and stage order.

## Reviewed sources

- [reverse-engineering-skill](https://github.com/sarveshtalele/reverse-engineering-skill), commit `e6139c314e3c89d2e84efe690a8bdfbce3dc17d2`.
- [requirement-analysis-skill](https://github.com/sarveshtalele/requirement-analysis-skill), commit `3eae93ae5b5f8d25acc7b0240bf64ee6f75a6f01`.

These repositories were inspected locally. The harness contains host-specific adaptations; this change does not modify the upstream repositories.

## Findings and adaptations

| Finding | Why it matters | Harness adaptation |
| --- | --- | --- |
| Reverse-engineering loader excludes directory names by substring and reads source without a host budget. | Legitimate folders can disappear; large clones consume excessive resources. | Exact generated-directory segments; contained regular files; 300 files, 256 KB/file and 4 MB total. Read/syntax failures become explicit gaps. |
| Regex declarations and architectural/domain heuristics are treated as inferred knowledge. | Symbol names do not establish implemented behavior. | Python AST declarations; other supported language candidates labeled heuristic. Source path, line, excerpt and hash retained. No inferred security or business guarantees. |
| Upstream pipeline owns clone/output lifecycle and offers optional model analysis. | Duplicates the host's approval and clone boundaries. | Analyze the already approved clone; no additional clone, target execution, installation or model call. |
| Requirement skill addresses feature-change impact rather than reconstructing existing requirements. | Change keywords and recommended locations are not source-proven product requirements. | Convert the deterministic callable inventory to source-linked review items within the existing RequirementsDraft contract. Leave unsupported contracts, acceptance criteria and environment claims empty. |
| Keyword-based priority and compliance checklists can sound definitive. | Negated text and generic controls can produce misleading conclusions. | Do not infer priority, compliance, status codes or validation rules. Record unknowns instead. User-pasted knowledge remains unverified context. |
| Skill metadata and installation assumptions differ from the harness. | Packages may appear usable without a compatible execution boundary. | Canonical SKILL.md packages, locked source provenance and file hashes, host-only entry points and catalog cards. |
| Stage indicators were display-only; old fetches could overwrite a newly selected run. | Completed analysis could not be revisited reliably during execution. | Clickable stage evidence with URL persistence; stale-fetch guard when changing runs. Failed execution evidence remains accessible. |
| Logs only listed execution-folder evidence and refreshed filters on every poll. | Early-stage and older-run audit/trace logs were hidden; search disappeared during execution. | Whitelist root audit.jsonl, trace.jsonl and workflow.log; preserve the selected file/filter while polling. Existing containment and credential redaction apply. |

## Implementation map

- `src/maf_test_harness/repo/reverse_engineering.py`: bounded static inventory.
- `src/maf_test_harness/repo/requirements.py`: deterministic requirements adapter.
- `workflow.py`, `workflow_steps/planning.py`: stage integration, provenance checks and audit events.
- `skills/reverse-engineering`, `skills/requirement-analysis`, `skills/SKILLS.lock.json`: reviewed packages and integrity lock.
- `ui/evidence.py`, `ui/evidence_api.py`: per-run audit and trace evidence.
- `frontend/components/workspace.tsx`, `reports.tsx`: stage navigation and stable log inspection.

## Practical limits

This is a deterministic source surface inventory, **not complete semantic reverse engineering**. Python public functions/methods use AST; JS/TS/Java/C#/Go/Kotlin declarations use limited regex candidates. Dynamic routes, indirect behavior and unsupported syntax need review. At most 30 declarations from files included in the host's bounded source evidence become requirements; repository analysis retains the larger inventory. Empty supported evidence blocks requirements rather than inventing them. Existing stack, route and dependency analysis remains available.

Planning, design, test generation and optional diagnosis still use the configured model. The default repository-analysis and requirements path makes zero model calls. Legacy saved runs default to their previous model mode; they are not rewritten. Approve source-linked requirements only after reviewing coverage gaps and the actual source.

## Verification

`tests/test_deterministic_analysis.py` checks stable facts, containment, comment/private-symbol handling, explicit unknowns, and a default workflow reaching requirements with zero runtime model calls and no target workspace. Backend regressions preserve legacy model-mode tests explicitly. Browser tests revisit repository analysis, reload its URL, inspect failed execution evidence, switch runs, and exercise reports/logs. CI covers Windows and macOS startup; scripts continue using portable pathlib/subprocess APIs.
