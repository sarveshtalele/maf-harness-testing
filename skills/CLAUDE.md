# skills/ (Agent Skill packages, hash-locked)

One folder per skill, loaded by the MAF `SkillsProvider` or run as a deterministic host skill. Plugin format and trust model: [../docs/SKILL_PLUGINS.md](../docs/SKILL_PLUGINS.md). Loader code: [../src/maf_test_harness/skills/CLAUDE.md](../src/maf_test_harness/skills/CLAUDE.md).

## Layout of a skill
`SKILL.md` (frontmatter + instructions), `plugin.yaml` (stages, role, kind, purpose, applies_when, priority, entrypoint), optional `scripts/`, `assets/`, `references/`. `SKILLS.lock.json` stores sha256 per file, audit results and `reviewed_scripts` (tier `host` or `model`).

## Packages (stage / role / kind)
- Primaries: `reverse-engineering` (repo_analysis, host), `requirement-analysis` (requirements, host), `repository-requirements` (requirements, model), `test-planning`, `test-design-generator`, `generic-test-generator` (test_generation; per-language `references/<lang>.md`).
- Companions (model): `api-contract-analyzer`, `spec-authoring` (greenfield specs), `test-data-generator`, `junit-generator`, `playwright-test-generator`.
- Validators (`stages: []`, host): `traceability-generator`, `coverage-analyzer`; run by `skills/reports.py`, never attached to a stage.

## Invariants
- `name` == folder name; `description` <= 1024 chars and says when to use; `SKILL.md` <= 500 lines; shape Goal -> Inputs -> Steps -> Output -> Self-check; push detail into `references/`.
- Metadata goes in `plugin.yaml`, not SKILL.md frontmatter (MAF drops nested metadata). Unknown `plugin.yaml` keys are errors.
- Per stage: exactly one primary per kind among repo skills. A reviewed script must be pure (no network, no subprocess, no writes except stdout) before it is added to `REVIEWED_SCRIPTS` in `scripts/vendor_skills.py`.
- Language-specific rules live only in `LANGUAGE_RULES` (`workflow_support.py`) and `generic-test-generator/references/<lang>.md`; prompts stay language-neutral.
- After editing a locked file, refresh only that file's hash in `SKILLS.lock.json`; never hand-edit audit data.

## Test
```bash
uv run maf-harness lint-skills --strict      # CI gate: every repo skill locked, hashes match
uv run pytest -q tests/test_skills_registry.py tests/test_skill_plugins.py tests/test_skill_contracts.py
```

## Recipes
- New project skill: create the folder + `plugin.yaml` + `SKILL.md`, run `uv run python scripts/vendor_skills.py --lock-project` (adds lock entries for unlocked project-owned skills), then `lint-skills --strict`.
- Edit existing skill: change files, update the hash entry in `SKILLS.lock.json`, run lint, add the case to a skill test if behaviour changed.
- Do not put user/third-party skills here; they go in the plugin dir (`HARNESS_PLUGIN_DIR`) and need UI approval.
