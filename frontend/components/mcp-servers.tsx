'use client';
import { useCallback, useEffect, useState } from 'react';
import { api, apiDelete, label } from '@/lib/harness';
import { Panel } from './data';

type Server = {
  name: string; transport: 'stdio' | 'http'; command: string; args: string[]; env_keys: string[]; url: string; enabled: boolean;
  stages: string[]; allowed_tools: string[]; approval_required_tools: string[]; preset: string; secrets: Record<string, boolean>; ready: boolean;
};
type Preset = { label: string; note: string; server: Omit<Server, 'secrets' | 'ready'> };
type Data = { servers: Server[]; presets: Record<string, Preset>; stages: string[]; max_tools_per_server: number };
type Desktop = { saveMcpSecrets(server: string, values: Record<string, string>): Promise<unknown>; deleteMcpServer(server: string): Promise<unknown> };
const desktop = (): Desktop | undefined => (typeof window === 'undefined' ? undefined : (window as unknown as { desktop?: Desktop }).desktop);
type Notice = { ok: boolean; text: string } | null;
const pick = (s: Server) => ({ name: s.name, transport: s.transport, command: s.command, args: s.args, env_keys: s.env_keys, url: s.url, enabled: s.enabled, stages: s.stages, allowed_tools: s.allowed_tools, approval_required_tools: s.approval_required_tools, preset: s.preset });

function ServerCard({ server, stages, onChange, onDeleted }: { server: Server; stages: string[]; onChange: () => void; onDeleted: () => void }) {
  const [s, setS] = useState(server);
  const [tested, setTested] = useState<string[]>([]);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const run = async (fn: () => Promise<string | void>) => {
    setBusy(true); setNotice(null);
    try { const text = await fn(); if (text) setNotice({ ok: true, text }); } catch (e) { setNotice({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  };
  const mode = (t: string) => (s.allowed_tools.includes(t) ? 'auto' : s.approval_required_tools.includes(t) ? 'approval' : 'hidden');
  const setMode = (t: string, m: string) => setS(v => ({
    ...v,
    allowed_tools: [...v.allowed_tools.filter(x => x !== t), ...(m === 'auto' ? [t] : [])],
    approval_required_tools: [...v.approval_required_tools.filter(x => x !== t), ...(m === 'approval' ? [t] : [])],
  }));
  const names = [...new Set([...s.allowed_tools, ...s.approval_required_tools, ...tested])].sort();
  const save = () => run(async () => { const next = await api<Server>('mcp-servers', pick(s)); setS(next); onChange(); return 'Saved.'; });
  const test = () => run(async () => {
    const r = await api<{ ok: boolean; message?: string; tools?: string[]; count?: number }>(`mcp-servers/${s.name}/test`, {});
    if (!r.ok) throw new Error(r.message || 'Connection failed.');
    setTested(r.tools || []); return `Connected: ${r.count} tool${r.count === 1 ? '' : 's'} available.`;
  });
  const saveSecrets = () => run(async () => {
    const values = Object.fromEntries(Object.entries(secrets).filter(([, v]) => v));
    if (!Object.keys(values).length) throw new Error('Enter a value first.');
    const d = desktop();
    if (d) await d.saveMcpSecrets(s.name, values); else await api(`mcp-servers/${s.name}/secrets`, values);
    setSecrets({}); const fresh = await api<Data>('mcp-servers'); const me = fresh.servers.find(x => x.name === s.name); if (me) setS(v => ({ ...v, secrets: me.secrets, ready: me.ready })); onChange();
    return 'Secrets stored (write-only).';
  });
  const remove = () => run(async () => { const d = desktop(); if (d) await d.deleteMcpServer(s.name); else await apiDelete(`mcp-servers/${s.name}`); onDeleted(); });

  return <section aria-label={`MCP server ${s.name}`} className="notice" style={{ borderLeftColor: s.enabled ? 'var(--good)' : undefined }}>
    <h2>{s.name} <span className="badge">{s.transport}</span> {s.enabled && <span className={`badge ${s.ready ? 'good' : 'warn'}`}>{s.ready ? 'enabled' : 'needs secrets'}</span>}</h2>
    <p className="field-help">{s.transport === 'stdio' ? <code>{[s.command, ...s.args].join(' ')}</code> : <code>{s.url}</code>}</p>
    <label className="check"><input type="checkbox" checked={s.enabled} onChange={e => setS({ ...s, enabled: e.target.checked })} />Enabled</label>
    <fieldset><legend>Stages that may use this server</legend>
      {stages.map(st => <label key={st} className="check"><input type="checkbox" checked={s.stages.includes(st)} onChange={e => setS({ ...s, stages: e.target.checked ? [...s.stages, st] : s.stages.filter(x => x !== st) })} />{label(st)}</label>)}
    </fieldset>
    <p className="field-help">Tools: only tools set to <em>Auto-approve</em> (read-only) or <em>Needs approval</em> are visible to the agent. Run Test to list the server&apos;s tools.</p>
    {names.length > 0 && <table><thead><tr><th>Tool</th><th>Access</th></tr></thead><tbody>{names.map(t => <tr key={t}><td><code>{t}</code></td><td><select aria-label={`Access for ${t}`} value={mode(t)} onChange={e => setMode(t, e.target.value)}><option value="hidden">Hidden</option><option value="auto">Auto-approve (read-only)</option><option value="approval">Needs human approval</option></select></td></tr>)}</tbody></table>}
    {Object.keys(s.secrets).length > 0 && <fieldset><legend>Secrets (write-only, kept in memory{desktop() ? ' and your OS keychain' : ''})</legend>
      {Object.entries(s.secrets).map(([k, set]) => <label key={k}>{k === 'BEARER_TOKEN' ? 'Token (Bearer, or "Basic …")' : k}<input type="password" autoComplete="new-password" spellCheck={false} value={secrets[k] || ''} onChange={e => setSecrets({ ...secrets, [k]: e.target.value })} placeholder={set ? 'Set — leave empty to keep' : 'Not set'} /></label>)}
      <button type="button" disabled={busy} onClick={saveSecrets}>Save secrets</button></fieldset>}
    <p className="field-help">The first Test of an npx server can time out while npx downloads the package; run it again.</p>
    <div className="header-actions"><button className="primary" disabled={busy} onClick={save}>Save</button><button disabled={busy} onClick={test}>Test</button><button className="danger-button" disabled={busy} onClick={remove}>Delete</button></div>
    {notice && <p role="status" className={notice.ok ? '' : 'bad'}>{notice.text}</p>}
  </section>;
}

export default function McpServers() {
  const [data, setData] = useState<Data | null>(null);
  const [preset, setPreset] = useState('');
  const [custom, setCustom] = useState({ name: '', transport: 'stdio', command: '', args: '', env_keys: '', url: '' });
  const [notice, setNotice] = useState<Notice>(null);
  const load = useCallback(() => { api<Data>('mcp-servers').then(setData).catch(e => setNotice({ ok: false, text: e.message })); }, []);
  useEffect(load, [load]);
  const add = async (body: Record<string, unknown>) => {
    setNotice(null);
    try { await api('mcp-servers', body); setPreset(''); setCustom({ name: '', transport: 'stdio', command: '', args: '', env_keys: '', url: '' }); load(); }
    catch (e) { setNotice({ ok: false, text: (e as Error).message }); }
  };
  const p = data?.presets[preset];
  return <Panel title="MCP servers" storageKey="config:mcp">
    <p>Give the agents extra tools from MCP servers (Jira, Playwright, others). Servers start disabled; read-only tools run automatically, anything else needs human approval. Output from a server is treated as untrusted evidence.</p>
    {data?.servers.map(s => <ServerCard key={s.name + JSON.stringify(s.stages)} server={s} stages={data.stages} onChange={load} onDeleted={load} />)}
    {data && !data.servers.length && <p className="field-help">No MCP servers configured.</p>}
    <details><summary>Add a server</summary>
      <label>From preset<select value={preset} onChange={e => setPreset(e.target.value)}><option value="">Choose a preset…</option>{Object.entries(data?.presets || {}).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select></label>
      {p && <><p className="field-help">{p.note}</p><button type="button" className="primary" onClick={() => add(p.server)}>Add preset</button></>}
      <h2 style={{ marginTop: 18 }}>Custom</h2>
      <label>Name (slug)<input value={custom.name} onChange={e => setCustom({ ...custom, name: e.target.value })} maxLength={32} /></label>
      <label>Transport<select value={custom.transport} onChange={e => setCustom({ ...custom, transport: e.target.value })}><option value="stdio">stdio (local command)</option><option value="http">HTTP (https URL)</option></select></label>
      {custom.transport === 'stdio'
        ? <><label>Command<input value={custom.command} onChange={e => setCustom({ ...custom, command: e.target.value })} placeholder="npx" /></label>
          <label>Arguments (one per line)<textarea value={custom.args} onChange={e => setCustom({ ...custom, args: e.target.value })} /></label>
          <label>Secret environment variable names (comma separated)<input value={custom.env_keys} onChange={e => setCustom({ ...custom, env_keys: e.target.value })} placeholder="API_TOKEN" /></label></>
        : <label>URL<input value={custom.url} onChange={e => setCustom({ ...custom, url: e.target.value })} placeholder="https://…" inputMode="url" /></label>}
      <button type="button" onClick={() => add({ name: custom.name.trim(), transport: custom.transport, command: custom.command.trim(), args: custom.args.split('\n').map(x => x.trim()).filter(Boolean), env_keys: custom.env_keys.split(',').map(x => x.trim()).filter(Boolean), url: custom.url.trim(), enabled: false, stages: [] })}>Add custom server</button>
    </details>
    {notice && <p role="status" className={notice.ok ? '' : 'bad'}>{notice.text}</p>}
  </Panel>;
}
