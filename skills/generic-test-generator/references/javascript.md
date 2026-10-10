# JavaScript

- Runner: read `package.json` (`jest`, `vitest` or `mocha`, plus `scripts.test`) and existing tests; do not add another runner.
- Module system: `"type": "module"` or `.mjs` means ESM `import`; otherwise CommonJS `require`. Match the source under test and use the file extensions its imports need.
- Write the test inside the component that owns the `package.json`; keep relative imports to files proven by `source_files`.
- Use `test.each` for variants of one case. Await promises; no fixed sleeps; use fake timers for time.
- With vitest import `describe/it/expect` explicitly unless `globals` is enabled in its config.

## Integration tests

- Use `supertest` against the exported app (never `listen`); `msw`/`nock` only if declared in package.json. Mock only external network; call only routes in the source.

```js
const request = require('supertest');
test('lists items', async () => {
  const res = await request(createApp()).get('/items');
  expect(res.status).toBe(200);
});
```
