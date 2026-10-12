import { test, expect, type Page } from '@playwright/test';

const APPROVE = /^(Approve repository inspection|Approve and proceed|Install in isolated environment|Run tests here)$/;

/** Widest overflowing elements (for diagnostics when the page scrolls sideways). */
async function overflowOffenders(page: Page) {
  return page.evaluate(() => {
    const limit = document.documentElement.clientWidth;
    const insideScroller = (el: Element) => { for (let p: Element | null = el; p && p !== document.body; p = p.parentElement) if (['auto', 'scroll', 'hidden'].includes(getComputedStyle(p).overflowX)) return true; return false; };
    const label = (el: Element) => `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''}${typeof el.className === 'string' && el.className ? `.${el.className.trim().split(/\s+/).slice(0, 3).join('.')}` : ''}`;
    return [...document.querySelectorAll('body *')]
      .map(el => ({ el, right: el.getBoundingClientRect().right, scroller: getComputedStyle(el).overflowX }))
      .filter(item => item.right > limit + 1 && !insideScroller(item.el))
      .slice(0, 8)
      .map(item => `${label(item.el)} right=${Math.round(item.right)} width=${Math.round(item.el.getBoundingClientRect().width)}`);
  });
}

async function expectNoHorizontalScroll(page: Page, where: string) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (overflow > 1) console.log(`OVERFLOW ${where}: ${overflow}px\n  ${(await overflowOffenders(page)).join('\n  ')}`);
  expect(overflow, `horizontal scroll at ${where}`).toBeLessThanOrEqual(1);
}

test('review gates have no horizontal overflow at 390px', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?page=new');
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill('https://github.com/acme/demo');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await expect(page.getByRole('button', { name: 'Approve repository inspection' })).toBeEnabled();
  const id = new URL(page.url()).searchParams.get('run')!;
  let budgetChecks = 0;
  for (let gate = 0; gate < 9; gate++) {
    await expect(page.getByRole('button', { name: APPROVE })).toBeEnabled({ timeout: 60000 });
    const pending = await (await request.get(`/api/harness/runs/${id}`)).json();
    await expectNoHorizontalScroll(page, `gate ${gate} ${pending.pending?.stage ?? ''}`);
    if (pending.pending?.stage === 'test_design' && pending.pending?.kind === 'review') {
      const budget = page.locator('.panel').filter({ has: page.getByRole('button', { name: 'Case budget', exact: true }) });
      if (await budget.count()) {
        budgetChecks++;
        const scrollable = await budget.locator('table').evaluate(table => {
          for (let el: Element | null = table; el; el = el.parentElement) if (['auto', 'scroll'].includes(getComputedStyle(el).overflowX)) return true;
          return false;
        });
        expect(scrollable).toBe(true);
      }
    }
    const before = pending;
    await page.getByRole('button', { name: APPROVE }).click();
    await expect.poll(async () => { const next = await (await request.get(`/api/harness/runs/${id}`)).json(); return !next.busy && (next.status === 'complete' || next.pending?.id !== before.pending?.id); }, { timeout: 60000 }).toBeTruthy();
    if ((await (await request.get(`/api/harness/runs/${id}`)).json()).status === 'complete') break;
  }
  await expectNoHorizontalScroll(page, 'final');
  expect(budgetChecks).toBeGreaterThan(0);
  await page.goto(`/?run=${id}&tab=Review`);
  await expect(page.getByRole('button', { name: 'Stop run' })).toHaveCount(0);
  for (const tab of ['Review', 'Errors', 'Artifacts', 'Generated tests', 'QA report', 'Test reports', 'Logs', 'Observability', 'Audit', 'Prompts']) {
    const tabButton = page.getByRole('button', { name: new RegExp(`^${tab}( \\d+)?$`) }).first();
    await tabButton.click();
    await expect(tabButton).toHaveAttribute('aria-pressed', 'true');
    await expectNoHorizontalScroll(page, `tab ${tab}`);
  }
  for (const pageName of ['Runs', 'New run', 'Marketplace', 'Configuration']) {
    const navButton = page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: new RegExp(`^${pageName}\\b`) });
    await navButton.click();
    await expect(navButton).toHaveClass(/active/);
    await expectNoHorizontalScroll(page, `page ${pageName}`);
  }
  await page.goto('/?page=prompts');
  await expectNoHorizontalScroll(page, 'page Prompts');
});
