---
name: reverse-engineering
description: Inspect an approved Git change set with bounded static evidence for brownfield E2E design.
---

# Reverse engineering for change-scoped QE

Adapted from the MAF `skills/reverse-engineering` package. Use only after `qe.py scope` records the user's exact repository, base/target or working-tree choice. Read changed regular files and only a directly relevant dependency outside the diff. Add each dependency to `scope.json.related_files` with its path, SHA256 and the reason it is needed.

1. Preserve original path, line number, change status and content hash for each observation. Compare renamed and deleted paths explicitly. Treat a changed declaration as a candidate effect until its body or a caller shows behavior.
2. For Python, inspect syntax and call sites; for other languages, label heuristic symbol matches as candidates. Check routes, public interfaces, feature flags and configuration only where changed files reach them.
3. Respect the captured file and byte limits. Do not follow symlinks, inspect generated or dependency trees, clone another repository, install packages or execute target code.
4. Put parse failures, skipped files, unsupported languages and unknown runtime effects in the scope gaps. A symbol name or README assertion is not proof of a business rule.

Output a short changed-path inventory, justified related files, evidence-backed effects and unresolved questions. The approved scope is the boundary for downstream requirements and cases.
