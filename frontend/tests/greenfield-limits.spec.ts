import { test, expect } from '@playwright/test';

test('greenfield limits are checked before upload', async ({ page }) => {
  const posts: string[] = [];
  await page.route('**/api/harness/**', route => {
    const path = new URL(route.request().url()).pathname.replace('/api/harness/', '');
    if (route.request().method() === 'POST') { posts.push(path); return route.fulfill({ json: { id: 'abc' } }); }
    if (path === 'config') return route.fulfill({ json: { model: 'm', base_url: 'u', key_configured: true, case_budget_default: 2, daily: { used: 0, remaining: 1, limit: 1 }, stages: ['repo_analysis'], personas: {} } });
    if (path === 'runs') return route.fulfill({ json: { runs: [], busy: false } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/?page=new');
  await page.getByRole('radio', { name: 'Greenfield specs' }).check();
  const input = page.getByLabel('Specification documents');
  const start = page.getByRole('button', { name: 'Start repository review →' });
  const cases: { files: { name: string; mimeType: string; buffer: Buffer }[]; message: RegExp }[] = [
    { files: Array.from({ length: 11 }, (_, i) => ({ name: `spec-${i + 1}.md`, mimeType: 'text/markdown', buffer: Buffer.from('# Spec') })), message: /at most 10 specification documents; you selected 11/ },
    { files: [{ name: 'huge-requirements.md', mimeType: 'text/markdown', buffer: Buffer.alloc(11 * 1024 * 1024, 'a') }], message: /Largest: huge-requirements\.md \(11\.0 MB\)/ },
    { files: [{ name: 'stories.xlsx', mimeType: 'application/octet-stream', buffer: Buffer.from('x') }], message: /Unsupported file type: stories\.xlsx/ },
  ];
  for (const { files, message } of cases) {
    await input.setInputFiles(files);
    await start.click();
    const alert = page.locator('form [role="alert"]');
    await expect(alert).toContainText(message);
    await expect(page.getByRole('button', { name: 'Refresh connection' })).toHaveCount(0);
    expect(posts).toEqual([]);
  }
});
