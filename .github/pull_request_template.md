## Summary

<!-- What changed and why. 2-4 sentences. -->

## Linked issue

<!-- e.g. Closes #123. Write "None" if there is no issue. -->

## Branch and type

- Branch name: `<type>/<component>-<short-topic>` (see the [guide, section 2](../docs/DEVELOPER_GUIDE.md#2-branching-model))
- Commit type (Conventional Commits, [guide, section 3](../docs/DEVELOPER_GUIDE.md#3-commits)):
  - [ ] feat
  - [ ] fix
  - [ ] refactor
  - [ ] test
  - [ ] docs
  - [ ] build
  - [ ] ci
  - [ ] chore
  - [ ] style

## Merge gate

- [ ] `uv run python scripts/check_all.py` passes. If you skipped a group with `--skip-ui` or `--skip-desktop`, say why here.
- [ ] Each commit is one logical change, and the AI-assisted commits carry a `Co-Authored-By:` trailer.

## Review checklist

Reviewers check each item. [Guide, section 8.1](../docs/DEVELOPER_GUIDE.md#81-review-checklist) explains them.

- [ ] **Correctness:** the change does what this PR says, including the edge cases. Every caller of a changed function still works.
- [ ] **Security:** no secret, `.env`, run evidence or PDF is committed. Untrusted input is treated as data. No `shell=True`. Redaction and approval gates are unchanged or stronger. No new allow-list entries without a reason. Security-sensitive changes (plugins, MCP, secrets, desktop, skills) have the owner's review.
- [ ] **Cross-OS:** `pathlib` and UTF-8 are used. Subprocesses go through `run_command` or `run_cancellable`. No `sys.executable` is used as an interpreter. OS branches go through `envs/platform_compat.py`. For changes to file or process code, describe how the Windows path was checked.
- [ ] **Accessibility and UI:** pages use the page template, confirmations use `ConfirmDialog`, no raw JSON or Markdown appears in the UI, new controls have keyboard support, and the layout has no overflow at 390 px.
- [ ] **Tests:** new logic has a test that fails without the change. Tests use fakes and `tmp_path`, with no network and no real model.
- [ ] **Docs:** the `CLAUDE.md` of each changed area, plus `docs/` where behaviour changed. The architecture map is regenerated if modules moved.

## Memory log

- [ ] This is a significant change (a new stage, a new skill, an architecture change, a release or a non-trivial bug fix). The orchestrator logs it in `memory.md`. Do not edit `memory.md` in this PR.
