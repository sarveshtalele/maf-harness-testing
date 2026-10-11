import { test, expect } from '@playwright/test';

const KEY = 'fake-playwright-0123456789abcdef';

test('model & API key panel saves, tests and never shows the key again', async ({ page }) => {
  let saved: Record<string, unknown> = {};
  let view = { base_url: '', model: 'm0', stage_models: {}, key_configured: false, key_hint: '', source: 'none' };
  await page.route('**/api/harness/settings/model', async route => {
    if (route.request().method() === 'POST') {
      saved = route.request().postDataJSON();
      view = { ...view, base_url: String(saved.base_url), model: String(saved.model), key_configured: true, key_hint: KEY.slice(-4), source: 'runtime' };
    }
    await route.fulfill({ json: view });
  });
  await page.route('**/api/harness/settings/model/test', route => route.fulfill({ json: { ok: true, message: 'Connection works.' } }));
  await page.goto('/?page=configuration');
  const title = page.getByRole('button', { name: 'Model & API key', exact: true });
  await expect(title).toBeVisible(); // panel titles are buttons
  await expect(page.getByText('No API key configured yet.')).toBeVisible();
  await page.getByLabel('Provider preset').selectOption('Azure OpenAI / LiteLLM gateway');
  await page.getByLabel('Base URL').fill('https://gw.example.com/v1');
  await page.getByLabel('Model', { exact: true }).fill('gpt-x');
  const field = page.getByLabel('API key', { exact: true });
  await expect(field).toHaveAttribute('type', 'password');
  await field.fill(KEY);
  await page.getByLabel('Show key while typing').check();
  await expect(field).toHaveAttribute('type', 'text');
  await page.getByLabel('Save to .env (developer)').check();
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible();
  expect(saved).toMatchObject({ api_key: KEY, model: 'gpt-x', base_url: 'https://gw.example.com/v1', save_to_env: true });
  await expect(field).toHaveValue('');
  await expect(page.getByText(KEY)).toHaveCount(0);
  await expect(page.getByText(KEY.slice(-4), { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Test connection' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Connection works.' })).toBeVisible();
  expect(await page.content()).not.toContain(KEY);
});

test('model test uses typed values and shows friendly errors', async ({ page }) => {
  const tests: Record<string, unknown>[] = [];
  const view = { base_url: 'https://saved.example.com/v1', model: 'saved-model', stage_models: {}, key_configured: true, key_hint: 'saved', source: 'runtime' };
  await page.route('**/api/harness/settings/model', route => route.fulfill({ json: view }));
  await page.route('**/api/harness/settings/model/test', route => {
    tests.push(route.request().postDataJSON());
    return route.fulfill({ json: { ok: false, message: 'The API key was rejected by the gateway.', detail: "AuthenticationError: Error code: 401 - {'error': {'message': 'invalid key'}}" } });
  });
  await page.goto('/?page=configuration');
  await page.getByLabel('Base URL').fill('https://typed.example.com/v1');
  await page.getByLabel('Model', { exact: true }).fill('typed-model');
  await page.getByRole('button', { name: 'Test connection' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'The API key was rejected by the gateway.' })).toBeVisible();
  // no key typed: the request carries the typed URL and model only, so the saved key is used
  expect(tests[0]).toEqual({ base_url: 'https://typed.example.com/v1', model: 'typed-model' });
  await page.getByLabel('API key', { exact: true }).fill(KEY);
  await page.getByRole('button', { name: 'Test connection' }).click();
  await expect.poll(() => tests.length).toBe(2);
  expect(tests[1]).toEqual({ base_url: 'https://typed.example.com/v1', model: 'typed-model', api_key: KEY });
  await page.getByRole('status').getByText('Details', { exact: true }).click();
  await expect(page.getByRole('status').getByText(/AuthenticationError: Error code: 401/)).toBeVisible();
  // an invalid URL is reported inline and never sent
  await page.getByLabel('Base URL').fill('http://typed.example.com/v1');
  await page.getByLabel('Base URL').press('Tab');
  await expect(page.locator('#base-url-error')).toContainText('Plain http:// is allowed only for localhost');
  await page.getByRole('button', { name: 'Test connection' }).click();
  await page.waitForTimeout(300);
  expect(tests).toHaveLength(2);
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForTimeout(300);
  expect(tests).toHaveLength(2);
});
