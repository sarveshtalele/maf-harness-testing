import { test, expect, type Page } from '@playwright/test';

const TOKEN = 'atl-secret-token-0123456789';
const PASTED = 'paste-secret-0123456789';
const STAGES = ['requirements', 'test_planning', 'test_design', 'test_generation', 'environment_setup', 'test_execution'];
const PRESETS = {
  playwright: { label: 'Playwright MCP (local browser, stdio)', note: 'Navigating requires approval.', server: { name: 'playwright', transport: 'stdio', command: 'npx', args: ['@playwright/mcp@latest'], env_keys: [], url: '', enabled: false, stages: [], allowed_tools: ['browser_snapshot'], approval_required_tools: ['browser_navigate'], preset: 'playwright' } },
  'atlassian-jira-cloud': { label: 'Atlassian Jira Cloud', note: 'Verify endpoint.', server: { name: 'jira', transport: 'http', command: '', args: [], env_keys: [], url: 'https://mcp.atlassian.com/v2/mcp', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: 'atlassian-jira-cloud' } },
};
const THREE = [
  { name: 'browser_snapshot', description: 'Capture an accessibility snapshot of the page' },
  { name: 'browser_navigate', description: 'Navigate to a URL' },
  { name: 'browser_click', description: 'Click an element on the page' },
];
type Srv = Record<string, unknown>;
type Import = { imported: { source_key: string; name: string; secret_keys: string[] }[]; added: Srv[] };

/** In-memory stand-in for the MCP routes: list, save, import, test, secrets and delete. */
async function mockMcp(page: Page, options: { servers?: Srv[]; tools?: { name: string; description: string }[]; importReply?: (text: string) => Import } = {}) {
  const state = { servers: [...(options.servers ?? [])], saved: [] as Srv[], secretBodies: [] as Record<string, unknown>[], deleted: [] as string[], tools: options.tools ?? THREE };
  const view = (s: Srv) => ({ ...s, discovered_tools: s.discovered_tools ?? [], secrets: s.transport === 'http' ? { BEARER_TOKEN: false } : {}, ready: s.transport !== 'http' });
  await page.route('**/api/harness/mcp-servers', async route => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as Srv;
      const existing = state.servers.find(s => s.name === body.name);
      const saved = { ...body, discovered_tools: existing?.discovered_tools ?? [] };
      state.saved.push(body);
      state.servers = [...state.servers.filter(s => s.name !== body.name), saved];
      return route.fulfill({ json: view(saved) });
    }
    return route.fulfill({ json: { servers: state.servers.map(view), stages: STAGES, max_tools_per_server: 200, max_tools_per_stage: 8, presets: PRESETS } });
  });
  await page.route('**/api/harness/mcp-servers/**', async route => {
    const request = route.request();
    const [name, action] = new URL(request.url()).pathname.split('/mcp-servers/')[1].split('/');
    if (name === 'import') {
      const reply = options.importReply?.((request.postDataJSON() as { text: string }).text) ?? { imported: [], added: [] };
      state.servers = [...state.servers, ...reply.added];
      return route.fulfill({ json: { imported: reply.imported, servers: state.servers.map(view) } });
    }
    if (action === 'test') {
      const server = state.servers.find(s => s.name === name);
      if (server) server.discovered_tools = state.tools;
      return route.fulfill({ json: { ok: true, tools: state.tools.map(t => t.name), count: state.tools.length } });
    }
    if (action === 'secrets') {
      state.secretBodies.push(request.postDataJSON() as Record<string, unknown>);
      return route.fulfill({ json: view(state.servers.find(s => s.name === name) ?? {}) });
    }
    if (request.method() === 'DELETE') {
      state.deleted.push(name);
      state.servers = state.servers.filter(s => s.name !== name);
      return route.fulfill({ json: { deleted: name } });
    }
    return route.fallback();
  });
  return state;
}

const card = (page: Page, name: string) => page.getByRole('region', { name: `MCP server ${name}` });
const manage = (page: Page, name: string) => page.getByRole('dialog', { name: `${name} · tools and settings` });

async function addPreset(page: Page, label: string) {
  await page.getByRole('button', { name: 'Add MCP server' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add MCP server' });
  await dialog.getByRole('tab', { name: 'Preset', exact: true }).click();
  await dialog.getByRole('combobox').selectOption({ label });
  await dialog.getByRole('button', { name: 'Add preset' }).click();
  await expect(dialog).toBeHidden();
}

test('MCP servers in Marketplace: add the Playwright preset, test, set tool access and stage, write-only secrets', async ({ page }) => {
  const state = await mockMcp(page);
  await page.goto('/?page=marketplace&tab=mcp');
  await expect(page.getByRole('tab', { name: 'MCP servers', exact: true })).toHaveAttribute('aria-selected', 'true');
  await addPreset(page, 'Playwright MCP (local browser, stdio)');
  const pw = card(page, 'playwright');
  await expect(pw).toBeVisible();
  await expect(pw.getByText('Disabled', { exact: true })).toBeVisible();
  await pw.getByRole('button', { name: 'Manage tools' }).click();
  const dialog = manage(page, 'playwright');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Test', exact: true }).click();
  await expect(dialog.getByRole('status').filter({ hasText: 'Connected: 3 tools available.' })).toBeVisible();
  await expect(dialog.getByLabel('Access for browser_snapshot')).toHaveValue('auto');
  await expect(dialog.getByLabel('Access for browser_navigate')).toHaveValue('approval');
  await expect(dialog.getByLabel('Access for browser_click')).toHaveValue('hidden'); // not listed: blocked
  await dialog.getByLabel('Access for browser_click').selectOption('hidden');
  await dialog.getByLabel('Enabled', { exact: true }).check();
  await dialog.getByLabel('Requirements', { exact: true }).check();
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(dialog.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible();
  expect(state.saved.at(-1)).toMatchObject({ name: 'playwright', enabled: true, stages: ['requirements'], allowed_tools: ['browser_snapshot'], approval_required_tools: ['browser_navigate'] });
  expect(JSON.stringify(state.saved)).not.toContain('secrets');
  await expect(pw.getByText('3 tools · 1 approved · 1 need approval · 1 blocked')).toBeVisible();
  await expect(pw.getByText('Enabled', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(pw.getByRole('button', { name: 'Manage tools' })).toBeFocused();

  // http preset: the token is a write-only password field that goes only to the secrets route
  await addPreset(page, 'Atlassian Jira Cloud');
  const jira = card(page, 'jira');
  await jira.getByRole('button', { name: 'Manage tools' }).click();
  const jiraDialog = manage(page, 'jira');
  const field = jiraDialog.getByLabel(/Token/);
  await expect(field).toHaveAttribute('type', 'password');
  await field.fill(TOKEN);
  await jiraDialog.getByRole('button', { name: 'Save secrets' }).click();
  await expect(jiraDialog.getByRole('status').filter({ hasText: 'Secrets stored' })).toBeVisible();
  expect(state.secretBodies).toEqual([{ BEARER_TOKEN: TOKEN }]);
  await expect(field).toHaveValue('');
  expect(await page.content()).not.toContain(TOKEN);
});

test('pasting a JSON config adds a stdio and an http server, both disabled, and stores their secrets', async ({ page }) => {
  const config = JSON.stringify({
    mcpServers: {
      files: { command: 'npx', args: ['-y', 'server-filesystem', 'C:\\work'], env: { API_TOKEN: PASTED } },
      jira: { type: 'http', url: 'https://mcp.example.com/mcp', headers: { Authorization: `Bearer ${PASTED}` } },
    },
  });
  const state = await mockMcp(page, {
    importReply: () => ({
      imported: [{ source_key: 'files', name: 'files', secret_keys: ['API_TOKEN'] }, { source_key: 'jira', name: 'jira', secret_keys: ['BEARER_TOKEN'] }],
      added: [
        { name: 'files', transport: 'stdio', command: 'npx', args: ['-y', 'server-filesystem', 'C:\\work'], env_keys: ['API_TOKEN'], url: '', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: '' },
        { name: 'jira', transport: 'http', command: '', args: [], env_keys: [], url: 'https://mcp.example.com/mcp', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: '' },
      ],
    }),
  });
  await page.goto('/?page=marketplace&tab=mcp');
  await page.getByRole('button', { name: 'Add MCP server' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add MCP server' });
  await dialog.getByLabel(/Paste an npx/).fill(config);
  await dialog.getByRole('button', { name: 'Add from paste' }).click();
  await expect(dialog).toBeHidden();
  await expect(card(page, 'files').getByText('Disabled', { exact: true })).toBeVisible();
  await expect(card(page, 'jira').getByText('Disabled', { exact: true })).toBeVisible();
  expect(state.secretBodies).toEqual([{ API_TOKEN: PASTED }, { BEARER_TOKEN: `Bearer ${PASTED}` }]);
  expect(await page.content()).not.toContain(PASTED);
});

test('pasting an npx command names the card after the package and stores an inline env secret', async ({ page }) => {
  const state = await mockMcp(page, {
    importReply: () => ({
      imported: [{ source_key: '', name: 'mcp', secret_keys: ['API_TOKEN'] }],
      added: [{ name: 'mcp', transport: 'stdio', command: 'npx', args: ['-y', '@playwright/mcp@latest'], env_keys: ['API_TOKEN'], url: '', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: '' }],
    }),
  });
  await page.goto('/?page=marketplace&tab=mcp');
  await page.getByRole('button', { name: 'Add MCP server' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add MCP server' });
  const textarea = dialog.getByLabel(/Paste an npx/);
  await textarea.fill(`API_TOKEN=${PASTED} npx -y @playwright/mcp@latest`);
  await dialog.getByRole('button', { name: 'Add from paste' }).click();
  await expect(dialog).toBeHidden();
  await expect(card(page, 'mcp')).toBeVisible();
  await expect(card(page, 'mcp').locator('code').first()).toHaveText('npx -y @playwright/mcp@latest');
  expect(state.secretBodies).toEqual([{ API_TOKEN: PASTED }]);
  await page.getByRole('button', { name: 'Add MCP server' }).click();
  await expect(page.getByRole('dialog', { name: 'Add MCP server' }).getByLabel(/Paste an npx/)).toHaveValue('');
});

test('approve all, deselect all, save: counts update and the over-budget warning appears', async ({ page }) => {
  const nine = Array.from({ length: 9 }, (_, i) => ({ name: `tool_${i + 1}`, description: `Tool number ${i + 1}` }));
  const state = await mockMcp(page, {
    tools: nine,
    servers: [{ name: 'pw', transport: 'stdio', command: 'npx', args: ['@playwright/mcp@latest'], env_keys: [], url: '', enabled: true, stages: ['requirements'], allowed_tools: [], approval_required_tools: [], preset: '' }],
  });
  await page.goto('/?page=marketplace&tab=mcp');
  const pw = card(page, 'pw');
  await expect(pw.getByText('Run Test to discover tools')).toBeVisible();
  await pw.getByRole('button', { name: 'Manage tools' }).click();
  const dialog = manage(page, 'pw');
  await dialog.getByRole('button', { name: 'Test', exact: true }).click();
  await expect(dialog.getByRole('status').filter({ hasText: 'Connected: 9 tools available.' })).toBeVisible();
  await expect(dialog.getByRole('table')).toContainText('Tool number 9');
  await dialog.getByRole('button', { name: 'Approve all', exact: true }).click();
  await expect(dialog.getByText('9 tools · 9 approved · 0 need approval · 0 blocked')).toBeVisible();
  await expect(dialog.getByText('This server would be skipped in a stage: at most 8 MCP tools per stage.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(dialog.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible();
  expect(state.saved.at(-1)?.allowed_tools).toHaveLength(9);
  await expect(pw.getByText('9 tools · 9 approved · 0 need approval · 0 blocked')).toBeVisible();
  await expect(pw.getByText('Over budget')).toBeVisible();
  await expect(pw.getByText('This server would be skipped in a stage: at most 8 MCP tools per stage.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Deselect all', exact: true }).click();
  await expect(dialog.getByText('9 tools · 0 approved · 0 need approval · 9 blocked')).toBeVisible();
  await expect(dialog.getByText('This server would be skipped in a stage')).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(pw.getByText('9 tools · 0 approved · 0 need approval · 9 blocked')).toBeVisible();
  await expect(pw.getByText('Over budget')).toHaveCount(0);
  await page.keyboard.press('Escape');
});

test('delete asks for confirmation, removes the card and calls the delete route', async ({ page }) => {
  const state = await mockMcp(page, { servers: [{ name: 'playwright', transport: 'stdio', command: 'npx', args: ['@playwright/mcp@latest'], env_keys: [], url: '', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: 'playwright' }] });
  await page.goto('/?page=marketplace&tab=mcp');
  const pw = card(page, 'playwright');
  await pw.getByRole('button', { name: 'Manage tools' }).click();
  const dialog = manage(page, 'playwright');
  await dialog.getByRole('button', { name: 'Delete', exact: true }).click();
  const confirm = page.getByRole('dialog', { name: 'Delete playwright?' });
  await expect(confirm).toBeVisible();
  await confirm.getByRole('button', { name: 'Delete server' }).click();
  await expect(pw).toHaveCount(0);
  expect(state.deleted).toEqual(['playwright']);
});

test('Configuration links to Marketplace MCP servers', async ({ page }) => {
  await mockMcp(page);
  await page.goto('/?page=configuration');
  const link = page.getByRole('link', { name: 'Marketplace → MCP servers' });
  await expect(link).toHaveAttribute('href', '/?page=marketplace&tab=mcp');
  await link.click();
  await expect(page).toHaveURL(/page=marketplace&tab=mcp/);
  await expect(page.getByRole('tab', { name: 'MCP servers', exact: true })).toHaveAttribute('aria-selected', 'true');
});

test('MCP tab and the manage dialog fit a 390 px screen with a long command', async ({ page }) => {
  await mockMcp(page, {
    servers: [{ name: 'long', transport: 'stdio', command: 'npx', args: ['-y', '@modelcontextprotocol/server-filesystem', '/a/very/long/path/'.repeat(12)], env_keys: [], url: '', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: '' }],
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?page=marketplace&tab=mcp');
  await expect(card(page, 'long')).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await card(page, 'long').getByRole('button', { name: 'Manage tools' }).click();
  await expect(manage(page, 'long')).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 1280, height: 720 });
});
