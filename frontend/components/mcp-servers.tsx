'use client';
import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { api, apiDelete, desktop, label, type Tone } from '@/lib/harness';
import { ConfirmDialog, EmptyState } from './page';
import Icon from './icons';

export type Tool = { name: string; description: string };
export type Server = {
  name: string; transport: 'stdio' | 'http'; command: string; args: string[]; env_keys: string[]; url: string; enabled: boolean;
  stages: string[]; allowed_tools: string[]; approval_required_tools: string[]; preset: string; discovered_tools: Tool[]; secrets: Record<string, boolean>; ready: boolean; problems?: string[];
};
export type Budget = Record<string, { tools: number; max: number; skipped: string[] }>;
export type Preset = { label: string; note: string; server: Omit<Server, 'secrets' | 'ready' | 'discovered_tools'> };
export type McpData = { servers: Server[]; presets: Record<string, Preset>; stages: string[]; max_tools_per_stage?: number; stage_budget?: Budget };
export type Desktop = { saveMcpSecrets(server: string, values: Record<string, string>): Promise<unknown>; deleteMcpServer(server: string): Promise<unknown> };
export type Access = 'auto' | 'approval' | 'hidden';

/** The fields the API stores for one server (secrets and discovered tools are separate). */
export const pick = (s: Server) => ({ name: s.name, transport: s.transport, command: s.command, args: s.args, env_keys: s.env_keys, url: s.url, enabled: s.enabled, stages: s.stages, allowed_tools: s.allowed_tools, approval_required_tools: s.approval_required_tools, preset: s.preset });
/** Discovered tools plus any tool named in the saved lists, sorted. */
export const toolNames = (discovered: Tool[], s: Pick<Server, 'allowed_tools' | 'approval_required_tools'>) => [...new Set([...discovered.map(t => t.name), ...s.allowed_tools, ...s.approval_required_tools])].sort();
export const accessOf = (s: Pick<Server, 'allowed_tools' | 'approval_required_tools'>, t: string): Access => (s.allowed_tools.includes(t) ? 'auto' : s.approval_required_tools.includes(t) ? 'approval' : 'hidden');
export const serverStatus = (s: Server): [string, Tone] => (s.problems?.length ? ['Problem', 'bad'] : !s.enabled ? ['Disabled', ''] : s.ready ? ['Enabled', 'good'] : ['Needs secrets', 'warn']);
/** Name heuristic for Claude Desktop-style grouping: a read-only tool has get, list, search, read, snapshot or console as a word. */
const READ_WORDS = new Set(['get', 'list', 'search', 'read', 'snapshot', 'console']);
export const isReadOnly = (name: string) => name.replace(/([a-z])([A-Z])/g, '$1_$2').toLowerCase().split(/[^a-z0-9]+/).some(word => READ_WORDS.has(word));
export const commandLine = (s: Server) => (s.transport === 'stdio' ? [s.command, ...s.args].join(' ') : s.url);
export const TIMEOUT_HINT = 'The first Test of an npx server can time out while npx downloads the package; run it again.';
export const MAX_TOOLS_PER_STAGE = 8;

export const desktopBridge = () => desktop<Desktop>();
export const saveSecretValues = (name: string, values: Record<string, string>) => {
  const d = desktopBridge();
  return d ? d.saveMcpSecrets(name, values) : api(`mcp-servers/${encodeURIComponent(name)}/secrets`, values);
};
export const removeServer = (name: string) => {
  const d = desktopBridge();
  return d ? d.deleteMcpServer(name) : apiDelete(`mcp-servers/${encodeURIComponent(name)}`);
};

/** Loads the server list; the page reloads it after every change. */
export function useMcpData() {
  const [data, setData] = useState<McpData | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(() => api<McpData>('mcp-servers').then(d => { setData(d); setError(''); }).catch(e => setError((e as Error).message)), []);
  useEffect(() => { void load(); }, [load]);
  return { data, error, load };
}

/** Per-stage budget notices (saved state): shared by the cards page and the server page. */
export function BudgetNotices({ budget, stages }: { budget: Budget; stages?: string[] }) {
  return <>{Object.entries(budget).filter(([st]) => !stages || stages.includes(st)).map(([st, b]) => <p key={st} className="mp-warn" role="status"><span className="badge warn">Over budget</span> Stage {label(st)}: {b.tools} MCP tools, max {b.max}; servers beyond the budget are skipped. Skipped: {b.skipped.join(', ')}.</p>)}</>;
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
    <p className="field-help">{names.length} tools · {approved} always allow · {asks} ask · {names.length - approved - asks} blocked</p>
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
  // Other pages leave a short notice in the URL (?deleted=name, ?added=count); read it once after mount.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const deleted = params.get('deleted'); const added = params.get('added');
    if (!deleted && !added) return;
    setNotice({ ok: true, text: deleted ? `${deleted} deleted.` : `${added} MCP server${added === '1' ? '' : 's'} added. ${added === '1' ? 'It starts' : 'They start'} disabled until you enable ${added === '1' ? 'it' : 'them'}.` });
    history.replaceState(null, '', '/marketplace/mcp');
  }, []);
  const remove = async () => {
    setBusy(true);
    try { await removeServer(target); setTarget(''); setNotice({ ok: true, text: `${target} deleted.` }); await load(); }
    catch (e) { setTarget(''); setNotice({ ok: false, text: (e as Error).message }); }
    finally { setBusy(false); }
  };
  const budget: Budget = data?.stage_budget ?? {};
  const servers = data?.servers ?? [];
  return <>
    <div className="page-header"><div><h2><Icon name="plug" />MCP servers</h2><p>Give the agents extra tools from MCP servers (Jira, Playwright, others). Servers start disabled. Approved tools run automatically, tools that need approval wait for a person, and blocked tools are hidden. Output from a server is untrusted evidence.</p></div><Link className="button primary" href="/marketplace/mcp/new">Add MCP server</Link></div>
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

