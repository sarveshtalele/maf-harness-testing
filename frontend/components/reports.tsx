'use client';
import { useEffect, useState } from 'react';
import { api, label, type Run } from '@/lib/harness';
import { Panel } from './data';

type File = { id: string; path: string; name: string; kind: string; bytes: number; updated: number };
export default function Reports({ run, logs = false }: { run: Run; logs?: boolean }) {
  const [files, setFiles] = useState<File[]>([]);
  const [selected, setSelected] = useState('');
  const [kind, setKind] = useState('');
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [truncated, setTruncated] = useState(false);
  const [content, setContent] = useState<{ text: string; truncated: boolean } | null>(null);
  const [query, setQuery] = useState('');
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try { const data = await api<{ files: File[]; truncated: boolean }>(`runs/${run.id}/evidence`, undefined, controller.signal); setFiles(data.files); setTruncated(data.truncated); setError(''); }
      catch (e) { if (!controller.signal.aborted) setError((e as Error).message); }
      if (run.busy && !controller.signal.aborted) timer = setTimeout(load, 4000);
    };
    void load(); return () => { controller.abort(); clearTimeout(timer); };
  }, [run.id, run.busy]);
  const available = files.filter(f => logs ? f.kind === 'log' : f.kind !== 'log');
  const shown = available.filter(f => (!kind || f.kind === kind) && f.path.toLowerCase().includes(search.toLowerCase()));
  const file = shown.find(f => f.id === selected) || (logs ? shown.find(f => f.bytes > 0) : shown.find(f => f.kind === 'report')) || shown[0];
  const url = file ? `/api/harness/runs/${encodeURIComponent(run.id)}/evidence/${file.id}` : '';
  useEffect(() => {
    setContent(null); setQuery('');
    if (!file || !['log', 'junit'].includes(file.kind)) return;
    const controller = new AbortController();
    void api<{ text: string; truncated: boolean }>(`runs/${run.id}/evidence/${file.id}`, undefined, controller.signal).then(setContent).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [run.id, file?.id, file?.updated]);
  return <><div className="report-intro"><div><h2>{logs ? 'Execution logs' : 'Browser evidence'}</h2><p>{logs ? 'Find the exact output from installation, generation and test execution.' : 'Open saved Playwright reports, inspect screenshots and download traces.'}</p></div><span className="badge">{available.length} files</span></div>{error && <p className="notice bad" role="alert">{error}</p>}{truncated && <p className="notice">The evidence scan reached its file limit. Additional files remain in the run directory.</p>}<div className="evidence-layout"><Panel title={logs ? 'Log files' : 'Reports & attachments'} storageKey={`${run.id}:files:${logs}`}><input type="search" aria-label="Search evidence files" placeholder="Search files or attempts…" value={search} onChange={e => setSearch(e.target.value)} />{!logs && <div className="evidence-types">{['', 'report', 'screenshot', 'trace', 'junit'].map(k => <button key={k} aria-pressed={kind === k} onClick={() => setKind(k)}>{k ? label(k) : 'All'}</button>)}</div>}<div className="evidence-files">{shown.map(f => <button className={file?.id === f.id ? 'selected' : ''} key={f.id} aria-pressed={file?.id === f.id} onClick={() => setSelected(f.id)}><span className={`file-icon ${f.kind}`}>{f.kind === 'report' ? '◫' : f.kind === 'screenshot' ? '▧' : f.kind === 'trace' ? '◈' : '≡'}</span><span><b>{f.name}</b><small>{f.path}</small><small>{label(f.kind)} · {(f.bytes / 1024).toFixed(1)} KB</small></span></button>)}</div>{!shown.length && <div className="empty-state"><span>▧</span><h3>{available.length ? 'No matching files' : logs ? 'No logs recorded yet' : 'No browser evidence yet'}</h3><p>{logs ? 'Files appear when approved commands execute.' : 'Reports and screenshots appear when a browser suite produces them. A run with only unit tests has no browser report.'}</p></div>}</Panel><Panel title={file?.name || 'Evidence preview'} storageKey={`${run.id}:preview:${logs}`} actions={file && <a className="button small" href={`${url}?download=1`} download>Download ↓</a>}>
    {file ? <><p className="evidence-path">{file.path}</p>{file.kind === 'report' ? <iframe key={file.id} className="report-frame" title="Playwright report" src={`${url}/view.html`} sandbox="allow-scripts" /> : file.kind === 'screenshot' ? <a href={url} target="_blank" rel="noreferrer"><img className="evidence-image" alt={`Screenshot ${file.path}`} src={url} /></a> : file.kind === 'trace' ? <div className="empty-state"><span>◈</span><h3>Playwright trace</h3><p>Download this trace and open it with your local Playwright trace viewer.</p><code>npx playwright show-trace trace.zip</code></div> : content ? <><label>Find in output<input type="search" aria-label="Find in output" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search errors, test names or paths…" /></label>{content.truncated && <p className="notice">Showing the first 512 KB. Download for the full log.</p>}<div className="log-output" role="region" aria-label="Log output">{content.text.replace(/\u001b\[[0-9;]*m/g, '').split('\n').map((line, i) => ({ line, i })).filter(r => !query || r.line.toLowerCase().includes(query.toLowerCase())).map(({ line, i }) => <div className={/error|failed|exception/i.test(line) ? 'log-error' : ''} key={i}><span>{i + 1}</span><code>{line || ' '}</code></div>)}</div></> : <p className="empty-inline">Loading output…</p>}</> : <p className="empty-inline">Select a file to inspect its evidence.</p>}
  </Panel></div></>;
}
