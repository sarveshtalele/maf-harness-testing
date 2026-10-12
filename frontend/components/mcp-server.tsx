'use client';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { api, desktop, label } from '@/lib/harness';
import { flashNotice, setLeaveGuard, setPopGuard, takeNotice } from '@/lib/leave-guard';
import { ConfirmDialog, DetailPage, type SectionSpec } from './page';
import {
  MAX_TOOLS_PER_STAGE, TIMEOUT_HINT, accessOf, commandLine, countedNames, isReadOnly, pick, removeServer, saveSecretValues, serverStatus, stageLoad, toolNames, useMcpData,
  type Access, type Server, type StageEntry, type Tool,
} from './mcp-servers';

type Draft = { enabled: boolean; stages: string[]; allowed_tools: string[]; approval_required_tools: string[] };
type Notice = { ok: boolean; text: string } | null;
type Leave = { proceed: () => void; cancel: () => void };
const draftOf = (s: Server): Draft => ({ enabled: s.enabled, stages: [...s.stages], allowed_tools: [...s.allowed_tools], approval_required_tools: [...s.approval_required_tools] });
const lines = (text: string) => text.split('\n').map(x => x.trim()).filter(Boolean);
const ACCESS: [Access, string][] = [['auto', 'Always allow'], ['approval', 'Ask every time'], ['hidden', 'Block']];
const DESCRIPTION_CLAMP = 100;
const plural = (n: number) => `${n} ${n === 1 ? 'tool' : 'tools'}`;

/** The comparable shape of a server's editable fields: unsaved changes are a difference in this. */
const shape = (s: Draft & { args: string[]; url: string }) => JSON.stringify({ enabled: s.enabled, stages: [...s.stages].sort(), allowed: [...s.allowed_tools].sort(), ask: [...s.approval_required_tools].sort(), args: s.args, url: s.url });

const crumbs = (serverName: string) => [{ label: 'Marketplace', href: '/marketplace/skills' }, { label: 'MCP servers', href: '/marketplace/mcp' }, { label: serverName || 'Server' }];

/** /marketplace/mcp/[name]: connection, secrets, stages and tools for one server (Claude Desktop-style settings). */
export default function McpServerPage() {
  const { name = '' } = useParams<{ name: string }>();
  const serverName = decodeURIComponent(name);
  const { data, error, load } = useMcpData();
  const server = data?.servers.find(s => s.name === serverName);
  if (error) return <DetailPage level={2} title={serverName} breadcrumbs={crumbs(serverName)}><p role="alert" className="notice bad">{error}</p></DetailPage>;
  if (!data) return <p role="status">Loading MCP server…</p>;
  if (!server) return <DetailPage level={2} title="Server not found" breadcrumbs={crumbs(serverName)}><p className="notice">No MCP server named {serverName} is configured. <Link href="/marketplace/mcp">Back to MCP servers</Link></p></DetailPage>;
  return <ServerEditor key={server.name} server={server} servers={data.servers} stages={data.stages} max={data.max_tools_per_stage ?? MAX_TOOLS_PER_STAGE} load={load} />;
}

function ServerEditor({ server, servers, stages, max, load }: { server: Server; servers: Server[]; stages: string[]; max: number; load: () => Promise<void> }) {
  const router = useRouter();
  const [draft, setDraft] = useState<Draft>(() => draftOf(server));
  const fixing = !!server.problems?.length; // stored with a problem: args or URL must be fixed before enabling
  const [argsText, setArgsText] = useState(server.args.join('\n'));
  const [urlText, setUrlText] = useState(server.url);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmReadOnly, setConfirmReadOnly] = useState(false);
  const [leaveOpen, setLeaveOpen] = useState(false);
  const leaveRef = useRef<Leave | null>(null); // the navigation waiting for the leave dialog
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState(true); // the footer's details: open on wide screens, closed on phones
  useEffect(() => { if (window.matchMedia('(max-width: 700px)').matches) setOpen(false); }, []);
  useEffect(() => {
    const flash = takeNotice();
    if (flash) setNotice({ ok: flash.ok, text: flash.text });
  }, []);

  const edited = { ...draft, args: fixing ? lines(argsText) : server.args, url: fixing ? urlText.trim() : server.url };
  const settingsDirty = shape(edited) !== shape({ ...draftOf(server), args: server.args, url: server.url });
  const secretsDirty = Object.values(secrets).some(Boolean);
  const dirty = settingsDirty || secretsDirty;
  const names = toolNames(server.discovered_tools, draft);
  const toolOf = (n: string): Tool => server.discovered_tools.find(t => t.name === n) ?? { name: n, description: '' };
  const t = (() => {
    const a = names.filter(n => accessOf(draft, n) === 'auto').length; const h = names.filter(n => accessOf(draft, n) === 'approval').length;
    return { a, h, b: names.length - a - h };
  })();
  const [status, tone] = serverStatus(server);
  const enableBlock = server.problems?.length ? 'Fix the problems below first.' : !server.ready ? 'Set the secrets first.' : '';

  // Stage budget, live: every enabled server in the file order, this server with its draft and the others as saved.
  const entries: StageEntry[] = servers.map(s => s.name === server.name
    ? { name: s.name, enabled: draft.enabled, stages: draft.stages, tools: countedNames(draft.allowed_tools, draft.approval_required_tools, server.discovered_tools) }
    : { name: s.name, enabled: s.enabled, stages: s.stages, tools: countedNames(s.allowed_tools, s.approval_required_tools, s.discovered_tools) });
  const budget = stageLoad(entries, stages, max);
  const budgetNotes = draft.enabled ? Object.entries(budget).filter(([st]) => draft.stages.includes(st)) : [];

  // Unsaved changes. beforeunload is cancelled; in-app links, the arrow keys on the marketplace tabs (through
  // guardedNavigate) and browser back/forward ask first. Back/forward is intercepted in the capture phase so the
  // app router does not move until the user decides.
  const openLeave = (step: Leave) => { leaveRef.current = step; setLeaveOpen(true); };
  useEffect(() => {
    if (!dirty) return;
    const here = location.pathname + location.search + location.hash;
    const hereState = history.state;
    const onBeforeUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const link = (e.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!link || link.target || link.hasAttribute('download')) return;
      const url = new URL(link.href);
      if (url.origin !== location.origin || url.pathname + url.search === location.pathname + location.search) return;
      e.preventDefault(); e.stopPropagation();
      const href = url.pathname + url.search + url.hash;
      openLeave({ proceed: () => router.push(href), cancel: () => {} });
    };
    // Back/forward: the shell's capture listener stops the app router; the dialog then decides. Stay re-creates this
    // entry (no stale history entry is left behind); Leave replaces it with the target.
    setPopGuard(target => openLeave({ proceed: () => router.replace(target), cancel: () => history.pushState(hereState, '', here) }));
    setLeaveGuard((proceed, cancel) => openLeave({ proceed, cancel }));
    window.addEventListener('beforeunload', onBeforeUnload);
    document.addEventListener('click', onClick, true);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      document.removeEventListener('click', onClick, true);
      setPopGuard(null);
      setLeaveGuard(null);
    };
  }, [dirty, router]);

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
  const discard = () => { setDraft(draftOf(server)); setArgsText(server.args.join('\n')); setUrlText(server.url); setSecrets({}); setNotice(null); };
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
    try {
      await removeServer(server.name);
      flashNotice({ ok: true, text: `${server.name} deleted.`, focusAdd: true });
      router.push('/marketplace/mcp');
    } catch (e) { setNotice({ ok: false, text: (e as Error).message }); }
  };

  // Tools: one access state per tool; the bulk actions only touch the draft until Save.
  const setAccess = (tool: string, m: Access) => setDraft(d => ({
    ...d,
    allowed_tools: [...d.allowed_tools.filter(x => x !== tool), ...(m === 'auto' ? [tool] : [])],
    approval_required_tools: [...d.approval_required_tools.filter(x => x !== tool), ...(m === 'approval' ? [tool] : [])],
  }));
  const readOnly = names.filter(n => isReadOnly(toolOf(n)));
  const allowReadOnly = () => setDraft(d => ({ ...d, allowed_tools: [...new Set([...d.allowed_tools, ...readOnly])], approval_required_tools: d.approval_required_tools.filter(x => !readOnly.includes(x)) }));
  const needle = search.trim().toLowerCase();
  const matches = (n: string) => !needle || n.toLowerCase().includes(needle) || toolOf(n).description.toLowerCase().includes(needle);
  const groups: [string, string[]][] = [['Read-only', names.filter(n => isReadOnly(toolOf(n)) && matches(n))], ['Can make changes', names.filter(n => !isReadOnly(toolOf(n)) && matches(n))]];

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
        <div className="table-tools"><input type="search" aria-label="Search tools" placeholder="Search tools…" value={search} onChange={e => setSearch(e.target.value)} /><small>{plural(names.length)}</small></div>
        <div className="header-actions mcp-bulk" role="group" aria-label="Bulk tool actions">
          <button type="button" disabled={busy || !readOnly.length} onClick={() => setConfirmReadOnly(true)}>Allow all read-only</button>
          <button type="button" disabled={busy} onClick={() => setDraft(d => ({ ...d, allowed_tools: names, approval_required_tools: [] }))}>Approve all</button>
          <button type="button" disabled={busy} onClick={() => setDraft(d => ({ ...d, allowed_tools: [], approval_required_tools: [] }))}>Block all</button>
          <button type="button" disabled={busy} onClick={() => setDraft(draftOf(server))}>Reset to saved</button>
        </div>
        {t.a > 0 && <p className="notice bad" role="note">Approved tools run without asking, including any that change data (create, edit, navigate). Approve only tools you trust.</p>}
        {groups.map(([title, list]) => list.length ? <div key={title} className="mcp-group"><h4>{title} <span className="section-count">{list.length}</span></h4><ul className="mcp-tool-list">
          {list.map(n => <ToolRow key={n} name={n} description={toolOf(n).description} found={server.discovered_tools.some(x => x.name === n)} access={accessOf(draft, n)} readOnly={isReadOnly(toolOf(n))} onAccess={m => setAccess(n, m)} />)}
        </ul></div> : null)}
        {!groups.some(([, l]) => l.length) && <p className="empty-inline">No tools match your search.</p>}
      </>}
    </> },
  ];

  const footer = <footer className="mcp-footer" aria-label="Tool summary and saving">
    <div className="mcp-footer-main">
      <strong className="mcp-summary" aria-live="polite">{names.length} {names.length === 1 ? 'tool' : 'tools'} · {t.a} always allow · {t.h} ask · {t.b} blocked</strong>
      <div className="header-actions"><button type="button" disabled={!dirty || busy} onClick={discard}>Discard</button><button type="button" className="primary" disabled={!settingsDirty || busy} onClick={save}>Save</button></div>
    </div>
    <details className="mcp-footer-details" open={open} onToggle={e => setOpen(e.currentTarget.open)}>
      <summary>Details</summary>
      <div className="mcp-footer-notes">
        <span className={dirty ? 'bad' : ''}>{dirty ? 'Unsaved changes' : 'All changes saved'}</span>
        {secretsDirty && !settingsDirty && <span>Secrets typed: use Save secrets to store them.</span>}
        {budgetNotes.map(([st, b]) => <span key={st} className="mp-warn"><span className="badge warn">Over budget</span> Stage {label(st)}: {b.demand} MCP tools, max {max}; {b.skipped.includes(server.name) ? 'this server is skipped in this stage.' : `skipped: ${b.skipped.join(', ')}.`}</span>)}
      </div>
    </details>
  </footer>;

  return <>
    <DetailPage level={2} eyebrow="MCP server" title={server.name} breadcrumbs={crumbs(server.name)} footer={footer}
      badges={<>
        <span className="badge">{server.transport}</span>
        <span className={`badge ${tone}`}>{status}</span>
        <label className="mcp-switch"><input type="checkbox" role="switch" checked={draft.enabled} disabled={busy || (!!enableBlock && !draft.enabled)} onChange={e => setDraft(d => ({ ...d, enabled: e.target.checked }))} /><span>Enable server</span></label>
        {enableBlock && <span className="field-help mcp-enable-reason">{enableBlock}</span>}
      </>}
      actions={<>
        <button type="button" disabled={busy} onClick={test}>Test connection</button>
        <button type="button" className="mcp-delete" disabled={busy} onClick={() => setConfirmDelete(true)}>Delete server</button>
      </>}
      notices={notice && <p role="status" className={notice.ok ? '' : 'bad'}>{notice.text}</p>}
      sections={sections}
    />
    <ConfirmDialog open={confirmDelete} title={`Delete ${server.name}?`} confirmLabel="Delete server" tone="danger" busy={busy} onConfirm={() => void deleteNow()} onClose={() => setConfirmDelete(false)}>
      <p>Its stored secrets are removed too. Runs no longer see its tools. This cannot be undone.</p>
    </ConfirmDialog>
    <ConfirmDialog open={confirmReadOnly} title={`Allow ${readOnly.length} read-only ${readOnly.length === 1 ? 'tool' : 'tools'}?`} confirmLabel="Allow these tools" onConfirm={() => { setConfirmReadOnly(false); allowReadOnly(); }} onClose={() => setConfirmReadOnly(false)}>
      <p>These tools will run without asking. Tools that can make changes keep their current access.</p>
      <ul className="mcp-confirm-list">{readOnly.map(n => <li key={n}><code>{n}</code></li>)}</ul>
    </ConfirmDialog>
    <ConfirmDialog open={leaveOpen} title="Leave with unsaved changes?" confirmLabel="Leave without saving" cancelLabel="Stay" tone="danger"
      onConfirm={() => { const step = leaveRef.current; leaveRef.current = null; setLeaveOpen(false); step?.proceed(); }}
      onClose={() => { const step = leaveRef.current; leaveRef.current = null; setLeaveOpen(false); step?.cancel(); }}>
      <p>Your changes to {server.name} are not saved. Save them first, or leave and lose them.</p>
    </ConfirmDialog>
  </>;
}

/** Three access states as a radio group: one tab stop, arrow keys move and choose. */
function AccessGroup({ name, access, onAccess }: { name: string; access: Access; onAccess: (m: Access) => void }) {
  const id = useId();
  const order = ACCESS.map(([m]) => m);
  const onKey = (e: KeyboardEvent) => {
    const i = order.indexOf(access);
    const next = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? order[(i + 1) % order.length]
      : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? order[(i + order.length - 1) % order.length]
      : e.key === 'Home' ? order[0] : e.key === 'End' ? order[order.length - 1] : null;
    if (!next) return;
    e.preventDefault();
    onAccess(next);
    document.getElementById(`${id}-${next}`)?.focus();
  };
  return <div role="radiogroup" aria-label={`Access for ${name}`} className="mcp-seg" onKeyDown={onKey}>
    {ACCESS.map(([m, text]) => <button key={m} type="button" role="radio" id={`${id}-${m}`} aria-checked={access === m} tabIndex={access === m ? 0 : -1} onClick={() => onAccess(m)}>{text}</button>)}
  </div>;
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
    <AccessGroup name={name} access={access} onAccess={onAccess} />
  </li>;
}
