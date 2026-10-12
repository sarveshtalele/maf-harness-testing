import { test, expect, type Page } from '@playwright/test';
import { WIDE_FONTS, layoutIssues } from './layout-audit';

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

/** Stage budget as the backend computes it: enabled servers in name order, at most 8 MCP tools per stage. */
function budgetOf(servers: Srv[]) {
  return Object.fromEntries(STAGES.flatMap(stage => {
    let used = 0; let demand = 0; const skipped: string[] = [];
    for (const s of [...servers].sort((a, b) => String(a.name).localeCompare(String(b.name)))) {
      if (!(s.enabled && (s.stages as string[]).includes(stage))) continue;
      const n = new Set([...(s.allowed_tools as string[]), ...(s.approval_required_tools as string[])]).size;
      demand += n;
      if (used + n > 8) skipped.push(String(s.name)); else used += n;
    }
    return skipped.length ? [[stage, { tools: demand, max: 8, skipped }]] : [];
  }));
}

/** In-memory stand-in for the MCP routes: list, save, import, test, secrets and delete. */
async function mockMcp(page: Page, options: { servers?: Srv[]; tools?: { name: string; description: string }[]; importReply?: (text: string) => Import; failSecrets?: boolean } = {}) {
  const state = { servers: [...(options.servers ?? [])], saved: [] as Srv[], secretBodies: [] as Record<string, unknown>[], deleted: [] as string[], tools: options.tools ?? THREE };
  const view = (s: Srv) => ({ ...s, discovered_tools: s.discovered_tools ?? [], secrets: s.transport === 'http' ? { BEARER_TOKEN: false } : {}, ready: s.transport !== 'http' });
  const list = () => ({ servers: state.servers.map(view), stages: STAGES, max_tools_per_server: 200, max_tools_per_stage: 8, stage_budget: budgetOf(state.servers), presets: PRESETS });
  await page.route('**/api/harness/mcp-servers', async route => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as Srv;
      const existing = state.servers.find(s => s.name === body.name);
      const saved = { ...body, discovered_tools: existing?.discovered_tools ?? [] };
      state.saved.push(body);
      state.servers = [...state.servers.filter(s => s.name !== body.name), saved];
      return route.fulfill({ json: view(saved) });
    }
    return route.fulfill({ json: list() });
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
      if (options.failSecrets) return route.fulfill({ status: 500, json: { error: 'keychain unavailable' } });
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

const card = (page: Page, name: string) => page.getByRole('article', { name: `MCP server ${name}` });
const tool = (page: Page, name: string) => page.getByRole('group', { name: `Access for ${name}` });
const footerSave = (page: Page) => page.locator('footer.mcp-footer').getByRole('button', { name: 'Save', exact: true });
const crumbLink = (page: Page, name: string) => page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name, exact: true });
const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

async function addPreset(page: Page, label: string) {
  await page.goto('/marketplace/mcp/new');
  await page.getByRole('tab', { name: 'Preset', exact: true }).click();
  await page.getByRole('combobox').selectOption({ label });
  await page.getByRole('button', { name: 'Add preset' }).click();
}

test('add the Playwright preset as a page, test, set tool access and stage, write-only secrets', async ({ page }) => {
  const state = await mockMcp(page);
  await page.goto('/marketplace/mcp');
  await expect(page.getByRole('tab', { name: 'MCP servers', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('link', { name: 'Add MCP server', exact: true }).click();
  await expect(page).toHaveURL(/\/marketplace\/mcp\/new$/);
  await addPreset(page, 'Playwright MCP (local browser, stdio)');
  await expect(page).toHaveURL(/\/marketplace\/mcp\/playwright$/);
  await expect(page.getByRole('heading', { level: 2, name: 'playwright', exact: true })).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'playwright added.' })).toBeVisible();
  await expect(page.getByRole('switch', { name: 'Enabled' })).not.toBeChecked();
  await page.getByRole('button', { name: 'Test connection', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Connected: 3 tools available.' })).toBeVisible();
  await expect(tool(page, 'browser_snapshot').getByRole('button', { name: 'Always allow' })).toHaveAttribute('aria-pressed', 'true');
  await expect(tool(page, 'browser_navigate').getByRole('button', { name: 'Ask every time' })).toHaveAttribute('aria-pressed', 'true');
  await expect(tool(page, 'browser_click').getByRole('button', { name: 'Block' })).toHaveAttribute('aria-pressed', 'true'); // not listed: blocked
  await tool(page, 'browser_click').getByRole('button', { name: 'Block' }).click();
  await page.getByRole('switch', { name: 'Enabled' }).check();
  await page.getByLabel('Requirements', { exact: true }).check();
  await footerSave(page).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible();
  expect(state.saved.at(-1)).toMatchObject({ name: 'playwright', enabled: true, stages: ['requirements'], allowed_tools: ['browser_snapshot'], approval_required_tools: ['browser_navigate'] });
  expect(JSON.stringify(state.saved)).not.toContain('secrets');
  await expect(page.getByText('3 tools · 1 always allow · 1 ask · 1 blocked', { exact: true })).toBeVisible();
  await crumbLink(page, 'MCP servers').click();
  await expect(card(page, 'playwright').getByText('Enabled', { exact: true })).toBeVisible();
  await expect(card(page, 'playwright').getByText('3 tools · 1 always allow · 1 ask · 1 blocked')).toBeVisible();

  // http preset: the token is a write-only password field that goes only to the secrets route
  await page.goto('/marketplace/mcp/new');
  await page.getByRole('tab', { name: 'Preset', exact: true }).click();
  await page.getByRole('combobox').selectOption({ label: 'Atlassian Jira Cloud' });
  await page.getByRole('button', { name: 'Add preset' }).click();
  await expect(page).toHaveURL(/\/marketplace\/mcp\/jira$/);
  const field = page.getByLabel(/Token/);
  await expect(field).toHaveAttribute('type', 'password');
  await field.fill(TOKEN);
  await page.getByRole('button', { name: 'Save secrets', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Secrets stored' })).toBeVisible();
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
  await page.goto('/marketplace/mcp/new');
  await page.getByLabel(/Paste an npx/).fill(config);
  await page.getByRole('button', { name: 'Add from paste' }).click();
  await expect(page).toHaveURL(/\/marketplace\/mcp$/);
  await expect(page.getByRole('status').filter({ hasText: '2 MCP servers added.' })).toBeVisible();
  await expect(card(page, 'files').getByText('Disabled', { exact: true })).toBeVisible();
  await expect(card(page, 'jira').getByText('Disabled', { exact: true })).toBeVisible();
  expect(state.secretBodies).toEqual([{ API_TOKEN: PASTED }, { BEARER_TOKEN: `Bearer ${PASTED}` }]);
  expect(await page.content()).not.toContain(PASTED);
});

test('pasting an npx command names the server after the package, opens its page and stores an inline env secret', async ({ page }) => {
  const state = await mockMcp(page, {
    importReply: () => ({
      imported: [{ source_key: '', name: 'mcp', secret_keys: ['API_TOKEN'] }],
      added: [{ name: 'mcp', transport: 'stdio', command: 'npx', args: ['-y', '@playwright/mcp@latest'], env_keys: ['API_TOKEN'], url: '', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: '' }],
    }),
  });
  await page.goto('/marketplace/mcp/new');
  await page.getByLabel(/Paste an npx/).fill(`API_TOKEN=${PASTED} npx -y @playwright/mcp@latest`);
  await page.getByRole('button', { name: 'Add from paste' }).click();
  await expect(page).toHaveURL(/\/marketplace\/mcp\/mcp$/);
  await expect(page.getByRole('status').filter({ hasText: 'mcp added.' })).toBeVisible();
  await expect(page.locator('.mcp-code').first()).toHaveText('npx -y @playwright/mcp@latest');
  expect(state.secretBodies).toEqual([{ API_TOKEN: PASTED }]);
  expect(await page.content()).not.toContain(PASTED);
});

test('Cancel leaves the add page and the pasted text is not kept', async ({ page }) => {
  await mockMcp(page);
  await page.goto('/marketplace/mcp/new');
  await page.getByLabel(/Paste an npx/).fill(`API_TOKEN=${PASTED} npx -y x`);
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page).toHaveURL(/\/marketplace\/mcp$/);
  await page.getByRole('link', { name: 'Add MCP server', exact: true }).click();
  await expect(page.getByLabel(/Paste an npx/)).toHaveValue('');
});

test('a failed secret save after import keeps the server, stays on the add page and names the keys not stored', async ({ page }) => {
  await mockMcp(page, {
    failSecrets: true,
    importReply: () => ({
      imported: [{ source_key: 'files', name: 'files', secret_keys: ['API_TOKEN'] }],
      added: [{ name: 'files', transport: 'stdio', command: 'npx', args: ['x'], env_keys: ['API_TOKEN'], url: '', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: '' }],
    }),
  });
  await page.goto('/marketplace/mcp/new');
  await page.getByLabel(/Paste an npx/).fill(JSON.stringify({ mcpServers: { files: { command: 'npx', args: ['x'], env: { API_TOKEN: PASTED } } } }));
  await page.getByRole('button', { name: 'Add from paste' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'NOT stored' })).toContainText('files: API_TOKEN');
  await expect(page).toHaveURL(/\/marketplace\/mcp\/new$/); // stays so the error is seen
  await expect(page.getByRole('link', { name: 'Open files', exact: true })).toBeVisible();
  expect(await page.content()).not.toContain(PASTED);
});

test('real backend: paste a docker -e secret and an env secret; the stored secrets never come back', async ({ page, request }) => {
  // No MCP routes are mocked here: the import goes through the real parse_import and the real secrets route.
  const names = ['e2e-real', 'e2e-docker'];
  try {
    await page.goto('/marketplace/mcp/new');
    await page.getByLabel(/Paste an npx/).fill(JSON.stringify({ mcpServers: { 'e2e-real': { command: 'npx', args: ['-y', '@playwright/mcp@latest'], env: { E2E_TOKEN: PASTED } } } }));
    await page.getByRole('button', { name: 'Add from paste' }).click();
    await expect(page).toHaveURL(/\/marketplace\/mcp\/e2e-real$/);
    await expect(page.getByRole('switch', { name: 'Enabled' })).not.toBeChecked();
    await page.goto('/marketplace/mcp/new');
    await page.getByLabel(/Paste an npx/).fill(`docker run -i --rm -e E2E_DOCKER=${PASTED} mcp/fetch`);
    await page.getByRole('button', { name: 'Add from paste' }).click();
    await expect(page).toHaveURL(/\/marketplace\/mcp\/e2e-docker$/);
    await expect(page.locator('.mcp-code').first()).toHaveText('docker run -i --rm -e E2E_DOCKER mcp/fetch');
    await expect(page.getByLabel('E2E_DOCKER', { exact: true })).toHaveAttribute('placeholder', /Set/);
    expect(await page.content()).not.toContain(PASTED);
    const list = await (await request.get('/api/harness/mcp-servers')).text();
    expect(list).toContain('E2E_DOCKER');
    expect(list).not.toContain(PASTED);
  } finally {
    for (const name of names) await request.delete(`/api/harness/mcp-servers/${name}`);
  }
});

test('a card opens the server page with its Tools section; search filters tools', async ({ page }) => {
  await mockMcp(page, { servers: [{ name: 'playwright', transport: 'stdio', command: 'npx', args: ['@playwright/mcp@latest'], env_keys: [], url: '', enabled: true, stages: [], allowed_tools: ['browser_snapshot'], approval_required_tools: ['browser_navigate'], preset: '', discovered_tools: THREE }] });
  await page.goto('/marketplace/mcp');
  await card(page, 'playwright').getByRole('link', { name: 'playwright', exact: true }).click();
  await expect(page).toHaveURL(/\/marketplace\/mcp\/playwright$/);
  await expect(page.locator('#tools')).toBeVisible();
  await expect(tool(page, 'browser_click')).toBeVisible();
  await page.getByRole('searchbox', { name: 'Search tools' }).fill('accessibility');
  await expect(tool(page, 'browser_snapshot')).toBeVisible();
  await expect(tool(page, 'browser_click')).toHaveCount(0);
  await expect(tool(page, 'browser_navigate')).toHaveCount(0);
});

test('per-tool three-state control saves to allowed_tools and approval_required_tools; blocked tools go to neither', async ({ page }) => {
  const state = await mockMcp(page, { servers: [{ name: 'pw', transport: 'stdio', command: 'npx', args: ['x'], env_keys: [], url: '', enabled: false, stages: [], allowed_tools: ['browser_snapshot'], approval_required_tools: ['browser_navigate'], preset: '', discovered_tools: THREE }] });
  await page.goto('/marketplace/mcp/pw');
  await tool(page, 'browser_snapshot').getByRole('button', { name: 'Ask every time' }).click();
  await tool(page, 'browser_navigate').getByRole('button', { name: 'Always allow' }).click();
  await tool(page, 'browser_click').getByRole('button', { name: 'Always allow' }).click();
  await tool(page, 'browser_click').getByRole('button', { name: 'Block' }).click();
  await expect(page.getByText('3 tools · 1 always allow · 1 ask · 1 blocked', { exact: true })).toBeVisible();
  await footerSave(page).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible();
  const saved = state.saved.at(-1)!;
  expect(saved.allowed_tools).toEqual(['browser_navigate']);
  expect(saved.approval_required_tools).toEqual(['browser_snapshot']);
  await expect(footerSave(page)).toBeDisabled();
});

test('Allow all read-only, Approve all (with its warning), Block all and Reset to saved', async ({ page }) => {
  await mockMcp(page, { servers: [{ name: 'pw', transport: 'stdio', command: 'npx', args: ['x'], env_keys: [], url: '', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: '', discovered_tools: THREE }] });
  await page.goto('/marketplace/mcp/pw');
  await expect(page.getByText('Read-only', { exact: true })).toBeVisible();
  await expect(page.getByText('Can make changes', { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Allow all read-only', exact: true }).click();
  await expect(tool(page, 'browser_snapshot').getByRole('button', { name: 'Always allow' })).toHaveAttribute('aria-pressed', 'true');
  await expect(tool(page, 'browser_navigate').getByRole('button', { name: 'Block' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('3 tools · 1 always allow · 0 ask · 2 blocked', { exact: true })).toBeVisible();
  await expect(page.getByText('Approved tools run without asking', { exact: false })).toHaveCount(1);
  await page.getByRole('button', { name: 'Approve all', exact: true }).click();
  await expect(page.getByText('3 tools · 3 always allow · 0 ask · 0 blocked', { exact: true })).toBeVisible();
  await expect(page.getByText('Approved tools run without asking, including any that change data')).toBeVisible();
  await page.getByRole('button', { name: 'Block all', exact: true }).click();
  await expect(page.getByText('3 tools · 0 always allow · 0 ask · 3 blocked', { exact: true })).toBeVisible();
  await expect(page.getByText('Approved tools run without asking', { exact: false })).toHaveCount(0);
  await page.getByRole('button', { name: 'Reset to saved', exact: true }).click();
  await expect(page.getByText('3 tools · 0 always allow · 0 ask · 3 blocked', { exact: true })).toBeVisible();
  await expect(footerSave(page)).toBeDisabled(); // reset to the saved state: no unsaved changes
});

test('unsaved changes: in-app links ask before leaving, Stay keeps the edits, and a reload asks the browser first', async ({ page }) => {
  await mockMcp(page, { servers: [{ name: 'pw', transport: 'stdio', command: 'npx', args: ['x'], env_keys: [], url: '', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: '', discovered_tools: THREE }] });
  await page.goto('/marketplace/mcp/pw');
  await page.getByRole('switch', { name: 'Enabled' }).check();
  await expect(page.getByText('Unsaved changes', { exact: true })).toBeVisible();
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Runs', exact: false }).first().click();
  const confirm = page.getByRole('dialog', { name: 'Leave with unsaved changes?' });
  await expect(confirm).toBeVisible();
  await confirm.getByRole('button', { name: 'Stay', exact: true }).click();
  await expect(page).toHaveURL(/\/marketplace\/mcp\/pw$/);
  await expect(page.getByRole('switch', { name: 'Enabled' })).toBeChecked();
  // A reload or tab close asks the browser first (beforeunload is cancelled while changes are unsaved).
  expect(await page.evaluate(() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; })).toBe(true);
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Runs', exact: false }).first().click();
  await confirm.getByRole('button', { name: 'Leave without saving', exact: true }).click();
  await expect(page).toHaveURL(/\/runs/);
});

test('delete from the card asks first (Cancel returns focus to Delete); the second delete removes the card and moves focus to Add', async ({ page }) => {
  const state = await mockMcp(page, { servers: [{ name: 'playwright', transport: 'stdio', command: 'npx', args: ['@playwright/mcp@latest'], env_keys: [], url: '', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: 'playwright' }] });
  await page.goto('/marketplace/mcp');
  const del = card(page, 'playwright').getByRole('button', { name: 'Delete playwright', exact: true });
  await del.click();
  const confirm = page.getByRole('dialog', { name: 'Delete playwright?' });
  await expect(confirm).toBeVisible();
  await confirm.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(del).toBeFocused();
  expect(state.deleted).toEqual([]);
  await del.click();
  await page.getByRole('dialog', { name: 'Delete playwright?' }).getByRole('button', { name: 'Delete server' }).click();
  await expect(card(page, 'playwright')).toHaveCount(0);
  expect(state.deleted).toEqual(['playwright']);
  await expect(page.getByRole('status').filter({ hasText: 'playwright deleted.' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Add MCP server', exact: true })).toBeFocused();
});

test('delete from the server page asks first, then returns to the card list with a notice', async ({ page }) => {
  const state = await mockMcp(page, { servers: [{ name: 'playwright', transport: 'stdio', command: 'npx', args: ['@playwright/mcp@latest'], env_keys: [], url: '', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: 'playwright' }] });
  await page.goto('/marketplace/mcp/playwright');
  await page.getByRole('button', { name: 'Delete server', exact: true }).click();
  const confirm = page.getByRole('dialog', { name: 'Delete playwright?' });
  await expect(confirm).toBeVisible();
  expect(state.deleted).toEqual([]);
  await confirm.getByRole('button', { name: 'Delete server' }).click();
  await expect(page).toHaveURL(/\/marketplace\/mcp$/);
  await expect(page.getByRole('status').filter({ hasText: 'playwright deleted.' })).toBeVisible();
  await expect(card(page, 'playwright')).toHaveCount(0);
  expect(state.deleted).toEqual(['playwright']);
});

test('a stored server with a secret in its arguments loads disabled, shows the fix, and can be fixed on its page', async ({ page }) => {
  const state = await mockMcp(page, {
    servers: [{ name: 'legacy', transport: 'stdio', command: 'npx', args: ['-y', 'server-x', '--api-key', 'abcdef123456'], env_keys: [], url: '', enabled: false, stages: [], allowed_tools: [], approval_required_tools: [], preset: '', problems: ['Move the token in the arguments to a secret before enabling.'] }],
  });
  await page.goto('/marketplace/mcp');
  const legacy = card(page, 'legacy');
  await expect(legacy.getByText('Move the token in the arguments to a secret before enabling.')).toBeVisible();
  await legacy.getByRole('link', { name: 'Manage', exact: true }).click();
  await expect(page).toHaveURL(/\/marketplace\/mcp\/legacy$/);
  const fix = page.getByRole('group', { name: 'Fix before enabling' });
  await expect(fix.getByLabel('Arguments (one per line)')).toHaveValue('-y\nserver-x\n--api-key\nabcdef123456');
  await fix.getByLabel('Arguments (one per line)').fill('-y\nserver-x');
  await footerSave(page).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible();
  expect(state.saved.at(-1)?.args).toEqual(['-y', 'server-x']);
});

test('Configuration links to the MCP servers page', async ({ page }) => {
  await mockMcp(page);
  await page.goto('/settings');
  const link = page.getByRole('link', { name: 'Marketplace → MCP servers' });
  await expect(link).toHaveAttribute('href', '/marketplace/mcp');
  await link.click();
  await expect(page).toHaveURL(/\/marketplace\/mcp$/);
  await expect(page.getByRole('tab', { name: 'MCP servers', exact: true })).toHaveAttribute('aria-selected', 'true');
});

test('stage budget: approve all for 9 tools warns on the card and in the footer; block all clears it', async ({ page }) => {
  const nine = Array.from({ length: 9 }, (_, i) => ({ name: `tool_${i + 1}`, description: `Tool number ${i + 1}` }));
  const state = await mockMcp(page, {
    tools: nine,
    servers: [{ name: 'pw', transport: 'stdio', command: 'npx', args: ['@playwright/mcp@latest'], env_keys: [], url: '', enabled: true, stages: ['requirements'], allowed_tools: [], approval_required_tools: [], preset: '' }],
  });
  await page.goto('/marketplace/mcp/pw');
  await expect(page.getByText('No tools yet. Run Test connection to discover them.')).toBeVisible();
  await page.getByRole('button', { name: 'Test connection', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Connected: 9 tools available.' })).toBeVisible();
  await expect(page.locator('.mcp-tool').filter({ hasText: 'tool_9' })).toContainText('Tool number 9');
  await page.getByRole('button', { name: 'Approve all', exact: true }).click();
  await expect(page.getByText('9 tools · 9 always allow · 0 ask · 0 blocked', { exact: true })).toBeVisible();
  await expect(page.getByText('Budget', { exact: true })).toBeVisible(); // live: more than 8 tools in a stage
  await footerSave(page).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible();
  expect(state.saved.at(-1)?.allowed_tools).toHaveLength(9);
  await crumbLink(page, 'MCP servers').click();
  await expect(card(page, 'pw').getByText('Stage Requirements: 9 MCP tools, max 8.')).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'Stage Requirements: 9 MCP tools, max 8' })).toBeVisible();
  await card(page, 'pw').getByRole('link', { name: 'Manage', exact: true }).click();
  await page.getByRole('button', { name: 'Block all', exact: true }).click();
  await footerSave(page).click();
  await expect(page.getByText('9 tools · 0 always allow · 0 ask · 9 blocked', { exact: true })).toBeVisible();
  await crumbLink(page, 'MCP servers').click();
  await expect(card(page, 'pw').getByText('9 tools · 0 always allow · 0 ask · 9 blocked')).toBeVisible();
  await expect(card(page, 'pw').getByText('Over budget')).toHaveCount(0);
});

test('MCP cards and the server page fit a 390 px screen with long commands and tool names', async ({ page }) => {
  const longTool = 'browser_snapshot_with_an_extremely_long_name_'.repeat(3);
  await mockMcp(page, {
    servers: [{ name: 'long', transport: 'stdio', command: 'npx', args: ['-y', '@modelcontextprotocol/server-filesystem', '/a/very/long/path/'.repeat(12)], env_keys: [], url: '', enabled: true, stages: ['requirements'], allowed_tools: [longTool], approval_required_tools: [], preset: '', discovered_tools: [{ name: longTool, description: 'Describes a very long tool. '.repeat(10) }] }],
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/marketplace/mcp');
  await expect(card(page, 'long')).toBeVisible();
  await expect.poll(() => noSideScroll(page)).toBe(true);
  await card(page, 'long').getByRole('link', { name: 'Manage', exact: true }).click();
  await expect(page.locator('#tools')).toBeVisible();
  await expect.poll(() => noSideScroll(page)).toBe(true);
  await expect(page.getByRole('switch', { name: 'Enabled' })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 720 });
});

for (const width of [1366, 1024]) for (const theme of ['light', 'dark'] as const) {
  test(`visual collisions: cards and the tools list at ${width}px ${theme}, with wide fonts`, async ({ page }) => {
    const long = 'a_tool_name_without_any_spaces_that_must_wrap_'.repeat(3);
    await mockMcp(page, {
      servers: [
        { name: 'playwright', transport: 'stdio', command: 'npx', args: ['@playwright/mcp@latest'], env_keys: ['API_TOKEN'], url: '', enabled: true, stages: ['requirements'], allowed_tools: ['browser_snapshot'], approval_required_tools: ['browser_navigate'], preset: '', discovered_tools: THREE },
        { name: 'long-name-server-for-layout-checks', transport: 'http', command: '', args: [], env_keys: [], url: `https://mcp.example.com/${'segment/'.repeat(30)}`, enabled: false, stages: [], allowed_tools: [long], approval_required_tools: [], preset: '', discovered_tools: [{ name: long, description: 'Long description. '.repeat(12) }] },
      ],
    });
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate(({ theme, css }) => {
      document.documentElement.dataset.theme = theme;
      const style = document.createElement('style'); style.id = 'audit-fonts'; style.textContent = css; document.head.append(style);
    }, { theme, css: WIDE_FONTS });
    await page.goto('/marketplace/mcp');
    await expect(card(page, 'playwright')).toBeVisible();
    expect(await layoutIssues(page), 'cards').toEqual([]);
    const overflowing = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>('.mcp-card')].filter(c => { const r = c.getBoundingClientRect(); return [...c.querySelectorAll<HTMLElement>('*')].some(el => el.getClientRects().length && el.getBoundingClientRect().right > r.right + 1); }).length);
    expect(overflowing, 'card contents past the card edge').toBe(0);
    await page.goto('/marketplace/mcp/long-name-server-for-layout-checks');
    await expect(page.locator('#tools')).toBeVisible();
    expect(await layoutIssues(page), 'server page').toEqual([]);
    const rows = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>('.mcp-tool')].map(r => r.getBoundingClientRect()).filter((r, i, all) => all.some((o, j) => j !== i && Math.min(r.bottom, o.bottom) - Math.max(r.top, o.top) > 2)).length);
    expect(rows, 'tool rows overlapping').toBe(0);
    await page.evaluate(() => { document.getElementById('audit-fonts')?.remove(); document.documentElement.dataset.theme = 'light'; });
  });
}
