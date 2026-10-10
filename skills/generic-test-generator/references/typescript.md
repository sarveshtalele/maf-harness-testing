# TypeScript

- Runner: read `package.json` (`jest` with ts-jest/babel, `vitest`, or `mocha`) and the nearest `tsconfig.json`; follow existing tests.
- Import only exports that exist in the source; respect `module`/`moduleResolution` (ESM needs the extension style the repository already uses).
- Keep types honest: do not cast with `any` to reach private members; use the public API or the repository's existing fixtures.
- Use `test.each` for variants of one case. Await promises; no fixed sleeps.
- Browser cases use the repository's Playwright/Cypress setup and read the target from `BASE_URL`.

## Integration tests

- Use `supertest` against the exported app (never `listen`); `msw`/`nock` only if declared in package.json. Mock only external network; call only routes in the source.

```ts
import request from 'supertest';
test('lists items', async () => {
  const res = await request(createApp()).get('/items');
  expect(res.status).toBe(200);
});
```
