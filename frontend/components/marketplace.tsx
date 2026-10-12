'use client';
import { useState } from 'react';
import Skills from './skills';
import McpServers from './mcp-servers';

type Tab = 'skills' | 'mcp';
const TABS: [Tab, string][] = [['skills', 'Agent Skills'], ['mcp', 'MCP servers']];

/** Marketplace: agent skills and MCP servers. The tab lives in the URL (&tab=mcp); /?page=skills opens Agent Skills. */
export default function Marketplace() {
  const [tab, setTab] = useState<Tab>(() => (typeof location !== 'undefined' && new URLSearchParams(location.search).get('tab') === 'mcp' ? 'mcp' : 'skills'));
  const choose = (next: Tab) => { setTab(next); history.replaceState({}, '', next === 'mcp' ? '/?page=marketplace&tab=mcp' : '/?page=marketplace'); };
  return <>
    <div className="tabs" role="tablist" aria-label="Marketplace sections">{TABS.map(([key, name]) => <button key={key} role="tab" id={`marketplace-tab-${key}`} aria-controls="marketplace-panel" aria-selected={tab === key} className={tab === key ? 'selected' : ''} onClick={() => choose(key)}>{name}</button>)}</div>
    <div role="tabpanel" id="marketplace-panel" aria-labelledby={`marketplace-tab-${tab}`}>{tab === 'skills' ? <Skills /> : <McpServers />}</div>
  </>;
}
