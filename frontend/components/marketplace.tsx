'use client';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import type { KeyboardEvent, ReactNode } from 'react';
import { guardedNavigate } from '@/lib/leave-guard';
import Icon, { type IconName } from './icons';

const TABS: [string, string, string, IconName][] = [['skills', '/marketplace/skills', 'Agent Skills', 'spark'], ['mcp', '/marketplace/mcp', 'MCP servers', 'plug']];

/** Marketplace shell: header and the two sections, each a route (/marketplace/skills, /marketplace/mcp). */
export default function MarketplaceLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const selected = TABS.find(([, href]) => pathname === href || pathname.startsWith(`${href}/`))?.[0] ?? 'skills';
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    const index = TABS.findIndex(([key]) => key === selected);
    const [key, href] = TABS[(index + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length];
    guardedNavigate(() => { router.push(href); document.getElementById(`marketplace-tab-${key}`)?.focus(); });
  };
  return <>
    <div className="page-header"><div><span className="eyebrow">Extensions</span><h1 className="page-title">Marketplace</h1><p>Agent skills and MCP servers for runs in this workspace.</p></div></div>
    <nav aria-label="Marketplace sections" className="tabs" onKeyDown={onKey}>
      {TABS.map(([key, href, name, icon]) => <Link key={key} href={href} id={`marketplace-tab-${key}`} aria-current={selected === key ? 'page' : undefined} className={selected === key ? 'selected' : ''} scroll={false}><Icon name={icon} />{name}</Link>)}
    </nav>
    <div id="marketplace-panel">{children}</div>
  </>;
}
