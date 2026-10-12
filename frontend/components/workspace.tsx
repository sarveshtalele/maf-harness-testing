'use client';
import { useCallback, useEffect, useRef, useState, type CSSProperties, type FormEvent, type ReactNode } from 'react';
import { api, artifacts, download, isConnectionError, label, minutes, record, rows, runTitle, text, type Config, type Row, type Run, type RunSummary } from '@/lib/harness';
import { Badge, DataTable, DataView, ExportButtons, Metrics, Panel, ResizeHandle, TestCaseTable, usePreference, useReturnFocus } from './data';
import Skills from './skills';
import Reports from './reports';
import StepReports, { QaDocument } from './step-reports';
import Prompts from './prompts';
import ModelSettings from './model-settings';
import McpServers from './mcp-servers';
import RunErrors from './run-errors';
import Observability, { Select } from './observability';

const pages: Record<string, string> = { skills: 'Skills', prompts: 'Prompts', configuration: 'Configuration', new: 'New run' };
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
const DOCUMENT_TYPES = ['.md', '.txt', '.docx', '.pdf'];
const megabytes = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(1)} MB`;
/** Client-side limits, checked before any file is read or posted. Names the offending files. */
export function documentProblem(files: File[]): string {
  if (files.length > MAX_DOCUMENTS) return `Choose at most ${MAX_DOCUMENTS} specification documents; you selected ${files.length}.`;
  const invalid = files.filter(f => f.name.startsWith('.')).map(f => f.name);
  if (invalid.length) return `Not a valid file name: ${invalid.join(', ')}. Use a name such as spec.md.`;
  const rejected = files.filter(f => !DOCUMENT_TYPES.some(ext => f.name.toLowerCase().endsWith(ext))).map(f => f.name);
  if (rejected.length) return `Unsupported file type: ${rejected.join(', ')}. Use .md, .txt, .docx or .pdf files.`;
  const large = files.filter(f => f.size > MAX_DOCUMENT_FILE_BYTES).map(f => `${f.name} (${f.size.toLocaleString('en-US')} bytes)`);
  if (large.length) return `Each document may be at most 10,000,000 bytes: ${large.join(', ')} is too large.`;
  const total = files.reduce((sum, f) => sum + f.size, 0);
  if (total > MAX_DOCUMENT_BYTES) {
    const largest = files.reduce((a, b) => (b.size > a.size ? b : a));
    return `The documents total ${megabytes(total)}; the limit is 10 MB. Largest: ${largest.name} (${megabytes(largest.size)}).`;
  }
  return '';
}
const tabs = ['Review', 'Errors', 'Artifacts', 'Generated tests', 'QA report', 'Test reports', 'Logs', 'Observability', 'Audit', 'Prompts'];
const stages = ['repo_analysis', 'requirements', 'test_planning', 'test_design', 'test_generation', 'environment_setup', 'test_execution'];

export default function Workspace() {
  const [page, setPage] = useState('Runs');
  const [id, setId] = useState('');
  const [tab, setTab] = useState('Review');
  const [selectedStage, setSelectedStage] = useState('');
  const [stageVisit, setStageVisit] = useState(0);
  const [rerunStage, setRerunStage] = useState<string | null>(null);
  const currentId = useRef(id); currentId.current = id;
  const refreshVersion = useRef(0);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [serviceBusy, setServiceBusy] = useState(false);
  const [error, setError] = useState('');
  const [prefill, setPrefill] = useState({ repo_knowledge: '' });
  const [inspecting, setInspecting] = useState<'idle' | 'loading' | 'filled'>('idle');
  const [mode, setMode] = useState<'brownfield' | 'greenfield'>('brownfield');
  const [types, setTypes] = useState({ unit: true, integration: true });
  const touched = useRef(new Set<string>());
  const inspectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const inspectAbort = useRef<AbortController>(undefined);
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
  const [connectionError, setConnectionError] = useState('');
  const [formError, setFormError] = useState('');
  const [sending, setSending] = useState(false);
  const [search, setSearch] = useState('');
  const [deleting, setDeleting] = useState<RunSummary | null>(null);
  const deleteDialog = useRef<HTMLDialogElement>(null);
  const rememberDelete = useReturnFocus(!!deleting);
  useEffect(() => { if (deleting) deleteDialog.current?.showModal(); }, [deleting]);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const renameDialog = useRef<HTMLDialogElement>(null);
  const rememberRename = useReturnFocus(renaming);
  useEffect(() => { if (renaming) renameDialog.current?.showModal(); }, [renaming]);
  const [confirmStop, setConfirmStop] = useState(false);
  const stopDialog = useRef<HTMLDialogElement>(null);
  const rememberStop = useReturnFocus(confirmStop);
  useEffect(() => { if (confirmStop) stopDialog.current?.showModal(); }, [confirmStop]);
  const [sidebar, setSidebar] = usePreference<number>('maf:sidebar', 236);
  const [split, setSplit] = usePreference<number>('maf:split', 68);
  const [theme, setTheme] = usePreference<string>('maf:theme', 'light');

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const key = params.get('run');
    if (key && /^[A-Za-z0-9_-]{1,100}$/.test(key)) { setId(key); setPage('Run'); }
    if (!key && pages[params.get('page') || '']) setPage(pages[params.get('page') || '']);
    setSelectedStage(stages.includes(params.get('stage') || '') ? params.get('stage')! : '');
    const value = params.get('tab'); if (value && tabs.includes(value)) setTab(value);
    const pop = () => { const p = new URLSearchParams(location.search); setId(p.get('run') || ''); setPage(p.has('run') ? 'Run' : pages[p.get('page') || ''] || 'Runs'); setTab(tabs.includes(p.get('tab') || '') ? p.get('tab')! : 'Review'); setSelectedStage(stages.includes(p.get('stage') || '') ? p.get('stage')! : ''); };
    window.addEventListener('popstate', pop); return () => window.removeEventListener('popstate', pop);
  }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme === 'dark' ? 'dark' : 'light'; }, [theme]);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const version = ++refreshVersion.current;
    const [listing, settings, current] = await Promise.all([
      api<{ runs: RunSummary[]; busy: boolean }>('runs', undefined, signal),
      api<Config>('config', undefined, signal),
      id ? api<Run>(`runs/${id}`, undefined, signal) : Promise.resolve(null),
    ]);
    if (signal?.aborted || currentId.current !== id || refreshVersion.current !== version) return;
    setRuns(listing.runs); setServiceBusy(listing.busy); setConfig(settings); setRun(current); setConnectionError('');
  }, [id]);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const poll = async () => { try { await refresh(controller.signal); } catch (e) { if (!controller.signal.aborted) setConnectionError((e as Error).message); } if (!controller.signal.aborted) timer = setTimeout(poll, 2000); };
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [refresh]);
  const navigate = (next: string, runId = '', runTab = 'Review') => {
    setPage(next); setSelectedStage(''); setError('');
    if (next === 'Run') { if (runId !== id) setRun(null); setId(runId); setTab(runTab); history.pushState({}, '', `?run=${encodeURIComponent(runId)}&tab=${encodeURIComponent(runTab)}`); }
    else { setId(''); setRun(null); history.pushState({}, '', Object.entries(pages).some(([, name]) => name === next) ? `/?page=${Object.entries(pages).find(([, name]) => name === next)![0]}` : '/'); }
  };
  const changeTab = (next: string) => { setSelectedStage(''); setTab(next); history.replaceState({}, '', `?run=${encodeURIComponent(id)}&tab=${encodeURIComponent(next)}`); };
  const showStage = (stage: string) => { setStageVisit(value => value + 1); setSelectedStage(stage); setTab('Artifacts'); history.pushState({}, '', `?run=${encodeURIComponent(id)}&tab=Artifacts&stage=${stage}`); };
  const act = async (path: string, body: Row) => {
    ++refreshVersion.current;
    setSending(true); setError('');
    try { const result = await api<{ id: string }>(path, body); await refresh(); return result; }
    catch (e) { if (isConnectionError(e)) setConnectionError((e as Error).message); else setError((e as Error).message); }
    finally { setSending(false); }
  };
  const deleteRun = async () => {
    if (!deleting) return;
    setSending(true); setError('');
    try {
      await api(`runs/${deleting.id}/delete`, { confirm_run_id: deleting.id });
      if (id === deleting.id) navigate('Runs');
      setRuns(current => current.filter(row => row.id !== deleting.id));
      setDeleting(null);
    } catch (e) { setError((e as Error).message); setDeleting(null); }
    finally { setSending(false); }
  };
  const start = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault(); const data = new FormData(e.currentTarget);
    const selected = (['unit', 'integration'] as const).filter(t => types[t]);
    setFormError('');
    if (!selected.length) { setFormError('Choose at least one test type.'); return; }
    const body: Record<string, unknown> = { run_mode: mode, test_types: selected, repo_url: data.get('repo_url'), run_name: data.get('run_name'), repo_knowledge: data.get('repo_knowledge'), case_budget: { per_requirement: Number(data.get('case_budget')) || config?.case_budget_default || 2 } };
    if (mode === 'greenfield') {
      const files = (data.getAll('documents') as File[]).filter(f => f.size);
      const problem = documentProblem(files);
      if (problem) { setFormError(problem); return; }
      let documents: { name: string; content_base64: string }[];
      try {
        documents = await Promise.all(files.map(async f => ({ name: f.name, content_base64: await readBase64(f) })));
      } catch { setFormError('Could not read one of the selected documents. Choose the files again.'); return; }
      const site = String(data.get('jira_base_url') || '').trim();
      const keys = String(data.get('jira_issue_keys') || '').split(/[\s,]+/).filter(Boolean);
      // The API token is read from the form only to send it once; it is never kept in state or storage.
      const jira = site ? { base_url: site, jql: data.get('jira_jql'), issue_keys: keys, email: data.get('jira_email'), api_token: data.get('jira_api_token') } : undefined;
      body.spec_sources = { documents, ...(jira ? { jira } : {}) };
    }
    setSending(true);
    let created: { id: string } | null = null;
    try { created = await api<{ id: string }>('runs', body); await refresh(); }
    catch (err) { if (isConnectionError(err)) setConnectionError((err as Error).message); else setFormError((err as Error).message); }
    finally { setSending(false); }
    if (created) { touched.current.clear(); setPrefill({ repo_knowledge: '' }); setMode('brownfield'); setTypes({ unit: true, integration: true }); setInspecting('idle'); navigate('Run', created.id); }
  };
  const response = (decision: string, feedback: string) => run?.pending && act(`runs/${id}/respond`, { request_id: run.pending.id, decision, feedback });
  const active = run?.busy ? String(run.state.active_stage || run.pending?.stage || 'repo_analysis') : run?.pending?.stage || String(run?.state.active_stage || 'repo_analysis');
  const errorCount = run?.errors?.items.length ?? 0;
  const genFailed = run?.errors?.items.filter(e => e.source === 'generation_failures' || e.source === 'audit' && e.stage === 'test_generation').length ?? 0;
  const firstSentence = (v: string) => (v.split(/(?<=[.!?])\s|\n/)[0] || v).slice(0, 200);
  const finish = run?.busy ? record(record(run.progress).eta).finish_estimate : null;
  const etaOf = (stage: string): Row => record(rows(record(run?.progress).eta ? record(record(run?.progress).eta).stages : []).find(r => r.stage === stage));
  const failedStage = run?.pending?.kind === 'error' ? run.pending.stage : (run ? record(record(artifacts(run).test_execution).qa_report).verdict : '') === 'FAIL' ? 'test_execution' : '';
  const visibleRuns = runs.filter(r => `${r.run_name} ${r.repo_url} ${r.id} ${r.status} ${r.verdict}`.toLowerCase().includes(search.toLowerCase()));
  const style = { '--sidebar': `${Math.max(190, Math.min(340, Number(sidebar) || 236))}px`, '--main-split': `${Math.max(45, Math.min(80, Number(split) || 68))}%` } as CSSProperties;
  return <div className="app-shell" style={style}>
    <aside className="sidebar"><a className="brand" href="/" onClick={e => { e.preventDefault(); navigate('Runs'); }}><span className="brand-mark">QE</span><span>Agentic QE Platform<small>Repository testing</small></span></a>
      <div className="workspace-label"><span className="dot" /> Local workspace</div>
      <nav aria-label="Main navigation"><button className={page === 'Runs' ? 'active' : ''} onClick={() => navigate('Runs')}><span aria-hidden="true">◫</span> Runs <small>{runs.length}</small></button><button className={page === 'New run' ? 'active' : ''} onClick={() => navigate('New run')}><span aria-hidden="true">＋</span> New run</button><button className={page === 'Skills' ? 'active' : ''} onClick={() => navigate('Skills')}><span aria-hidden="true">◇</span> Agent skills</button><button className={page === 'Prompts' ? 'active' : ''} onClick={() => navigate('Prompts')}><span aria-hidden="true">≡</span> Agent prompts</button><button className={page === 'Configuration' ? 'active' : ''} onClick={() => navigate('Configuration')}><span aria-hidden="true">⚙</span> Configuration</button></nav>
      {id && <div className="sidebar-run"><span className="eyebrow">Selected run</span><b>{runTitle(run?.state.run_name, run?.state.repo_url, id)}</b><small>{run?.state.run_name ? `${repoName(text(run.state.repo_url))} · ` : ''}{id}</small>{run && <Badge value={run.busy && run.status !== 'stopping' ? 'working' : run.status} />}</div>}
      <div className="sidebar-footer">{config && <><span className="eyebrow">Daily token usage</span><progress value={config.daily.used} max={config.daily.limit || 1} /><small>{config.daily.used.toLocaleString()} / {config.daily.limit.toLocaleString()}</small></>}<button onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? '☀ Light appearance' : '◐ Dark appearance'}</button><small>History stays on this user’s machine.</small></div>
      <ResizeHandle label="Resize sidebar" start={() => { const initial = sidebar; return dx => setSidebar(Math.max(190, Math.min(340, initial + dx))); }} />
    </aside>
    {deleting && <dialog ref={deleteDialog} aria-labelledby="delete-run-title" className="detail-dialog" onCancel={() => setDeleting(null)} onClose={() => setDeleting(null)}><header className="panel-heading"><h2 id="delete-run-title">Delete this run?</h2><button onClick={() => setDeleting(null)}>Cancel</button></header><div className="panel-body"><p><strong>{repoName(deleting.repo_url || deleting.id)}</strong> · {deleting.id}</p><p>This removes the run from history. Its files and reports move to this user's local .deleted-runs folder for recovery. Running operations must finish first.</p><div className="header-actions"><button onClick={() => setDeleting(null)}>Keep run</button><button className="primary" disabled={sending || serviceBusy} onClick={() => void deleteRun()}>{sending ? 'Deleting…' : 'Delete run'}</button></div></div></dialog>}
    {confirmStop && <dialog ref={stopDialog} aria-labelledby="stop-run-title" className="detail-dialog" onCancel={() => setConfirmStop(false)} onClose={() => setConfirmStop(false)}><header className="panel-heading"><h2 id="stop-run-title">Stop this run?</h2></header><div className="panel-body"><p>Completed stages and evidence are kept.</p><div className="header-actions"><button onClick={() => setConfirmStop(false)}>Cancel</button><button className="danger-button" disabled={sending} onClick={() => { setConfirmStop(false); void act(`runs/${id}/stop`, {}); }}>Stop run</button></div></div></dialog>}
    {renaming && <dialog ref={renameDialog} aria-labelledby="rename-run-title" className="detail-dialog" onCancel={() => setRenaming(false)} onClose={() => setRenaming(false)}><form onSubmit={e => { e.preventDefault(); setRenaming(false); void act(`runs/${id}/rename`, { run_name: renameValue }); }}><header className="panel-heading"><h2 id="rename-run-title">Rename run</h2></header><div className="panel-body"><label htmlFor="rename-run-input">Run name (max 80 characters; empty restores the default)</label><input id="rename-run-input" maxLength={80} value={renameValue} onChange={e => setRenameValue(e.target.value)} /><div className="header-actions"><button type="button" onClick={() => setRenaming(false)}>Cancel</button><button className="primary" type="submit" disabled={sending}>Save</button></div></div></form></dialog>}
    <main id="main-content" tabIndex={-1}><header className="topbar"><div><span>Workspace</span><span className="slash">/</span><b>{page === 'Run' ? runTitle(run?.state.run_name, run?.state.repo_url, id) : page}</b></div><div className="top-actions"><span className="local-status"><span className={`dot ${connectionError ? 'offline' : ''}`} />{connectionError ? 'Connection needs attention' : 'Local backend'}</span></div></header>
      <div className="main-content">
        {(error || connectionError) && <div className="notice bad" role="alert"><strong>{run && id ? `[${runTitle(run.state.run_name, run.state.repo_url, id)} · ${id}] ` : ''}{error || connectionError}</strong>{!error && connectionError && <button onClick={() => { void refresh().catch(e => setConnectionError(e.message)); }}>Refresh connection</button>}</div>}
        {page === 'Runs' && <><div className="page-header"><div><span className="eyebrow">Test engineering workspace</span><h1>Repository runs</h1><p>Review each stage. Execute with approval. Keep the evidence.</p></div><button className="primary" onClick={() => navigate('New run')}>＋ Start a run</button></div><Metrics values={{ saved_runs: runs.length, awaiting_review: runs.filter(r => r.pending).length, active_operations: serviceBusy ? 1 : 0 }} /><Panel title="Run history" storageKey="runs:history"><div className="table-tools"><input aria-label="Search runs" type="search" placeholder="Search repositories, run IDs or status…" value={search} onChange={e => setSearch(e.target.value)} /><small>{visibleRuns.length} runs</small></div>{!runs.length ? <div className="empty-state"><span>◇</span><h2>Your first run starts here</h2><p>Add a public GitHub repository. The harness will ask before cloning, installing or running tests.</p><button className="primary" onClick={() => navigate('New run')}>Start repository review</button></div> : <div className="run-list">{visibleRuns.map(r => <div className="run-row" key={r.id}><div className="repo-icon">◇</div><div className="run-name"><b>{runTitle(r.run_name, r.repo_url, r.id)}</b><small>{repoName(r.repo_url || r.id)} · {r.id} · Updated {new Date(r.updated * 1000).toLocaleString()}</small><progress aria-label={`Workflow completion ${r.id}`} max={100} value={Number(record(record(r).progress).percent) || 0} /><small>{text(record(record(r).progress).percent) || 0}% of approved workflow stages</small></div><span className="run-stage">{label(r.stage)}</span><Badge value={r.busy && r.status !== 'stopping' ? 'working' : r.verdict || r.status} /><button disabled={r.status === 'unreadable'} onClick={() => navigate('Run', r.id, r.status === 'complete' ? 'QA report' : 'Review')}>Open run →</button><button className="delete-run small" aria-label={`Delete run ${r.id}`} disabled={sending || serviceBusy || r.busy} onClick={e => { rememberDelete(e.currentTarget); setDeleting(r); }}>Delete</button></div>)}</div>}</Panel>{serviceBusy && <p className="notice">An operation is running. You can inspect saved runs; new actions wait until it reaches a gate.</p>}</>}
        {page === 'New run' && <><div className="page-header"><div><span className="eyebrow">New review</span><h1>New test run</h1><p>Requirements, design, executable tests and a QA report from repository evidence.</p></div></div><SplitWorkspace split={split} setSplit={setSplit}><Panel title="Repository details" storageKey="new:form"><form onSubmit={start}><fieldset className="radio-group"><legend>Mode</legend><label className="check"><input type="radio" name="run_mode" value="brownfield" checked={mode === 'brownfield'} onChange={() => setMode('brownfield')} />Brownfield repository</label><label className="check"><input type="radio" name="run_mode" value="greenfield" checked={mode === 'greenfield'} onChange={() => setMode('greenfield')} />Greenfield specs</label></fieldset><fieldset className="radio-group"><legend>Test types</legend><label className="check"><input type="checkbox" checked={types.unit} onChange={e => setTypes(t => ({ ...t, unit: e.target.checked }))} />Unit</label><label className="check"><input type="checkbox" checked={types.integration} onChange={e => setTypes(t => ({ ...t, integration: e.target.checked }))} />Integration</label></fieldset><p className="field-help">Unit and integration tests for the complete application. At least one is required.</p><label>Run name <span className="muted">optional</span><input name="run_name" maxLength={80} placeholder="owner/repository · YYYY-MM-DD HH:MM" /></label><p className="field-help">Shown in the run list, reports and generated test headers so each result can be traced to this run.</p><label>Public GitHub repository URL{mode === 'greenfield' && <> <span className="muted">optional</span></>}<input name="repo_url" type="text" inputMode="url" required={mode === 'brownfield'} placeholder="https://github.com/owner/repository" onChange={e => inspectRepo(e.target.value)} />{inspecting === 'loading' && <p className="field-help" role="status"><span className="spinner" aria-hidden="true" /> Inspecting repository…</p>}{inspecting === 'filled' && <p className="field-help" role="status">Auto-filled from repository — review before starting.</p>}</label><p className="field-help">Repository page, clone URL, supported branch/tag URL or git clone followed by one URL. Commands are parsed as text, never executed.</p><label>Repository knowledge <span className="muted">optional</span><textarea name="repo_knowledge" value={prefill.repo_knowledge} onChange={e => edit('repo_knowledge', e.target.value)} maxLength={32000} rows={6} placeholder="Paste README setup notes, required tool versions, install/start commands and known prerequisites…" /></label><p className="field-help">Used as unverified context for planning and manual setup guidance. Pasted commands are never executed automatically.</p>{mode === 'greenfield' && <><label>Specification documents<input name="documents" type="file" multiple accept=".md,.txt,.docx,.pdf" onChange={e => setFormError(documentProblem(Array.from(e.currentTarget.files ?? [])))} /></label><p className="field-help">Up to 10 files (md, txt, docx, pdf), 10 MB in total.</p><fieldset className="jira-fields"><legend>Jira <span className="muted">optional</span></legend><label>Site URL<input name="jira_base_url" type="url" placeholder="https://your-site.atlassian.net" /></label><label>JQL<input name="jira_jql" placeholder="project = PROJ AND type = Story" /></label><label>Issue keys<input name="jira_issue_keys" placeholder="PROJ-1, PROJ-2" /></label><label>Email<input name="jira_email" type="email" autoComplete="off" /></label><label>API token<input name="jira_api_token" type="password" autoComplete="off" /></label><p className="field-help">The token is sent once for fetching stories and is never saved with the run.</p></fieldset></>}<label>Test cases per requirement<input name="case_budget" type="number" min={1} max={10} step={1} defaultValue={config?.case_budget_default ?? 2} key={`budget:${config?.case_budget_default ?? 2}`} required /></label><p className="field-help">Fewer cases = faster run. You can change this until test design is approved.</p><div className="notice">You will review the clone, installation and execution actions before they happen.</div>{formError && <p className="notice bad field-error" role="alert">{formError}</p>}<button className="primary" disabled={sending || serviceBusy || !config?.key_configured}>{sending ? 'Creating run…' : 'Start repository review →'}</button>{config && !config.key_configured && <p className="bad">No API key yet. <button type="button" className="link" onClick={() => navigate('Configuration')}>Add it in Configuration › Model &amp; API key</button>.</p>}</form></Panel><Panel title="How the review works" storageKey="new:journey"><ol className="journey">{stages.map(s => <li key={s}>{label(s)}</li>)}</ol><p>The harness selects testing frameworks from the detected stack. Missing prerequisites and unsupported execution paths remain visible.</p><p className="muted">Approved commands run as your local user. Isolated dependencies are not a security sandbox.</p></Panel></SplitWorkspace></>}
        {page === 'Skills' && <Skills />}
        {page === 'Prompts' && <Prompts />}
        {page === 'Configuration' && config && <><div className="page-header"><div><h1>Configuration</h1><p>Current backend settings and local workspace preferences.</p></div></div><ModelSettings stages={config.stages} onSaved={() => void refresh()} /><McpServers /><Panel title="Daily usage" storageKey="config:daily"><Metrics values={config.daily} /><p>Shared across runs for this OS user; resets at execution-host local midnight.</p></Panel><Panel title="Debugging logs" storageKey="config:logs"><DataView value={{ service_and_setup_logs: record(config).service_log_directory, run_evidence: record(config).run_directory }} /><p>Each run’s Logs, Audit and Observability show workflow activity. Service logs also capture startup failures before a run exists.</p></Panel></>}
        {page === 'Run' && !run && !error && <div className="empty-state">Loading run evidence…</div>}
        {page === 'Run' && run && <><div className="page-header"><div><span className="eyebrow">Run {id}</span><h1>{runTitle(run.state.run_name, run.state.repo_url, id)}</h1><p>{repoName(text(run.state.repo_url))} · {text(run.state.repo_ref || 'Default branch')} · <Badge value={run.busy && run.status !== 'stopping' ? 'working' : run.status} /> <span className="badge">{text(run.state.run_mode || 'brownfield')}</span>{(Array.isArray(run.state.test_types) ? run.state.test_types : ['unit', 'integration']).map(t => <span className="badge" key={text(t)}>{text(t)}</span>)}</p></div><div className="header-actions">{run.has_zip && <a className="button primary" href={download(id, 'zip')} download>Download deliverable ZIP ↓</a>}{run.busy && run.status !== 'stopping' && <button className="danger-button" disabled={sending} onClick={e => { rememberStop(e.currentTarget); setConfirmStop(true); }}>Stop run</button>}<button disabled={sending || run.busy || run.service_busy} onClick={e => { rememberRename(e.currentTarget); setRenameValue(typeof run.state.run_name === 'string' ? run.state.run_name : ''); setRenaming(true); }}>Rename run</button><button disabled={sending || run.service_busy} onClick={() => void act(`runs/${id}/reconnect`, {})}>Reconnect</button><button disabled={sending || run.service_busy} onClick={() => setRerunStage(selectedStage || active || 'repo_analysis')}>Rerun from step ↻</button></div></div>
          {rerunStage !== null && <section className="rerun-choice" aria-label="Rerun workflow step"><h2>Rerun from a step</h2><p>Previous evidence is retained in the run’s attempts folder. This step and all later approvals are reset. Clone, installation and execution still require their normal review gates.</p><div className="step-picker">{(run.state.stages as string[]).map((stage, index, all) => <button key={stage} aria-pressed={rerunStage === stage} disabled={sending || run.service_busy || all.slice(0,index).some(s => !record(run.state.approved)[s])} onClick={() => setRerunStage(stage)}>{label(stage)}</button>)}</div><div className="header-actions"><button onClick={() => setRerunStage(null)}>Cancel</button><button className="primary" disabled={sending || run.service_busy} onClick={async () => { const result = await act(`runs/${id}/rerun`, { stage: rerunStage, request_id: run.pending?.id || null }); if(result) { setRerunStage(null); changeTab('Review'); } }}>Rerun {label(rerunStage)} →</button></div></section>}
          <div className="run-completion"><span>Workflow progress · {text(record(record(run).progress).percent) || 0}%</span><progress aria-label="Workflow completion" max={100} value={Number(record(record(run).progress).percent) || 0} /><small>Approved stage milestones; test verdict is reported separately.</small></div>
          <div className="stage-progress" aria-label="Workflow stages">{(Array.isArray(run.state.stages) ? run.state.stages as string[] : stages).map((s, i) => <button type="button" key={s} onClick={() => showStage(s)} aria-label={`View ${label(s)} evidence`} aria-pressed={selectedStage === s} className={`${active === s ? 'current' : ''} ${failedStage === s ? 'failed' : record(run.state.approved)[s] ? 'done' : ''}`}><span>{failedStage === s ? '!' : record(run.state.approved)[s] ? '✓' : i + 1}</span><small>{label(s)}</small>{etaOf(s).predicted_s != null ? <small>{etaOf(s).status === 'done' ? 'took ' : '~'}{minutes(Number(etaOf(s).predicted_s))}</small> : null}</button>)}</div>
          {finish ? <p className="muted">Estimated completion ~{new Date(String(finish)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} (≈{minutes(Number(record(record(run.progress).eta).total_remaining_s))} left)</p> : null}
          {(run.busy || sending) && <WorkingStatus key={`${id}:${active}`} stage={active} eta={etaOf(active)} operation={record(run.operation)} directory={run.run_directory} cancel={run.busy && record(run.operation).kind === 'clone' && record(run.operation).status === 'running' ? () => void act(`runs/${id}/cancel-clone`, {}) : undefined} sending={sending} />}
          {run.run_directory && !run.busy && <p className="field-help">Saved run directory: <code>{run.run_directory}</code></p>}
          {errorCount > 0 && <div className="notice bad" role="alert">{errorCount} errors in this run{genFailed > 0 ? ` (${genFailed} generation batches failed)` : ''} <button onClick={() => changeTab('Errors')}>Open Errors tab</button></div>}
          {!run.busy && run.pending?.error && <div className="notice bad" role="alert">{firstSentence(run.pending.error)} Details in Errors tab.</div>}
          {run.status === 'interrupted' && <div className="notice bad">An approved operation was interrupted. Automatic replay is blocked. Inspect the audit and retained artifacts; reconnecting does not repeat the action.</div>}
          {run.status === 'stopping' && <div className="notice" role="status">Stopping… waiting for running work to end. Approved evidence is kept. If this takes more than a minute, restart the app; the run then shows as interrupted and can be resumed with Reconnect.</div>}
          {run.status === 'stopped' && <div className="notice">Workflow stopped. Approved evidence remains available.</div>}
          {run.state.completed_reason ? <div className="notice"><Badge value={record(record(artifacts(run).test_execution).qa_report).verdict} /> {text(run.state.completed_reason)}</div> : null}
          {!record(run.state.approved).test_design && Array.isArray(run.state.stages) && (run.state.stages as string[]).includes('test_design') && record(record(run.progress).case_budget).rows ? <CaseBudget key={`${id}:${text(record(record(run.progress).case_budget).current)}`} budget={record(record(run.progress).case_budget)} id={id} disabled={sending || run.busy || run.service_busy} act={act} /> : null}
          <div className="tabs" aria-label="Run views">{tabs.map(t => <button className={tab === t ? 'selected' : ''} aria-pressed={tab === t} key={t} onClick={() => changeTab(t)}>{t === 'Errors' ? `Errors${errorCount ? ` ${errorCount}` : ''}` : t}{t === 'Review' && run.actions.length > 0 && <span className="tab-dot" />}</button>)}</div>
          {tab === 'Review' && <SplitWorkspace split={split} setSplit={setSplit}><div><Review run={run} /><Panel title="Run context" storageKey={`${id}:context`} defaultOpen={false}><DataView value={{ repository: run.state.repo_url, application_url: run.state.app_url, budget: run.state.budget, repository_knowledge: run.state.repo_knowledge, environment: run.state.environment }} /></Panel></div><Decision key={run.pending?.id || id} run={run} sending={sending} respond={response} /></SplitWorkspace>}
          {tab === 'Errors' && <RunErrors run={run} id={id} />}
          {tab === 'Prompts' && <Prompts key={`${id}:${run.busy}`} run={run} />}
          {tab === 'Artifacts' && <Artifacts run={run} selectedStage={selectedStage} stageVisit={stageVisit} />}
          {tab === 'Generated tests' && <Generated run={run} />}
          {tab === 'QA report' && <QA run={run} />}
          {tab === 'Test reports' && <Reports key={`${id}:reports`} run={run} theme={theme} />}
          {tab === 'Logs' && <Reports key={`${id}:logs`} run={run} logs theme={theme} />}
          {tab === 'Observability' && <Evidence key={`${id}:obs`} id={id} kind="observability" busy={run.busy} run={run} />}
          {tab === 'Audit' && <Evidence key={`${id}:audit`} id={id} kind="audit" busy={run.busy} run={run} />}
        </>}
      </div>
    </main>
  </div>;
}

function SplitWorkspace({ children, split, setSplit }: { children: [ReactNode, ReactNode]; split: number; setSplit: (value: number) => void }) {
  const grid = useRef<HTMLDivElement>(null);
  return <div ref={grid} className="workspace-grid">{children[0]}<ResizeHandle label="Resize workspace columns" start={() => {
    const width = grid.current!.clientWidth; const initial = split;
    return dx => setSplit(Math.max(45, Math.min(80, initial + dx / width * 100)));
  }} />{children[1]}</div>;
}

function repoName(url: string) { return url.replace(/^https?:\/\/github\.com\//, '').replace(/\.git$/, ''); }
function CaseBudget({ budget, id, disabled, act }: { budget: Row; id: string; disabled: boolean; act: (path: string, body: Row) => Promise<unknown> }) {
  const [value, setValue] = useState(Number(budget.current) || 2);
  const recommended = Number(budget.recommended);
  return <Panel title="Case budget" storageKey={`${id}:case-budget`}>
    <p>Current: <strong>{text(budget.current)}</strong> test case(s) per requirement. Recommended for ~1 hour of generation: <strong>{recommended}</strong>. Re-run test design to apply a changed budget.</p>
    <div className="header-actions"><label>Cases per requirement<select value={value} disabled={disabled} onChange={e => setValue(Number(e.target.value))}>{Array.from({ length: 10 }, (_, i) => i + 1).map(k => <option key={k} value={k}>{k}</option>)}</select></label><button disabled={disabled || value === Number(budget.current)} onClick={() => void act(`runs/${id}/case-budget`, { per_requirement: value })}>Save</button></div>
    {!rows(budget.rows).some(r => Number(r.cases) > 0) ? <p className="notice">Time estimates appear after the requirements step, when the requirement count is known.</p> : <div className="table-scroll" tabIndex={0} role="region" aria-label="Case budget estimates"><table className="data-table"><thead><tr><th>k</th><th>~cases</th><th>~batches</th><th>~generation time</th><th>~design time</th></tr></thead><tbody>{rows(budget.rows).map(r => <tr key={text(r.per_requirement)} className={Number(r.per_requirement) === recommended ? 'selected' : ''} aria-current={Number(r.per_requirement) === recommended ? 'true' : undefined}><td>{text(r.per_requirement)}{Number(r.per_requirement) === recommended ? ' (recommended)' : ''}</td><td>{text(r.cases)}</td><td>{text(r.batches)}</td><td>~{minutes(Number(r.generation_min) * 60)}</td><td>~{minutes(Number(r.design_min) * 60)}</td></tr>)}</tbody></table></div>}
    <p className="field-help">Estimates use measured or historical batch time. Cases beyond the budget are dropped by priority (P0 first).</p>
  </Panel>;
}

function WorkingStatus({ stage, eta, operation, directory, cancel, sending }: { stage: string; eta: Row; operation: Row; directory?: string; cancel?: () => void; sending: boolean }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => { const start = Date.now(); const timer = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000); return () => clearInterval(timer); }, [stage]);
  const current = operation.stage === stage ? operation : {};
  const batch = eta.batch ? record(eta.batch) : null;
  const cloning = current.kind === 'clone' && current.status === 'running';
  return <div className="notice working" role="status" aria-live="polite"><span className="spinner" aria-hidden="true" /><div><strong>{cloning ? 'Cloning repository from GitHub…' : stage === 'repo_analysis' ? 'Inspecting repository source…' : stage === 'test_generation' ? 'Generating tests…' : `${label(stage || 'workflow')} is running…`}</strong><div>{cloning ? `Git attempt ${text(current.attempt)} · ${text(current.elapsed_seconds)}s elapsed · timeout ${text(current.timeout_seconds)}s` : `${batch ? `Batch ${text(batch.done)} of ${text(batch.total)}${eta.remaining_s != null ? ` · ~${minutes(Number(eta.remaining_s))} left of ~${minutes(Number(eta.predicted_s))}` : ''}` : eta.remaining_s != null ? `~${minutes(Number(eta.remaining_s))} left of ~${minutes(Number(eta.predicted_s))}` : 'Waiting for the next saved output'} · ${minutes(eta.elapsed_s != null ? Number(eta.elapsed_s) : elapsed)} active.`} Updates refresh automatically.</div>{current.latest_output ? <pre className="live-clone-output">{text(current.latest_output)}</pre> : null}{directory && <small>Actual run directory: <code>{directory}</code></small>}{cancel && <div><button disabled={sending} onClick={cancel}>{sending ? 'Cancelling…' : 'Cancel clone'}</button><small>Stops this run’s Git process tree; preserves output for retry.</small></div>}</div></div>;
}
function Review({ run }: { run: Run }) {
  const p = run.pending;
  if (!p) return <Panel title="Review" storageKey={`${run.id}:review`}><p>No pending approval for this run.</p></Panel>;
  const value = p.kind === 'review' ? p.details.artifact : p.details;
  const executing = run.busy && run.pending_consumed && p.kind === 'tool';
  return <Panel title={executing ? p.title.replace(/^Approve /, '') : p.title || label(p.stage)} storageKey={`${run.id}:review:${p.stage}`} actions={p.kind === 'review' ? <ExportButtons id={run.id} kind={p.stage} /> : undefined}>
    <div className="review-meta"><Badge value={executing ? 'Approved action in progress' : p.kind === 'tool' ? 'Action approval' : p.kind === 'error' ? 'Error gate' : 'Artifact review'} /><span>{text(p.details.role || p.details.persona || '')}</span></div>
    {p.kind === 'tool' && p.details.effects ? <div className="notice">{text(p.details.effects)}</div> : null}
    {p.kind === 'tool' && p.stage !== 'repo_analysis' && <p className="bad">Commands execute locally as your user. Review all commands and download sources before approval.</p>}
    {record(value).setup_guide ? <SetupGuide guide={record(record(value).setup_guide)} /> : null}
    {p.kind === 'review' && p.stage === 'test_design' && <TestCaseTable design={record(value)} requirements={record(artifacts(run).requirements)} />}
    <DataView value={Object.fromEntries(Object.entries(record(value)).filter(([key]) => key !== 'setup_guide'))} />
    <details className="raw-json"><summary>Raw JSON</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>
  </Panel>;
}
function Decision({ run, sending, respond }: { run: Run; sending: boolean; respond: (decision: string, feedback: string) => unknown }) {
  const [feedback, setFeedback] = useState(''); const p = run.pending;
  const revisionRequired = p && !['environment_setup', 'test_execution'].includes(p.stage);
  const names: Record<string, string> = { approve: p?.kind === 'tool' ? p.stage === 'environment_setup' ? 'Install in isolated environment' : p.stage === 'test_execution' ? 'Run tests here' : 'Approve repository inspection' : 'Approve and proceed', revise: p?.stage === 'test_execution' ? 'Regenerate failing tests' : p?.stage === 'environment_setup' ? 'Re-check environment' : 'Request revision', stop: 'Stop workflow', retry: 'Retry stage', deny: 'Deny action', skip: 'Skip action' };
  return <div><Panel title="Your decision" storageKey={`${run.id}:decision`}>
    {run.actions.length ? <><p>{p?.kind === 'tool' ? 'Approval authorizes only the action shown in this request.' : 'Review the complete evidence before continuing.'}</p>{run.actions.includes('revise') && <label>Modification instructions<textarea maxLength={20000} value={feedback} onChange={e => setFeedback(e.target.value)} placeholder={revisionRequired ? 'Required when requesting revision…' : 'Optional instructions…'} /></label>}<div className="decision-buttons">{run.actions.map(action => <button key={action} className={action === 'approve' ? 'primary' : action === 'stop' ? 'danger-button' : ''} disabled={sending || run.service_busy || (action === 'revise' && !!revisionRequired && !feedback.trim())} onClick={() => respond(action, feedback)}>{names[action]}</button>)}</div><small>Request {p?.id.slice(0, 12)} · revision {text(p?.details.revision || '—')}</small></> : <p>{run.busy ? 'Decisions become available when the operation reaches its next gate.' : run.service_busy ? 'Another run is working. Wait before responding.' : 'No available approval. Inspect artifacts and audit for this run.'}</p>}
  </Panel><Panel title="Agent & usage" storageKey={`${run.id}:agent`}><DataView value={{ persona: p?.details.persona, goal: p?.details.goal, tokens: record(run.state.budget).reported_tokens, provider_calls: record(run.state.budget).calls }} /></Panel></div>;
}
function Artifacts({ run, selectedStage = '', stageVisit = 0 }: { run: Run; selectedStage?: string; stageVisit?: number }) {
  const data = artifacts(run);
  return <>{selectedStage && data[selectedStage] === undefined && <p className="notice">{label(selectedStage)} has no saved artifact yet. You can revisit this stage while the run continues.</p>}{!Object.keys(data).length && <p className="empty-inline">No artifacts yet.</p>}{stages.filter(s => data[s] !== undefined && (!selectedStage || s === selectedStage)).map(stage => <Panel key={stage} title={`${label(stage)} · revision ${text(record(run.state.revisions)[stage])}`} storageKey={`${run.id}:artifact:${stage}`} revealKey={selectedStage ? `${selectedStage}:${stageVisit}` : undefined} defaultOpen={!!selectedStage || stage === run.pending?.stage} actions={<ExportButtons id={run.id} kind={stage} />}>{stage === 'test_design' && <TestCaseTable design={record(data[stage])} requirements={record(data.requirements)} />}<DataView value={data[stage]} /><details className="raw-json"><summary>Raw JSON</summary><pre>{JSON.stringify(data[stage], null, 2)}</pre></details></Panel>)}</>;
}
function Generated({ run }: { run: Run }) {
  const data = record(artifacts(run).test_generation); const files = rows(data.files); const validation = record(data.validation);
  const latestReviews = new Map<string, Row>(); rows(run.state.test_reviews).forEach(batch => rows(batch.reviews).forEach(review => latestReviews.set(text(review.case_id), review))); const reviews = rows(data.case_reviews).length ? rows(data.case_reviews) : [...latestReviews.values()].sort((a, b) => Number(b.confidence) - Number(a.confidence));
  if (!Object.keys(data).length) return <><p className="empty-inline">Generated test source is not available yet.</p>{reviews.length > 0 && <Panel title="Independent test review" storageKey={`${run.id}:test-review`}><p>Latest source-based review per case. These scores are judgments, not pass probabilities.</p><DataTable data={reviews} columns={['confidence', 'case_id', 'verdict', 'reason', 'source_files']} /></Panel>}</>;
  return <><Metrics values={{ generated_files: validation.files || files.length, test_declarations: validation.test_declarations, covered_cases: Array.isArray(validation.covered_case_ids) ? validation.covered_case_ids.length : 0 }} /><Panel title="Case catalog" storageKey={`${run.id}:catalog`}><p>Independent reviewer scores are source-based judgments, not pass probabilities. Cases are ranked by confidence; execution results remain authoritative.</p><DataTable data={rows(data.case_catalog)} columns={['confidence', 'title', 'reviewer_verdict', 'reviewer_reason', 'priority', 'expected_result']} /></Panel><Panel title="Independent test review" storageKey={`${run.id}:test-review`}><DataTable data={reviews} columns={['confidence', 'case_id', 'verdict', 'reason', 'source_files']} /></Panel>{files.map((file, index) => <Panel key={index} title={text(file.path)} storageKey={`${run.id}:source:${index}`} defaultOpen={index === 0} actions={<a className="button small" href={download(run.id, 'source', 'json', index)} download>Source ↓</a>}><p>Cases: {text(file.case_ids)}</p><pre className="source-code">{text(file.content)}</pre></Panel>)}<Panel title="Generation validation" storageKey={`${run.id}:validation`} defaultOpen={false}><DataView value={data.validation} /></Panel></>;
}
function QA({ run }: { run: Run }) {
  const execution = record(artifacts(run).test_execution); const qa = record(execution.qa_report);
  if (!Object.keys(qa).length) return <div className="empty-state"><span>◫</span><h2>Your results will appear here</h2><p>Approve execution to produce case results, requirement coverage and failure evidence.</p></div>;
  const updates = record(qa.updates);
  const failures = rows(qa.cases).filter(c => ['failed', 'error', 'not_executed'].includes(text(c.status)));
  const actions = [...rows(updates.generated_test_files_to_update), ...rows(updates.repository_files_to_review)];
  return <><div className="qa-summary"><div><span className="eyebrow">Saved execution verdict</span><h2><Badge value={qa.verdict} /></h2><p>Execution: {text(qa.execution_status)} · Runner: {text(qa.adapter)}</p></div><ExportButtons id={run.id} kind="qa" /></div><QaDocument run={run} />{qa.verdict === 'INCONCLUSIVE' && <p className="notice bad">Tests did not produce sufficient execution evidence. Inconclusive is never a pass.</p>}<Metrics values={record(qa.case_status_counts)} />
    <StepReports run={run} />
    <Panel title="Why tests failed & what to improve" storageKey={`${run.id}:qa:failures`}>
      <p className="field-help">Observed output and recommendations from the saved QA report. A failing assertion needs review of both application behavior and the test expectation.</p>
      {failures.length ? <div className="failure-cards">{failures.map((c, i) => {
        const related = actions.filter(a => a.case_id === c.id);
        return <article className="failure-card" key={text(c.id) || i}><header><div><span className="eyebrow">{text(c.id)} · {label(c.level)} · {runTitle(run.state.run_name, run.state.repo_url, run.id)} ({run.id})</span><h3>{text(c.title)}</h3></div><Badge value={c.status} /></header><code className="failure-location">{text(c.file || 'No generated file recorded')}</code><div className="failure-reason"><b>Observed failure</b><pre>{text(c.message || 'No failure output was recorded. This case did not execute; inspect the execution logs and environment status.')}</pre></div><div className="failure-next"><span aria-hidden="true">↗</span><div><b>Recommended next step</b>{related.length ? related.map((a, n) => <div key={n}><span className="badge">{label(a.classification)}</span><p>{text(a.action)}</p><small>{text(a.file)}{a.lines ? ` · lines ${text(a.lines)}` : a.line ? `:${text(a.line)}` : ''}</small></div>) : <p>Inspect this case’s output and the execution logs. Confirm the environment and reproduce the behavior before changing the application or its test.</p>}</div></div></article>;
      })}</div> : <div className="success-note"><span>✓</span><div><b>No failed cases in the saved QA report</b><p>Review requirement coverage and warnings before treating this run as complete coverage.</p></div></div>}
    </Panel>
    <Panel title="Case results" storageKey={`${run.id}:qa:Case results`}><DataTable data={rows(qa.cases)} columns={['id', 'title', 'level', 'actor', 'priority', 'status', 'file', 'confidence']} /></Panel>
    <Panel title="Repository evidence scope" storageKey={`${run.id}:qa:scope`}><p>Generated suite: <Badge value={qa.generated_verdict || qa.verdict} />. This verdict does not certify the complete application.</p><DataView value={{ source_intake: qa.source_intake, runtime_coverage: qa.runtime_coverage }} /><DataTable data={rows(qa.baseline_regression)} columns={['scope', 'verdict', 'tests', 'reason']} />{rows(qa.baseline_regression).map((scope, i) => <details key={i}><summary>{label(scope.scope)} · {text(scope.verdict)}</summary><DataTable data={rows(scope.cases)} columns={['classname', 'name', 'status', 'message']} /></details>)}</Panel>
    <Panel title="Requirement traceability" storageKey={`${run.id}:qa:Requirement coverage`}><p>Links between requirements and generated tests. This is not measured line, branch or complete behavior coverage. A requirement is verified only when its linked tests passed.</p><DataTable data={rows(qa.requirement_coverage)} columns={['state', 'category', 'actor', 'text']} /></Panel>
    <Panel title="Improvement worklist" storageKey={`${run.id}:qa:updates`}><DataTable data={[...actions, ...rows(updates.requirements_needing_tests), ...rows(updates.environment_actions)]} columns={['classification', 'file', 'case', 'detail', 'action']} /></Panel>
    <Panel title="Warnings & limitations" storageKey={`${run.id}:qa:limits`}><DataView value={{ warnings: qa.warnings, limitations: qa.limitations, unmapped_results: qa.unmapped_results }} /></Panel>
    <Panel title="Skill contract checks" storageKey={`${run.id}:qa:checks`}><DataView value={qa.skill_checks} /></Panel>
    <Panel title="Complete QA evidence" storageKey={`${run.id}:qa:full`} defaultOpen={false}><DataView value={qa} /></Panel>{run.actions.includes('revise') && run.pending?.stage === 'test_execution' && <p className="notice">Use the Review tab to regenerate failing tests. New source and execution require approval again.</p>}</>;
}
function Evidence({ id, kind, busy, run }: { id: string; kind: 'audit' | 'observability'; busy: boolean; run?: Run }) {
  const [data, setData] = useState<Row | null>(null); const [error, setError] = useState(''); const [event, setEvent] = useState(''); const [stage, setStage] = useState('');
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const load = async () => { try { const next = await api<Row>(`runs/${id}/${kind}`, undefined, controller.signal); if (!controller.signal.aborted) setData(next); setError(''); } catch (e) { if (!controller.signal.aborted) setError((e as Error).message); } if (!controller.signal.aborted) timer = setTimeout(load, 2500); };
    void load(); return () => { controller.abort(); clearTimeout(timer); };
  }, [id, kind, busy, run?.pending?.id]);
  if (error) return <p className="notice bad" role="alert">{error} Other run views remain available.</p>;
  if (!data) return <p className="empty-inline">Loading persisted evidence…</p>;
  if (kind === 'observability') return <Observability id={id} report={data} />;
  const events = rows(data.events); const filtered = events.filter(e => (!event || e.event === event) && (!stage || e.stage === stage));
  return <><Panel title="Approval & activity audit" storageKey={`${id}:audit:events`} actions={<a className="button small" href={download(id, 'audit')} download>Audit JSONL ↓</a>}><div className="filters"><Select label="Event" value={event} set={setEvent} options={events.map(e => text(e.event))} /><Select label="Stage" value={stage} set={setStage} options={events.map(e => text(e.stage || ''))} /></div>{Number(data.malformed_lines) > 0 && <p className="notice">{text(data.malformed_lines)} malformed lines were omitted from this view.</p>}<div className="audit-feed">{filtered.slice().reverse().slice(0, 100).map((e, i) => <details key={i}><summary><span className="audit-icon">{e.decision === 'approve' ? '✓' : '·'}</span><span><b>{label(e.event)}</b><small>{label(e.stage)} · {text(e.at)}{e.decision ? ` · ${label(e.decision)}` : ''}</small></span></summary><DataView value={e} /></details>)}</div><details><summary>Search all audit records</summary><DataTable data={filtered} columns={['at', 'event', 'stage', 'decision', 'request_id']} /></details></Panel><Panel title="Runtime & skill provenance" storageKey={`${id}:audit:provenance`}><DataView value={{ runtime: run?.state.runtime_provenance, skills: run?.state.skill_provenance, skill_tool_trace: run?.state.skill_tool_trace, invocations: run?.state.invocations }} /></Panel></>;
}

function SetupGuide({ guide }: { guide: Record<string, unknown> }) {
  return <section aria-label="Repository setup guide"><h3>Step-by-step repository setup</h3><p>{text(guide.platform)} · {text(guide.status)}</p><ol className="setup-guide">{(Array.isArray(guide.steps) ? guide.steps : []).map((item, index) => { const step = record(item); return <li key={index}><h4>{text(step.title)}</h4>{step.working_directory ? <p>Working directory: <code>{text(step.working_directory)}</code></p> : null}{step.command ? <pre>{text(step.command)}</pre> : null}{Object.keys(record(step.environment)).length ? <DataView value={{ required_environment: step.environment }} /> : null}{step.instructions ? <p>{text(step.instructions)}</p> : null}</li>; })}</ol>{guide.repository_knowledge ? <details><summary>Pasted repository knowledge</summary><p>{text(guide.knowledge_notice)}</p><pre>{text(guide.repository_knowledge)}</pre></details> : null}</section>;
}
