import { test, expect } from '@playwright/test';

test('complete browser workflow, layouts, evidence, downloads and stale gates', async ({ page, request }, testInfo) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByText('Your first run starts here')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Adjust layout', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Resize sidebar', exact: true }).press('ArrowRight');
  const sidebarWidth = (await page.locator('.sidebar').boundingBox())!.width;
  await page.reload();
  await expect.poll(async () => (await page.locator('.sidebar').boundingBox())!.width).toBeCloseTo(sidebarWidth, 0);
  await page.getByRole('button', { name: 'New run', exact: true }).click();
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill('https://github.com/acme/demo');
  await page.getByRole('textbox', { name: 'Repository knowledge' }).fill('Use the repository README and Python 3.12.');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await expect(page.getByRole('button', { name: 'Approve repository inspection' })).toBeEnabled();
  const key = new URL(page.url()).searchParams.get('run')!;
  const original = await (await request.get(`/api/harness/runs/${key}`)).json();
  expect(original.state.repo_knowledge).toBe('Use the repository README and Python 3.12.');
  await page.getByRole('button', { name: 'Approve repository inspection' }).click();
  await expect(page.getByRole('button', { name: 'Approve and proceed', exact: true })).toBeEnabled();
  const stale = await request.post(`/api/harness/runs/${key}/respond`, { data: { request_id: original.pending.id, decision: 'approve' } });
  expect(stale.status()).toBe(409);
  await expect(page.getByRole('button', { name: 'Request revision', exact: true })).toBeDisabled();
  for (let i = 0; i < 9; i++) {
    const approve = page.getByRole('button', { name: /^(Approve and proceed|Install in isolated environment|Run tests here)$/ });
    await expect(approve).toBeEnabled();
    const before = await (await request.get(`/api/harness/runs/${key}`)).json();
    if (before.pending.stage === 'environment_setup' && before.pending.kind === 'tool') {
      await expect(page.getByRole('heading', { name: 'Step-by-step repository setup' })).toBeVisible();
      await expect(page.getByRole('region', { name: 'Repository setup guide' })).toBeVisible();
    }
    await approve.click();
    await expect.poll(async () => { const next = await (await request.get(`/api/harness/runs/${key}`)).json(); return !next.busy && (next.status === 'complete' || next.pending?.id !== before.pending?.id); }, { timeout: 60000 }).toBeTruthy();
  }
  await expect.poll(async () => (await (await request.get(`/api/harness/runs/${key}`)).json()).status).toBe('complete');
  await page.reload();
  await page.getByRole('button', { name: 'Prompts', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Run prompts', exact: true })).toBeVisible();
  await page.getByRole('button', { name: /1\. Requirements/ }).click();
  const fullPrompt = page.getByLabel('Complete user prompt and output contract', { exact: true });
  await expect(fullPrompt).not.toHaveValue('');
  const originalPrompt = await fullPrompt.inputValue();
  await fullPrompt.fill(originalPrompt + '\nBrowser proof saved override.');
  await page.getByRole('button', { name: 'Save for next attempt', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Saved for future calls in this run');
  const savedSettings = await (await request.get(`/api/harness/runs/${key}/prompts`)).json();
  expect(savedSettings.settings.overrides.requirements).toBe(originalPrompt + '\nBrowser proof saved override.');
  await page.reload();
  await page.getByRole('button', { name: /1\. Requirements/ }).click();
  await expect(fullPrompt).toHaveValue(originalPrompt);
  await page.getByRole('button', { name: 'QA report', exact: true }).click();
  await expect(page.getByText('PASS', { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Test reports', exact: true }).click();
  const report = page.frameLocator('iframe[title="Playwright report"]');
  await expect(report.getByRole('heading', { name: 'Browser report fixture' })).toBeVisible();
  await expect(report.locator('#verdict')).toHaveText('PASS');
  expect(await report.locator('body').getAttribute('data-isolated')).toBe('yes');
  expect(await page.locator('body').getAttribute('data-report-escaped')).toBeNull();
  await page.getByRole('button', { name: 'Logs', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Log output' })).toBeVisible();
  await page.getByRole('searchbox', { name: 'Find in output' }).fill('not-a-real-log-line');
  await expect(page.getByRole('region', { name: 'Log output' })).toBeEmpty();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download deliverable ZIP ↓' }).click();
  expect((await downloadPromise).suggestedFilename()).toMatch(/\.zip$/);
  await page.getByRole('button', { name: 'Generated tests', exact: true }).click();
  await expect(page.locator('.source-code')).toContainText('def test_');
  await page.getByRole('button', { name: 'Observability', exact: true }).click();
  await page.getByRole('button', { name: 'Expand Stage activity & token usage' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  for (const name of ['Skills', 'Tools', 'Automation', 'Timeline', 'Traceability', 'Integrity', 'Export']) {
    await page.getByRole('button', { name, exact: true }).click();
    await expect(page.locator('.panel').first()).toBeVisible();
  }
  await page.getByRole('button', { name: 'Audit', exact: true }).click();
  await expect(page.locator('.audit-feed').getByText('Human Response', { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Expand Approval & activity audit' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Close expanded view' }).click();
  const auditPanel = page.locator('.panel').filter({ has: page.getByRole('button', { name: 'Approval & activity audit', exact: true }) });
  const auditResize = page.getByRole('button', { name: 'Resize Approval & activity audit', exact: true });
  await auditResize.press('ArrowUp');
  await auditResize.press('ArrowLeft');
  const auditSize = (await auditPanel.boundingBox())!;
  await page.reload();
  await expect.poll(async () => (await auditPanel.boundingBox())!.height).toBeCloseTo(auditSize.height, 0);
  await expect.poll(async () => (await auditPanel.boundingBox())!.width).toBeCloseTo(auditSize.width, 0);
  await expect(page.getByText('Adjust panel', { exact: true })).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath('audit-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('button', { name: 'Audit', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  const secret = await request.get('/api/harness/config');
  expect(JSON.stringify(await secret.json())).not.toContain('fixture-configured-flag');
  expect(errors).toEqual([]);
});

test('failed execution shows recorded reason, improvement advice and inspectable results', async ({ page, request }) => {
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
  await expect(page.locator('.failure-card')).toHaveCount(1);
  await expect(page.locator('.failure-reason')).toContainText('assert 3 == 4');
  await expect(page.locator('.failure-next')).toContainText('never weaken the assertion');
  const table = page.locator('.panel').filter({ has: page.getByRole('button', { name: 'Case results', exact: true }) });
  await table.getByRole('button', { name: 'Status', exact: false }).click();
  await table.getByRole('button', { name: 'Inspect record 1' }).click();
  await expect(page.getByRole('dialog', { name: 'Record details' })).toContainText('assert 3 == 4');
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.getByRole('button', { name: 'Test reports', exact: true }).click();
  await expect(page.frameLocator('iframe').locator('#verdict')).toHaveText('FAIL');
});

test('deny, retry, revise, stop, invalid inputs and hostile origins', async ({ page, request }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'New run', exact: true }).click();
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill('https://example.com/repo');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await expect(page.locator('.notice[role=alert]')).toBeVisible();
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill('https://github.com/acme/demo');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await expect(page.getByRole('button', { name: 'Deny action' })).toBeEnabled();
  await page.getByRole('button', { name: 'Deny action' }).click();
  await expect(page.getByRole('button', { name: 'Retry stage' })).toBeEnabled();
  await page.getByRole('button', { name: 'Retry stage' }).click();
  await expect(page.getByRole('button', { name: 'Approve repository inspection' })).toBeEnabled();
  await page.getByRole('button', { name: 'Approve repository inspection' }).click();
  await page.getByRole('textbox', { name: 'Modification instructions' }).fill('Add a human review note.');
  await page.getByRole('button', { name: 'Request revision' }).click();
  await expect(page.getByText('Add a human review note.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Stop workflow' }).click();
  await expect(page.getByText('Workflow stopped. Approved evidence remains available.')).toBeVisible();
  const blocked = await request.post('/api/harness/runs', { headers: { Origin: 'https://evil.example' }, data: { repo_url: 'https://github.com/acme/demo' } });
  expect(blocked.status()).toBe(403);
});


test('mouse resizing persists and project skill cards show real package details', async ({ page }) => {
  await page.goto('/');
  async function drag(name: string, dx: number, dy = 0) {
    const handle = page.getByRole('button', { name, exact: true });
    await handle.scrollIntoViewIfNeeded();
    const box = (await handle.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy, { steps: 8 });
    await page.mouse.up();
  }
  const sidebar = page.locator('.sidebar');
  const before = (await sidebar.boundingBox())!.width;
  await drag('Resize sidebar', 40);
  await expect.poll(async () => (await sidebar.boundingBox())!.width).toBeCloseTo(before + 40, 0);
  await page.reload();
  await expect.poll(async () => (await sidebar.boundingBox())!.width).toBeCloseTo(before + 40, 0);
  await page.getByRole('button', { name: 'New run', exact: true }).click();
  const columns = page.locator('.workspace-grid');
  const first = columns.locator(':scope > .panel').first();
  const originalWidth = (await first.boundingBox())!.width;
  await drag('Resize workspace columns', -80);
  await expect.poll(async () => (await first.boundingBox())!.width).toBeLessThan(originalWidth - 60);
  const panel = page.locator('.panel').filter({ has: page.getByRole('button', { name: 'Repository details', exact: true }) });
  const initial = (await panel.boundingBox())!;
  await drag('Resize Repository details', -50, 80);
  await expect.poll(async () => (await panel.boundingBox())!.height).toBeGreaterThan(initial.height + 60);
  await expect.poll(async () => (await panel.boundingBox())!.width).toBeCloseTo(initial.width - 50, 0);
  const resized = (await panel.boundingBox())!;
  await page.reload();
  await page.getByRole('button', { name: 'New run', exact: true }).click();
  await expect.poll(async () => (await panel.boundingBox())!.width).toBeCloseTo(resized.width, 0);
  await expect.poll(async () => (await panel.boundingBox())!.height).toBeCloseTo(resized.height, 0);
  await page.getByRole('button', { name: 'Agent skills', exact: true }).click();
  await expect(page.locator('.skill-card')).toHaveCount(10);
  await page.getByRole('searchbox', { name: 'Search agent skills' }).fill('playwright');
  await expect(page.locator('.skill-card')).toHaveCount(1);
  await expect(page.getByText('Upstream library provenance', { exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: 'View skill details →', exact: true }).click();
  await expect(page).toHaveURL(/skill=playwright-test-generator/);
  await expect(page.locator('.skill-document h2').first()).toContainText('Playwright');
  await expect(page.locator('.markdown-document pre').first()).not.toBeEmpty();
  await expect(page.locator('.markdown-document table').first()).toBeVisible();
  await expect(page.getByText('Upstream library provenance', { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.locator('.skill-document')).toBeVisible();
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--primary').trim())).toBe('#202024');
  await page.getByRole('button', { name: 'Dark appearance' }).click();
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--primary').trim())).toBe('#eeeeef');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test('editable prompt defaults persist', async ({ page }) => {
  await page.goto('/?page=prompts');
  await expect(page.getByRole('heading', { name: 'Agent prompts', exact: true })).toBeVisible();
  const guidance = page.getByLabel('Review guidance', { exact: true });
  const original = await guidance.inputValue();
  await guidance.fill('Browser regression guidance: use real source and isolated data.');
  await page.getByRole('button', { name: 'Save prompts', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Saved as defaults');
  await page.reload();
  await expect(guidance).toHaveValue('Browser regression guidance: use real source and isolated data.');
  await page.getByLabel('Complete persona prompt', { exact: true }).fill('Editable requirements persona: source evidence only.');
  await page.getByRole('button', { name: 'Save persona', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Saved as defaults');
  await guidance.fill(original);
  await page.getByRole('button', { name: 'Restore stage default', exact: true }).click();
  await page.getByRole('button', { name: 'Save prompts', exact: true }).click();
});


test('platform branding and navigation exclude Azure proposals', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveTitle('Agentic QE Platform');
  await expect(page.locator('.brand')).toContainText('Agentic QE');
  await expect(page.getByRole('button', { name: 'Azure guide', exact: true })).toHaveCount(0);
});


test('aligned editors, configuration separation and confirmed run removal', async ({ page, request }) => {
  await page.goto('/?page=prompts');
  const textarea = page.getByLabel('Review guidance', { exact: true });
  await expect(textarea).toBeVisible();
  const inputBox = (await textarea.boundingBox())!;
  const saveBox = (await page.getByRole('button', { name: 'Save prompts', exact: true }).boundingBox())!;
  expect(saveBox.y - (inputBox.y + inputBox.height)).toBeGreaterThanOrEqual(15);
  const resize = page.getByRole('button', { name: 'Resize Repository review prompt', exact: true });
  await resize.press('ArrowLeft');
  await resize.press('Home');
  await expect.poll(async () => (await page.locator('.prompt-editor .panel').first().boundingBox())!.width).toBeGreaterThan(900);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.getByRole('button', { name: 'Configuration', exact: true }).click();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Configuration', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Agent personas', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Edit agent prompts →', exact: true })).toHaveCount(0);
  const response = await request.post('/api/harness/runs', { data: { repo_url: 'https://github.com/acme/demo' } });
  const key = (await response.json()).id;
  await expect.poll(async () => (await (await request.get(`/api/harness/runs/${key}`)).json()).busy).toBe(false);
  await page.getByRole('button', { name: /^Runs\b/ }).click();
  const remove = page.getByRole('button', { name: `Delete run ${key}`, exact: true });
  await expect(remove).toBeEnabled(); await remove.click();
  await page.getByRole('button', { name: 'Keep run', exact: true }).click();
  await expect(remove).toBeVisible(); await remove.click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete run', exact: true }).click();
  await expect(remove).toHaveCount(0);
  expect((await request.get(`/api/harness/runs/${key}`)).status()).toBe(404);
});
