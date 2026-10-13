import { test, expect } from '@playwright/test';

// Uses the failing fixture repository so the saved QA report has FAIL verdict and failed status cells.
test('QA report shows a structured verdict, status badges, scrollable tables and no overflow at 390px', async ({ page, request }, testInfo) => {
  await page.goto('/runs/new');
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill('https://github.com/acme/failure');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await expect(page.getByRole('button', { name: 'Approve repository inspection' })).toBeEnabled();
  const id = new URL(page.url()).pathname.split('/')[2];
  for (let i = 0; i < 10; i++) {
    const before = await (await request.get(`/api/harness/runs/${id}`)).json();
    await page.getByRole('button', { name: /^(Approve repository inspection|Approve and proceed|Install in isolated environment|Run tests here)$/ }).click();
    await expect.poll(async () => { const next = await (await request.get(`/api/harness/runs/${id}`)).json(); return !next.busy && next.pending?.id !== before.pending?.id; }, { timeout: 60000 }).toBeTruthy();
  }
  await page.goto(`/runs/${id}/qa`);
  const verdict = page.getByRole('status').filter({ has: page.locator('strong', { hasText: 'FAIL' }) }).first();
  await expect(verdict).toBeVisible({ timeout: 30000 });
  await expect(verdict).toHaveClass(/bad/);
  // Structured tables, not Markdown: each table has a caption and a focusable scroll region.
  await page.getByRole('button', { name: 'Case results' }).click();
  const wrapper = page.getByRole('region', { name: 'Case results' });
  await expect(wrapper).toBeVisible();
  await expect(wrapper).toHaveAttribute('tabindex', '0');
  await expect(wrapper.locator('table > caption')).toHaveCount(1);
  await expect(wrapper.locator('thead th').first()).toHaveCSS('position', 'sticky');
  await expect(wrapper.locator('.badge.bad').filter({ hasText: /^(Failed|Error|Generation Failed)$/ }).first()).toBeVisible();
  // Behaviour split (product, component, characterization), failure split and the measured-coverage state.
  await expect(page.getByRole('button', { name: 'Behaviour: product, component, characterization' })).toBeVisible();
  const product = page.getByRole('region', { name: 'Product behaviour' });
  await expect(product).toBeVisible();
  await expect(product.locator('td').filter({ hasText: 'specified' }).first()).toBeVisible();
  await expect(page.getByRole('region', { name: 'Component behaviour' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Test defects (auto-repaired / still failing)' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Test defects still failing' })).toBeVisible();
  await expect(page.getByText('Line and branch coverage: not measured', { exact: false }).first()).toBeVisible();
  // Markdown and JSON are offered only as downloads.
  await expect(page.locator('a[download]', { hasText: 'Markdown' }).first()).toBeVisible();
  await expect(page.locator('a[download]', { hasText: 'JSON' }).first()).toBeVisible();
  await expect(page.locator('pre')).toHaveCount(0);

  await page.screenshot({ path: testInfo.outputPath('qa-document-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath('qa-document-390.png'), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await expect(wrapper).toBeVisible();
  // The wide case table scrolls inside its wrapper instead of widening the page.
  expect(await wrapper.evaluate(el => el.scrollWidth > el.clientWidth)).toBeTruthy();
});
