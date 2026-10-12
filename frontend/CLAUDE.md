# frontend/ (Next.js 16, React 19, Node 24)

Next.js App Router workspace over the local API. Node `>=24 <25` is enforced (`engines`, `.npmrc`, `.nvmrc` in this directory). Rules: [../CLAUDE.md](../CLAUDE.md).

## Key files
- `app/(workspace)/`: pages under a shared layout (`layout.tsx` renders `components/shell.tsx`). Run routes live in `app/(workspace)/runs/[id]/`; the run layout is `components/run-shell.tsx` (header, stage stepper, tabs, dialogs). Every route is a dynamic client page; no ISR or next/image.
- `app/page.tsx` redirects to `/runs`; `next.config.ts` `redirects()` maps old `?run=&tab=&stage=` and `?page=` URLs to the new routes.
- `app/globals.css`: tokens, then grouped blocks with comment headers (page template, tables, dialogs, run layout, sidebar links).
- `app/api/harness/[...path]/route.ts`: same-origin proxy to the Python API; requires the session cookie, checks Host/Origin and `Sec-Fetch-Site`, adds the bearer token, validates path segments, 2 MiB body limit (28 MB for `runs`, `skills/import`). The token never reaches the browser.
- `app/api/session/route.ts` + `lib/session.ts`: the launch secret (`HARNESS_LAUNCH_SECRET`) is exchanged once for an HttpOnly, SameSite=Strict `aqe_session_<port>` cookie valid 7 days. The cookie is HMAC-signed with the install key in `HARNESS_UI_STATE_DIR/ui-session-state.json` (0600), which also records the spent launch secret, so a restart neither replays a link nor logs anyone out. The 401 page (`unauthorizedPage`) explains a manual visit.
- `app/api/harness/report/[token]/[...path]/route.ts` + `lib/report-token.ts`: report files for one run, reached by the sandboxed report frame, which has no cookie. The token (`<expiry>.<mac>`, mac over the run id and expiry, 1 h) is in the path. Only GET of that run's evidence files is served. `app/api/report-token/[runId]/route.ts` gives a signed-in page the prefix (`ReportFrame` in `run-pages.tsx`). `lib/backend.ts` is the one relay to the Python API.
- `proxy.ts` + `lib/csp.ts`: gates every page without a session (401) and sets the per-request nonce CSP (scripts: nonce + `strict-dynamic`, no `unsafe-inline`). A nonce cannot live in `next.config.ts`, so CSP is set here in both launcher and desktop modes.
- `lib/harness.ts`: types (`Run`, `Pending`, `Config`), `api(path, body?)`, `label`, `download` URL builder.
- `components/page.tsx`: the page template. `DetailPage` (breadcrumbs, title, ID, status, meta, downloads, notices, lazy `Section`s with Expand/Collapse all), `Section`, `Fields` (replaces DataView), `Table` (the one table renderer), `StatusBadge`, `ConfirmDialog` (the only dialog for confirmations), `Exports`.
- `components/views/`: route content. `run-pages.tsx` (Review, Errors, Artifacts, stage and section pages, Tests, QA, step report, Test reports, Logs, Audit, record pages), `shared.tsx` (case table, report blocks, verdict banner, record lookup), `workspace-pages.tsx` (runs list, new run, settings).
- `components/data.tsx`: `usePreference` (hydration-safe), `ResizeHandle`, `useReturnFocus`, `Panel` (a card-style `Section`).
- `lib/report.ts`: parses saved step reports into structured blocks. `components/markdown.tsx` renders SKILL.md documents only.
- `components/observability.tsx`, `prompts.tsx`, `skills.tsx`, `model-settings.tsx`, `marketplace.tsx`, `mcp-servers.tsx` (MCP still uses its own dialogs; moving them to pages is a later task).
- `next.config.ts`: `DESKTOP_BUILD=1` switches to standalone output for Electron.
- `playwright.config.ts` + `tests/*.spec.ts`: browser journeys against `../tests/ui_fixture_server.py` (real workflow, scripted model) on port 8876, UI on 3100 (`HARNESS_UI_FIXTURE_PORT` / `HARNESS_UI_TEST_PORT` override them for parallel runs). `playwright.global-setup.ts` spends the launch secret once and saves `test-results/session.json`, which every spec loads; `tests/session-auth.spec.ts` covers the 401, cross-site, single-use and CSP rules.

## Invariants
- Call the API only through `api()` and `/api/harness/*`; never embed tokens or call 127.0.0.1:8765 from the browser. Every page and API call needs the session cookie: a test that builds its own browser or API context must start with empty storage state (`request.newContext` inherits the config's).
- Preferences persist in browser storage; wrap access defensively. Resizable panels need keyboard support (arrows, Home).
- Server and first client render must match: read URL, storage or `window` after mount (or via `useSyncExternalStore`), never in a `useState` initializer.
- Raw JSON and Markdown never render as `<pre>`; offer them as download links. Modals are `ConfirmDialog` only; a section's ⤢ opens its own page.
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

## Recipe: new run tab or page
Add the route folder under `app/(workspace)/runs/[id]/` (a thin `page.tsx` that renders a view from `components/views/`), add the tab to `RUN_TABS` in `components/run-shell.tsx`, build the view from `DetailPage`/`Section`/`Fields`/`Table`, fetch with `api('runs/${id}/...')`, and add a spec in `tests/` (the routes spec mocks a rich run).
