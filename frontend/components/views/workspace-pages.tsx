'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, label, record, rows, runTitle, text, type Row, type RunSummary } from '@/lib/harness';
import { ConfirmDialog, Fields, Metrics, StatusBadge } from '../page';
import { Panel, usePreference, ResizeHandle } from '../data';
import { useShell } from '../shell';
import ModelSettings from '../model-settings';
import Icon from '../icons';
import { SplitWorkspace } from './run-pages';

const repoName = (url: string) => url.replace(/^https?:\/\/github\.com\//, '').replace(/\.git$/, '');

// ---------- Runs ----------

export function RunsView() {
  const { runs, serviceBusy, refresh } = useShell();
  const router = useRouter();
  const [search, setSearch] = useState('');
  const [deleting, setDeleting] = useState<RunSummary | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const visibleRuns = runs.filter(r => `${r.run_name} ${r.repo_url} ${r.id} ${r.status} ${r.verdict}`.toLowerCase().includes(search.toLowerCase()));
  const deleteRun = async () => {
    if (!deleting) return;
    setSending(true); setError('');
    try {
      await api(`runs/${encodeURIComponent(deleting.id)}/delete`, { confirm_run_id: deleting.id });
      setDeleting(null); await refresh();
    } catch (e) { setError((e as Error).message); setDeleting(null); }
    finally { setSending(false); }
  };
  const awaiting = runs.filter(r => r.pending).length;
  return <>
    <div className="page-header"><div><span className="eyebrow">Test engineering workspace</span><h1 className="page-title"><Icon name="runs" />Repository runs</h1><p>Review each stage. Execute with approval. Keep the evidence.</p></div><Link className="button primary" href="/runs/new"><Icon name="plus" />Start a run</Link></div>
    {error && <div className="notice bad" role="alert">{error}</div>}
    <Metrics values={{ saved_runs: runs.length, awaiting_review: awaiting, active_operations: serviceBusy ? 1 : 0 }} />
    <Panel title="Run history" storageKey="runs:history">
      <div className="table-tools"><input aria-label="Search runs" type="search" placeholder="Search repositories, run IDs or status…" value={search} onChange={e => setSearch(e.target.value)} /><small>{visibleRuns.length} runs</small></div>
      {!runs.length ? <div className="empty-state"><span aria-hidden="true">◇</span><h2>Your first run starts here</h2><p>Add a public GitHub repository. The harness will ask before cloning, installing or running tests.</p><Link className="button primary" href="/runs/new">Start repository review</Link></div> : <div className="run-list">{visibleRuns.map(r => <div className="run-row" key={r.id}>
        <div className="repo-icon" aria-hidden="true">◇</div>
        <div className="run-name"><b>{runTitle(r.run_name, r.repo_url, r.id)}</b><small>{repoName(r.repo_url || r.id)} · {r.id} · Updated {new Date(r.updated * 1000).toLocaleString()}</small><progress aria-label={`Workflow completion ${r.id}`} max={100} value={Number(record(record(r).progress).percent) || 0} /><small>{text(record(record(r).progress).percent) || 0}% of approved workflow stages</small></div>
        <span className="run-stage">{label(r.stage)}</span>
        <StatusBadge value={r.busy && r.status !== 'stopping' ? 'working' : r.verdict || r.status} />
        {r.status === 'unreadable' ? <span className="muted">Unreadable</span> : <Link className="button" href={r.status === 'complete' ? `/runs/${encodeURIComponent(r.id)}/qa` : `/runs/${encodeURIComponent(r.id)}`}>Open run →</Link>}
        <button type="button" className="delete-run small" aria-label={`Delete run ${r.id}`} disabled={sending || serviceBusy || r.busy} onClick={() => setDeleting(r)}>Delete</button>
      </div>)}</div>}
    </Panel>
    {serviceBusy && <p className="notice">An operation is running. You can inspect saved runs; new actions wait until it reaches a gate.</p>}
    <ConfirmDialog open={deleting !== null} title="Delete this run?" confirmLabel={sending ? 'Deleting…' : 'Delete run'} tone="danger" busy={sending} disabled={serviceBusy} onConfirm={() => void deleteRun()} onClose={() => setDeleting(null)}>
      <p><strong>{deleting ? repoName(deleting.repo_url || deleting.id) : ''}</strong> · {deleting?.id}</p>
      <p>This removes the run from history. Its files and reports move to this user's local .deleted-runs folder for recovery. Running operations must finish first.</p>
    </ConfirmDialog>
    {router && null}
  </>;
}

// ---------- New run ----------

/** Base64 of a file without FileReader's data: prefix; avoids building huge strings char by char. */
const readBase64 = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => { const url = String(reader.result); resolve(url.slice(url.indexOf(',') + 1)); };
  reader.onerror = () => reject(reader.error);
  reader.readAsDataURL(file);
});
const MAX_DOCUMENTS = 10;
const MAX_DOCUMENT_FILE_BYTES = 10_000_000; // per file, same as the server (repo/specs.py)
const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024; // all documents together
const DOCUMENT_TYPES = ['.md', '.txt', '.docx'];
const megabytes = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(1)} MB`;
/** Client-side limits, checked before any file is read or posted. Names the offending files. */
export function documentProblem(files: File[]): string {
  if (files.length > MAX_DOCUMENTS) return `Choose at most ${MAX_DOCUMENTS} specification documents; you selected ${files.length}.`;
  const invalid = files.filter(f => f.name.startsWith('.')).map(f => f.name);
  if (invalid.length) return `Not a valid file name: ${invalid.join(', ')}. Use a name such as spec.md.`;
  const rejected = files.filter(f => !DOCUMENT_TYPES.some(ext => f.name.toLowerCase().endsWith(ext))).map(f => f.name);
  if (rejected.length) return `Unsupported file type: ${rejected.join(', ')}. Use .md, .txt or .docx files.`;
  const large = files.filter(f => f.size > MAX_DOCUMENT_FILE_BYTES).map(f => `${f.name} (${f.size.toLocaleString('en-US')} bytes)`);
  if (large.length) return `Each document may be at most 10,000,000 bytes: ${large.join(', ')} is too large.`;
  const total = files.reduce((sum, f) => sum + f.size, 0);
  if (total > MAX_DOCUMENT_BYTES) {
    const largest = files.reduce((a, b) => (b.size > a.size ? b : a));
    return `The documents total ${megabytes(total)}; the limit is 10 MB. Largest: ${largest.name} (${megabytes(largest.size)}).`;
  }
  return '';
}
const stageNames = ['repo_analysis', 'requirements', 'test_planning', 'test_design', 'test_generation', 'environment_setup', 'test_execution'];

export function NewRunView() {
  const { config, refresh } = useShell();
  const router = useRouter();
  const [mode, setMode] = useState<'brownfield' | 'greenfield'>('brownfield');
  const [types, setTypes] = useState({ unit: true, integration: true });
  const [prefill, setPrefill] = useState({ repo_knowledge: '' });
  const [inspecting, setInspecting] = useState<'idle' | 'loading' | 'filled'>('idle');
  const [formError, setFormError] = useState('');
  const [sending, setSending] = useState(false);
  const [connectionNote, setConnectionNote] = useState('');
  const [split, setSplit] = usePreference<number>('maf:split', 68);
  const touched = useRef(new Set<string>());
  const inspectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const inspectAbort = useRef<AbortController>(undefined);
  useEffect(() => () => { clearTimeout(inspectTimer.current); inspectAbort.current?.abort(); }, []);
  const edit = (name: keyof typeof prefill, value: string) => { touched.current.add(name); setPrefill(p => ({ ...p, [name]: value })); };
  const inspectRepo = (value: string) => {
    clearTimeout(inspectTimer.current); inspectAbort.current?.abort();
    if (!/^\s*(git clone\s+)?https:\/\/(www\.)?github\.com\/[^/\s]+\/[^/\s]+/.test(value)) { setInspecting('idle'); return; }
    setInspecting('loading');
    inspectTimer.current = setTimeout(async () => {
      const abort = inspectAbort.current = new AbortController();
      try {
        const r = await api<Row>('repo-preview', { repo_url: value.trim() }, abort.signal);
        const next = { repo_knowledge: text(r.repo_knowledge || '') };
        setPrefill(p => ({ ...p, ...Object.fromEntries(Object.entries(next).filter(([k, v]) => !touched.current.has(k) && v)) }));
        setInspecting(next.repo_knowledge ? 'filled' : 'idle');
      } catch { if (!abort.signal.aborted) setInspecting('idle'); }
    }, 600);
  };
  const start = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault(); const data = new FormData(e.currentTarget);
    const selected = (['unit', 'integration'] as const).filter(t => types[t]);
    setFormError(''); setConnectionNote('');
    if (!selected.length) { setFormError('Choose at least one test type.'); return; }
    const body: Record<string, unknown> = { run_mode: mode, test_types: selected, repo_url: data.get('repo_url'), run_name: data.get('run_name'), repo_knowledge: data.get('repo_knowledge'), case_budget: { per_requirement: Number(data.get('case_budget')) || config?.case_budget_default || 2 } };
    if (mode === 'greenfield') {
      const files = (data.getAll('documents') as File[]).filter(f => f.size);
      const problem = documentProblem(files);
      if (problem) { setFormError(problem); return; }
      let documents: { name: string; content_base64: string }[];
      try { documents = await Promise.all(files.map(async f => ({ name: f.name, content_base64: await readBase64(f) }))); }
      catch { setFormError('Could not read one of the selected documents. Choose the files again.'); return; }
      const site = String(data.get('jira_base_url') || '').trim();
      const keys = String(data.get('jira_issue_keys') || '').split(/[\s,]+/).filter(Boolean);
      // The API token is read from the form only to send it once; it is never kept in state or storage.
      const jira = site ? { base_url: site, jql: data.get('jira_jql'), issue_keys: keys, email: data.get('jira_email'), api_token: data.get('jira_api_token') } : undefined;
      body.spec_sources = { documents, ...(jira ? { jira } : {}) };
    }
    setSending(true);
    let created: { id: string } | null = null;
    try { created = await api<{ id: string }>('runs', body); await refresh(); }
    catch (err) { setFormError((err as Error).message); }
    finally { setSending(false); }
    if (created) { touched.current.clear(); setPrefill({ repo_knowledge: '' }); setMode('brownfield'); setTypes({ unit: true, integration: true }); setInspecting('idle'); router.push(`/runs/${encodeURIComponent(created.id)}`); }
  };
  return <>
    <div className="page-header"><div><span className="eyebrow">New review</span><h1 className="page-title"><Icon name="plusCircle" />New test run</h1><p>Requirements, design, executable tests and a QA report from repository evidence.</p></div></div>
    {connectionNote && <div className="notice bad" role="alert">{connectionNote}</div>}
    <SplitWorkspace split={split} setSplit={setSplit}>
      <Panel title="Repository details" storageKey="new:form">
        <form onSubmit={start}>
          <fieldset className="radio-group"><legend>Mode</legend><label className="check"><input type="radio" name="run_mode" value="brownfield" checked={mode === 'brownfield'} onChange={() => setMode('brownfield')} />Brownfield repository</label><label className="check"><input type="radio" name="run_mode" value="greenfield" checked={mode === 'greenfield'} onChange={() => setMode('greenfield')} />Greenfield specs</label></fieldset>
          <fieldset className="radio-group"><legend>Test types</legend><label className="check"><input type="checkbox" checked={types.unit} onChange={e => setTypes(t => ({ ...t, unit: e.target.checked }))} />Unit</label><label className="check"><input type="checkbox" checked={types.integration} onChange={e => setTypes(t => ({ ...t, integration: e.target.checked }))} />Integration</label></fieldset>
          <p className="field-help">Unit and integration tests for the complete application. At least one is required.</p>
          <label>Run name <span className="muted">optional</span><input name="run_name" maxLength={80} placeholder="owner/repository · YYYY-MM-DD HH:MM" /></label>
          <p className="field-help">Shown in the run list, reports and generated test headers so each result can be traced to this run.</p>
          <label>Public GitHub repository URL{mode === 'greenfield' && <> <span className="muted">optional</span></>}<input name="repo_url" type="text" inputMode="url" required={mode === 'brownfield'} placeholder="https://github.com/owner/repository" onChange={e => inspectRepo(e.target.value)} />{inspecting === 'loading' && <p className="field-help" role="status"><span className="spinner" aria-hidden="true" /> Inspecting repository…</p>}{inspecting === 'filled' && <p className="field-help" role="status">Auto-filled from repository — review before starting.</p>}</label>
          <p className="field-help">Repository page, clone URL, supported branch/tag URL or git clone followed by one URL. Commands are parsed as text, never executed.</p>
          <label>Repository knowledge <span className="muted">optional</span><textarea name="repo_knowledge" value={prefill.repo_knowledge} onChange={e => edit('repo_knowledge', e.target.value)} maxLength={32000} rows={6} placeholder="Paste README setup notes, required tool versions, install/start commands and known prerequisites…" /></label>
          <p className="field-help">Used as unverified context for planning and manual setup guidance. Pasted commands are never executed automatically.</p>
          {mode === 'greenfield' && <><label>Specification documents<input name="documents" type="file" multiple accept=".md,.txt,.docx" onChange={e => setFormError(documentProblem(Array.from(e.currentTarget.files ?? [])))} /></label><p className="field-help">Up to 10 files (md, txt, docx), 10 MB in total.</p><fieldset className="jira-fields"><legend>Jira <span className="muted">optional</span></legend><label>Site URL<input name="jira_base_url" type="url" placeholder="https://your-site.atlassian.net" /></label><label>JQL<input name="jira_jql" placeholder="project = PROJ AND type = Story" /></label><label>Issue keys<input name="jira_issue_keys" placeholder="PROJ-1, PROJ-2" /></label><label>Email<input name="jira_email" type="email" autoComplete="off" /></label><label>API token<input name="jira_api_token" type="password" autoComplete="off" /></label><p className="field-help">The token is sent once for fetching stories and is never saved with the run.</p></fieldset></>}
          <label>Test cases per requirement<input name="case_budget" type="number" min={1} max={10} step={1} defaultValue={config?.case_budget_default ?? 2} key={`budget:${config?.case_budget_default ?? 2}`} required /></label>
          <p className="field-help">Fewer cases = faster run. You can change this until test design is approved.</p>
          <div className="notice">You will review the clone, installation and execution actions before they happen.</div>
          {formError && <p className="notice bad field-error" role="alert">{formError}</p>}
          <button className="primary" disabled={sending || !config?.key_configured}>{sending ? 'Creating run…' : 'Start repository review →'}</button>
          {config && !config.key_configured && <p className="bad">No API key yet. <Link href="/settings">Add it in Configuration › Model &amp; API key</Link>.</p>}
        </form>
      </Panel>
      <Panel title="How the review works" storageKey="new:journey"><ol className="journey">{stageNames.map(s => <li key={s}>{label(s)}</li>)}</ol><p>The harness selects testing frameworks from the detected stack. Missing prerequisites and unsupported execution paths remain visible.</p><p className="muted">Approved commands run as your local user. Isolated dependencies are not a security sandbox.</p></Panel>
    </SplitWorkspace>
  </>;
}

// ---------- Settings ----------

export function SettingsView() {
  const { config, refresh } = useShell();
  if (!config) return <p role="status">Loading configuration…</p>;
  return <>
    <div className="page-header"><div><h1 className="page-title"><Icon name="gear" />Configuration</h1><p>Current backend settings and local workspace preferences.</p></div></div>
    <ModelSettings stages={config.stages} onSaved={() => void refresh()} />
    <p className="field-help">MCP servers are managed in <Link href="/marketplace/mcp">Marketplace → MCP servers</Link>.</p>
    <Panel title="Daily usage" storageKey="config:daily"><Metrics values={config.daily} /><p>Shared across runs for this OS user; resets at execution-host local midnight.</p></Panel>
    <Panel title="Debugging logs" storageKey="config:logs"><Fields data={{ service_and_setup_logs: record(config).service_log_directory, run_evidence: record(config).run_directory }} /><p>Each run’s Logs, Audit and Observability show workflow activity. Service logs also capture startup failures before a run exists.</p></Panel>
  </>;
}
