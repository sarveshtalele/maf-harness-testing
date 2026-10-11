import { test, expect } from '@playwright/test';

test('theme toggle persists across reload', async ({ page }) => {
  await page.goto('/');
  const html = page.locator('html');
  await page.getByRole('button', { name: '◐ Dark appearance' }).click();
  await expect(html).toHaveAttribute('data-theme', 'dark');
  await page.reload();
  await expect(html).toHaveAttribute('data-theme', 'dark');
  await expect(page.getByRole('button', { name: '☀ Light appearance' })).toBeVisible();
  await page.getByRole('button', { name: '☀ Light appearance' }).click();
  await expect(html).toHaveAttribute('data-theme', 'light');
  await page.reload();
  await expect(html).toHaveAttribute('data-theme', 'light');
});

test('agent prompts reset to defaults', async ({ page }) => {
  await page.goto('/?page=prompts');
  const guidance = page.getByLabel('Review guidance', { exact: true });
  const persona = page.getByLabel('Complete persona prompt', { exact: true });
  const defaultGuidance = await guidance.inputValue();
  const defaultPersona = await persona.inputValue();
  await expect(persona).not.toHaveValue('');
  // Restore example guidance replaces the edited text with the shipped default, then Save keeps it.
  await guidance.fill('Reset probe: this text must not survive a restore.');
  await page.getByRole('button', { name: /^Restore example/ }).click();
  await expect(guidance).toHaveValue(defaultGuidance);
  await page.getByRole('button', { name: 'Save prompts', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Saved as defaults');
  // Persona: edit, restore the stage default, and confirm the saved default is unchanged after reload.
  await persona.fill('Reset probe persona text.');
  await page.getByRole('button', { name: 'Restore stage default', exact: true }).click();
  await expect(persona).toHaveValue(defaultPersona);
  await page.getByRole('button', { name: 'Save persona', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Saved as defaults');
  await page.reload();
  await expect(guidance).toHaveValue(defaultGuidance);
  await expect(persona).toHaveValue(defaultPersona);
});
