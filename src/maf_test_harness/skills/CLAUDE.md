# skills/ (Python: registry, trust, host runner)

Code that loads, validates and runs the packages in repo-root `skills/` ([../../../skills/CLAUDE.md](../../../skills/CLAUDE.md)). Spec: [../../../docs/SKILL_PLUGINS.md](../../../docs/SKILL_PLUGINS.md).

## Key files
- `registry.py`: `lint_skill`, `lint_all`, `tree_sha256`, `load_lock`, `verify_lock`, `reviewed_scripts`, `require_valid`; plugin manifest: `load_plugin`, `discover_skills`, `lint_plugins`.
- `config.py`: `skills_for(stage, state)` -> `SkillSet` (primary + companions via `applies_when`); `plugins`, `purpose_of`.
- `trust.py`: verified vs unverified vs approved. `is_verified`, `is_approved`, `trust`, `approve`, `import_skill`, `full_tree_sha256`, `plugin_policy_problems`; approvals file from `approvals_path()`.
- `scripts.py`: `HarnessScriptRunner` (hash-checked script execution; `ScriptRefused`), `script_approval_rule`.
- `host.py` `execute_host_skill`: runs deterministic host skills and records hash/duration/output. `contracts.py`: adapters/finalizers (`finalize_design`, `finalize_discovered_requirements`, `resolve_id`, `expand_generated_lines`). `reports.py`: `build_traceability`, `build_coverage`, `run_skill_checks`.

## Invariants
- The lock (`skills/SKILLS.lock.json`) covers every file incl. `plugin.yaml`; CI runs `lint-skills --strict`.
- Plugins are companion-only, model kind, never override a repo primary, never import an entrypoint. Any file change un-approves.
- Approved plugin scripts run `-I -B --check-hash-based-pycs always`; no bytecode, no symlinks.

## Test
`uv run pytest -q tests/test_skill_plugins.py tests/test_skill_trust.py tests/test_skills_registry.py tests/test_skill_contracts.py` and `uv run maf-harness lint-skills --strict`.

## Recipe: trust API change
`ui/evidence_api.py` (`import_skill`, `approve_skill`, `skills`) -> `trust.py`; keep `POST /skills/import` and `/skills/<name>/approve` in `ui/api.py`, update docs/SKILL_PLUGINS.md.
