import { test, expect } from '@playwright/test';

// Uses the failing fixture repository so the saved QA report has FAIL verdict and failed status cells.
test('QA report shows verdict banner, status badges, scrollable tables and no overflow at 390px', async ({ page, request }, testInfo) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'New run', exact: true }).click();
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill('https://github.com/acme/failure');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await expect(page.getByRole('button', { name: 'Approve repository inspection' })).toBeEnabled();
  const id = new URL(page.url()).searchParams.get('run')!;
  for (let i = 0; i < 10; i++) {
    const before = await (await request.get(`/api/harness/runs/${id}`)).json();
    await page.getByRole('button', { name: /^(Approve repository inspection|Approve and proceed|Install in isolated environment|Run tests here)$/ }).click();
    await expect.poll(async () => { const next = await (await request.get(`/api/harness/runs/${id}`)).json(); return !next.busy && next.pending?.id !== before.pending?.id; }, { timeout: 60000 }).toBeTruthy();
  }
  await page.getByRole('button', { name: 'QA report', exact: true }).click();
  await expect(page.locator('section.report-verdict')).toHaveCount(1, { timeout: 30000 });
  await expect(page.locator('section.report-verdict.bad')).toContainText('FAIL');
  await expect(page.locator('section.report-verdict')).toHaveAttribute('aria-label', 'Verdict FAIL');

  // Status cells in the saved QA report render as text badges, never colour alone.
  const scroll = page.locator('.markdown-table').first();
  await expect(scroll).toBeVisible();
  await expect(scroll).toHaveAttribute('role', 'region');
  await expect(scroll).toHaveCSS('overflow-x', 'auto');
  await expect(scroll.locator('table > caption')).toHaveCount(1);
  await expect(scroll.locator('thead th').first()).toHaveCSS('position', 'sticky');
  await expect(page.locator('.markdown-table .badge.bad').filter({ hasText: /^(failed|error|generation_failed)$/ }).first()).toBeVisible();

  await page.screenshot({ path: testInfo.outputPath("qa-document-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("qa-document-390.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await expect(page.locator('.markdown-table').first()).toBeVisible();
  expect(await page.locator('.markdown-table').first().evaluate(el => el.scrollWidth >= el.clientWidth)).toBeTruthy();
});
