# Skill plugins

A skill is a folder with `SKILL.md`, optional `scripts/` and `assets/`, and a `plugin.yaml` manifest. Dropping the
folder into `skills/` (or into the user plugin dir `HARNESS_PLUGIN_DIR`) registers it in the workflow with no code
change. Metadata lives in `plugin.yaml`, not in `SKILL.md`, because MAF drops nested frontmatter `metadata` values.

```yaml
name: junit-generator            # must equal the folder name
stages: [test_generation]        # repo_analysis|requirements|test_planning|test_design|test_generation|environment_setup|test_execution|qa
role: companion                  # primary | companion
kind: model                      # model (loaded by the agent) | host (deterministic, no model calls)
purpose: "follow JUnit conventions (naming, assertions, fixtures) for Java/Kotlin tests"  # one line shown to the model
applies_when:                    # companions only, all optional; AND across keys, OR within a list
  languages: [Java, Kotlin]      #   languages from repo_analysis
  frameworks: []                 #   frameworks from repo_analysis / stack
  levels: []                     #   design levels, e.g. ui, e2e, accessibility
  files_glob: []                 #   globs over repo source/test files, e.g. "**/openapi*.{yml,yaml,json}"
  stack_flags: []                #   truthy keys of the stack profile, e.g. openapi_files
entrypoint: pkg.module:function  # host kind only; must be importable
priority: 20                     # int tie-break; higher wins (primaries) / listed first (companions)
```

Rules: unknown keys are errors; a stage has exactly one primary per kind (`model`, `host`) among repo skills;
a host primary is chosen for `repo_analysis` and for deterministic `requirements`; a skill with `stages: []`
is a host report validator that is never attached to a stage. Run `maf-harness lint-skills` to validate.
Repository skills stay hash-locked in `skills/SKILLS.lock.json` (the lock covers `plugin.yaml` too).

## Trust: verified, unverified, approved

- **Verified**: a repository `skills/` folder with an entry in `skills/SKILLS.lock.json` whose files still match.
  Add entries for reviewed project skills with `python scripts/vendor_skills.py --lock-project`.
  `maf-harness lint-skills --strict` (CI) fails for any repository folder without an entry.
- **Unverified**: every other skill, including everything in the plugin dir (`HARNESS_PLUGIN_DIR`, default
  `<repo>/.maf-harness/plugins`). It is not selected, not loaded (runtime logs `skill <name> skipped: unverified`) and its
  scripts never run.
- **Approved**: a user action (UI "Approve", `POST /skills/{name}/approve`) records `{name, sha256 of all files,
  approved_at}` in `HARNESS_PLUGIN_APPROVALS` (default `<repo>/.maf-harness/plugins.lock.json`). Approval enables
  selection, loading and `scripts/` (model tier, through the hash-checking script runner). Any file change makes the hash
  differ and the skill is unverified again. Packages with symlinks cannot be approved.
- **Import**: `POST /skills/import` with `{zip_base64}` or `{path}` validates `plugin.yaml` and `SKILL.md`, rejects path
  traversal, symlinks, host kind/entrypoints and archives over 20 MB or 500 files, then copies into the plugin dir (unverified).
