import type { Page } from '@playwright/test';

/** Layout checks shared by the visual audit and the MCP journeys. */
export const WIDE_FONTS = 'body,button,input,select,textarea{font-family:"Segoe UI",Verdana,sans-serif!important}';
/** No sideways page scroll, no clipped navigation labels, and the h1 wraps inside its box instead of overflowing. */
export async function layoutIssues(page: Page): Promise<string[]> {
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

