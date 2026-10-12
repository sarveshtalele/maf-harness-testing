'use client';
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react';
import { api, apiDelete, desktop, label, record } from '@/lib/harness';
import { useReturnFocus } from './data';

type Tool = { name: string; description: string };
type Server = {
  name: string; transport: 'stdio' | 'http'; command: string; args: string[]; env_keys: string[]; url: string; enabled: boolean;
  stages: string[]; allowed_tools: string[]; approval_required_tools: string[]; preset: string; discovered_tools: Tool[]; secrets: Record<string, boolean>; ready: boolean;
};
type Budget = Record<string, { tools: number; max: number; skipped: string[] }>;
type Preset = { label: string; note: string; server: Omit<Server, 'secrets' | 'ready' | 'discovered_tools'> };
type Data = { servers: Server[]; presets: Record<string, Preset>; stages: string[]; max_tools_per_stage?: number; stage_budget?: Budget };
type Imported = { imported: { source_key: string; name: string; secret_keys: string[] }[]; servers: Server[] };
type Desktop = { saveMcpSecrets(server: string, values: Record<string, string>): Promise<unknown>; deleteMcpServer(server: string): Promise<unknown> };
type Notice = { ok: boolean; text: string } | null;
type Access = 'auto' | 'approval' | 'hidden';
type Pick3 = Pick<Server, 'allowed_tools' | 'approval_required_tools'>;

const pick = (s: Server) => ({ name: s.name, transport: s.transport, command: s.command, args: s.args, env_keys: s.env_keys, url: s.url, enabled: s.enabled, stages: s.stages, allowed_tools: s.allowed_tools, approval_required_tools: s.approval_required_tools, preset: s.preset });
const access = (s: Pick3, t: string): Access => (s.allowed_tools.includes(t) ? 'auto' : s.approval_required_tools.includes(t) ? 'approval' : 'hidden');
const toolNames = (discovered: Tool[], s: Pick3) => [...new Set([...discovered.map(t => t.name), ...s.allowed_tools, ...s.approval_required_tools])].sort();
const tally = (names: string[], s: Pick3) => ({ n: names.length, a: s.allowed_tools.length, h: s.approval_required_tools.length, b: names.length - s.allowed_tools.length - s.approval_required_tools.length });
const summary = (t: ReturnType<typeof tally>) => `${t.n} tools · ${t.a} approved · ${t.h} need approval · ${t.b} blocked`;
const STATE: Record<Access, [string, string]> = { auto: ['Approved', 'good'], approval: ['Needs approval', 'warn'], hidden: ['Blocked', ''] };
const TIMEOUT_HINT = 'The first Test of an npx server can time out while npx downloads the package; run it again.';
const PLACEHOLDER = /^\$\{(input|env):[^}]*\}$/; // VS Code variables are not secret values (mirrors the backend)
const isSecretValue = (v: string | undefined) => !!v && !PLACEHOLDER.test(v.trim());
const DOCKER = /(?:^|[\\/])(docker|podman)(?:\.exe)?$/i;

/** docker/podman `-e KEY=value`, `--env KEY=value`, `--env=KEY=value`: the value is a secret KEY (mirrors _docker_env). */
function dockerEnv(tokens: string[], env: Record<string, string>): string[] {
  const out: string[] = [];
  for (let i = 0; i < tokens.length; i++) {
    const a = tokens[i];
    if ((a === '-e' || a === '--env') && i + 1 < tokens.length && tokens[i + 1].includes('=')) { const [k, ...v] = tokens[++i].split('='); env[k] = v.join('='); out.push('-e', k); continue; }
    if (a.startsWith('--env=') && a.slice(6).includes('=')) { const [k, ...v] = a.slice(6).split('='); env[k] = v.join('='); out.push('-e', k); continue; }
    out.push(a);
  }
  return out;
}

/** Secret values in pasted text, keyed like the import (''= a command or a single object). Only used to store them. */
function pastedSecrets(text: string): Record<string, Record<string, string>> {
  const body = text.trim();
  if (body[0] !== '{' && body[0] !== '[') {
    const env: Record<string, string> = {};
    let rest = body; let m: RegExpExecArray | null;
    while ((m = /^\s*([A-Za-z_][A-Za-z0-9_]*)=("([^"]*)"|'([^']*)'|(\S*))/.exec(rest))) { env[m[1]] = m[3] ?? m[4] ?? m[5] ?? ''; rest = rest.slice(m[0].length); }
    const words = [...rest.replace(/(?:\\|\^|`)[ \t]*\r?\n/g, ' ').matchAll(/"([^"]*)"|'([^']*)'|(\S+)/g)].map(x => x[1] ?? x[2] ?? x[3]);
    if (DOCKER.test(words[0] ?? '')) dockerEnv(words.slice(1), env);
    return { '': env };
  }
  let data: unknown;
  try { data = JSON.parse(body); } catch { return {}; }
  const root = record(data);
  const isServer = (v: unknown) => ['command', 'url', 'type'].some(k => k in record(v));
  const block = root.mcpServers ? record(root.mcpServers) : root.servers && !isServer(root.servers) ? record(root.servers) : isServer(root) ? { '': root } : root;
  return Object.fromEntries(Object.entries(block).map(([key, value]) => {
    const entry = record(value);
    const env: Record<string, string> = Object.fromEntries(Object.entries(record(entry.env)).map(([k, v]) => [k, String(v)]));
    if (typeof entry.command === 'string' && DOCKER.test(entry.command) && Array.isArray(entry.args)) dockerEnv(entry.args.map(String), env);
    const auth = Object.entries(record(entry.headers)).find(([h]) => h.toLowerCase() === 'authorization');
    return [key, { ...env, ...(auth ? { BEARER_TOKEN: String(auth[1]) } : {}) }];
  }));
}

const saveSecretValues = (name: string, values: Record<string, string>) => {
  const d = desktop<Desktop>();
  return d ? d.saveMcpSecrets(name, values) : api(`mcp-servers/${name}/secrets`, values);
};

/** Left/Right (wrapping) move between tabs and focus the new tab (roving tabindex). */
const arrowTo = <T extends string>(e: KeyboardEvent, keys: readonly T[], current: T, go: (next: T) => void) => {
  if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
  e.preventDefault();
  const next = keys[(keys.indexOf(current) + (e.key === 'ArrowRight' ? 1 : keys.length - 1)) % keys.length];
  go(next);
  document.getElementById(`${(e.currentTarget as HTMLElement).dataset.prefix}-${next}`)?.focus();
};

/** Native dialog: opened and closed by `open`; Escape or close() reports through onClose. */
function Modal({ open, onClose, labelId, children }: { open: boolean; onClose: () => void; labelId: string; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const d = ref.current; if (open && d && !d.open) d.showModal(); else if (!open && d?.open) d.close(); }, [open]);
  return <dialog ref={ref} aria-labelledby={labelId} className="detail-dialog" onClose={onClose}>{open && children}</dialog>;
}

function ServerCard({ server: s, budget, onManage }: { server: Server; budget: Budget; onManage: (e: MouseEvent<HTMLButtonElement>) => void }) {
  const names = toolNames(s.discovered_tools, s); const t = tally(names, s);
  const [status, tone] = !s.enabled ? ['Disabled', ''] : s.ready ? ['Enabled', 'good'] : ['Needs secrets', 'warn'];
  const over = s.enabled ? s.stages.filter(st => budget[st]) : [];
  return <section className="skill-card" aria-label={`MCP server ${s.name}`}>
    <div className="skill-card-heading"><h2>{s.name}</h2><span className="skill-tags"><span className="badge">{s.transport}</span><span className={`badge ${tone}`}>{status}</span></span></div>
    <code className="mp-command">{s.transport === 'stdio' ? [s.command, ...s.args].join(' ') : s.url}</code>
    <p className="field-help">Stages: {s.stages.length ? s.stages.map(st => label(st)).join(', ') : 'none'}</p>
    <p className="field-help">{summary(t)}</p>
    {!s.discovered_tools.length && <p className="field-help">Run Test to discover tools</p>}
    {!!names.length && <ul className="mp-tools">{names.slice(0, 12).map(n => { const [text, cls] = STATE[access(s, n)]; return <li key={n}><code>{n}</code> <span className={`badge ${cls}`}>{text}</span></li>; })}{names.length > 12 && <li className="field-help">+{names.length - 12} more</li>}</ul>}
    {over.map(st => <p key={st} className="mp-warn"><span className="badge warn">Over budget</span> Stage {label(st)}: {budget[st].tools} MCP tools, max {budget[st].max}; servers beyond the budget are skipped.</p>)}
    <button className="small" onClick={onManage}>Manage tools</button>
  </section>;
}

function ManageServer({ server, stages, onChange, onDeleted, onClose }: { server: Server; stages: string[]; onChange: () => void; onDeleted: () => void; onClose: () => void }) {
  const [s, setS] = useState(server);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState(false);
  const rememberDelete = useReturnFocus(confirm);
  const names = toolNames(server.discovered_tools, s); const t = tally(names, s);
  const run = async (fn: () => Promise<string | void>) => {
    setBusy(true); setNotice(null);
    try { const text = await fn(); if (text) setNotice({ ok: true, text }); } catch (e) { setNotice({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  };
  const setMode = (tool: string, m: Access) => setS(v => ({
    ...v,
    allowed_tools: [...v.allowed_tools.filter(x => x !== tool), ...(m === 'auto' ? [tool] : [])],
    approval_required_tools: [...v.approval_required_tools.filter(x => x !== tool), ...(m === 'approval' ? [tool] : [])],
  }));
  const setAll = (m: 'auto' | 'hidden') => setS(v => ({ ...v, allowed_tools: m === 'auto' ? names : [], approval_required_tools: [] }));
  const save = () => run(async () => { const next = await api<Server>('mcp-servers', pick(s)); setS(next); onChange(); return 'Saved.'; });
  const test = () => run(async () => {
    const r = await api<{ ok: boolean; message?: string; tools?: string[]; count?: number }>(`mcp-servers/${s.name}/test`, {});
    onChange();
    if (!r.ok) throw new Error(r.message || 'Connection failed.');
    return `Connected: ${r.count} tool${r.count === 1 ? '' : 's'} available.`;
  });
  const saveSecretsNow = () => run(async () => {
    const values = Object.fromEntries(Object.entries(secrets).filter(([, v]) => v));
    if (!Object.keys(values).length) throw new Error('Enter a value first.');
    await saveSecretValues(s.name, values);
    setSecrets({}); onChange();
    return 'Secrets stored (write-only).';
  });
  const remove = () => run(async () => { const d = desktop<Desktop>(); if (d) await d.deleteMcpServer(s.name); else await apiDelete(`mcp-servers/${s.name}`); onDeleted(); });

  return <>
    <header className="panel-heading"><h2 id="mp-manage-title">{s.name} · tools and settings</h2></header>
    <div className="panel-body">
      <label className="check"><input type="checkbox" checked={s.enabled} onChange={e => setS({ ...s, enabled: e.target.checked })} />Enabled</label>
      <fieldset><legend>Stages that may use this server</legend>
        {stages.map(st => <label key={st} className="check"><input type="checkbox" checked={s.stages.includes(st)} onChange={e => setS({ ...s, stages: e.target.checked ? [...s.stages, st] : s.stages.filter(x => x !== st) })} />{label(st)}</label>)}
      </fieldset>
      {Object.keys(server.secrets).length > 0 && <fieldset><legend>Secrets (write-only, kept in memory{desktop<Desktop>() ? ' and your OS keychain' : ''})</legend>
        {Object.entries(server.secrets).map(([k, set]) => <label key={k}>{k === 'BEARER_TOKEN' ? 'Token (Bearer, or "Basic …")' : k}<input type="password" autoComplete="new-password" spellCheck={false} value={secrets[k] || ''} onChange={e => setSecrets({ ...secrets, [k]: e.target.value })} placeholder={set ? 'Set — leave empty to keep' : 'Not set'} /></label>)}
        <button type="button" disabled={busy} onClick={saveSecretsNow}>Save secrets</button></fieldset>}
      <div className="header-actions"><button disabled={busy} onClick={test}>Test</button><span className="field-help">{TIMEOUT_HINT}</span></div>
      <h3>Tools</h3>
      {names.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label={`Tools of ${s.name}`}><table className="data-table"><thead><tr><th scope="col">Tool</th><th scope="col">Description</th><th scope="col">Access</th></tr></thead><tbody>{names.map(n => <tr key={n}>
        <td><code>{n}</code></td>
        <td className="cell-text">{server.discovered_tools.find(x => x.name === n)?.description || (server.discovered_tools.some(x => x.name === n) ? '' : 'Listed, not found by Test')}</td>
        <td><select aria-label={`Access for ${n}`} value={access(s, n)} onChange={e => setMode(n, e.target.value as Access)}><option value="auto">Approved (auto-run)</option><option value="approval">Needs human approval</option><option value="hidden">Blocked (hidden)</option></select></td>
      </tr>)}</tbody></table></div> : <p className="field-help">No tools yet. Run Test to discover them.</p>}
      <div className="header-actions"><button type="button" disabled={busy || !names.length} onClick={() => setAll('auto')}>Approve all</button><button type="button" disabled={busy || !names.length} onClick={() => setAll('hidden')}>Deselect all</button></div>
      {t.a > 0 && <p className="notice bad" role="note">Approved tools run without asking, including any that change data (create, edit, navigate). Approve only tools you trust.</p>}
      <p aria-live="polite">{summary(t)}</p>
      <div className="header-actions"><button className="primary" disabled={busy} onClick={save}>Save</button><button className="danger-button" disabled={busy} onClick={e => { rememberDelete(e.currentTarget); setConfirm(true); }}>Delete</button><button type="button" onClick={onClose}>Close</button></div>
      {notice && <p role="status" className={notice.ok ? '' : 'bad'}>{notice.text}</p>}
    </div>
    <Modal open={confirm} onClose={() => setConfirm(false)} labelId="mp-delete-title">
      <header className="panel-heading"><h2 id="mp-delete-title">Delete {s.name}?</h2></header>
      <div className="panel-body"><p>Its stored secrets are removed too. This cannot be undone.</p><div className="header-actions"><button onClick={() => setConfirm(false)}>Cancel</button><button className="danger-button" disabled={busy} onClick={remove}>Delete server</button></div></div>
    </Modal>
  </>;
}

const MODES = ['paste', 'preset', 'manual'] as const;
type Mode = typeof MODES[number];

function AddServer({ open, onClose, presets, done }: { open: boolean; onClose: () => void; presets: Record<string, Preset>; done: (text: string) => void }) {
  const [mode, setMode] = useState<Mode>('paste');
  const [pasteText, setPasteText] = useState('');
  const [preset, setPreset] = useState('');
  const [custom, setCustom] = useState({ name: '', transport: 'stdio', command: '', args: '', env_keys: '', url: '' });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const p = presets[preset];
  useEffect(() => { if (!open) { setPasteText(''); setNotice(null); } }, [open]); // closing (Escape, Cancel, success) drops the pasted text
  const addBody = async (body: Record<string, unknown>) => {
    setBusy(true); setNotice(null);
    try { const saved = await api<Server>('mcp-servers', body); setPreset(''); setCustom({ name: '', transport: 'stdio', command: '', args: '', env_keys: '', url: '' }); done(`${saved.name} added. It is disabled until you enable it under Manage tools.`); onClose(); }
    catch (e) { setNotice({ ok: false, text: (e as Error).message }); }
    finally { setBusy(false); }
  };
  const addPaste = async () => {
    const text = pasteText; setBusy(true); setNotice(null);
    setPasteText(''); // the text may hold secrets: never keep it in the field
    let r: Imported;
    try { r = await api<Imported>('mcp-servers/import', { text }); }
    catch (e) { setNotice({ ok: false, text: (e as Error).message }); setBusy(false); return; }
    const values = pastedSecrets(text);
    const missed: string[] = [];
    for (const item of r.imported) {
      const v = values[item.source_key] ?? {};
      const picked = Object.fromEntries(item.secret_keys.filter(k => isSecretValue(v[k])).map(k => [k, v[k]]));
      if (!Object.keys(picked).length) continue;
      try { await saveSecretValues(item.name, picked); } catch { missed.push(`${item.name}: ${Object.keys(picked).join(', ')}`); }
    }
    const names = r.imported.map(i => i.name).join(', ');
    done(`Added ${names}. Servers start disabled.`); // reload the list in every case
    if (missed.length) setNotice({ ok: false, text: `Added ${names}, but these secrets were NOT stored: ${missed.join('; ')}. Set them under Manage tools for each server (re-importing would create copies).` });
    else onClose();
    setBusy(false);
  };
  return <Modal open={open} onClose={onClose} labelId="mp-add-title">
    <header className="panel-heading"><h2 id="mp-add-title">Add MCP server</h2></header>
    <div className="panel-body">
      <div className="tabs" role="tablist" aria-label="How to add" data-prefix="mp-add" onKeyDown={e => arrowTo(e, MODES, mode, m => { setMode(m); setNotice(null); })}>{MODES.map(m => <button key={m} role="tab" id={`mp-add-${m}`} aria-controls="mp-add-panel" tabIndex={mode === m ? 0 : -1} aria-selected={mode === m} className={mode === m ? 'selected' : ''} onClick={() => { setMode(m); setNotice(null); }}>{label(m)}</button>)}</div>
      <div role="tabpanel" id="mp-add-panel" aria-labelledby={`mp-add-${mode}`}>
        {mode === 'paste' && <form onSubmit={e => { e.preventDefault(); void addPaste(); }}>
          <label>Paste an npx/uvx/docker command or an MCP JSON config (mcpServers)<textarea rows={6} spellCheck={false} autoComplete="off" value={pasteText} onChange={e => setPasteText(e.target.value)} /></label>
          <p className="field-help">Secret values (env values, docker -e values, Authorization headers) are stored write-only and never shown again.</p>
          <div className="header-actions"><button className="primary" type="submit" disabled={busy || !pasteText.trim()}>Add from paste</button><button type="button" onClick={onClose}>Cancel</button></div>
        </form>}
        {mode === 'preset' && <>
          <label>Preset<select value={preset} onChange={e => setPreset(e.target.value)}><option value="">Choose a preset…</option>{Object.entries(presets).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select></label>
          {p && <><p className="field-help">{p.note}</p><div className="header-actions"><button type="button" className="primary" disabled={busy} onClick={() => void addBody(p.server)}>Add preset</button><button type="button" onClick={onClose}>Cancel</button></div></>}
        </>}
        {mode === 'manual' && <form onSubmit={e => { e.preventDefault(); void addBody({ name: custom.name.trim(), transport: custom.transport, command: custom.command.trim(), args: custom.args.split('\n').map(x => x.trim()).filter(Boolean), env_keys: custom.env_keys.split(',').map(x => x.trim()).filter(Boolean), url: custom.url.trim(), enabled: false, stages: [] }); }}>
          <label>Name (slug)<input value={custom.name} onChange={e => setCustom({ ...custom, name: e.target.value })} maxLength={32} /></label>
          <label>Transport<select value={custom.transport} onChange={e => setCustom({ ...custom, transport: e.target.value })}><option value="stdio">stdio (local command)</option><option value="http">HTTP (https URL)</option></select></label>
          {custom.transport === 'stdio'
            ? <><label>Command<input value={custom.command} onChange={e => setCustom({ ...custom, command: e.target.value })} placeholder="npx" /></label>
              <label>Arguments (one per line)<textarea value={custom.args} onChange={e => setCustom({ ...custom, args: e.target.value })} /></label>
              <label>Secret environment variable names (comma separated)<input value={custom.env_keys} onChange={e => setCustom({ ...custom, env_keys: e.target.value })} placeholder="API_TOKEN" /></label></>
            : <label>URL<input value={custom.url} onChange={e => setCustom({ ...custom, url: e.target.value })} placeholder="https://…" inputMode="url" /></label>}
          <div className="header-actions"><button className="primary" type="submit" disabled={busy}>Add custom server</button><button type="button" onClick={onClose}>Cancel</button></div>
        </form>}
      </div>
      {notice && <p role="status" className={notice.ok ? '' : 'bad'}>{notice.text}</p>}
    </div>
  </Modal>;
}

export default function McpServers() {
  const [data, setData] = useState<Data | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [adding, setAdding] = useState(false);
  const [managing, setManaging] = useState('');
  const [focusAdd, setFocusAdd] = useState(false);
  const addRef = useRef<HTMLButtonElement>(null);
  const rememberAdd = useReturnFocus(adding);
  const rememberManage = useReturnFocus(!!managing);
  const load = useCallback(() => api<Data>('mcp-servers').then(setData).catch(e => setNotice({ ok: false, text: (e as Error).message })), []);
  useEffect(() => { void load(); }, [load]);
  // after a delete the card is gone: focus goes to Add MCP server once the manage dialog has closed
  useEffect(() => { if (focusAdd && !managing) { addRef.current?.focus(); setFocusAdd(false); } }, [focusAdd, managing]);
  const live = data?.servers.find(s => s.name === managing);
  const budget: Budget = data?.stage_budget ?? {};
  return <>
    <div className="page-header"><div><span className="eyebrow">Marketplace</span><h1>MCP servers</h1><p>Give the agents extra tools from MCP servers (Jira, Playwright, others). Servers start disabled. Approved tools run automatically, tools that need approval wait for a person, and blocked tools are hidden. Output from a server is untrusted evidence.</p></div><button ref={addRef} className="primary" onClick={e => { rememberAdd(e.currentTarget); setAdding(true); }}>Add MCP server</button></div>
    {Object.entries(budget).map(([st, b]) => <p key={st} className="mp-warn" role="status"><span className="badge warn">Over budget</span> Stage {label(st)}: {b.tools} MCP tools, max {b.max}; servers beyond the budget are skipped. Skipped: {b.skipped.join(', ')}.</p>)}
    {notice && <p role="status" className={notice.ok ? '' : 'bad'}>{notice.text}</p>}
    {data && !data.servers.length && <p className="field-help">No MCP servers configured yet.</p>}
    <div className="skills-grid">{data?.servers.map(s => <ServerCard key={s.name} server={s} budget={budget} onManage={e => { rememberManage(e.currentTarget); setManaging(s.name); }} />)}</div>
    <AddServer open={adding} onClose={() => setAdding(false)} presets={data?.presets ?? {}} done={text => { setNotice({ ok: true, text }); void load(); }} />
    <Modal open={!!live} onClose={() => setManaging('')} labelId="mp-manage-title">
      {live && <ManageServer key={live.name} server={live} stages={data?.stages ?? []} onChange={() => void load()} onClose={() => setManaging('')} onDeleted={() => { rememberManage(null); setManaging(''); setFocusAdd(true); setNotice({ ok: true, text: `${live.name} deleted.` }); void load(); }} />}
    </Modal>
  </>;
}
