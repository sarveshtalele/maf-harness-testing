'use client';
import { useEffect, useState, type KeyboardEvent } from 'react';
import Skills from './skills';
import McpServers from './mcp-servers';
import Icon, { type IconName } from './icons';

type Tab = 'skills' | 'mcp';
const TABS: [Tab, string, IconName][] = [['skills', 'Agent Skills', 'spark'], ['mcp', 'MCP servers', 'plug']];
const tabFromUrl = (): Tab => (new URLSearchParams(location.search).get('tab') === 'mcp' ? 'mcp' : 'skills');

/** Marketplace: agent skills and MCP servers. The tab lives in the URL (?tab=mcp). Old /?page=marketplace links redirect here. */
export default function Marketplace() {
  const [tab, setTab] = useState<Tab>(() => (typeof location !== 'undefined' ? tabFromUrl() : 'skills'));
  // Back and Forward move between tab entries (choose pushes them), so follow the URL
  useEffect(() => { const sync = () => setTab(tabFromUrl()); window.addEventListener('popstate', sync); return () => window.removeEventListener('popstate', sync); }, []);
  const choose = (next: Tab) => { setTab(next); history.pushState({}, '', next === 'mcp' ? '/marketplace?tab=mcp' : '/marketplace'); };
  const keys = TABS.map(([key]) => key);
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    const next = keys[(keys.indexOf(tab) + (e.key === 'ArrowRight' ? 1 : keys.length - 1)) % keys.length];
    choose(next);
    document.getElementById(`marketplace-tab-${next}`)?.focus();
  };
  return <>
    <div className="page-header"><div><span className="eyebrow">Extensions</span><h1 className="page-title">Marketplace</h1><p>Agent skills and MCP servers for runs in this workspace.</p></div></div>
    <div className="tabs" role="tablist" aria-label="Marketplace sections" onKeyDown={onKey}>{TABS.map(([key, name, icon]) => <button key={key} role="tab" id={`marketplace-tab-${key}`} aria-controls="marketplace-panel" tabIndex={tab === key ? 0 : -1} aria-selected={tab === key} className={tab === key ? 'selected' : ''} onClick={() => choose(key)}><Icon name={icon} />{name}</button>)}</div>
    <div role="tabpanel" id="marketplace-panel" aria-labelledby={`marketplace-tab-${tab}`}>{tab === 'skills' ? <Skills /> : <McpServers />}</div>
  </>;
}
