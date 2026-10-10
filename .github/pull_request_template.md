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

## Checklist

- [ ] `uv run ruff check` and `uv run ruff format --check` pass
- [ ] `uv run pytest -q --ignore=tests/targets` passes
- [ ] `npm run typecheck` passes (frontend changes)
- [ ] Playwright suite run, if UI changed
- [ ] `uv run maf-harness lint-skills` passes (skills changed, or not applicable)
- [ ] `python scripts/gen_architecture.py --check` passes
- [ ] Cross-OS considered (Windows, macOS, Linux paths, subprocesses, encoding)
- [ ] `memory.md` "Core memory log" entry added (significant changes)
- [ ] No secrets, `.env`, run evidence or `agent-framework.pdf` committed
