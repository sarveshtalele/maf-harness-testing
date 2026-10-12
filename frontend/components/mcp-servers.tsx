'use client';
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, apiDelete, desktop, label, type Tone } from '@/lib/harness';
import { takeNotice } from '@/lib/leave-guard';
import { ConfirmDialog, EmptyState } from './page';
import Icon from './icons';

export type Tool = { name: string; description: string; read_only?: boolean; destructive?: boolean };
export type Server = {
  name: string; transport: 'stdio' | 'http'; command: string; args: string[]; env_keys: string[]; url: string; enabled: boolean;
  stages: string[]; allowed_tools: string[]; approval_required_tools: string[]; preset: string; discovered_tools: Tool[]; secrets: Record<string, boolean>; ready: boolean; problems?: string[];
};
export type Budget = Record<string, { tools: number; max: number; skipped: string[] }>;
export type Preset = { label: string; note: string; server: Omit<Server, 'secrets' | 'ready' | 'discovered_tools'> };
export type McpData = { servers: Server[]; presets: Record<string, Preset>; stages: string[]; max_tools_per_stage?: number; stage_budget?: Budget };
export type Desktop = { saveMcpSecrets(server: string, values: Record<string, string>): Promise<unknown>; deleteMcpServer(server: string): Promise<unknown> };
export type Access = 'auto' | 'approval' | 'hidden';
export const MAX_TOOLS_PER_STAGE = 8; // the API sends max_tools_per_stage; this is only the fallback before it loads

/** The fields the API stores for one server (secrets and discovered tools are separate). */
export const pick = (s: Server) => ({ name: s.name, transport: s.transport, command: s.command, args: s.args, env_keys: s.env_keys, url: s.url, enabled: s.enabled, stages: s.stages, allowed_tools: s.allowed_tools, approval_required_tools: s.approval_required_tools, preset: s.preset });
/** Discovered tools plus any tool named in the saved lists, sorted. */
export const toolNames = (discovered: Tool[], s: Pick<Server, 'allowed_tools' | 'approval_required_tools'>) => [...new Set([...discovered.map(t => t.name), ...s.allowed_tools, ...s.approval_required_tools])].sort();
export const accessOf = (s: Pick<Server, 'allowed_tools' | 'approval_required_tools'>, t: string): Access => (s.allowed_tools.includes(t) ? 'auto' : s.approval_required_tools.includes(t) ? 'approval' : 'hidden');
export const serverStatus = (s: Server): [string, Tone] => (s.problems?.length ? ['Problem', 'bad'] : !s.enabled ? ['Disabled', ''] : s.ready ? ['Enabled', 'good'] : ['Needs secrets', 'warn']);
export const commandLine = (s: Server) => (s.transport === 'stdio' ? [s.command, ...s.args].join(' ') : s.url);
export const TIMEOUT_HINT = 'The first Test of an npx server can time out while npx downloads the package; run it again.';

/** Read-only rule. The server's own readOnlyHint wins when it sent one. Otherwise a tool is read-only only when its
 *  first word is a read verb and no word is a write verb; unknown names count as "Can make changes". */
const READ_FIRST = new Set(['get', 'list', 'search', 'read', 'snapshot', 'console', 'describe', 'find', 'query', 'fetch', 'show', 'view']);
const WRITE_WORDS = new Set(('create update delete remove write set add run exec execute click type navigate send post put patch purge drop clear reset upload merge close fill press edit move submit assign comment mark replace install kill start stop and or save insert rename copy approve').split(' '));
export const nameWords = (name: string) => name.replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
export const isReadOnly = (tool: Tool) => {
  if (typeof tool.read_only === 'boolean') return tool.read_only;
  const words = nameWords(tool.name);
  return words.length > 0 && READ_FIRST.has(words[0]) && !words.some(w => WRITE_WORDS.has(w));
};

export const desktopBridge = () => desktop<Desktop>();
export const saveSecretValues = (name: string, values: Record<string, string>) => {
  const d = desktopBridge();
  return d ? d.saveMcpSecrets(name, values) : api(`mcp-servers/${encodeURIComponent(name)}/secrets`, values);
};
export const removeServer = (name: string) => {
  const d = desktopBridge();
  return d ? d.deleteMcpServer(name) : apiDelete(`mcp-servers/${encodeURIComponent(name)}`);
};

/** Tools a server counts toward a stage: its approved and approval-required names; once it has been tested, names
 *  it no longer lists are dropped (they cannot run). Never tested: the saved names count as they are. */
export const countedNames = (allowed: string[], approval: string[], discovered: Tool[]) => {
  const names = [...new Set([...allowed, ...approval])];
  return discovered.length ? names.filter(n => discovered.some(t => t.name === n)) : names;
};

/** The runtime's stage rule, in list order: a server whose tools would exceed the stage's maximum is skipped. */
export type StageEntry = { name: string; enabled: boolean; stages: string[]; tools: string[] };
export function stageLoad(entries: StageEntry[], stages: string[], max: number) {
  const out: Record<string, { demand: number; skipped: string[] }> = {};
  for (const st of stages) {
    let used = 0; let demand = 0; const skipped: string[] = [];
    for (const e of entries) {
      if (!(e.enabled && e.stages.includes(st))) continue;
      demand += e.tools.length;
      if (used + e.tools.length > max) skipped.push(e.name); else used += e.tools.length;
    }
    if (skipped.length) out[st] = { demand, skipped };
  }
  return out;
}

/** Loads the server list; the page reloads it after every change. */
export function useMcpData() {
  const [data, setData] = useState<McpData | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(() => api<McpData>('mcp-servers').then(d => { setData(d); setError(''); }).catch(e => setError((e as Error).message)), []);
  useEffect(() => { void load(); }, [load]);
  return { data, error, load };
}

/** Saved-state budget notices (the cards page): the API's stage_budget. */
export function BudgetNotices({ budget }: { budget: Budget }) {
  return <>{Object.entries(budget).map(([st, b]) => <p key={st} className="mp-warn" role="status"><span className="badge warn">Over budget</span> Stage {label(st)}: {b.tools} MCP tools, max {b.max}; servers beyond the budget are skipped. Skipped: {b.skipped.join(', ')}.</p>)}</>;
}

function ServerCard({ server: s, budget, onDelete }: { server: Server; budget: Budget; onDelete: () => void }) {
  const names = toolNames(s.discovered_tools, s);
  const [status, tone] = serverStatus(s);
  const over = s.enabled ? s.stages.filter(st => budget[st]) : [];
  const approved = s.allowed_tools.length; const asks = s.approval_required_tools.length;
  return <article className="mcp-card" aria-label={`MCP server ${s.name}`}>
    <div className="mcp-card-head">
      <h2 className="mcp-card-title"><Link href={`/marketplace/mcp/${encodeURIComponent(s.name)}`} className="mcp-stretch">{s.name}</Link></h2>
      <span className="skill-tags"><span className="badge">{s.transport}</span><span className={`badge ${tone}`}>{status}</span></span>
    </div>
    <code className="mcp-code">{commandLine(s)}</code>
    {s.problems?.map(p => <p key={p} className="mp-warn"><span className="badge warn">Fix before enabling</span> {p}</p>)}
    <p className="field-help">Stages: {s.stages.length ? s.stages.map(st => label(st)).join(', ') : 'none'}</p>
    <p className="field-help">{names.length} {names.length === 1 ? 'tool' : 'tools'} · {approved} always allow · {asks} ask · {names.length - approved - asks} blocked</p>
    {!s.discovered_tools.length && <p className="field-help">No tools yet. Run Test connection to discover them.</p>}
    {!!names.length && <ul className="mp-tools">{names.slice(0, 6).map(n => <li key={n}><code>{n}</code></li>)}{names.length > 6 && <li className="field-help">+{names.length - 6} more</li>}</ul>}
    {over.map(st => <p key={st} className="mp-warn"><span className="badge warn">Over budget</span> Stage {label(st)}: {budget[st].tools} MCP tools, max {budget[st].max}.</p>)}
    <div className="mcp-card-actions">
      <Link className="button small" href={`/marketplace/mcp/${encodeURIComponent(s.name)}`}>Manage</Link>
      <button type="button" className="small danger-button" aria-label={`Delete ${s.name}`} onClick={onDelete}>Delete</button>
    </div>
  </article>;
}

/** /marketplace/mcp: the server card grid. Clicking a card opens its page; Delete asks first (ConfirmDialog). */
export default function McpServers() {
  const { data, error, load } = useMcpData();
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [target, setTarget] = useState('');
  const [busy, setBusy] = useState(false);
  const addRef = useRef<HTMLAnchorElement>(null);
  // A page that just changed the list leaves its message in session storage: shown once, then removed.
  useEffect(() => {
    const flash = takeNotice();
    if (!flash) return;
    setNotice({ ok: flash.ok, text: flash.text });
    if (flash.focusAdd) addRef.current?.focus(); // the card is gone: focus moves to Add MCP server
  }, []);
  const remove = async () => {
    setBusy(true);
    const name = target;
    try { await removeServer(name); setTarget(''); setNotice({ ok: true, text: `${name} deleted.` }); await load(); addRef.current?.focus(); }
    catch (e) { setTarget(''); setNotice({ ok: false, text: (e as Error).message }); }
    finally { setBusy(false); }
  };
  const budget: Budget = data?.stage_budget ?? {};
  const servers = data?.servers ?? [];
  return <>
    <div className="page-header"><div><h2><Icon name="plug" />MCP servers</h2><p>Give the agents extra tools from MCP servers (Jira, Playwright, others). Servers start disabled. Approved tools run automatically, tools that need approval wait for a person, and blocked tools are hidden. Output from a server is untrusted evidence.</p></div><Link ref={addRef} className="button primary" href="/marketplace/mcp/new">Add MCP server</Link></div>
    <BudgetNotices budget={budget} />
    {notice && <p role="status" className={notice.ok ? '' : 'bad'}>{notice.text}</p>}
    {error && <p role="alert" className="bad">{error}</p>}
    {data && !servers.length && <EmptyState title="No MCP servers yet" icon={<Icon name="plug" />}>Add a server from a pasted command or config, a preset, or by hand. Servers start disabled.</EmptyState>}
    <div className="mcp-grid">{servers.map(s => <ServerCard key={s.name} server={s} budget={budget} onDelete={() => setTarget(s.name)} />)}</div>
    <ConfirmDialog open={!!target} title={`Delete ${target}?`} confirmLabel="Delete server" tone="danger" busy={busy} onConfirm={() => void remove()} onClose={() => setTarget('')}>
      <p>The server and its stored secrets are removed. Runs no longer see its tools. This cannot be undone.</p>
    </ConfirmDialog>
  </>;
}
