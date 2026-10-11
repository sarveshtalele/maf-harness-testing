import { test, expect, type Locator, type Page } from '@playwright/test';

/** Presses Tab (keyboard only) until the target is focused; each attempt is checked, the whole search is bounded. */
async function tabTo(page: Page, target: Locator) {
  await expect(async () => {
    await page.keyboard.press('Tab');
    await expect(target).toBeFocused({ timeout: 300 });
  }).toPass({ timeout: 15000 });
}

test('keyboard-only journey: skip link, New run with Enter, dialogs close on Escape and return focus', async ({ page, request }) => {
  await page.goto('/');
  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Skip to content' });
  await expect(skip).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main-content')).toBeFocused();

  // Start from the first sidebar control, then Tab forward to New run.
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button').first().focus();
  await tabTo(page, page.getByRole('button', { name: 'New run', exact: true }));
  await page.keyboard.press('Enter');
  const url = page.getByRole('textbox', { name: 'Public GitHub repository URL' });
  await url.focus();
  await page.keyboard.type('https://github.com/acme/keyboard');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'Approve repository inspection' })).toBeEnabled({ timeout: 60000 }); // CI runners reach the first gate slower
  const id = new URL(page.url()).searchParams.get('run')!;

  // Rename dialog: Escape closes it and focus returns to the trigger.
  const rename = page.getByRole('button', { name: 'Rename run', exact: true });
  await rename.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: 'Rename run' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(rename).toBeFocused();

  // Delete dialog from the run list: Escape returns focus to Delete.
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: /^Runs\b/ }).click();
  const remove = page.getByRole('button', { name: `Delete run ${id}`, exact: true });
  await remove.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: 'Delete this run?' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(remove).toBeFocused();
  await expect(remove).toBeVisible();

  // Stop dialog needs active work: start the slow fixture, open Stop with the keyboard, Escape, then really stop.
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'New run', exact: true }).click();
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill('https://github.com/acme/slow');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await page.getByRole('button', { name: 'Approve repository inspection', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Approve and proceed', exact: true })).toBeEnabled({ timeout: 60000 });
  await page.getByRole('button', { name: 'Approve and proceed', exact: true }).click();
  const stop = page.getByRole('button', { name: 'Stop run', exact: true });
  await expect(stop).toBeEnabled();
  await stop.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: 'Stop this run?' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(stop).toBeFocused();
  await stop.click();
  await page.getByRole('dialog').getByRole('button', { name: 'Stop run', exact: true }).click();
  await expect(page.getByText('Workflow stopped. Approved evidence remains available.', { exact: true })).toBeVisible();
  await expect.poll(async () => (await (await request.get(`/api/harness/runs/${id}`)).json()).busy).toBe(false);
});
