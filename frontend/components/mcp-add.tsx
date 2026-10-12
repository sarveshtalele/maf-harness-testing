'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState, type KeyboardEvent } from 'react';
import { api, label, record } from '@/lib/harness';
import { flashNotice } from '@/lib/leave-guard';
import { DetailPage } from './page';
import { saveSecretValues, useMcpData, type Preset, type Server } from './mcp-servers';

type Imported = { imported: { source_key: string; name: string; secret_keys: string[] }[]; servers: Server[] };
type Notice = { ok: boolean; text: string } | null;

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


/** Left/Right (wrapping) move between tabs and focus the new tab (roving tabindex). */
const arrowTo = <T extends string>(e: KeyboardEvent, keys: readonly T[], current: T, go: (next: T) => void) => {
  if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
  e.preventDefault();
  const next = keys[(keys.indexOf(current) + (e.key === 'ArrowRight' ? 1 : keys.length - 1)) % keys.length];
  go(next);
  document.getElementById(`${(e.currentTarget as HTMLElement).dataset.prefix}-${next}`)?.focus();
};

const MODES = ['paste', 'preset', 'manual'] as const;
type Mode = typeof MODES[number];
const EMPTY_CUSTOM = { name: '', transport: 'stdio', command: '', args: '', env_keys: '', url: '' };

/** /marketplace/mcp/new: add a server from a pasted command or config, a preset, or by hand. Secrets are stored write-only. */
export default function AddServer() {
  const router = useRouter();
  const { data, error } = useMcpData();
  const presets: Record<string, Preset> = data?.presets ?? {};
  const [mode, setMode] = useState<Mode>('paste');
  const [pasteText, setPasteText] = useState('');
  const [preset, setPreset] = useState('');
  const [custom, setCustom] = useState(EMPTY_CUSTOM);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [saved, setSaved] = useState<string[]>([]);
  const p = presets[preset];
  // Leaving the Paste tab drops the pasted text: it may hold secrets and must not wait in a hidden field.
  const choose = (m: Mode) => { if (mode === 'paste' && m !== 'paste') setPasteText(''); setMode(m); setNotice(null); };
  const addBody = async (body: Record<string, unknown>) => {
    setBusy(true); setNotice(null);
    try {
      const added = await api<Server>('mcp-servers', body);
      flashNotice({ ok: true, text: `${added.name} added. It stays disabled until you enable it and save.` });
      router.push(`/marketplace/mcp/${encodeURIComponent(added.name)}`);
    }
    catch (e) { setNotice({ ok: false, text: (e as Error).message }); setBusy(false); }
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
    const names = r.imported.map(i => i.name);
    if (missed.length) { // stay on the page so the error is seen; the servers exist
      setSaved(names);
      setNotice({ ok: false, text: `Added ${names.join(', ')}, but these secrets were NOT stored: ${missed.join('; ')}. Set them on each server's page (re-importing would create copies).` });
      setBusy(false);
      return;
    }
    if (names.length === 1) {
      flashNotice({ ok: true, text: `${names[0]} added. It stays disabled until you enable it and save.` });
      router.push(`/marketplace/mcp/${encodeURIComponent(names[0])}`);
    } else {
      flashNotice({ ok: true, text: `${names.length} MCP servers added. They start disabled until you enable them.` });
      router.push('/marketplace/mcp');
    }
  };
  const cancel = () => router.push('/marketplace/mcp');
  return <DetailPage level={2} eyebrow="MCP servers" title="Add MCP server" breadcrumbs={[{ label: 'Marketplace', href: '/marketplace/skills' }, { label: 'MCP servers', href: '/marketplace/mcp' }, { label: 'Add server' }]}>
    {error && <p role="alert" className="notice bad">{error}</p>}
    <div className="tabs" role="tablist" aria-label="How to add" data-prefix="mcp-add" onKeyDown={e => arrowTo(e as KeyboardEvent, MODES, mode, choose)}>{MODES.map(m => <button key={m} type="button" role="tab" id={`mcp-add-${m}`} aria-controls="mcp-add-panel" tabIndex={mode === m ? 0 : -1} aria-selected={mode === m} className={mode === m ? 'selected' : ''} onClick={() => choose(m)}>{label(m)}</button>)}</div>
    <div role="tabpanel" id="mcp-add-panel" aria-labelledby={`mcp-add-${mode}`} className="mcp-add-panel">
      {mode === 'paste' && <form onSubmit={e => { e.preventDefault(); void addPaste(); }}>
        <label>Paste an npx/uvx/docker command or an MCP JSON config (mcpServers)<textarea rows={6} spellCheck={false} autoComplete="off" value={pasteText} onChange={e => setPasteText(e.target.value)} /></label>
        <p className="field-help">Secret values (env values, docker -e values, Authorization headers) are stored write-only and never shown again.</p>
        <div className="header-actions"><button className="primary" type="submit" disabled={busy || !pasteText.trim()}>Add from paste</button><button type="button" onClick={cancel}>Cancel</button></div>
      </form>}
      {mode === 'preset' && <>
        <label>Preset<select value={preset} onChange={e => setPreset(e.target.value)}><option value="">Choose a preset…</option>{Object.entries(presets).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select></label>
        {p && <><p className="field-help">{p.note}</p><div className="header-actions"><button type="button" className="primary" disabled={busy} onClick={() => void addBody(p.server)}>Add preset</button><button type="button" onClick={cancel}>Cancel</button></div></>}
      </>}
      {mode === 'manual' && <form onSubmit={e => { e.preventDefault(); void addBody({ name: custom.name.trim(), transport: custom.transport, command: custom.command.trim(), args: custom.args.split('\n').map(x => x.trim()).filter(Boolean), env_keys: custom.env_keys.split(',').map(x => x.trim()).filter(Boolean), url: custom.url.trim(), enabled: false, stages: [] }); }}>
        <label>Name (slug)<input value={custom.name} onChange={e => setCustom({ ...custom, name: e.target.value })} maxLength={32} /></label>
        <label>Transport<select value={custom.transport} onChange={e => setCustom({ ...custom, transport: e.target.value })}><option value="stdio">stdio (local command)</option><option value="http">HTTP (https URL)</option></select></label>
        {custom.transport === 'stdio'
          ? <><label>Command<input value={custom.command} onChange={e => setCustom({ ...custom, command: e.target.value })} placeholder="npx" /></label>
            <label>Arguments (one per line)<textarea value={custom.args} onChange={e => setCustom({ ...custom, args: e.target.value })} /></label>
            <label>Secret environment variable names (comma separated)<input value={custom.env_keys} onChange={e => setCustom({ ...custom, env_keys: e.target.value })} placeholder="API_TOKEN" /></label></>
          : <label>URL<input value={custom.url} onChange={e => setCustom({ ...custom, url: e.target.value })} placeholder="https://…" inputMode="url" /></label>}
        <div className="header-actions"><button className="primary" type="submit" disabled={busy}>Add custom server</button><button type="button" onClick={cancel}>Cancel</button></div>
      </form>}
    </div>
    {notice && <p role="status" className={notice.ok ? '' : 'bad'}>{notice.text}</p>}
    {!!saved.length && <p className="mcp-open-links">{saved.map(n => <Link key={n} className="button small" href={`/marketplace/mcp/${encodeURIComponent(n)}`}>Open {n}</Link>)}</p>}
  </DetailPage>;
}
