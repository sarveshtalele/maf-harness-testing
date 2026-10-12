import { test, expect, type APIRequestContext } from '@playwright/test';

/** A new run that waits at the first approval gate (repository inspection). */
async function newRun(request: APIRequestContext, repo: string): Promise<string> {
  const created = await request.post('/api/harness/runs', { data: { repo_url: repo } });
  const id = (await created.json()).id as string;
  await expect.poll(async () => (await (await request.get(`/api/harness/runs/${id}`)).json()).busy).toBe(false);
  return id;
}

test('rename a run through the confirmation dialog and see it in the header', async ({ page, request }) => {
  const id = await newRun(request, 'https://github.com/acme/rename-me');
  await page.goto(`/runs/${id}`);
  await expect(page.getByRole('button', { name: 'Approve repository inspection' })).toBeEnabled();
  await page.getByRole('button', { name: 'Rename run', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Rename run' });
  await dialog.getByLabel(/Run name \(max 80/).fill('Renamed run');
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Renamed run' })).toBeVisible();
  await expect.poll(async () => (await (await request.get(`/api/harness/runs/${id}`)).json()).state.run_name).toBe('Renamed run');
});

test('a rerun refusal is shown inside the rerun dialog and the dialog stays open', async ({ page, request }) => {
  const id = await newRun(request, 'https://github.com/acme/rerun-refused');
  await page.route(`**/api/harness/runs/${id}/rerun`, route => route.fulfill({ status: 409, json: { error: 'The run is busy; wait for the next gate.' } }));
  await page.goto(`/runs/${id}`);
  await expect(page.getByRole('button', { name: 'Approve repository inspection' })).toBeEnabled();
  await page.getByRole('button', { name: 'Rerun from step', exact: true }).click();
  await page.getByRole('group', { name: 'Step to rerun' }).getByRole('button', { name: 'Repo Analysis', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Rerun from a step' });
  await dialog.getByRole('button', { name: 'Rerun Repo Analysis', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('The run is busy; wait for the next gate.');
  await expect(dialog).toBeVisible();
});

test('a revision with feedback is sent from the review gate', async ({ page, request }) => {
  const id = await newRun(request, 'https://github.com/acme/revise-me');
  await page.goto(`/runs/${id}`);
  await page.getByRole('button', { name: 'Approve repository inspection' }).click();
  await expect(page.getByRole('button', { name: 'Approve and proceed', exact: true })).toBeEnabled();
  await page.getByRole('textbox', { name: 'Modification instructions' }).fill('Add a human review note.');
  const response = page.waitForResponse(r => r.url().endsWith(`/runs/${id}/respond`) && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Request revision', exact: true }).click();
  const sent = await response;
  expect(sent.status()).toBeLessThan(300);
  expect(sent.request().postDataJSON()).toMatchObject({ decision: 'revise', feedback: 'Add a human review note.' });
});
