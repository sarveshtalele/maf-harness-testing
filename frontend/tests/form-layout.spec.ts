import { test, expect } from '@playwright/test';

const config = { model: 'm', base_url: 'u', key_configured: true, case_budget_default: 2, daily: { used: 0, remaining: 1, limit: 1 }, stages: ['repo_analysis'], personas: {} };
const viewports = [{ name: '1280x800', size: { width: 1280, height: 800 } }, { name: '390px phone', size: { width: 390, height: 844 } }];

for (const { name, size } of viewports) {
  test(`greenfield Jira labels stack above their inputs at ${name}`, async ({ page }) => {
    await page.setViewportSize(size);
    await page.route('**/api/harness/config', route => route.fulfill({ json: config }));
    await page.route('**/api/harness/runs', route => route.fulfill({ json: { runs: [], busy: false } }));
    await page.goto('/?page=new');
    await page.getByRole('radio', { name: 'Greenfield specs' }).check();
    const labels = page.locator('fieldset.jira-fields > label');
    await expect(labels).toHaveCount(5);
    for (let i = 0; i < 5; i++) {
      const label = labels.nth(i);
      const labelBox = (await label.boundingBox())!;
      const inputBox = (await label.locator('input').boundingBox())!;
      // Text top: the rectangle of the label's own text node, not of the input inside it.
      const textTop = await label.evaluate(el => {
        // First non-empty text node of the label, whatever element comes first.
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        let node = walker.nextNode();
        while (node && !(node.textContent || '').trim()) node = walker.nextNode();
        const range = document.createRange(); range.selectNodeContents(node!);
        return range.getBoundingClientRect().top;
      });
      expect(inputBox.y - textTop, `input below label text ${i}`).toBeGreaterThanOrEqual(10);
      expect(labelBox.width, `label box width ${i}`).toBeGreaterThanOrEqual(40);
    }
  });
}
