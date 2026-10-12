import { test, expect, type APIRequestContext, type Page } from '@playwright/test';

// Adapted from the review's frontend-audit: layout only, on every page, at 1366, 1024 and 390 px, in light and dark,
// and with forced wide fonts (Segoe UI and Verdana, the Windows metrics that broke the first layouts).
const WIDTHS = [1366, 1024, 390];
const THEMES = ['light', 'dark'] as const;
const FONTS = ['default', 'wide'] as const;
const WIDE_FONTS = 'body,button,input,select,textarea{font-family:"Segoe UI",Verdana,sans-serif!important}';
const LONG_REPO = `https://github.com/acme/${'very-long-repository-name-for-wrapping-tests-'.repeat(3)}end`;

/** No sideways page scroll, no clipped navigation labels, and the h1 wraps inside its box instead of overflowing. */
async function layoutIssues(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const issues: string[] = [];
    const viewport = document.documentElement.clientWidth;
    if (document.documentElement.scrollWidth > viewport + 1) issues.push(`horizontal page scroll ${document.documentElement.scrollWidth} > ${viewport}`);
    for (const el of document.querySelectorAll<HTMLElement>('.sidebar .nav-label, .sidebar .brand span, .theme-toggle span')) {
      if (el.clientWidth === 0 || el.getClientRects().length === 0) continue;
      if (el.scrollWidth > el.clientWidth + 1) issues.push(`clipped navigation label "${el.textContent?.trim()}"`);
    }
    const h1 = document.querySelector<HTMLElement>('h1');
    if (h1) {
      const wrap = getComputedStyle(h1).overflowWrap;
      if (wrap !== 'anywhere' && wrap !== 'break-word') issues.push('h1 cannot wrap');
      if (h1.scrollWidth > h1.clientWidth + 1) issues.push(`h1 overflows its box "${h1.textContent?.slice(0, 40)}"`);
      if (h1.getBoundingClientRect().right > viewport + 1) issues.push('h1 extends past the viewport');
    }
    // Collisions: controls in the same layout group must never overlap each other or their neighbours' boxes.
    const groups = ['.tabs a', '.stage-progress li > a', '.page-hero > *', '.header-actions > *', '.meta-strip > div', '.run-hero > *', '.sidebar nav a'];
    const boxes = groups.flatMap(selector => [...document.querySelectorAll<HTMLElement>(selector)].filter(el => el.getClientRects().length > 0).map(el => ({ el, r: el.getBoundingClientRect() })));
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const ox = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
      const oy = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
      const chain = (el: HTMLElement) => { const out: string[] = []; for (let p: HTMLElement | null = el; p && out.length < 5; p = p.parentElement) out.push(`${p.tagName.toLowerCase()}.${String(p.className).split(' ')[0]}`); return out.join('<'); };
      if (ox > 2 && oy > 2) issues.push(`collision "${a.el.textContent?.trim().slice(0, 24)}" [${chain(a.el)}] with "${b.el.textContent?.trim().slice(0, 24)}" [${chain(b.el)}] at ${Math.round(a.r.top)}/${Math.round(b.r.top)}`);
    }
    return issues;
  });
}

async function audit(page: Page, name: string) {
  for (const width of WIDTHS) for (const theme of THEMES) for (const font of FONTS) {
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate(({ theme, font, css }) => {
      document.documentElement.dataset.theme = theme;
      let style = document.getElementById('audit-fonts');
      if (font === 'wide') { if (!style) { style = document.createElement('style'); style.id = 'audit-fonts'; document.head.append(style); } style.textContent = css; }
      else style?.remove();
    }, { theme, font, css: WIDE_FONTS });
    await page.waitForTimeout(150);
    expect(await layoutIssues(page), `${name} at ${width}px ${theme} ${font} fonts`).toEqual([]);
  }
  await page.evaluate(() => { document.getElementById('audit-fonts')?.remove(); document.documentElement.dataset.theme = 'light'; });
  await page.setViewportSize({ width: 1366, height: 900 });
}

async function completeRun(page: Page, request: APIRequestContext, repo: string): Promise<string> {
  await page.goto('/runs/new');
  await page.getByRole('textbox', { name: 'Public GitHub repository URL' }).fill(repo);
  await page.getByRole('button', { name: 'Start repository review →' }).click();
  await expect(page.getByRole('button', { name: 'Approve repository inspection' })).toBeEnabled();
  const id = new URL(page.url()).pathname.split('/')[2];
  for (let i = 0; i < 12; i++) {
    const before = await (await request.get(`/api/harness/runs/${id}`)).json();
    if (before.status === 'complete') break;
    await page.getByRole('button', { name: /^(Approve repository inspection|Approve and proceed|Install in isolated environment|Run tests here)$/ }).click();
    await expect.poll(async () => { const next = await (await request.get(`/api/harness/runs/${id}`)).json(); return !next.busy && (next.status === 'complete' || next.pending?.id !== before.pending?.id); }, { timeout: 60000 }).toBeTruthy();
  }
  return id;
}

test('visual audit: no page scroll, no clipped labels, h1 wraps, at 1366, 1024 and 390 px in light and dark with wide fonts', async ({ page, request }) => {
  test.setTimeout(900000);
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  const long = await completeRun(page, request, LONG_REPO);
  const failing = await completeRun(page, request, 'https://github.com/acme/failure');
  const pages: [string, string][] = [
    ['/runs', 'run list'],
    ['/runs/new', 'new run'],
    [`/runs/${long}`, 'run review'],
    [`/runs/${long}/errors`, 'errors'],
    [`/runs/${long}/artifacts`, 'artifacts'],
    [`/runs/${long}/artifacts/test_design`, 'stage page'],
    [`/runs/${long}/tests`, 'generated tests'],
    [`/runs/${long}/evidence`, 'test reports'],
    [`/runs/${long}/logs`, 'logs'],
    [`/runs/${long}/observability`, 'observability'],
    [`/runs/${long}/audit`, 'audit'],
    [`/runs/${long}/prompts`, 'run prompts'],
    [`/runs/${failing}/qa`, 'qa report'],
    ['/prompts', 'agent prompts'],
    ['/settings', 'configuration'],
    ['/marketplace', 'marketplace'],
  ];
  for (const [path, name] of pages) {
    await page.goto(path);
    await expect(page.locator('h1, h2').first()).toBeVisible();
    await audit(page, name);
  }
  expect(errors).toEqual([]);
});
