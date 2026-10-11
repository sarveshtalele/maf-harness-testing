import { test, expect } from '@playwright/test';

const TOKEN = 'atl-secret-token-0123456789';

test('MCP servers panel: add Playwright preset, test, set tool access and stage, write-only secrets', async ({ page }) => {
  const preset = { name: 'playwright', transport: 'stdio', command: 'npx', args: ['@playwright/mcp@latest'], env_keys: [], url: '', enabled: false, stages: [], allowed_tools: ['browser_snapshot'], approval_required_tools: ['browser_navigate'], preset: 'playwright' };
  const jira = { ...preset, name: 'jira', transport: 'http', command: '', args: [], url: 'https://mcp.atlassian.com/v2/mcp', preset: 'atlassian-jira-cloud' };
  let servers: Record<string, unknown>[] = [];
  let saved: Record<string, unknown> = {};
  let secretBody: Record<string, unknown> = {};
  const view = (s: Record<string, unknown>) => ({ ...s, secrets: s.transport === 'http' ? { BEARER_TOKEN: false } : {}, ready: s.transport !== 'http' });
  await page.route('**/api/harness/mcp-servers', async route => {
    if (route.request().method() === 'POST') {
      saved = route.request().postDataJSON();
      servers = [...servers.filter(s => s.name !== saved.name), saved];
      await route.fulfill({ json: view(saved) });
    } else {
      await route.fulfill({ json: { servers: servers.map(view), stages: ['requirements', 'test_planning', 'test_design', 'test_generation', 'environment_setup', 'test_execution'], max_tools_per_server: 8,
        presets: { playwright: { label: 'Playwright MCP (local browser, stdio)', note: 'Navigating requires approval.', server: preset }, 'atlassian-jira-cloud': { label: 'Atlassian Jira Cloud', note: 'Verify endpoint.', server: jira } } } });
    }
  });
  await page.route('**/api/harness/mcp-servers/playwright/test', route => route.fulfill({ json: { ok: true, tools: ['browser_click', 'browser_navigate', 'browser_snapshot'], count: 3 } }));
  await page.route('**/api/harness/mcp-servers/jira/secrets', route => { secretBody = route.request().postDataJSON(); return route.fulfill({ json: view(jira) }); });
  await page.goto('/?page=configuration');
  await expect(page.getByRole('button', { name: 'MCP servers', exact: true })).toBeVisible();
  await page.getByText('Add a server').click();
  await page.getByLabel('From preset').selectOption('playwright');
  await page.getByRole('button', { name: 'Add preset' }).click();
  const card = page.getByRole('region', { name: 'MCP server playwright' });
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: 'Test' }).click();
  await expect(card.getByRole('status').filter({ hasText: 'Connected: 3 tools available.' })).toBeVisible();
  await expect(card.getByLabel('Access for browser_snapshot')).toHaveValue('auto');
  await expect(card.getByLabel('Access for browser_navigate')).toHaveValue('approval');
  await expect(card.getByLabel('Access for browser_click')).toHaveValue('hidden'); // unlisted tools stay hidden
  await card.getByLabel('Enabled').check();
  await card.getByLabel('Requirements').check();
  await card.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(card.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible();
  expect(saved).toMatchObject({ name: 'playwright', enabled: true, stages: ['requirements'], allowed_tools: ['browser_snapshot'], approval_required_tools: ['browser_navigate'] });
  expect(JSON.stringify(saved)).not.toContain('secrets');
  // http preset: secret field is a write-only password input; the value goes only to the secrets route
  await page.getByText('Add a server').click();
  await page.getByLabel('From preset').selectOption('atlassian-jira-cloud');
  await page.getByRole('button', { name: 'Add preset' }).click();
  const jcard = page.getByRole('region', { name: 'MCP server jira' });
  const field = jcard.getByLabel(/Token/);
  await expect(field).toHaveAttribute('type', 'password');
  await field.fill(TOKEN);
  await jcard.getByRole('button', { name: 'Save secrets' }).click();
  await expect(jcard.getByRole('status').filter({ hasText: 'Secrets stored' })).toBeVisible();
  expect(secretBody).toEqual({ BEARER_TOKEN: TOKEN });
  await expect(field).toHaveValue('');
  expect(await page.content()).not.toContain(TOKEN);
});

test('MCP server delete removes its card and calls the delete route', async ({ page }) => {
  let servers: Record<string, unknown>[] = [{ name: 'playwright', transport: 'stdio', command: 'npx', args: ['@playwright/mcp@latest'], env_keys: [], url: '', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: 'playwright', secrets: {}, ready: true }];
  const deleted: string[] = [];
  await page.route('**/api/harness/mcp-servers', route => route.fulfill({ json: { servers, stages: ['requirements'], max_tools_per_server: 8, presets: {} } }));
  await page.route('**/api/harness/mcp-servers/playwright', route => {
    if (route.request().method() !== 'DELETE') return route.fallback();
    deleted.push('playwright');
    servers = [];
    return route.fulfill({ json: { servers: [] } });
  });
  await page.goto('/?page=configuration');
  const card = page.getByRole('region', { name: 'MCP server playwright' });
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(card).toHaveCount(0);
  expect(deleted).toEqual(['playwright']);
});
