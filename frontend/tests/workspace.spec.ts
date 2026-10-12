import { test, expect, type Page } from '@playwright/test';

const CASE_HEADERS = ['Sno', 'Requirement ID', 'Test Case ID', 'Test Case Description', 'Steps', 'Expected Result'];
const tab = (page: Page, name: string) => page.getByRole('navigation', { name: 'Run views' }).getByRole('link', { name, exact: true });
const runId = (page: Page) => new URL(page.url()).pathname.split('/')[2];

async function expectCaseTableHeaders(page: Page) {
  const table = page.getByRole('region', { name: 'Positive cases' });
  const headers = table.getByRole('columnheader');
  await expect(headers).toHaveCount(CASE_HEADERS.length);
  for (let i = 0; i < CASE_HEADERS.length; i++) await expect(headers.nth(i)).toContainText(CASE_HEADERS[i]);
  // Real data: Sno restarts at 1 in the second category, steps are an ordered list, and the case linked only through
  // an acceptance criterion shows its parent requirement (not a dash).
  await expect(page.getByRole('region', { name: 'Negative cases' }).locator('tbody tr').first().locator('td').first()).toHaveText('1');
  await expect(page.locator('.case-steps li').first()).toHaveText('Call add(1, 2)');
  const criterionRow = page.locator('.data-table tbody tr').filter({ hasText: 'add returns sum for the criterion' });
  await expect(criterionRow.locator('td').nth(1)).toHaveText(/^REQ-/);
  await expect(criterionRow.locator('.case-meta')).toHaveText('P2 · unit · Developer · service.add');
}

test('complete browser workflow, layouts, evidence, downloads and stale gates', async ({ page, request }, testInfo) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page).toHaveURL(/\/runs$/);
  await expect(page.getByText('Your first run starts here')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Adjust layout', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Resize sidebar', exact: true }).press('ArrowRight');
  const sidebarWidth = (await page.locator('.sidebar').boundingBox())!.width;
  await page.reload();
  await expect.poll(async () => (await page.locator('.sidebar').boundingBox())!.width).toBeCloseTo(sidebarWidth, 0);
  await page.getByRole('link', { name: 'New run', exact: true }).click();
  await expect(page).toHaveURL(/\/runs\/new$/);
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill('https://github.com/acme/demo');
  await page.getByRole('textbox', { name: 'Repository knowledge' }).fill('Use the repository README and Python 3.12.');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await expect(page.getByRole('button', { name: 'Approve repository inspection' })).toBeEnabled();
  const key = runId(page);
  const original = await (await request.get(`/api/harness/runs/${key}`)).json();
  expect(original.state.repo_knowledge).toBe('Use the repository README and Python 3.12.');
  await page.getByRole('button', { name: 'Approve repository inspection' }).click();
  await expect(page.getByRole('button', { name: 'Approve and proceed', exact: true })).toBeEnabled();
  const stale = await request.post(`/api/harness/runs/${key}/respond`, { data: { request_id: original.pending.id, decision: 'approve' } });
  expect(stale.status()).toBe(409);
  await expect(page.getByRole('button', { name: 'Request revision', exact: true })).toBeDisabled();
  // Keep inspection usable while the API reports a running operation.
  const runRoute = `**/api/harness/runs/${key}`;
  await page.route(runRoute, async route => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, busy: true, state: { ...body.state, active_stage: 'test_generation' }, progress: { ...body.progress, eta: { stages: [{ stage: 'test_generation', status: 'running', elapsed_s: 365, batch: { done: 13, total: 37 }, predicted_s: 1680, remaining_s: 960 }, { stage: 'environment_setup', status: 'pending', predicted_s: 180, remaining_s: 180 }], total_remaining_s: 1140, finish_estimate: new Date(Date.now() + 1140000).toISOString() } } } });
  });
  await page.reload();
  await expect(page.getByRole('status').filter({ hasText: 'Generating tests…' })).toBeVisible();
  await expect(page.getByRole('status').getByText(/Batch 13 of 37 · ~16 min left of ~28 min/)).toBeVisible();
  await expect(page.getByText('~3 min').first()).toBeVisible();
  await expect(page.locator('.working .spinner')).toBeVisible();
  expect(await page.locator('.working .spinner').evaluate(el => getComputedStyle(el).animationName)).toBe('spin');
  await page.screenshot({ path: testInfo.outputPath('generation-in-progress.png') });
  await page.getByRole('link', { name: 'View Repo Analysis evidence', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${key}/artifacts/repo_analysis`));
  await expect(page.getByRole('heading', { level: 1, name: 'Repo Analysis', exact: true })).toBeVisible();
  await tab(page, 'Logs').click();
  await page.getByRole('link', { name: /audit\.jsonl/ }).click();
  await expect(page.locator('.audit-feed').first()).toBeVisible();
  await page.unroute(runRoute);
  await tab(page, 'Review').click();
  for (let i = 0; i < 9; i++) {
    const approve = page.getByRole('button', { name: /^(Approve and proceed|Install in isolated environment|Run tests here)$/ });
    await expect(approve).toBeEnabled();
    const before = await (await request.get(`/api/harness/runs/${key}`)).json();
    if (before.pending.stage === 'environment_setup' && before.pending.kind === 'tool') {
      await expect(page.getByRole('heading', { name: 'Step-by-step repository setup' })).toBeVisible();
      await expect(page.getByRole('region', { name: 'Repository setup guide' })).toBeVisible();
    }
    if (before.pending.stage === 'test_design' && before.pending.kind === 'review') await expectCaseTableHeaders(page);
    await approve.click();
    await expect.poll(async () => { const next = await (await request.get(`/api/harness/runs/${key}`)).json(); return !next.busy && (next.status === 'complete' || next.pending?.id !== before.pending?.id); }, { timeout: 60000 }).toBeTruthy();
  }
  await expect.poll(async () => (await (await request.get(`/api/harness/runs/${key}`)).json()).status).toBe('complete');
  // Completed test design artifact: the six QA columns, no page overflow at phone width.
  await page.goto(`/runs/${key}/artifacts/test_design`);
  await expectCaseTableHeaders(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto(`/runs/${key}`);
  await expect(page.getByRole('progressbar', { name: 'Workflow completion', exact: true })).toHaveAttribute('value', '100');
  await tab(page, 'Prompts').click();
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
  await page.goto(`/runs/${key}/artifacts/repo_analysis`);
  await expect(page.getByRole('heading', { level: 1, name: 'Repo Analysis', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name: 'Repo Analysis', exact: true })).toBeVisible();
  await tab(page, 'QA report').click();
  await expect(page.getByText('PASS', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('not_measured', { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('qa-evidence-scope.png'), fullPage: true });
  await tab(page, 'Test reports').click();
  const report = page.frameLocator('iframe[title="Playwright report"]');
  await expect(report.getByRole('heading', { name: 'Browser report fixture' })).toBeVisible();
  await expect(report.locator('#verdict')).toHaveText('PASS');
  expect(await report.locator('body').getAttribute('data-isolated')).toBe('yes');
  expect(await page.locator('body').getAttribute('data-report-escaped')).toBeNull();
  await tab(page, 'Logs').click();
  await page.getByRole('link', { name: /workflow\.log/ }).click();
  await expect(page.getByRole('region', { name: 'Log output' })).toBeVisible();
  await page.getByRole('searchbox', { name: 'Find in output' }).fill('not-a-real-log-line');
  await expect(page.getByRole('region', { name: 'Log output' })).toBeEmpty();
  const downloadPromise = page.waitForEvent('download');
  await page.goto(`/runs/${key}`);
  await page.getByRole('link', { name: 'Download deliverable ZIP ↓' }).click();
  expect((await downloadPromise).suggestedFilename()).toMatch(/\.zip$/);
  await tab(page, 'Generated tests').click();
  await page.getByRole('link', { name: /\.py$/ }).first().click();
  await expect(page.locator('.source-code')).toContainText('def test_');
  await tab(page, 'Generated tests').click();
  await page.getByRole('button', { name: 'Independent test review' }).click();
  const review = page.getByRole('region', { name: 'Independent test review' });
  await expect(review.getByRole('columnheader', { name: 'Confidence', exact: false })).toBeVisible();
  // host-computed confidence = rubric sum (the scripted reviewer gives full marks), not the model's self-reported 90
  await expect(review.getByRole('cell', { name: '100', exact: true }).first()).toBeVisible();
  await expect(review.getByRole('cell', { name: 'Source-review accepted', exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Dark appearance' }).click();
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--primary').trim())).toBe('#eeeeef');
  await page.screenshot({ path: testInfo.outputPath('review-confidence-dark.png'), fullPage: true, animations: 'disabled' });
  await page.getByRole('button', { name: 'Light appearance' }).click();
  await tab(page, 'Observability').click();
  await expect(page.getByRole('group', { name: 'Workflow step' })).toBeVisible();
  await page.getByRole('button', { name: 'Stage activity & token usage', exact: true }).click();
  for (const name of ['Skills', 'Tools', 'Automation', 'Timeline', 'Traceability', 'Integrity', 'Export']) {
    await page.getByRole('button', { name, exact: true }).click();
    await expect(page.locator('.section').first()).toBeVisible();
  }
  await tab(page, 'Audit').click();
  await expect(page.locator('.audit-feed').getByText('Human Response', { exact: true }).first()).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath('audit-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(tab(page, 'Audit')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  const secret = await request.get('/api/harness/config');
  expect(JSON.stringify(await secret.json())).not.toContain('fixture-configured-flag');
  expect(errors).toEqual([]);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('button', { name: 'Rerun from step', exact: true }).click();
  await page.getByRole('group', { name: 'Step to rerun' }).getByRole('button', { name: 'Requirements', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Rerun Requirements', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Approve and proceed', exact: true })).toBeEnabled();
  await expect(page.getByText('Workflow progress · 14%', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Approve and proceed', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Stop workflow', exact: true }).click();
  await expect(page.getByText('Workflow stopped. Approved evidence remains available.', { exact: true })).toBeVisible();
});

test('failed execution shows recorded reason, improvement advice and inspectable results', async ({ page, request }) => {
  await page.goto('/runs');
  await page.getByRole('link', { name: 'New run', exact: true }).click();
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill('https://github.com/acme/failure');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await expect(page.getByRole('button', { name: 'Approve repository inspection' })).toBeEnabled();
  const id = runId(page);
  for (let i = 0; i < 10; i++) {
    const before = await (await request.get(`/api/harness/runs/${id}`)).json();
    await page.getByRole('button', { name: /^(Approve repository inspection|Approve and proceed|Install in isolated environment|Run tests here)$/ }).click();
    await expect.poll(async () => { const next = await (await request.get(`/api/harness/runs/${id}`)).json(); return !next.busy && next.pending?.id !== before.pending?.id; }, { timeout: 60000 }).toBeTruthy();
  }
  await tab(page, 'QA report').click();
  await expect(page.locator('.failure-card')).toHaveCount(1);
  await expect(page.locator('.failure-reason')).toContainText('assert 3 == 4');
  await expect(page.locator('.failure-next')).toContainText('never weaken the assertion');
  await page.getByRole('button', { name: 'Case results' }).click();
  const table = page.getByRole('region', { name: 'Case results' });
  await table.getByRole('button', { name: 'Status', exact: false }).click();
  await page.getByRole('link', { name: 'Open Case results row 1' }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${id}/records/case/`));
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.locator('main')).toContainText('assert 3 == 4');
  await page.goBack();
  await expect(page.getByRole('heading', { level: 2, name: 'QA report' })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await tab(page, 'Test reports').click();
  await expect(page.frameLocator('iframe').locator('#verdict')).toHaveText('FAIL');
});

test('deny, retry, revise, stop, invalid inputs and hostile origins', async ({ page, request }) => {
  await page.goto('/runs/new');
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
  await page.goto('/runs');
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
  await page.getByRole('link', { name: 'New run', exact: true }).click();
  const columns = page.locator('.workspace-grid');
  const first = columns.locator(':scope > section').first();
  const originalWidth = (await first.boundingBox())!.width;
  await drag('Resize workspace columns', -80);
  await expect.poll(async () => (await first.boundingBox())!.width).toBeLessThan(originalWidth - 60);
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Marketplace', exact: true }).click();
  await expect(page.getByLabel('requirement-analysis workflow map')).toBeVisible();
  await expect(page.locator('.skill-card')).toHaveCount(14); // 12 repo skills + spec-authoring (greenfield) + test-reviewer
  await page.getByRole('searchbox', { name: 'Search agent skills' }).fill('playwright');
  await expect(page.locator('.skill-card')).toHaveCount(1);
  await expect(page.getByText('Upstream library provenance', { exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: 'View skill details →', exact: true }).click();
  await expect(page).toHaveURL(/\/marketplace\/skills\/playwright-test-generator$/);
  await expect(page.locator('.markdown-document h2').first()).toContainText('Playwright');
  // SKILL.md keeps numbered steps; code samples and tables live in references/ (progressive disclosure).
  await expect(page.locator('.markdown-document ol li').first()).not.toBeEmpty();
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
  await page.goto('/prompts');
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
  await page.goto('/runs');
  await expect(page).toHaveTitle('Agentic QE Platform');
  await expect(page.locator('.brand')).toContainText('Agentic QE');
  await expect(page.getByRole('button', { name: 'Azure guide', exact: true })).toHaveCount(0);
});

test('aligned editors, configuration separation and confirmed run removal', async ({ page, request }) => {
  await page.goto('/prompts');
  const textarea = page.getByLabel('Review guidance', { exact: true });
  await expect(textarea).toBeVisible();
  const inputBox = (await textarea.boundingBox())!;
  const saveBox = (await page.getByRole('button', { name: 'Save prompts', exact: true }).boundingBox())!;
  expect(saveBox.y - (inputBox.y + inputBox.height)).toBeGreaterThanOrEqual(15);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Configuration', exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Configuration', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Agent personas', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Edit agent prompts →', exact: true })).toHaveCount(0);
  const response = await request.post('/api/harness/runs', { data: { repo_url: 'https://github.com/acme/demo' } });
  const key = (await response.json()).id;
  await expect.poll(async () => (await (await request.get(`/api/harness/runs/${key}`)).json()).busy).toBe(false);
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: /^Runs\b/ }).click();
  const remove = page.getByRole('button', { name: `Delete run ${key}`, exact: true });
  await expect(remove).toBeEnabled(); await remove.click();
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(remove).toBeVisible(); await remove.click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete run', exact: true }).click();
  await expect(remove).toHaveCount(0);
  expect((await request.get(`/api/harness/runs/${key}`)).status()).toBe(404);
});

test('clone progress has a stage, actual directory, live output and cancellation', async ({ page, request }, testInfo) => {
  const created = await request.post('/api/harness/runs', { data: { repo_url: 'git clone https://github.com/acme/demo' } });
  const key = (await created.json()).id;
  await expect.poll(async () => (await (await request.get(`/api/harness/runs/${key}`)).json()).busy).toBe(false);
  const saved = await (await request.get(`/api/harness/runs/${key}`)).json();
  let cancelled = false;
  await page.route(`**/api/harness/runs/${key}`, async route => route.fulfill({ json: { ...saved,
    busy: !cancelled, service_busy: !cancelled, pending_consumed: !cancelled,
    state: { ...saved.state, active_stage: undefined }, actions: cancelled ? ['retry', 'stop'] : [],
    pending: cancelled ? { ...saved.pending, kind: 'error', error: 'Repository clone cancelled. Retry stage or stop.' } : saved.pending,
    operation: cancelled ? {} : { kind: 'clone', stage: 'repo_analysis', status: 'running', attempt: 1, elapsed_seconds: 12, timeout_seconds: 180, latest_output: 'Receiving objects: 12%' }
  } }));
  await page.route(`**/api/harness/runs/${key}/cancel-clone`, async route => { cancelled = true; await route.fulfill({ status: 202, json: { id: key } }); });
  await page.goto(`/runs/${key}`);
  await expect(page.getByRole('status').filter({ hasText: 'Cloning repository from GitHub…' })).toBeVisible();
  await expect(page.getByText('Receiving objects: 12%', { exact: true })).toBeVisible();
  await expect(page.getByText(saved.run_directory, { exact: true }).first()).toBeVisible();
  await expect(page.getByText('— is running…', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Approved Action In Progress', { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('live-clone-progress.png') });
  await page.getByRole('button', { name: 'Cancel clone', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Retry stage', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Cancel clone', exact: true })).toHaveCount(0);
});

test('stop active model work preserves repository analysis after reload', async ({ page, request }, testInfo) => {
  await page.goto('/runs/new');
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill('https://github.com/acme/slow');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await page.getByRole('button', { name: 'Approve repository inspection', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Approve and proceed', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Approve and proceed', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Stop run', exact: true })).toBeEnabled();
  const stopPosts: string[] = [];
  page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/stop')) stopPosts.push(r.url()); });
  await page.getByRole('button', { name: 'Stop run', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.waitForTimeout(500);
  expect(stopPosts).toHaveLength(0);
  await expect(page.getByRole('button', { name: 'Stop run', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Stop run', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Stop run', exact: true }).click();
  await expect(page.getByText('Workflow stopped. Approved evidence remains available.', { exact: true })).toBeVisible();
  await page.reload();
  const id = runId(page);
  await page.getByRole('link', { name: 'View Repo Analysis evidence', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Repo Analysis', exact: true })).toBeVisible();
  const body = await (await request.get(`/api/harness/runs/${id}`)).json();
  expect(body.status).toBe('stopped');
  expect(body.state.artifacts.repo_analysis.source_files).toContain('service.py');
  await page.screenshot({ path: testInfo.outputPath('stopped-run-analysis.png'), fullPage: true });
});

test('repository URL auto-fills repository knowledge without overwriting edits; app fields are hidden', async ({ page }) => {
  const preview = { app_component: 'frontend', app_url: 'http://localhost:3000', start_app_suggested: true, repo_knowledge: 'Auto-detected from GitHub (unverified)\nNext.js', detected: {}, warnings: [] };
  let calls = 0;
  await page.route('**/api/harness/repo-preview', route => { calls++; return route.fulfill({ json: preview }); });
  await page.goto('/runs/new');
  const url = page.getByRole('textbox', { name: 'Public GitHub repository URL' });
  await url.fill('https://github.com/acme/demo');
  await expect(page.getByText('Auto-filled from repository — review before starting.')).toBeVisible();
  await expect(page.getByRole('textbox', { name: /Repository knowledge/ })).toHaveValue(/Next\.js/);
  await expect(page.getByText('Running application URL')).toHaveCount(0);
  await expect(page.getByText('Application component')).toHaveCount(0);
  await expect(page.getByText('Start the application for me')).toHaveCount(0);
  await page.getByRole('textbox', { name: /Repository knowledge/ }).fill('mine');
  await url.fill('https://github.com/acme/other');
  await expect.poll(() => calls).toBe(2);
  await expect(page.getByRole('textbox', { name: /Repository knowledge/ })).toHaveValue('mine');
});

test('greenfield form posts mode, test types, documents and Jira without persisting the token', async ({ page }) => {
  const posts: Record<string, any>[] = [];
  await page.route('**/api/harness/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/harness/', '');
    if (route.request().method() === 'POST') { posts.push({ path, ...route.request().postDataJSON() }); return route.fulfill({ json: { id: 'abc' } }); }
    if (path === 'config') return route.fulfill({ json: { model: 'm', base_url: 'u', key_configured: true, case_budget_default: 2, daily: { used: 0, remaining: 1, limit: 1 }, stages: ['repo_analysis'], personas: {} } });
    if (path === 'runs') return route.fulfill({ json: { runs: [], busy: false } });
    if (path === 'runs/abc') return route.fulfill({ json: { id: 'abc', status: 'running', busy: false, service_busy: false, actions: [], pending: null, state: { run_mode: 'greenfield', test_types: ['unit'], stages: ['repo_analysis'], approved: {}, artifacts: {} }, progress: { percent: 1 } } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/runs/new');
  await expect(page.getByLabel('API token')).toHaveCount(0);
  await page.getByRole('radio', { name: 'Greenfield specs' }).check();
  await page.getByRole('checkbox', { name: 'Integration' }).uncheck();
  await page.getByLabel('Specification documents').setInputFiles({ name: 'spec.md', mimeType: 'text/markdown', buffer: Buffer.from('# Spec') });
  await page.getByLabel('Site URL').fill('https://acme.atlassian.net');
  await page.getByLabel('Issue keys').fill('ACME-1, ACME-2');
  await page.getByLabel('Email').fill('ba@acme.com');
  await page.getByLabel('API token').fill('s3cret-token');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await expect.poll(() => posts.find(p => p.path === 'runs')).toMatchObject({
    run_mode: 'greenfield', test_types: ['unit'],
    spec_sources: { documents: [{ name: 'spec.md', content_base64: Buffer.from('# Spec').toString('base64') }], jira: { base_url: 'https://acme.atlassian.net', issue_keys: ['ACME-1', 'ACME-2'], email: 'ba@acme.com', api_token: 's3cret-token' } },
  });
  expect(posts.find(p => p.path === 'runs')).not.toHaveProperty('start_app');
  await expect(page.getByText('greenfield', { exact: true })).toBeVisible();
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
  expect(stored).not.toContain('s3cret-token');
});

test('brownfield form hides application and Jira fields and posts brownfield defaults', async ({ page }) => {
  const posts: Record<string, any>[] = [];
  await page.route('**/api/harness/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/harness/', '');
    if (route.request().method() === 'POST') { posts.push({ path, ...route.request().postDataJSON() }); return route.fulfill({ json: { id: 'abc' } }); }
    if (path === 'config') return route.fulfill({ json: { model: 'm', base_url: 'u', key_configured: true, case_budget_default: 2, daily: { used: 0, remaining: 1, limit: 1 }, stages: ['repo_analysis'], personas: {} } });
    if (path === 'runs') return route.fulfill({ json: { runs: [], busy: false } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/runs/new');
  await expect(page.getByRole('radio', { name: 'Brownfield repository' })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'Unit' })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'Integration' })).toBeChecked();
  for (const hidden of ['Running application URL', 'Application component', 'Start the application for me', 'Site URL', 'Specification documents']) await expect(page.getByText(hidden)).toHaveCount(0);
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill('https://github.com/acme/demo');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await expect.poll(() => posts.find(p => p.path === 'runs')).toMatchObject({ run_mode: 'brownfield', test_types: ['unit', 'integration'] });
  expect(posts.find(p => p.path === 'runs')).not.toHaveProperty('start_app');
});

test('case budget: new-run field posts case_budget and run view shows the recommendation table', async ({ page }) => {
  const posts: Record<string, unknown>[] = [];
  const run = {
    id: 'abc', status: 'running', busy: false, service_busy: false, actions: [], pending: null,
    state: { repo_url: 'https://github.com/acme/demo', stages: ['repo_analysis', 'requirements', 'test_planning', 'test_design', 'test_generation'], approved: { repo_analysis: {} }, artifacts: {}, case_budget: { per_requirement: 2 } },
    progress: { percent: 10, case_budget: { current: 2, recommended: 1, rows: [1, 2, 3, 4].map(k => ({ per_requirement: k, cases: 191 * Math.min(k, 2), batches: 64 * Math.min(k, 2), generation_min: 149 * Math.min(k, 2), design_min: 12 })) } },
  };
  await page.route('**/api/harness/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace('/api/harness/', '');
    if (route.request().method() === 'POST') {
      posts.push({ path, ...route.request().postDataJSON() });
      return route.fulfill({ json: { id: 'abc' } });
    }
    if (path === 'config') return route.fulfill({ json: { model: 'm', base_url: 'u', key_configured: true, case_budget_default: 2, daily: { used: 0, remaining: 1, limit: 1 }, stages: run.state.stages, personas: {} } });
    if (path === 'runs') return route.fulfill({ json: { runs: [], busy: false } });
    if (path === 'repo-preview') return route.fulfill({ json: {} });
    if (path === 'runs/abc') return route.fulfill({ json: run });
    return route.fulfill({ json: {} });
  });
  await page.goto('/runs/new');
  await expect(page.getByLabel('Test cases per requirement')).toHaveValue('2');
  await expect(page.getByText('Fewer cases = faster run. You can change this until test design is approved.')).toBeVisible();
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill('https://github.com/acme/demo');
  await page.getByLabel('Test cases per requirement').fill('3');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await expect.poll(() => posts.find(p => p.path === 'runs')).toMatchObject({ case_budget: { per_requirement: 3 } });
  await expect(page.getByRole('button', { name: 'Case budget', exact: true })).toBeVisible();
  await expect(page.getByRole('row', { name: /1 \(recommended\)/ })).toBeVisible();
  await expect(page.getByRole('row')).toHaveCount(5);
  await page.getByLabel('Cases per requirement').selectOption('4');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(() => posts.find(p => p.path === 'runs/abc/case-budget')).toMatchObject({ per_requirement: 4 });
});

test('run name: shown in list and header; posted from the new-run form', async ({ page }) => {
  const posts: Record<string, unknown>[] = [];
  const run = {
    id: 'abc', status: 'running', busy: false, service_busy: false, actions: [], pending: null,
    state: { run_name: 'Nightly smoke', repo_url: 'https://github.com/acme/demo', stages: ['repo_analysis'], approved: {}, artifacts: {} },
    progress: { percent: 10 },
  };
  await page.route('**/api/harness/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/harness/', '');
    if (route.request().method() === 'POST') { posts.push({ path, ...route.request().postDataJSON() }); return route.fulfill({ json: { id: 'abc' } }); }
    if (path === 'config') return route.fulfill({ json: { model: 'm', base_url: 'u', key_configured: true, daily: { used: 0, remaining: 1, limit: 1 }, stages: run.state.stages, personas: {} } });
    if (path === 'runs') return route.fulfill({ json: { runs: [{ id: 'abc', run_name: 'Nightly smoke', repo_url: run.state.repo_url, status: 'running', updated: 1, progress: { percent: 10 } }], busy: false } });
    if (path === 'runs/abc') return route.fulfill({ json: run });
    return route.fulfill({ json: {} });
  });
  await page.goto('/runs');
  await expect(page.locator('.run-row .run-name b')).toHaveText('Nightly smoke');
  await page.getByRole('searchbox', { name: 'Search runs' }).fill('nightly');
  await expect(page.locator('.run-row')).toHaveCount(1);
  await page.getByRole('link', { name: 'Open run →' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Nightly smoke' })).toBeVisible();
  await page.getByRole('link', { name: 'New run', exact: true }).click();
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill('https://github.com/acme/demo');
  await page.getByRole('textbox', { name: /Run name/ }).fill('My named run');
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await expect.poll(() => posts.find(p => p.path === 'runs')).toMatchObject({ run_name: 'My named run' });
});

test('Errors tab: one run notice, filterable rows, expandable full text', async ({ page }) => {
  const long = 'Independent test review requires repair: TC-1: ' + 'detail '.repeat(60) + 'END-OF-DETAIL';
  const item = (n: number, category: string, detail: string) => ({ id: `e${n}`, at: `2026-10-07T01:0${n}:00+00:00`, stage: 'test_generation', batch: n, case_ids: ['TC-A', 'TC-B', 'TC-C'], category, summary: detail.split('. ')[0].slice(0, 160), detail, source: 'generation_failures' });
  const items = [item(3, 'review_rejected', long), item(2, 'syntax', 'source is invalid. Line 3'), item(1, 'import', 'No module named foo. x')];
  const run = {
    id: 'abc', status: 'running', busy: false, service_busy: false, actions: [], pending: null,
    state: { repo_url: 'https://github.com/acme/demo', stages: ['repo_analysis', 'test_generation'], approved: {}, artifacts: {} },
    progress: { percent: 10 }, errors: { items, counts: { review_rejected: 1, syntax: 1, import: 1 } },
  };
  await page.route('**/api/harness/**', route => {
    const path = new URL(route.request().url()).pathname.replace('/api/harness/', '');
    if (path === 'config') return route.fulfill({ json: { model: 'm', base_url: 'u', key_configured: true, daily: { used: 0, remaining: 1, limit: 1 }, stages: run.state.stages, personas: {} } });
    if (path === 'runs') return route.fulfill({ json: { runs: [], busy: false } });
    if (path === 'runs/abc') return route.fulfill({ json: run });
    return route.fulfill({ json: {} });
  });
  await page.goto('/runs/abc');
  await expect(page.locator('.notice.bad')).toHaveCount(1);
  await expect(page.locator('.notice.bad')).toContainText('3 errors in this run');
  await expect(page.getByRole('link', { name: 'Errors 3', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Open Errors tab' }).click();
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await page.getByRole('button', { name: /^Syntax 1/ }).click();
  await expect(page.locator('tbody tr')).toHaveCount(1);
  await page.getByRole('button', { name: /^Syntax 1/ }).click();
  await page.locator('tbody tr').first().locator('summary').click();
  await expect(page.locator('tbody pre').first()).toContainText('END-OF-DETAIL');
});
