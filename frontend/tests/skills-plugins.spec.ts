import { test, expect } from '@playwright/test';

const card = (name: string, extra: Record<string, unknown>) => ({
  name, description: `${name} description`, role: 'Companion', primary_stages: [], purpose: `${name} purpose`, instructions: '# T', resources: ['SKILL.md'],
  metadata: {}, reviewed_scripts: {}, sha256: 'abc', valid: true, locked: false, errors: [], warnings: [], provenance: {}, executions: [], stages: ['test_generation'], kind: 'model', ...extra,
});

test('skill cards show trust badges and stages; import and approve use the plugin API', async ({ page }) => {
  let skills = [card('junit-generator', { source: 'repo', verified: true, approved: false })];
  let imported = false; let approved = false;
  await page.route('**/api/harness/skills', route => route.fulfill({ json: { skills, upstream: {}, executions: [] } }));
  await page.route('**/api/harness/skills/import', async route => {
    imported = JSON.parse(route.request().postData() || '{}').zip_base64.length > 0;
    skills = [...skills, card('go-helper', { source: 'plugin', verified: false, approved: false })];
    await route.fulfill({ json: { name: 'go-helper', source: 'plugin', verified: false, approved: false, trusted: false } });
  });
  await page.route('**/api/harness/skills/go-helper/approve', async route => {
    approved = true;
    skills = skills.map(s => s.name === 'go-helper' ? { ...s, approved: true } : s);
    await route.fulfill({ json: { name: 'go-helper', source: 'plugin', verified: false, approved: true, trusted: true } });
  });
  await page.goto('/');
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Marketplace', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Agent Skills', exact: true })).toHaveAttribute('aria-selected', 'true');
  const junit = page.locator('.skill-card').filter({ hasText: 'junit-generator' });
  await expect(junit.getByText('Repo', { exact: true })).toBeVisible();
  await expect(junit.getByText('Verified', { exact: true })).toBeVisible();
  await expect(junit.getByLabel('junit-generator stages').getByText('Test Generation')).toBeVisible();
  await expect(junit.getByRole('button', { name: /^Approve/ })).toHaveCount(0);

  await page.getByLabel('Add skill (.zip)').setInputFiles({ name: 'go-helper.zip', mimeType: 'application/zip', buffer: Buffer.from('PK\u0005\u0006' + '\0'.repeat(18)) });
  const go = page.locator('.skill-card').filter({ hasText: 'go-helper' });
  await expect(go.getByText('Plugin', { exact: true })).toBeVisible();
  await expect(go.getByText('Unverified', { exact: true })).toBeVisible();
  expect(imported).toBeTruthy();

  await go.getByRole('button', { name: 'Approve go-helper' }).click();
  const approveDialog = page.getByRole('dialog', { name: 'Approve go-helper?' });
  await expect(approveDialog).toContainText('scripts to run');
  await approveDialog.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(go.getByText('Approved', { exact: true })).toBeVisible();
  expect(approved).toBeTruthy();
  await expect(go.getByRole('button', { name: /^Approve/ })).toHaveCount(0);
});

test('/?page=skills still opens the Agent Skills list in Marketplace', async ({ page }) => {
  await page.route('**/api/harness/skills', route => route.fulfill({ json: { skills: [card('junit-generator', { source: 'repo', verified: true, approved: false })], upstream: {}, executions: [] } }));
  await page.goto('/?page=skills');
  await expect(page.getByRole('heading', { name: 'Agent skills', exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Agent Skills', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.skill-card').filter({ hasText: 'junit-generator' })).toBeVisible();
});
