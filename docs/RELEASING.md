# Releasing

How a version moves from `develop` to a desktop installer, as the repository is run today. Branch rules are in [CONTRIBUTING.md](../CONTRIBUTING.md).

Items marked **(paid plan)** need a GitHub plan with rulesets, required reviewers and secret scanning. The current plan answers those settings with HTTP 403, so they are not enforced. Recheck after any plan change.

## Release flow

1. Feature PRs merge into `develop`.
2. Bump the six version fields (see the versioning rule below) in one `chore(release): X.Y.Z` commit on `develop`, then run `uv run python scripts/check_version.py`.
3. Merge `develop` into `main`. Release branches (`release/x.y`) and `hotfix/*` branches have not been used; releases are made from `develop` only. Keep the `main` version of `README.md` on merge (`git checkout --ours README.md`, the user guide), then copy user-guide changes from `docs/USER_GUIDE.md`.
4. Tag the `main` commit `vX.Y.Z` and push the tag.
5. The `desktop-release` workflow then:
   - fails unless the tag commit is reachable from `origin/main` (`verify-main`);
   - runs the `ci` and `ui` workflows on the tag commit, as reusable workflows;
   - builds the installers on four runners, runs `check_version.py`, `maf-backend --self-check` and the packaged-app smoke test;
   - creates a **draft** GitHub Release with the installers, a `SHA256SUMS` file and generated release notes.
6. Check the draft: notes, asset list and checksums (`sha256sum -c SHA256SUMS` on the downloaded files). Publish it by hand.

**Release candidates** use a tag `vX.Y.Z-rcN` on any commit whose six version fields already say `X.Y.Z`. The `desktop-prerelease` workflow runs on that tag or on manual dispatch. It runs the same `ci`, `ui`, build and smoke steps, but it does not check that the commit is on `main` and it creates no release: the installers are workflow artifacts named `desktop-<os>`.

A prerelease built by manual dispatch carries the version currently in the files, so its MSI replaces an installed release of the same version on that machine. Test release candidates on a separate machine, or uninstall the release first.

## Versioning rule

These fields must hold the same version for a release, and the tag must be `vX.Y.Z` (or `vX.Y.Z-rcN`):

- `pyproject.toml`: `[project] version`
- `uv.lock`: the editable `maf-test-harness` entry (`uv lock` refreshes it)
- `frontend/package.json` and `frontend/package-lock.json`: `version` and `packages[""].version`
- `desktop/package.json` and `desktop/package-lock.json`: the same two fields

`npm version X.Y.Z --no-git-tag-version` in `frontend/` and `desktop/` updates both npm files. `scripts/check_version.py` checks all six files and the tag, and it runs in CI on every push, and in both desktop workflows before the build. Do not tag a commit where they differ.

## Rollback and downgrade

The Windows MSI blocks downgrades, so an older installer cannot replace a newer install. Uninstall the current version first, then install the older MSI. Runs, settings and the API key live in the user profile and survive the uninstall. For macOS and Linux, replace the app. Users should follow the same steps: [USER_GUIDE.md](USER_GUIDE.md), "Roll back to an older version".

## Packaged-app smoke test

Both desktop workflows launch each installer after the build (`scripts/desktop_smoke.py`, see [DESKTOP.md](DESKTOP.md#packaged-app-smoke-test)). A release is not built unless the packaged app starts, reaches its UI, quits, and leaves no backend or UI process behind. On failure, download the `smoke-desktop-<os>` artifact for the log output. Run it locally with `uv run python scripts/desktop_smoke.py` after building.

## macOS unsigned builds

Current macOS builds are not signed or notarised. Gatekeeper will block the app with a "damaged" or "unidentified developer" message. After copying the app to Applications, run:

```bash
xattr -dr com.apple.quarantine "/Applications/Agentic QE.app"
```

Only do this for installers downloaded from the team's own draft or published releases.

## Windows MSI

The Windows installer is an MSI built by electron-builder (WiX), per machine, into Program Files. Its `upgradeCode` in `desktop/electron-builder.yml` is fixed for the life of the product, so each new version upgrades the installed one in place. Never change it, or users get a second, parallel installation. The build is unsigned, so UAC shows "Unknown publisher". Users of the older per-user `.exe` build must uninstall it first, because the two install locations differ; their runs, settings and API key stay in the user profile. The release notes should say so and give the silent command `msiexec /i Agentic-QE-<version>-win-x64.msi /qn`. Close the app before a silent upgrade: the app does not handle Restart Manager, so a file still in use defers the change to a reboot (msiexec exit code 3010).

## Owner settings checklist

These settings live in the GitHub UI and cannot be set from the repository. The repository owner must click through them once, and recheck after any org change.

**Repository**

- [ ] Settings > General > Default branch: set to `develop`.

**Rulesets** (Settings > Rules > Rulesets) **(paid plan)**

Create one ruleset each for `main` and `develop`:

- [ ] Require a pull request before merging, with 1 approval and "Require review from Code Owners".
- [ ] Required status checks: `test (ubuntu-latest, 3.11)`, `test (ubuntu-latest, 3.12)`, `test (windows-latest, 3.12)`, `test (macos-latest, 3.12)`, `secrets-scan`, `local-ui (macos-latest)`, `local-ui (windows-latest)`, `local-ui (ubuntu-latest)`. Check the exact names on a recent PR first; matrix jobs carry their matrix values in the name.
- [ ] Block force pushes, block branch deletion, require linear history.

**Environments** (Settings > Environments)

- [ ] `dev`: no required reviewers.
- [ ] `prod` **(paid plan for required reviewers)**: required reviewer (the owner). Deployment branches and tags: allow `main` and tags matching `v*` only.

**Security** (Settings > Advanced Security / Code security)

- [ ] Enable Dependabot alerts and Dependabot security updates.
- [ ] Enable secret scanning and push protection **(paid plan)**. The `secrets-scan` job in `ci.yml` runs a pattern scan in the meantime.

**Actions** (Settings > Actions > General)

- [ ] Workflow permissions: "Read repository contents and packages permissions" (read-only default).
- [ ] Do not allow workflows to create or approve pull requests unless a workflow needs it.
