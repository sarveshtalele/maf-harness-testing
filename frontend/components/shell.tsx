'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { api, isConnectionError, type Config, type RunSummary } from '@/lib/harness';
import { ResizeHandle, usePreference } from './data';
import Icon, { type IconName } from './icons';

type Shell = { runs: RunSummary[]; config: Config | null; serviceBusy: boolean; connectionError: string; refresh: () => Promise<void> };
const ShellContext = createContext<Shell | null>(null);
export const useShell = () => { const value = useContext(ShellContext); if (!value) throw new Error('useShell must be used inside the workspace layout'); return value; };

/** Interval for listing polls: fast while anything runs, slow when idle (findings 7 and 12). */
export const POLL_BUSY_MS = 2000;
export const POLL_IDLE_MS = 10000;

const NAV: { name: string; icon: IconName; href: string; match: string }[] = [
  { name: 'Runs', icon: 'runs', href: '/runs', match: '/runs' },
  { name: 'New run', icon: 'plusCircle', href: '/runs/new', match: '/runs/new' },
  { name: 'Marketplace', icon: 'store', href: '/marketplace', match: '/marketplace' },
  { name: 'Agent prompts', icon: 'message', href: '/prompts', match: '/prompts' },
  { name: 'Configuration', icon: 'gear', href: '/settings', match: '/settings' },
];

function pageName(pathname: string): string {
  if (pathname.startsWith('/runs/new')) return 'New run';
  if (pathname.startsWith('/runs/')) return 'Run';
  return NAV.find(item => pathname === item.href || pathname.startsWith(`${item.href}/`))?.name ?? 'Runs';
}

/** Shared layout for every workspace page: sidebar with collapse and theme, the run list and daily usage, one poll. */
export default function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [config, setConfig] = useState<Config | null>(null);
  const [serviceBusy, setServiceBusy] = useState(false);
  const [connectionError, setConnectionError] = useState('');
  const [sidebar, setSidebar] = usePreference<number>('maf:sidebar', 236);
  const [theme, setTheme] = usePreference<string>('maf:theme', 'light');
  const [collapsed, setCollapsed] = usePreference<boolean>('maf:sidebar-collapsed', false);
  const [moving, setMoving] = useState(false); // transitions only while the rail toggles, never while dragging the resize handle
  const movingTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(movingTimer.current), []);
  const toggleSidebar = () => {
    const next = !collapsed; clearTimeout(movingTimer.current);
    setMoving(true); setCollapsed(next); document.documentElement.dataset.sidebar = next ? 'collapsed' : '';
    movingTimer.current = setTimeout(() => setMoving(false), 250);
  };
  useEffect(() => { document.documentElement.dataset.theme = theme === 'dark' ? 'dark' : 'light'; }, [theme]);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const [listing, settings] = await Promise.allSettled([api<{ runs: RunSummary[]; busy: boolean }>('runs', undefined, signal), api<Config>('config', undefined, signal)]);
    if (signal?.aborted) return;
    let problem = '';
    if (listing.status === 'fulfilled') { setRuns(listing.value.runs); setServiceBusy(listing.value.busy); }
    else if (isConnectionError(listing.reason)) problem = (listing.reason as Error).message;
    if (settings.status === 'fulfilled') setConfig(settings.value);
    else if (isConnectionError(settings.reason)) problem ||= (settings.reason as Error).message;
    setConnectionError(problem);
  }, []);
  const busyRef = useRef(false);
  busyRef.current = serviceBusy || runs.some(run => run.busy);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await refresh(controller.signal); } catch (e) { if (!controller.signal.aborted) setConnectionError((e as Error).message); }
      if (!controller.signal.aborted) timer = setTimeout(poll, busyRef.current ? POLL_BUSY_MS : POLL_IDLE_MS);
    };
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [refresh]);

  // Route changes move focus to the page so screen readers announce the new content (finding 28).
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    document.getElementById('main-content')?.focus({ preventScroll: true });
  }, [pathname]);

  const value = useMemo<Shell>(() => ({ runs, config, serviceBusy, connectionError, refresh: () => refresh() }), [runs, config, serviceBusy, connectionError, refresh]);
  const style = { '--sidebar-width': `${Math.max(190, Math.min(340, Number(sidebar) || 236))}px` } as CSSProperties;
  const navLink = (item: (typeof NAV)[number]) => {
    const active = pathname === item.match || pathname.startsWith(`${item.match}/`);
    return <Link key={item.name} href={item.href} className={active ? 'active' : ''} data-label={item.name} aria-current={active ? 'page' : undefined}><Icon name={item.icon} /><span className="nav-label rail-hide">{item.name}{item.name === 'Runs' && <> <small>{runs.length}</small></>}</span></Link>;
  };
  return <ShellContext.Provider value={value}><div className={`app-shell${moving ? ' moving' : ''}`} style={style}>
    <aside className="sidebar"><div className="sidebar-top"><Link className="brand" href="/runs" data-label="Agentic QE Platform"><span className="brand-mark">QE</span><span className="rail-hide">Agentic QE Platform<small>Repository testing</small></span></Link><button className="sidebar-toggle" aria-label="Collapse sidebar" aria-expanded={!collapsed} aria-controls="main-navigation" data-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} onClick={toggleSidebar}><Icon name={collapsed ? 'panelOpen' : 'panelClose'} /></button></div>
      <div className="workspace-label"><span className="dot" /> Local workspace</div>
      <nav id="main-navigation" aria-label="Main navigation">{NAV.map(navLink)}</nav>
      <div className="sidebar-footer">{config && <><span className="eyebrow">Daily token usage</span><progress aria-label="Daily token usage" value={config.daily.used} max={config.daily.limit || 1} /><small>{config.daily.used.toLocaleString()} / {config.daily.limit.toLocaleString()}</small></>}<button className="theme-toggle" data-label={theme === 'dark' ? 'Light appearance' : 'Dark appearance'} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}><Icon name={theme === 'dark' ? 'sun' : 'moon'} /><span className="rail-hide">{theme === 'dark' ? 'Light appearance' : 'Dark appearance'}</span></button><small>History stays on this user’s machine.</small></div>
      <ResizeHandle label="Resize sidebar" start={() => { const initial = sidebar; return dx => setSidebar(Math.max(190, Math.min(340, initial + dx))); }} />
    </aside>
    <main id="main-content" tabIndex={-1}><header className="topbar"><div><span>Workspace</span><span className="slash">/</span><b>{pageName(pathname)}</b></div><div className="top-actions"><span className="local-status"><span className={`dot ${connectionError ? 'offline' : ''}`} />{connectionError ? 'Connection needs attention' : 'Local backend'}</span></div></header>
      <div className="main-content">
        {connectionError && <div className="notice bad" role="alert"><strong>{connectionError}</strong><button type="button" onClick={() => { void refresh().catch(() => undefined); }}>Refresh connection</button></div>}
        {children}
      </div>
    </main>
  </div></ShellContext.Provider>;
}

