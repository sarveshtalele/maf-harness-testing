# frontend/ (Next.js 16, React 19, Node 24)

Single-page workspace over the local API. Node `>=24 <25` is enforced (`engines`, `.npmrc`, `.nvmrc` in this directory). Rules: [../CLAUDE.md](../CLAUDE.md).

## Key files
- `app/page.tsx`, `app/layout.tsx`, `app/globals.css`: shell and styles.
- `app/api/harness/[...path]/route.ts`: same-origin proxy to the Python API; adds the bearer token, checks Host/Origin, validates path segments, 2 MiB body limit (28 MB for `runs`, `skills/import`). The token never reaches the browser.
- `lib/harness.ts`: types (`Run`, `Pending`, `Config`), `api(path, body?)`, `label`, `download` URL builder.
- `components/workspace.tsx`: pages and the run `tabs` array (Review, Errors, Artifacts, Generated tests, QA report, Test reports, Logs, Observability, Audit, Prompts); `data.tsx` shared widgets (`Panel`, `DataTable`, `ResizeHandle`); `run-errors.tsx`, `observability.tsx`, `reports.tsx`, `skills.tsx`, `prompts.tsx`, `model-settings.tsx`, `step-reports.tsx`, `markdown.tsx`.
- `next.config.ts`: `DESKTOP_BUILD=1` switches to standalone output for Electron.
- `playwright.config.ts` + `tests/*.spec.ts`: browser journeys against `../tests/ui_fixture_server.py` (real workflow, scripted model) on port 8876, UI on 3100.

## Invariants
- Call the API only through `api()` and `/api/harness/*`; never embed tokens or call 127.0.0.1:8765 from the browser.
- Preferences persist in browser storage; wrap access defensively. Resizable panels need keyboard support (arrows, Home).
- Production build uses webpack: `npm run build` (`next build --webpack`).

## Commands
```bash
npm ci --include=dev
npm run typecheck
npm run build
npx playwright install chromium        # first time
PYTHONPATH=<repo>/src OPENAI_API_KEY=dummy npm test   # needs the repo .venv or HARNESS_TEST_PYTHON
```
Playwright's fixture server imports `maf_test_harness` and `tests/` helpers, hence `PYTHONPATH` and a dummy key.

## Recipe: new run tab / panel
Add the name to `tabs`, render it in the `tab === '...'` block in `workspace.tsx`, put the component in `components/`, fetch with `api('runs/${id}/...')`, add a fixture response in `tests/ui_fixture_server.py` if needed and a spec in `tests/`.
