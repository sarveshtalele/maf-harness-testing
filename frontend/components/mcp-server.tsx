'use client';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { api, desktop, label } from '@/lib/harness';
import { ConfirmDialog, DetailPage, type SectionSpec } from './page';
import {
  BudgetNotices, MAX_TOOLS_PER_STAGE, TIMEOUT_HINT, accessOf, commandLine, isReadOnly, pick, removeServer, saveSecretValues, serverStatus, toolNames, useMcpData,
  type Access, type Budget, type Server,
} from './mcp-servers';

type Draft = { enabled: boolean; stages: string[]; allowed_tools: string[]; approval_required_tools: string[] };
type Notice = { ok: boolean; text: string } | null;
const draftOf = (s: Server): Draft => ({ enabled: s.enabled, stages: [...s.stages], allowed_tools: [...s.allowed_tools], approval_required_tools: [...s.approval_required_tools] });
const lines = (text: string) => text.split('\n').map(x => x.trim()).filter(Boolean);
const ACCESS: [Access, string][] = [['auto', 'Always allow'], ['approval', 'Ask every time'], ['hidden', 'Block']];
const DESCRIPTION_CLAMP = 100;

/** The comparable shape of a server's editable fields: unsaved changes are a difference in this. */
const shape = (s: Draft & { args: string[]; url: string }) => JSON.stringify({ enabled: s.enabled, stages: [...s.stages].sort(), allowed: [...s.allowed_tools].sort(), ask: [...s.approval_required_tools].sort(), args: s.args, url: s.url });

/** /marketplace/mcp/[name]: connection, secrets, stages and tools for one server (Claude Desktop-style settings). */
export default function McpServerPage() {
  const { name = '' } = useParams<{ name: string }>();
  const serverName = decodeURIComponent(name);
  const { data, error, load } = useMcpData();
  const server = data?.servers.find(s => s.name === serverName);
  if (error) return <DetailPage level={2} title={serverName} breadcrumbs={crumbs(serverName)}><p role="alert" className="notice bad">{error}</p></DetailPage>;
  if (!data) return <p role="status">Loading MCP server…</p>;
  if (!server) return <DetailPage level={2} title="Server not found" breadcrumbs={crumbs(serverName)}><p className="notice">No MCP server named {serverName} is configured. <Link href="/marketplace/mcp">Back to MCP servers</Link></p></DetailPage>;
  return <ServerEditor key={server.name} server={server} budget={data.stage_budget ?? {}} stages={data.stages} load={load} />;
}

const crumbs = (serverName: string) => [{ label: 'Marketplace', href: '/marketplace/skills' }, { label: 'MCP servers', href: '/marketplace/mcp' }, { label: serverName || 'Server' }];

function ServerEditor({ server, budget, stages, load }: { server: Server; budget: Budget; stages: string[]; load: () => Promise<void> }) {
  const router = useRouter();
  const [draft, setDraft] = useState<Draft>(() => draftOf(server));
  const fixing = !!server.problems?.length; // stored with a problem: args or URL must be fixed before enabling
  const [argsText, setArgsText] = useState(server.args.join('\n'));
  const [urlText, setUrlText] = useState(server.url);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [leaving, setLeaving] = useState('');
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const edited = { ...draft, args: fixing ? lines(argsText) : server.args, url: fixing ? urlText.trim() : server.url };
  const dirty = shape(edited) !== shape({ ...draftOf(server), args: server.args, url: server.url });
  const names = toolNames(server.discovered_tools, draft);
  const counts = (d: Draft) => {
    const a = names.filter(n => accessOf(d, n) === 'auto').length; const h = names.filter(n => accessOf(d, n) === 'approval').length;
    return { a, h, b: names.length - a - h };
  };
  const t = counts(draft);
  const [status, tone] = serverStatus(server);
  const needed = draft.enabled ? draft.stages.filter(st => budget[st]) : [];
  const liveTooMany = draft.enabled && draft.stages.length > 0 && t.a + t.h > MAX_TOOLS_PER_STAGE;

  // Unsaved changes: beforeunload, and in-app link clicks (the route-change guard) ask first.
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const link = (e.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!link || link.target || link.hasAttribute('download')) return;
      const url = new URL(link.href);
      if (url.origin !== location.origin || url.pathname + url.search === location.pathname + location.search) return;
      e.preventDefault(); e.stopPropagation();
      setLeaving(url.pathname + url.search + url.hash);
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    document.addEventListener('click', onClick, true);
    return () => { window.removeEventListener('beforeunload', onBeforeUnload); document.removeEventListener('click', onClick, true); };
  }, [dirty]);

  // Opened from Add MCP server: ?added=1 shows a one-time notice.
  useEffect(() => {
    if (new URLSearchParams(location.search).get('added')) { setNotice({ ok: true, text: `${server.name} added. It stays disabled until you enable it and save.` }); history.replaceState(null, '', location.pathname); }
  }, [server.name]);

  const run = async (fn: () => Promise<string | void>) => {
    setBusy(true); setNotice(null);
    try { const text = await fn(); if (text) setNotice({ ok: true, text }); } catch (e) { setNotice({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  };
  const save = () => run(async () => {
    const next = await api<Server>('mcp-servers', { ...pick(server), enabled: draft.enabled, stages: draft.stages, allowed_tools: draft.allowed_tools, approval_required_tools: draft.approval_required_tools, args: edited.args, url: edited.url });
    setDraft(draftOf(next)); setArgsText(next.args.join('\n')); setUrlText(next.url);
    await load();
    return 'Saved.';
  });
  const discard = () => { setDraft(draftOf(server)); setArgsText(server.args.join('\n')); setUrlText(server.url); setNotice(null); };
  const test = () => run(async () => {
    const r = await api<{ ok: boolean; message?: string; count?: number }>(`mcp-servers/${encodeURIComponent(server.name)}/test`, {});
    await load(); // discovered tools only: unsaved edits to access stay in the form
    if (!r.ok) throw new Error(r.message || 'Connection failed.');
    return `Connected: ${r.count} tool${r.count === 1 ? '' : 's'} available.`;
  });
  const saveSecretsNow = () => run(async () => {
    const values = Object.fromEntries(Object.entries(secrets).filter(([, v]) => v));
    if (!Object.keys(values).length) throw new Error('Enter a value first.');
    await saveSecretValues(server.name, values);
    setSecrets({}); await load();
    return 'Secrets stored (write-only).';
  });
  const deleteNow = async () => {
    setConfirmDelete(false);
    try { await removeServer(server.name); router.push(`/marketplace/mcp?deleted=${encodeURIComponent(server.name)}`); }
    catch (e) { setNotice({ ok: false, text: (e as Error).message }); }
  };

  // Tools: one access state per tool; the bulk actions only touch the draft until Save.
  const setAccess = (tool: string, m: Access) => setDraft(d => ({
    ...d,
    allowed_tools: [...d.allowed_tools.filter(x => x !== tool), ...(m === 'auto' ? [tool] : [])],
    approval_required_tools: [...d.approval_required_tools.filter(x => x !== tool), ...(m === 'approval' ? [tool] : [])],
  }));
  const bulk = (fn: (d: Draft) => Draft) => setDraft(fn);
  const readOnly = names.filter(isReadOnly);
  const needle = search.trim().toLowerCase();
  const matches = (n: string) => !needle || n.toLowerCase().includes(needle) || (server.discovered_tools.find(x => x.name === n)?.description ?? '').toLowerCase().includes(needle);
  const groups: [string, string[]][] = [['Read-only', names.filter(n => isReadOnly(n) && matches(n))], ['Can make changes', names.filter(n => !isReadOnly(n) && matches(n))]];

  const sections: SectionSpec[] = [
    { id: 'connection', title: 'Connection', defaultOpen: true, render: () => <>
      <p className="field-help">{server.transport === 'stdio' ? 'Runs this command on this computer.' : 'Connects to this HTTPS endpoint.'}</p>
      <div className="mcp-code" aria-label={server.transport === 'stdio' ? 'Command' : 'URL'}><code>{commandLine(server)}</code></div>
      {!!server.env_keys.length && <p className="field-help">Secret environment variables: {server.env_keys.join(', ')}</p>}
      {fixing && <fieldset><legend>Fix before enabling</legend>
        <ul className="mcp-problems">{server.problems?.map(p => <li key={p}>{p}</li>)}</ul>
        <p className="field-help">Remove the value from the arguments or URL and put it in the secret field (docker: -e KEY with the value as KEY=value in the environment), then Save.</p>
        {server.transport === 'stdio'
          ? <label>Arguments (one per line)<textarea value={argsText} onChange={e => setArgsText(e.target.value)} spellCheck={false} /></label>
          : <label>URL<input value={urlText} onChange={e => setUrlText(e.target.value)} inputMode="url" /></label>}
      </fieldset>}
      <p className="field-help">{TIMEOUT_HINT}</p>
    </> },
    ...(Object.keys(server.secrets).length ? [{ id: 'secrets', title: 'Secrets', defaultOpen: true, render: () => <>
      <p className="field-help">Write-only{desktop() ? ', kept in your OS keychain' : ''}. Values are never shown again; leave a field empty to keep its value.</p>
      {Object.entries(server.secrets).map(([k, set]) => <label key={k}>{k === 'BEARER_TOKEN' ? 'Token (Bearer, or "Basic …")' : k}<input type="password" autoComplete="new-password" spellCheck={false} value={secrets[k] || ''} onChange={e => setSecrets({ ...secrets, [k]: e.target.value })} placeholder={set ? 'Set — leave empty to keep' : 'Not set'} /></label>)}
      <div className="header-actions"><button type="button" disabled={busy} onClick={saveSecretsNow}>Save secrets</button></div>
    </> }] : []),
    { id: 'stages', title: 'Stages', count: draft.stages.length, defaultOpen: true, render: () => <fieldset><legend>Stages that may use this server</legend>
      {stages.map(st => <label key={st} className="check"><input type="checkbox" checked={draft.stages.includes(st)} onChange={e => setDraft(d => ({ ...d, stages: e.target.checked ? [...d.stages, st] : d.stages.filter(x => x !== st) }))} />{label(st)}</label>)}
    </fieldset> },
    { id: 'tools', title: 'Tools', count: names.length, defaultOpen: true, render: () => <>
      {!names.length ? <p className="field-help">No tools yet. Run Test connection to discover them.</p> : <>
        <div className="table-tools"><input type="search" aria-label="Search tools" placeholder="Search tools…" value={search} onChange={e => setSearch(e.target.value)} /><small>{names.length} tools</small></div>
        <div className="header-actions mcp-bulk" role="group" aria-label="Bulk tool actions">
          <button type="button" disabled={busy || !readOnly.length} onClick={() => bulk(d => ({ ...d, allowed_tools: [...new Set([...d.allowed_tools, ...readOnly])], approval_required_tools: d.approval_required_tools.filter(x => !readOnly.includes(x)) }))}>Allow all read-only</button>
          <button type="button" disabled={busy} onClick={() => bulk(d => ({ ...d, allowed_tools: names, approval_required_tools: [] }))}>Approve all</button>
          <button type="button" disabled={busy} onClick={() => bulk(d => ({ ...d, allowed_tools: [], approval_required_tools: [] }))}>Block all</button>
          <button type="button" disabled={busy} onClick={() => bulk(() => draftOf(server))}>Reset to saved</button>
        </div>
        {t.a > 0 && <p className="notice bad" role="note">Approved tools run without asking, including any that change data (create, edit, navigate). Approve only tools you trust.</p>}
        {groups.map(([title, list]) => list.length ? <div key={title} className="mcp-group"><h4>{title} <span className="section-count">{list.length}</span></h4><ul className="mcp-tool-list">
          {list.map(n => <ToolRow key={n} name={n} description={server.discovered_tools.find(x => x.name === n)?.description ?? ''} found={server.discovered_tools.some(x => x.name === n)} access={accessOf(draft, n)} readOnly={isReadOnly(n)} onAccess={m => setAccess(n, m)} />)}
        </ul></div> : null)}
        {!groups.some(([, l]) => l.length) && <p className="empty-inline">No tools match your search.</p>}
      </>}
    </> },
  ];

  const footer = <footer className="mcp-footer" aria-label="Tool summary and saving">
    <div className="mcp-footer-text">
      <strong aria-live="polite">{names.length} {names.length === 1 ? 'tool' : 'tools'} · {t.a} always allow · {t.h} ask · {t.b} blocked</strong>
      {needed.map(st => <span key={st} className="mp-warn"><span className="badge warn">Over budget</span> Stage {label(st)}: {budget[st].tools} MCP tools, max {budget[st].max}; servers beyond the budget are skipped (saved state).</span>)}
      {liveTooMany && <span className="mp-warn"><span className="badge warn">Budget</span> {t.a + t.h} tools can run here; a stage allows at most {MAX_TOOLS_PER_STAGE} MCP tools across all servers.</span>}
      <span className={dirty ? 'bad' : ''}>{dirty ? 'Unsaved changes' : 'All changes saved'}</span>
    </div>
    <div className="header-actions"><button type="button" disabled={!dirty || busy} onClick={discard}>Discard</button><button type="button" className="primary" disabled={!dirty || busy} onClick={save}>Save</button></div>
  </footer>;

  return <>
    <DetailPage level={2} eyebrow="MCP server" title={server.name} breadcrumbs={crumbs(server.name)} footer={footer}
      badges={<>
        <span className="badge">{server.transport}</span>
        <span className={`badge ${tone}`}>{status}</span>
        <label className="mcp-switch"><input type="checkbox" role="switch" checked={draft.enabled} disabled={busy} onChange={e => setDraft(d => ({ ...d, enabled: e.target.checked }))} /><span>Enabled</span></label>
      </>}
      actions={<>
        <button type="button" disabled={busy} onClick={test}>Test connection</button>
        <button type="button" className="mcp-delete" disabled={busy} onClick={() => setConfirmDelete(true)}>Delete server</button>
      </>}
      notices={<>
        {notice && <p role="status" className={notice.ok ? '' : 'bad'}>{notice.text}</p>}
        <BudgetNotices budget={budget} stages={draft.enabled ? draft.stages : []} />
      </>}
      sections={sections}
    />
    <ConfirmDialog open={confirmDelete} title={`Delete ${server.name}?`} confirmLabel="Delete server" tone="danger" busy={busy} onConfirm={() => void deleteNow()} onClose={() => setConfirmDelete(false)}>
      <p>Its stored secrets are removed too. Runs no longer see its tools. This cannot be undone.</p>
    </ConfirmDialog>
    <ConfirmDialog open={!!leaving} title="Leave with unsaved changes?" confirmLabel="Leave without saving" cancelLabel="Stay" tone="danger" onConfirm={() => { const next = leaving; setLeaving(''); router.push(next); }} onClose={() => setLeaving('')}>
      <p>Your changes to {server.name} are not saved. Save them first, or leave and lose them.</p>
    </ConfirmDialog>
  </>;
}

function ToolRow({ name, description, found, access, readOnly, onAccess }: { name: string; description: string; found: boolean; access: Access; readOnly: boolean; onAccess: (m: Access) => void }) {
  const [expanded, setExpanded] = useState(false);
  const long = description.length > DESCRIPTION_CLAMP;
  return <li className="mcp-tool">
    <div className="mcp-tool-main">
      <div className="mcp-tool-title"><code>{name}</code> <span className={`badge ${readOnly ? '' : 'warn'}`}>{readOnly ? 'Read-only' : 'Can make changes'}</span></div>
      {description ? <p className={`mcp-tool-desc${expanded ? ' open' : ''}`}>{description}</p> : <p className="field-help">{found ? 'No description.' : 'Listed, not found by Test connection.'}</p>}
      {long && <button type="button" className="small" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? 'Show less' : 'Show more'}</button>}
    </div>
    <div className="mcp-seg" role="group" aria-label={`Access for ${name}`}>
      {ACCESS.map(([m, text]) => <button key={m} type="button" aria-pressed={access === m} className={access === m ? 'selected' : ''} onClick={() => onAccess(m)}>{text}</button>)}
    </div>
  </li>;
}

