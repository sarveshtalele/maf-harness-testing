import { test, expect, type Page } from '@playwright/test';

const nav = (page: Page) => page.getByRole('navigation', { name: 'Main navigation' });
const width = async (page: Page, selector: string) => Math.round((await page.locator(selector).boundingBox())!.width);
const runFixture = {
  id: 'icons', status: 'running', busy: false, service_busy: false, actions: [], pending: null, run_directory: '', operation: {}, progress: { percent: 10 },
  state: { repo_url: 'https://github.com/acme/demo', stages: ['repo_analysis', 'test_generation'], approved: {}, artifacts: {} }, errors: { items: [], counts: {} },
};

test('sidebar collapses to an icon rail, keeps navigation and persists across reload', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 860 });
  await page.goto('/');
  const toggle = page.getByRole('button', { name: 'Collapse sidebar', exact: true });
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const expandedWidth = await width(page, '.sidebar');
  const expandedMain = await width(page, 'main');
  await toggle.click();
  await expect(page.getByRole('button', { name: 'Expand sidebar', exact: true })).toHaveAttribute('aria-expanded', 'false');
  await expect.poll(() => width(page, '.sidebar')).toBe(64);
  await expect.poll(() => width(page, 'main')).toBeGreaterThan(expandedMain);
  expect(expandedWidth).toBeGreaterThan(64);
  await expect(page.locator('.sidebar .sidebar-run, .sidebar .sidebar-footer progress')).toBeHidden();
  await expect(page.getByRole('button', { name: 'Dark appearance', exact: true })).toBeVisible();
  await nav(page).getByRole('button', { name: 'New run', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'New test run' })).toBeVisible();
  await nav(page).getByRole('button', { name: /^Configuration\b/ }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Configuration' })).toBeVisible();
  await nav(page).getByRole('button', { name: /^Runs\b/ }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Repository runs' })).toBeVisible();
  await page.reload();
  await expect.poll(() => width(page, '.sidebar')).toBe(64);
  await page.getByRole('button', { name: 'Expand sidebar', exact: true }).click();
  await expect.poll(() => width(page, '.sidebar')).toBe(expandedWidth);
});

test('collapse toggle is the first control after the skip link and works from the keyboard', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  await page.keyboard.press('Tab');
  const toggle = page.getByRole('button', { name: 'Collapse sidebar', exact: true });
  await expect(toggle).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'Expand sidebar', exact: true })).toBeFocused();
  await expect.poll(() => width(page, '.sidebar')).toBe(64);
  await page.keyboard.press('Space');
  await expect(page.getByRole('button', { name: 'Collapse sidebar', exact: true })).toBeFocused();
  await expect.poll(() => width(page, '.sidebar')).toBeGreaterThan(64);
});

test('nav items, marketplace tabs and run tabs carry decorative icons', async ({ page }) => {
  await page.route('**/api/harness/**', route => {
    const path = new URL(route.request().url()).pathname.replace('/api/harness/', '');
    if (path === 'config') return route.fulfill({ json: { model: 'm', base_url: 'u', key_configured: true, daily: { used: 0, remaining: 1, limit: 1 }, stages: runFixture.state.stages, personas: {} } });
    if (path === 'runs') return route.fulfill({ json: { runs: [], busy: false } });
    if (path === 'runs/icons') return route.fulfill({ json: runFixture });
    return route.fulfill({ json: {} });
  });
  await page.goto('/?run=icons&tab=Review');
  for (const name of [/^Runs\b/, 'New run', 'Marketplace', 'Agent prompts', 'Configuration']) {
    await expect(nav(page).getByRole('button', { name })).toHaveCount(1);
    await expect(nav(page).getByRole('button', { name }).locator('svg[aria-hidden=true]')).toHaveCount(1);
  }
  for (const name of ['Review', 'Errors', 'Artifacts', 'Generated tests', 'QA report', 'Test reports', 'Logs', 'Observability', 'Audit', 'Prompts']) {
    await expect(page.getByRole('button', { name: new RegExp(`^${name}( \\d+)?$`) }).first().locator('svg[aria-hidden=true]')).toHaveCount(1);
  }
  await page.goto('/?page=marketplace');
  for (const name of ['Agent Skills', 'MCP servers']) await expect(page.getByRole('tab', { name, exact: true }).locator('svg[aria-hidden=true]')).toHaveCount(1);
});

test('mobile keeps the top bar, hides the collapse button and never scrolls sideways', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Collapse sidebar', exact: true })).toBeHidden();
  await page.evaluate(() => localStorage.setItem('maf:sidebar-collapsed', 'true'));
  await page.reload();
  await expect(nav(page).getByRole('button', { name: 'New run', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Collapse sidebar', exact: true })).toBeHidden();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
