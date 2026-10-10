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

## Merge gate

Mirrors the merge gate in the root `CLAUDE.md`. Tick each item that ran, or that does not apply, and say which in the description.

- [ ] `uv run pytest -q --ignore=tests/targets` passes with 0 failures
- [ ] `uv run ruff check src tests scripts` passes
- [ ] `uv run ruff format --check src tests scripts` passes
- [ ] `uv run python scripts/gen_architecture.py --check` passes (`docs/ARCHITECTURE_MAP.md` is current)
- [ ] `uv run maf-harness lint-skills --strict` passes (skills or plugins changed)
- [ ] Frontend: `npm run typecheck` and `npm run build` pass (frontend changed)
- [ ] Playwright suite passes with `PYTHONPATH=<worktree>/src` and a dummy `OPENAI_API_KEY` (UI changed)
- [ ] Desktop: `npm test` in `desktop/` passes (desktop changed)
- [ ] Cross-OS considered: Windows, macOS and Linux paths, subprocesses, encoding
- [ ] Docs updated: the `CLAUDE.md` of each changed directory, plus `docs/` where behaviour changed
- [ ] No secrets, `.env`, run evidence or `agent-framework.pdf` committed
- [ ] Security-sensitive change (plugins, MCP, secrets, desktop) reviewed by Opus before merge
- [ ] `memory.md` BEFORE/AFTER entries added (significant changes)
