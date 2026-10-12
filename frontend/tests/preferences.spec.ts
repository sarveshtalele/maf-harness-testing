import { test, expect } from '@playwright/test';

test('theme toggle persists across reload', async ({ page }) => {
  await page.goto('/');
  const html = page.locator('html');
  await page.getByRole('button', { name: 'Dark appearance' }).click();
  await expect(html).toHaveAttribute('data-theme', 'dark');
  await page.reload();
  await expect(html).toHaveAttribute('data-theme', 'dark');
  await expect(page.getByRole('button', { name: 'Light appearance' })).toBeVisible();
  await page.getByRole('button', { name: 'Light appearance' }).click();
  await expect(html).toHaveAttribute('data-theme', 'light');
  await page.reload();
  await expect(html).toHaveAttribute('data-theme', 'light');
});

test('agent prompts reset to defaults', async ({ page, request }) => {
  // Read the saved values first; the finally block puts exactly these back, whatever the test changed.
  const catalog = await (await request.get('/api/harness/prompts')).json();
  const originals = { repository_prompt: catalog.settings.repository_prompt, personas: catalog.settings.personas };
  const shipped = { repository_prompt: catalog.defaults.repository_prompt, requirements: catalog.defaults.personas.requirements };
  try {
    await page.goto('/?page=prompts');
    const guidance = page.getByLabel('Review guidance', { exact: true });
    const persona = page.getByLabel('Complete persona prompt', { exact: true });
    await expect(persona).not.toHaveValue('');
    // Restore example guidance replaces the edited text with the shipped default; Save stores that default.
    await guidance.fill('Reset probe: this text must not survive a restore.');
    await page.getByRole('button', { name: /^Restore example/ }).click();
    await expect(guidance).toHaveValue(shipped.repository_prompt);
    await page.getByRole('button', { name: 'Save prompts', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Saved as defaults');
    // Persona: edit, restore the stage default, save, and confirm after reload.
    await persona.fill('Reset probe persona text.');
    await page.getByRole('button', { name: 'Restore stage default', exact: true }).click();
    await expect(persona).toHaveValue(shipped.requirements);
    await page.getByRole('button', { name: 'Save persona', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Saved as defaults');
    await page.reload();
    await expect(guidance).toHaveValue(shipped.repository_prompt);
    await expect(persona).toHaveValue(shipped.requirements);
  } finally {
    const restored = await request.post('/api/harness/prompts', { data: originals });
    expect(restored.ok()).toBeTruthy();
  }
});
