# Releasing

How a version moves from `develop` to a desktop installer. Branch rules are in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Release flow

1. Feature PRs merge into `develop`.
2. Cut `release/x.y` from `develop` when the scope is ready. Bump the version (see the rule below) on that branch.
3. QA the release branch: build installers from `release/x.y` and test them on the dev environment (`dev`). Fix blockers with PRs into `release/x.y`, and back-merge fixes to `develop`.
4. Open a PR from `release/x.y` to `main`. One approving review (CODEOWNERS) and green checks are required.
5. After merge, tag `main` as `vX.Y.Z`.
6. Pushing a tag that matches `v*.*.*` (not `v*-rc*`) starts the `desktop-release` workflow, which builds the installers and creates a **draft** GitHub Release.
7. Review the draft release notes and assets, then publish it manually.

`README.md` differs by branch on purpose. On `main` it is the user guide: install the desktop app from Releases and use it (generated from `docs/USER_GUIDE.md`, with links rewritten for the repository root). On `develop` it is the developer README. When `develop` or `release/x.y` merges into `main`, keep the `main` version (`git checkout --ours README.md` during the merge). Then copy over any user-guide changes from `docs/USER_GUIDE.md`.

## Versioning rule

These three values must be identical for a release:

- `pyproject.toml` `version`
- `frontend/package.json` `version`
- the git tag `vX.Y.Z` (tag without the `v` must equal both files)

Bump both files in the same commit on the release branch. Do not tag a commit where they differ. `python scripts/check_version.py` fails when the two files differ. It does not read `desktop/package.json`, so bump that file by hand.

## macOS unsigned builds

Current macOS builds are not signed or notarised. Gatekeeper will block the app with a "damaged" or "unidentified developer" message. After copying the app to Applications, run:

```bash
xattr -dr com.apple.quarantine "/Applications/Agentic QE.app"
```

Only do this for installers downloaded from the team's own draft or published releases.

## Owner settings checklist

These settings live in the GitHub UI and cannot be set from the repository. The repository owner must click through them once, and recheck after any org change.

**Repository**

- [ ] Settings > General > Default branch: set to `develop`.

**Rulesets** (Settings > Rules > Rulesets)

Create one ruleset each for `main` and `develop`:

- [ ] Target branches: `main` (and a second ruleset for `develop`).
- [ ] Require a pull request before merging.
- [ ] Required approvals: 1. Enable "Require review from Code Owners".
- [ ] Required status checks: `test`, `secrets-scan`, `local-ui`.
- [ ] Block force pushes.
- [ ] Block branch deletion.
- [ ] Require linear history.

**Environments** (Settings > Environments)

- [ ] `dev`: no required reviewers.
- [ ] `prod`: required reviewer (the owner). Deployment branches and tags: allow `main` and tags matching `v*` only.

**Security** (Settings > Advanced Security / Code security)

- [ ] Enable Dependabot alerts.
- [ ] Enable Dependabot security updates.
- [ ] Enable secret scanning (and push protection).

**Actions** (Settings > Actions > General)

- [ ] Workflow permissions: "Read repository contents and packages permissions" (read-only default).
- [ ] Do not allow workflows to create or approve pull requests unless a workflow needs it.
