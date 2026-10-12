import { test, expect, type Page, type Route } from '@playwright/test';

// Every route is served from a rich, scripted run so each page has real structure to render (cases, QA, logs, audit).
const STAGES = ['repo_analysis', 'requirements', 'test_planning', 'test_design', 'test_generation', 'environment_setup', 'test_execution'];
const LONG_REPO = `https://github.com/acme/${'very-long-repository-name-for-wrapping-tests-'.repeat(3)}end`;
const config = { model: 'm', base_url: 'u', key_configured: true, case_budget_default: 2, daily: { used: 0, remaining: 1, limit: 1 }, stages: STAGES, personas: {} };
const run = {
  id: 'rich', status: 'complete', busy: false, service_busy: false, actions: [], pending: null, has_zip: false, run_directory: '/runs/rich', progress: { percent: 100 }, pending_consumed: false,
  state: {
    repo_url: LONG_REPO, run_name: '', repo_ref: 'main', run_mode: 'brownfield', test_types: ['unit', 'integration'], stages: STAGES, active_stage: 'test_execution',
    approved: Object.fromEntries(STAGES.map(s => [s, true])), revisions: { requirements: 1 }, completed_reason: '',
    artifacts: {
      repo_analysis: { source_files: ['service.py'], summary: 'Summary of the repository.' },
      requirements: { requirements: [{ id: 'REQ-1', text: 'Add two numbers', category: 'functional', actor: 'Developer', priority: 'P1' }], requirement_model: { acceptance_criteria: [] } },
      test_planning: { objective: 'Verify the adder.', recommended_frameworks: ['pytest', 'unittest'], plan_areas: [{ id: 'PLN-1', title: 'Adder' }] },
      test_design: {
        positive_tests: [{ id: 'TC-1', title: 'adds two numbers', description: 'adds', steps: ['Call add(1, 2)'], expected_result: '3', priority: 'P1', level: 'unit', traceability_links: [{ type: 'requirement', id: 'REQ-1' }] }],
        negative_tests: [], boundary_tests: [], validation_tests: [], contract_tests: [],
      },
      test_generation: {
        files: [{ path: 'tests/test_add.py', case_ids: ['TC-1'], content: 'def test_add():\n    assert add(1, 2) == 3\n' }],
        case_catalog: [{ id: 'TC-1', title: 'adds two numbers', confidence: 100, reviewer_verdict: 'supported', reviewer_reason: 'ok', priority: 'P1', expected_result: '3' }],
        case_reviews: [{ case_id: 'TC-1', verdict: 'supported', confidence: 100, reason: 'ok', source_files: 'tests/test_add.py' }],
        validation: { files: 1, test_declarations: 1, covered_case_ids: ['TC-1'] },
      },
      test_execution: {
        verdict: 'FAIL', markdown: '',
        qa_report: {
          verdict: 'FAIL', execution_status: 'completed', adapter: 'pytest', generated_verdict: 'FAIL',
          repository: { name: 'acme/very-long-repository-name-for-wrapping-tests', url: LONG_REPO, commit: '0123456789abcdef0123456789abcdef01234567' },
          case_status_counts: { passed: 0, failed: 1 },
          verification: { verified: 1, total: 2, threshold: 80, min_confidence: 90, enforced: true },
          cases: [{ id: 'TC-1', control_ids: ['CTL-001'], title: 'adds two numbers', level: 'unit', status: 'failed', message: 'assert 3 == 4', file: 'tests/test_add.py', priority: 'P1', confidence: 100, actor: 'Developer' }],
          test_cases: [{ group: 'positive', sno: 1, id: 'TC-1', requirement_ids: ['REQ-1'], title: 'adds two numbers', description: 'adds', steps: ['Call add(1, 2)'], expected_result: '3', priority: 'P1', level: 'unit', actor: 'Developer', target: 'service.add', preconditions: ['Module imported'], data_notes: '', traceability_links: [{ type: 'requirement', id: 'REQ-1' }] }],
          traceability: [{ control_id: 'CTL-001', requirement_id: 'REQ-1', plan_area_id: 'PLN-1', plan_area_name: 'Adder', case_ids: ['TC-1'], test_files: ['tests/test_add.py'], generation: 'passed', execution: 'failed', final_status: 'failed', failed_at: 'execution', reason: 'assert 3 == 4' }],
          traceability_summary: { controls: 1, final_status: { failed: 1 }, failed_at: { execution: 1 } },
          requirement_coverage: [{ id: 'REQ-1', state: 'failing', requirement_state: 'new', category: 'functional', actor: 'Developer', text: 'Add two numbers' }, { id: 'REQ-2', state: 'passed', requirement_state: 'new', category: 'functional', actor: 'Developer', text: 'Subtract two numbers' }],
          requirement_state_counts: { new: 2, pre_existing_witness: 0, excluded: 0, unexplained: 0 },
          requirement_status_counts: { failing: 1, passed: 1 },
          baseline_regression: [{ scope: 'unit', verdict: 'PASS', tests: 2, reason: 'existing suite', cases: [] }],
          source_intake: {}, runtime_coverage: { line: 'not_measured' },
          failures: [{ id: 'TC-1', title: 'adds two numbers', level: 'unit', status: 'failed', message: 'assert 3 == 4', file: 'tests/test_add.py' }],
          unmapped_results: ['tests/test_legacy.py::test_old'],
          skill_checks: { 'contract-1': { skill: 'generic-test-generator', passed: true, output: 'ok' } },
          updates: {
            generated_test_files_to_update: [{ file: 'tests/test_add.py', lines: [2], case: 'adds two numbers', classification: 'test expectation', detail: 'Expectation contradicts source', action: 'Never weaken the assertion.' }],
            repository_files_to_review: [{ file: 'service.py', line: 4, case: 'adds two numbers', classification: 'application', detail: 'add returns a wrong sum' }],
            requirements_needing_tests: [{ requirement: 'Add two numbers', state: 'failing', source_files_to_cover: ['service.py'] }],
            environment_actions: [{ action: 'Install pytest 8.x in the isolated environment.' }],
          },
          warnings: ['Coverage is static.'], limitations: ['Local-process execution is not a security sandbox.'],
        },
      },
    },
  },
  errors: { items: [{ id: 'err-1', at: '2026-10-07T01:00:00+00:00', stage: 'test_generation', batch: 1, case_ids: ['TC-1'], category: 'syntax', summary: 'source is invalid', detail: 'Line 3 invalid', source: 'generation_failures' }], counts: { syntax: 1 } },
};
const step = '# Step 0: Summary\n\n| Field | Value |\n|---|---|\n| Step status | Completed |\n\n## Purpose\n\nRecords the repository summary.\n\n## Results\n\n- Source files: 1\n';
const prompts = { settings: { repository_prompt: 'Review guidance', personas: { requirements: 'Persona text' }, overrides: {} }, defaults: { repository_prompt: 'Review guidance', personas: { requirements: 'Persona text' } }, records: [{ id: 'r1', stage: 'requirements', override_key: 'requirements', sha256: 'abc123' }], host_stages: [] };
const observability = { totals: { tokens: 10, provider_calls: 1, tool_calls: 0, human_gates: 1 }, stages: [{ stage: 'requirements', tokens: 10 }], model_runs: [], skills: [], tool_calls: [], tool_summary: [], automation: {}, timeline: [], traceability: { chain: [] }, checks: [], run: {}, cache: {}, trace_level: 'full' };

async function mock(page: Page) {
  await page.route('**/api/harness/**', (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace('/api/harness/', '');
    if (route.request().method() === 'POST') return route.fulfill({ json: { id: 'rich' } });
    if (path === 'config') return route.fulfill({ json: config });
    if (path === 'runs') return route.fulfill({ json: { runs: [{ id: 'rich', run_name: '', repo_url: LONG_REPO, status: 'complete', stage: 'test_execution', verdict: 'FAIL', updated: 1, progress: { percent: 100 } }], busy: false } });
    if (path === 'runs/rich') return route.fulfill({ json: run });
    if (path === 'prompts' || path === 'runs/rich/prompts') return route.fulfill({ json: prompts });
    if (path === 'runs/rich/prompts/r1') return route.fulfill({ json: { id: 'r1', stage: 'requirements', user_prompt: 'Requirements prompt', model: 'm' } });
    if (path === 'runs/rich/audit') return route.fulfill({ json: { events: [{ event: 'human_response', stage: 'requirements', at: '2026-10-07T01:00:00+00:00', decision: 'approve', request_id: 'req-1' }], malformed_lines: 0 } });
    if (path === 'runs/rich/observability') return route.fulfill({ json: observability });
    if (path === 'runs/rich/evidence') return route.fulfill({ json: { files: [
      { id: 'f1', path: 'workflow.log', name: 'workflow.log', kind: 'log', bytes: 120, updated: 1 },
      { id: 'f2', path: 'audit.jsonl', name: 'audit.jsonl', kind: 'log', bytes: 80, updated: 1 },
      { id: 'f3', path: 'browser-reports/index.html', name: 'index.html', kind: 'report', bytes: 10, updated: 1 },
    ], truncated: false } });
    if (path === 'runs/rich/evidence/f1') return route.fulfill({ json: { text: 'Starting\nERROR boom', truncated: false } });
    if (path === 'runs/rich/evidence/f2') return route.fulfill({ json: { text: '{"event":"approved","stage":"requirements","at":"2026-10-07T01:00:00+00:00"}\n', truncated: false } });
    if (path === 'runs/rich/download' && url.searchParams.get('kind') === 'step' && url.searchParams.get('format') === 'json') return route.fulfill({ json: { steps: [{ name: '00-summary', title: 'Step 0: Summary', status: 'Completed' }] } });
    if (path === 'runs/rich/download' && url.searchParams.get('kind') === 'step') return route.fulfill({ body: step, contentType: 'text/markdown' });
    if (path === 'settings/model') return route.fulfill({ json: { base_url: 'https://gateway.example.com/v1', model: 'm', stage_models: {}, key_configured: true, key_hint: '…1234', source: 'env', encrypted: false } });
    if (path === 'skills') return route.fulfill({ json: { skills: [], upstream: {} } });
    if (path === 'mcp-servers') return route.fulfill({ json: { servers: [], presets: {}, stages: [], max_tools_per_stage: 8, stage_budget: {} } });
    return route.fulfill({ json: {} });
  });
}

/** Raw Markdown or JSON must never be shown as a <pre> block; downloads are links, not panes. */
async function rawOutputIssues(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const issues: string[] = [];
    for (const pre of document.querySelectorAll('pre')) {
      const text = pre.textContent || '';
      if (/^\s*[[{]/.test(text) && /"\w+"\s*:|\[\s*\]|\{\s*\}/.test(text)) issues.push(`JSON in <pre>: ${text.slice(0, 40)}`);
      if (/^#{1,6} /m.test(text) || /^\|.*\|$/m.test(text)) issues.push(`Markdown in <pre>: ${text.slice(0, 40)}`);
    }
    // Inline code and paragraphs must not hold a whole JSON object or Markdown document either.
    for (const el of document.querySelectorAll('code, p')) {
      const text = el.textContent || '';
      if (text.length > 80 && /^\s*\{[\s\S]*"\w+"\s*:/.test(text)) issues.push(`JSON in <${el.tagName.toLowerCase()}>: ${text.slice(0, 40)}`);
      if (text.length > 80 && /(^|\n)#{1,6} \S/.test(text) && /\n\|/.test(text)) issues.push(`Markdown in <${el.tagName.toLowerCase()}>: ${text.slice(0, 40)}`);
    }
    if (/Raw JSON/.test(document.body.innerText)) issues.push('"Raw JSON" is shown');
    return issues;
  });
}

const RUN_ROUTES: [string, RegExp | string, string?][] = [
  ['/runs/rich', /very-long-repository-name/],
  ['/runs/rich/errors', 'Errors'],
  ['/runs/rich/artifacts', 'Artifacts'],
  ['/runs/rich/artifacts/test_planning', 'Test Planning'],
  ['/runs/rich/artifacts/test_planning/objective', 'Objective'],
  ['/runs/rich/artifacts/test_design', 'Test Design'],
  ['/runs/rich/records/case/TC-1', 'adds two numbers'],
  ['/runs/rich/tests', 'Generated tests'],
  ['/runs/rich/tests/0', 'test_add.py'],
  ['/runs/rich/qa', 'QA report'],
  ['/runs/rich/qa/steps/00-summary', 'Step 0: Summary'],
  ['/runs/rich/evidence', 'Test reports'],
  ['/runs/rich/logs', 'Logs'],
  ['/runs/rich/logs/f1', 'workflow.log'],
  ['/runs/rich/logs/f2', 'audit.jsonl'],
  ['/runs/rich/observability', 'Observability'],
  ['/runs/rich/audit', 'Audit'],
  ['/runs/rich/prompts', 'Run prompts'],
];

test.beforeEach(async ({ page }) => { await mock(page); });

test('one smoke per new route renders its page without raw output or errors', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  for (const [path, heading] of RUN_ROUTES) {
    await page.goto(path);
    const title = page.getByRole('heading', { name: heading, exact: typeof heading === 'string' });
    await expect(title, `${path} heading`).toHaveCount(1);
    await expect(title, `${path} heading`).toBeVisible();
    expect(await rawOutputIssues(page), `${path} raw output`).toEqual([]);
    await expect(page.getByRole('columnheader', { name: 'Text', exact: true }), `${path} "Text" header`).toHaveCount(0);
  }
  for (const path of ['/runs', '/runs/new', '/prompts', '/settings', '/marketplace']) {
    await page.goto(path);
    await expect(page.locator('h1'), `${path} h1`).toHaveCount(1);
    await expect(page.locator('h1'), `${path} h1`).toBeVisible();
    expect(await rawOutputIssues(page), `${path} raw output`).toEqual([]);
  }
  expect(errors).toEqual([]);
});

test('the expand arrow opens the section as its own page, and Back returns', async ({ page }) => {
  await page.goto('/runs/rich/artifacts/test_planning');
  const expand = page.getByRole('link', { name: 'Open Objective as a page' });
  await expect(expand).toBeVisible();
  await expand.click();
  await expect(page).toHaveURL(/\/runs\/rich\/artifacts\/test_planning\/objective$/);
  await expect(page.locator('.page-id')).toHaveText('test_planning.objective');
  await expect(page.getByRole('heading', { level: 1, name: 'Objective' })).toBeVisible();
  await expect(page.getByText('Verify the adder.', { exact: true })).toHaveCount(1);
  await page.goBack();
  await expect(page).toHaveURL(/\/runs\/rich\/artifacts\/test_planning$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Test Planning' })).toBeVisible();
});

test('the test-design requirement table names its description column "Description"', async ({ page }) => {
  await page.goto('/runs/rich/artifacts/requirements/requirements');
  await expect(page.getByRole('columnheader', { name: 'Description' })).toHaveCount(1);
  await expect(page.getByRole('columnheader', { name: 'Text', exact: true })).toHaveCount(0);
});

test('the QA report renders every section of the Markdown export', async ({ page }) => {
  await page.goto('/runs/rich/qa');
  for (const title of ['Verdict and case counts', 'Evidence scope', 'End-to-end traceability', 'Case results', 'Test cases', 'Requirement states', 'Requirement traceability', 'Why tests failed & what to improve', 'Skill contract checks', 'Improvement worklist', 'Review vs execution', 'Executed tests not mapped to an approved case', 'Warnings & limitations', 'Step reports']) {
    await expect(page.getByRole('button', { name: title }), `section ${title}`).toHaveCount(1);
  }
  await expect(page.getByText('source-review accepted 1/2 cases', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'End-to-end traceability' }).click();
  await expect(page.getByRole('region', { name: 'End-to-end traceability' }).getByRole('cell', { name: 'CTL-001' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Generated test files to update' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Repository source files to review' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Requirements that still need a passing test' })).toBeVisible();
  await expect(page.getByText('Install pytest 8.x', { exact: false })).toBeVisible();
});

test('requirement states read as passed execution; review acceptance is source-review accepted', async ({ page }) => {
  await page.goto('/runs/rich/qa');
  await page.getByRole('button', { name: 'Requirement traceability' }).click();
  const table = page.getByRole('region', { name: 'Requirement traceability' });
  await expect(table.getByText('Passed', { exact: true })).toBeVisible();
  await expect(page.locator('main')).not.toContainText(/\bverified\b/i);
  await page.goto('/runs/rich/tests');
  await page.getByRole('button', { name: 'Independent test review' }).click();
  await expect(page.getByRole('region', { name: 'Independent test review' }).getByRole('cell', { name: 'Source-review accepted', exact: true })).toBeVisible();
});

test('old ?run= and ?page= URLs redirect to the new routes', async ({ page }) => {
  await page.goto('/?run=rich&tab=QA%20report');
  await expect(page).toHaveURL(/\/runs\/rich\/qa/);
  await page.goto('/?run=rich&tab=Artifacts&stage=test_design');
  await expect(page).toHaveURL(/\/runs\/rich\/artifacts\/test_design/);
  await page.goto('/?run=rich&tab=Review');
  await expect(page).toHaveURL(/\/runs\/rich(\?|$)/);
  await page.goto('/?page=configuration');
  await expect(page).toHaveURL(/\/settings/);
  await page.goto('/?page=new');
  await expect(page).toHaveURL(/\/runs\/new/);
  await page.goto('/?page=prompts');
  await expect(page).toHaveURL(/\/prompts/);
  await page.goto('/?page=marketplace');
  await expect(page).toHaveURL(/\/marketplace\/skills(\?.*)?$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Marketplace' })).toBeVisible();
});

for (const width of [390, 1024]) {
  test(`no horizontal scroll at ${width}px on every run route with a long repository name`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    for (const [path] of RUN_ROUTES) {
      await page.goto(path);
      await expect(page.locator('h1')).toHaveCount(1);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${path} at ${width}px`).toBeLessThanOrEqual(1);
    }
  });
}

test('section page shows its content once and downloads that section only', async ({ page }) => {
  await page.goto('/runs/rich/artifacts/test_planning/objective');
  await expect(page.getByText('Verify the adder.', { exact: true })).toHaveCount(1);
  await expect(page.getByText('Contents', { exact: true })).toHaveCount(0);
  const download = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Section JSON ↓' }).click();
  expect((await download).suggestedFilename()).toBe('test_planning-objective.json');
});

test('redirects: logs tab, marketplace MCP, skill detail, unknown tab and a hostile id', async ({ page }) => {
  await page.goto('/?run=rich&tab=Logs');
  await expect(page).toHaveURL(/\/runs\/rich\/logs/);
  await page.goto('/?page=marketplace&tab=mcp');
  await expect(page).toHaveURL(/\/marketplace\/mcp(\?.*)?$/);
  await page.goto('/?page=skills&skill=demo-skill');
  await expect(page).toHaveURL(/\/marketplace\/skills\/demo-skill(\?.*)?$/);
  await page.goto('/marketplace?tab=mcp');
  await expect(page).toHaveURL(/\/marketplace\/mcp(\?.*)?$/);
  await page.goto('/?run=rich&tab=Nope');
  await expect(page).toHaveURL(/\/runs\/rich/);
  await page.goto('/?run=..%2Fsecret&tab=Logs');
  await expect(page).not.toHaveURL(/secret/);
  await expect(page).toHaveURL(/\/runs(\?|$)/);
});

test('the sidebar fills the viewport height after the page scrolls', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 500 });
  await page.goto('/runs/rich/qa');
  await page.getByRole('button', { name: 'Expand all' }).click();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(100);
  const box = await page.locator('.sidebar').boundingBox();
  expect(box!.y).toBe(0);
  expect(Math.round(box!.height)).toBe(500);
});

test('at 390px the stage stepper and tab bar are single scrollable rows', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/runs/rich');
  await expect(page.locator('.stage-current')).toBeVisible();
  const tops = await page.locator('.stage-progress li').evaluateAll(items => new Set(items.map(item => Math.round(item.getBoundingClientRect().top))).size);
  expect(tops).toBe(1);
  const tabTops = await page.getByRole('navigation', { name: 'Run views' }).getByRole('link').evaluateAll(links => new Set(links.map(link => Math.round(link.getBoundingClientRect().top))).size);
  expect(tabTops).toBe(1);
});
