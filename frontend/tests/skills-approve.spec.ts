import { test, expect } from '@playwright/test';

// Approval names the reviewed tree hash; a skill that changed since review is refused and the list reloads.
const HASH = 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0';
const skill = {
  name: 'demo-skill', description: 'Demo skill', role: 'Test generator', primary_stages: ['requirements'], instructions: '# Demo', resources: [],
  metadata: {}, reviewed_scripts: {}, sha256: HASH, tree_sha256: HASH, source: 'plugin', verified: false, approved: false, valid: true, locked: false,
  errors: [], warnings: [], provenance: {}, executions: [], stages: [], kind: 'companion',
};

test('approve sends the reviewed tree hash and shows a changed skill refusal with a reload', async ({ page }) => {
  const posts: Record<string, unknown>[] = [];
  let listings = 0;
  await page.route('**/api/harness/**', route => {
    const path = new URL(route.request().url()).pathname.replace('/api/harness/', '');
    if (route.request().method() === 'POST' && path === 'skills/demo-skill/approve') {
      posts.push(route.request().postDataJSON());
      return route.fulfill({ status: 409, json: { error: 'demo-skill changed since it was reviewed; reload and review it again.' } });
    }
    if (path === 'skills') { listings++; return route.fulfill({ json: { skills: [skill], upstream: {} } }); }
    if (path === 'config') return route.fulfill({ json: { model: 'm', base_url: 'u', key_configured: true, daily: { used: 0, remaining: 1, limit: 1 }, stages: ['requirements'], personas: {} } });
    if (path === 'runs') return route.fulfill({ json: { runs: [], busy: false } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/marketplace');
  await page.getByRole('button', { name: 'Approve demo-skill' }).click();
  const dialog = page.getByRole('dialog', { name: 'Approve demo-skill?' });
  await expect(dialog).toContainText('a1b2c3d4e5f6');
  const before = listings;
  await dialog.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect.poll(() => posts).toEqual([{ tree_sha256: HASH }]);
  await expect(page.locator('.notice.bad')).toContainText('changed since it was reviewed');
  await expect.poll(() => listings).toBeGreaterThan(before);
});
