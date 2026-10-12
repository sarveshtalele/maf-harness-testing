## Summary

<!-- What changed and why. 2-4 sentences. -->

## Linked issue

<!-- e.g. Closes #123. Write "None" if there is no issue. -->

## Type

- [ ] feat
- [ ] fix
- [ ] docs
- [ ] chore
- [ ] test
- [ ] refactor
- [ ] ci
- [ ] build
- [ ] style

## Merge gate

Contributors tick the items relevant to the change; the orchestrator runs the full merge gate before merge.

- [ ] `uv run python scripts/check_all.py` passes (ruff, architecture map, version files, skills lint, pytest, frontend typecheck/build/Playwright, desktop tests). If you skipped a group with `--skip-ui` or `--skip-desktop`, say why.
- [ ] Cross-OS considered: Windows, macOS and Linux paths, subprocesses, encoding
- [ ] Docs updated: the `CLAUDE.md` of each changed directory, plus `docs/` where behaviour changed
- [ ] No secrets, `.env`, run evidence or `agent-framework.pdf` committed
- [ ] Security-sensitive change (plugins, MCP, secrets, desktop) reviewed by Opus before merge
- [ ] Significant change: say so in the description so the orchestrator logs BEFORE/AFTER in `memory.md` (do not edit it)
