'use client';
import { useCallback, useEffect, useRef, useState, type CSSProperties, type FormEvent, type ReactNode } from 'react';
import { api, artifacts, download, label, record, rows, text, type Config, type Row, type Run, type RunSummary } from '@/lib/harness';
import { Badge, DataTable, DataView, ExportButtons, Metrics, Panel, ResizeHandle, usePreference } from './data';
import Skills from './skills';
import Reports from './reports';
import Prompts from './prompts';
import Observability, { Select } from './observability';

const pages: Record<string, string> = { skills: 'Skills', prompts: 'Prompts', configuration: 'Configuration', new: 'New run' };
const tabs = ['Review', 'Artifacts', 'Generated tests', 'QA report', 'Test reports', 'Logs', 'Observability', 'Audit', 'Prompts'];
const stages = ['repo_analysis', 'requirements', 'environment_setup', 'test_planning', 'test_design', 'test_generation', 'test_execution'];

export default function Workspace() {
  const [page, setPage] = useState('Runs');
  const [id, setId] = useState('');
  const [tab, setTab] = useState('Review');
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [serviceBusy, setServiceBusy] = useState(false);
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [sending, setSending] = useState(false);
  const [search, setSearch] = useState('');
  const [deleting, setDeleting] = useState<RunSummary | null>(null);
  const deleteDialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (deleting) deleteDialog.current?.showModal(); }, [deleting]);
  const [sidebar, setSidebar] = usePreference<number>('maf:sidebar', 236);
  const [split, setSplit] = usePreference<number>('maf:split', 68);
  const [theme, setTheme] = usePreference<string>('maf:theme', 'light');

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const key = params.get('run');
    if (key && /^[A-Za-z0-9_-]{1,100}$/.test(key)) { setId(key); setPage('Run'); }
    if (!key && pages[params.get('page') || '']) setPage(pages[params.get('page') || '']);
    const value = params.get('tab'); if (value && tabs.includes(value)) setTab(value);
    const pop = () => { const p = new URLSearchParams(location.search); setId(p.get('run') || ''); setPage(p.has('run') ? 'Run' : pages[p.get('page') || ''] || 'Runs'); setTab(tabs.includes(p.get('tab') || '') ? p.get('tab')! : 'Review'); };
    window.addEventListener('popstate', pop); return () => window.removeEventListener('popstate', pop);
  }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme === 'dark' ? 'dark' : 'light'; }, [theme]);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const [listing, settings, current] = await Promise.all([
      api<{ runs: RunSummary[]; busy: boolean }>('runs', undefined, signal),
      api<Config>('config', undefined, signal),
      id ? api<Run>(`runs/${id}`, undefined, signal) : Promise.resolve(null),
    ]);
    setRuns(listing.runs); setServiceBusy(listing.busy); setConfig(settings); setRun(current); setConnectionError('');
  }, [id]);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const poll = async () => { try { await refresh(controller.signal); } catch (e) { if (!controller.signal.aborted) setConnectionError((e as Error).message); } if (!controller.signal.aborted) timer = setTimeout(poll, 2000); };
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [refresh]);
  const navigate = (next: string, runId = '', runTab = 'Review') => {
    setPage(next); setError('');
    if (next === 'Run') { if (runId !== id) setRun(null); setId(runId); setTab(runTab); history.pushState({}, '', `?run=${encodeURIComponent(runId)}&tab=${encodeURIComponent(runTab)}`); }
    else { setId(''); setRun(null); history.pushState({}, '', Object.entries(pages).some(([, name]) => name === next) ? `/?page=${Object.entries(pages).find(([, name]) => name === next)![0]}` : '/'); }
  };
  const changeTab = (next: string) => { setTab(next); history.replaceState({}, '', `?run=${encodeURIComponent(id)}&tab=${encodeURIComponent(next)}`); };
  const act = async (path: string, body: Row) => {
    setSending(true); setError('');
    try { const result = await api<{ id: string }>(path, body); await refresh(); return result; }
    catch (e) { setError((e as Error).message); }
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
    const result = await act('runs', { repo_url: data.get('repo_url'), app_url: data.get('app_url'), start_app: data.get('start_app') === 'on', app_component: data.get('app_component') });
    if (result) navigate('Run', result.id);
  };
  const response = (decision: string, feedback: string) => run?.pending && act(`runs/${id}/respond`, { request_id: run.pending.id, decision, feedback });
  const active = run?.pending?.stage || text(run?.state.active_stage);
  const visibleRuns = runs.filter(r => `${r.repo_url} ${r.id} ${r.status} ${r.verdict}`.toLowerCase().includes(search.toLowerCase()));
  const style = { '--sidebar': `${Math.max(190, Math.min(340, Number(sidebar) || 236))}px`, '--main-split': `${Math.max(45, Math.min(80, Number(split) || 68))}%` } as CSSProperties;
  return <div className="app-shell" style={style}>
    <aside className="sidebar"><a className="brand" href="/" onClick={e => { e.preventDefault(); navigate('Runs'); }}><span className="brand-mark">QE</span><span>Agentic QE Platform<small>Repository testing</small></span></a>
      <div className="workspace-label"><span className="dot" /> Local workspace</div>
      <nav aria-label="Main navigation"><button className={page === 'Runs' ? 'active' : ''} onClick={() => navigate('Runs')}><span aria-hidden="true">◫</span> Runs <small>{runs.length}</small></button><button className={page === 'New run' ? 'active' : ''} onClick={() => navigate('New run')}><span aria-hidden="true">＋</span> New run</button><button className={page === 'Skills' ? 'active' : ''} onClick={() => navigate('Skills')}><span aria-hidden="true">◇</span> Agent skills</button><button className={page === 'Prompts' ? 'active' : ''} onClick={() => navigate('Prompts')}><span aria-hidden="true">≡</span> Agent prompts</button><button className={page === 'Configuration' ? 'active' : ''} onClick={() => navigate('Configuration')}><span aria-hidden="true">⚙</span> Configuration</button></nav>
      {id && <div className="sidebar-run"><span className="eyebrow">Selected run</span><b>{run?.state.repo_url ? repoName(text(run.state.repo_url)) : id}</b><small>{id}</small>{run && <Badge value={run.busy ? 'working' : run.status} />}</div>}
      <div className="sidebar-footer">{config && <><span className="eyebrow">Daily token usage</span><progress value={config.daily.used} max={config.daily.limit || 1} /><small>{config.daily.used.toLocaleString()} / {config.daily.limit.toLocaleString()}</small></>}<button onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? '☀ Light appearance' : '◐ Dark appearance'}</button><small>History stays on this user’s machine.</small></div>
      <ResizeHandle label="Resize sidebar" start={() => { const initial = sidebar; return dx => setSidebar(Math.max(190, Math.min(340, initial + dx))); }} />
    </aside>
    {deleting && <dialog ref={deleteDialog} aria-labelledby="delete-run-title" className="detail-dialog" onCancel={() => setDeleting(null)} onClose={() => setDeleting(null)}><header className="panel-heading"><h2 id="delete-run-title">Delete this run?</h2><button onClick={() => setDeleting(null)}>Cancel</button></header><div className="panel-body"><p><strong>{repoName(deleting.repo_url || deleting.id)}</strong> · {deleting.id}</p><p>This removes the run from history. Its files and reports move to this user's local .deleted-runs folder for recovery. Running operations must finish first.</p><div className="header-actions"><button onClick={() => setDeleting(null)}>Keep run</button><button className="primary" disabled={sending || serviceBusy} onClick={() => void deleteRun()}>{sending ? 'Deleting…' : 'Delete run'}</button></div></div></dialog>}
    <main><header className="topbar"><div><span>Workspace</span><span className="slash">/</span><b>{page === 'Run' ? repoName(text(run?.state.repo_url || id)) : page}</b></div><div className="top-actions"><span className="local-status"><span className={`dot ${connectionError ? 'offline' : ''}`} />{connectionError ? 'Connection needs attention' : 'Local backend'}</span></div></header>
      <div className="main-content">
        {(error || connectionError) && <div className="notice bad" role="alert"><strong>{error || connectionError}</strong><button onClick={() => { setError(''); void refresh().catch(e => setConnectionError(e.message)); }}>Refresh connection</button></div>}
        {page === 'Runs' && <><div className="page-header"><div><span className="eyebrow">Test engineering workspace</span><h1>Repository runs</h1><p>Review each stage. Execute with approval. Keep the evidence.</p></div><button className="primary" onClick={() => navigate('New run')}>＋ Start a run</button></div><Metrics values={{ saved_runs: runs.length, awaiting_review: runs.filter(r => r.pending).length, active_operations: serviceBusy ? 1 : 0 }} /><Panel title="Run history" storageKey="runs:history"><div className="table-tools"><input aria-label="Search runs" type="search" placeholder="Search repositories, run IDs or status…" value={search} onChange={e => setSearch(e.target.value)} /><small>{visibleRuns.length} runs</small></div>{!runs.length ? <div className="empty-state"><span>◇</span><h2>Your first run starts here</h2><p>Add a public GitHub repository. The harness will ask before cloning, installing or running tests.</p><button className="primary" onClick={() => navigate('New run')}>Start repository review</button></div> : <div className="run-list">{visibleRuns.map(r => <div className="run-row" key={r.id}><div className="repo-icon">◇</div><div className="run-name"><b>{repoName(r.repo_url || r.id)}</b><small>{r.id} · Updated {new Date(r.updated * 1000).toLocaleString()}</small></div><span className="run-stage">{label(r.stage)}</span><Badge value={r.busy ? 'working' : r.verdict || r.status} /><button disabled={r.status === 'unreadable'} onClick={() => navigate('Run', r.id, r.status === 'complete' ? 'QA report' : 'Review')}>Open run →</button><button className="delete-run small" aria-label={`Delete run ${r.id}`} disabled={sending || serviceBusy || r.busy} onClick={() => setDeleting(r)}>Delete</button></div>)}</div>}</Panel>{serviceBusy && <p className="notice">An operation is running. You can inspect saved runs; new actions wait until it reaches a gate.</p>}</>}
        {page === 'New run' && <><div className="page-header"><div><span className="eyebrow">New review</span><h1>Test a repository</h1><p>Requirements, design, executable tests and a QA report from repository evidence.</p></div></div><SplitWorkspace split={split} setSplit={setSplit}><Panel title="Repository details" storageKey="new:form"><form onSubmit={start}><label>Public GitHub repository URL<input name="repo_url" type="url" required placeholder="https://github.com/owner/repository" /></label><p className="field-help">Repository page, clone URL or supported branch/tag URL.</p><label>Running application URL <span className="muted">optional</span><input name="app_url" type="url" placeholder="http://localhost:8080" /></label><p className="field-help">Use localhost or 127.0.0.1 on the machine running the harness.</p><label>Application component <span className="muted">optional</span><input name="app_component" placeholder="frontend" /></label><p className="field-help">Repository-relative folder to launch when the repository contains multiple applications.</p><label className="check"><input name="start_app" type="checkbox" />Start the application for me</label><p className="field-help">Enables browser E2E testing. A supplied application URL takes precedence.</p><div className="notice">You will review the clone, installation and execution actions before they happen.</div><button className="primary" disabled={sending || serviceBusy || !config?.key_configured}>{sending ? 'Creating run…' : 'Start repository review →'}</button>{config && !config.key_configured && <p className="bad">Configure OPENAI_API_KEY in .env and restart the launcher first.</p>}</form></Panel><Panel title="How the review works" storageKey="new:journey"><ol className="journey">{stages.map(s => <li key={s}>{label(s)}</li>)}</ol><p>The harness selects testing frameworks from the detected stack. Missing prerequisites and unsupported execution paths remain visible.</p><p className="muted">Approved commands run as your local user. Isolated dependencies are not a security sandbox.</p></Panel></SplitWorkspace></>}
        {page === 'Skills' && <Skills />}
        {page === 'Prompts' && <Prompts />}
        {page === 'Configuration' && config && <><div className="page-header"><div><h1>Configuration</h1><p>Current backend settings and local workspace preferences.</p></div></div><Panel title="Model gateway" storageKey="config:gateway"><DataView value={{ model: config.model, base_url: config.base_url || 'Default provider endpoint', api_key: config.key_configured ? 'Configured' : 'Missing' }} /><p>Set gateway values in the git-ignored .env file and restart. Credentials are never displayed.</p></Panel><Panel title="Daily usage" storageKey="config:daily"><Metrics values={config.daily} /><p>Shared across runs for this OS user; resets at execution-host local midnight.</p></Panel></>}
        {page === 'Run' && !run && !error && <div className="empty-state">Loading run evidence…</div>}
        {page === 'Run' && run && <><div className="page-header"><div><span className="eyebrow">Run {id}</span><h1>{repoName(text(run.state.repo_url))}</h1><p>{text(run.state.repo_ref || 'Default branch')} · <Badge value={run.busy ? 'working' : run.status} /></p></div><div className="header-actions">{run.has_zip && <a className="button primary" href={download(id, 'zip')} download>Download deliverable ZIP ↓</a>}<button disabled={sending || run.service_busy} onClick={() => void act(`runs/${id}/reconnect`, {})}>Reconnect</button></div></div>
          <div className="stage-progress" aria-label="Workflow stages">{stages.map((s, i) => <div key={s} className={`${active === s ? 'current' : ''} ${record(run.state.approved)[s] ? 'done' : ''}`}><span>{record(run.state.approved)[s] ? '✓' : i + 1}</span><small>{label(s)}</small></div>)}</div>
          {run.busy && <div className="notice working" role="status"><span className="spinner" />The harness is working. The next action will be a human review gate.</div>}
          {(run.operation_error || run.error || run.pending?.error) && <div className="notice bad" role="alert">{run.operation_error || run.error || run.pending?.error}</div>}
          {run.status === 'interrupted' && <div className="notice bad">An approved operation was interrupted. Automatic replay is blocked. Inspect the audit and retained artifacts; reconnecting does not repeat the action.</div>}
          {run.status === 'stopped' && <div className="notice">Workflow stopped. Approved evidence remains available.</div>}
          {run.state.completed_reason ? <div className="notice"><Badge value={record(record(artifacts(run).test_execution).qa_report).verdict} /> {text(run.state.completed_reason)}</div> : null}
          <div className="tabs" aria-label="Run views">{tabs.map(t => <button className={tab === t ? 'selected' : ''} aria-pressed={tab === t} key={t} onClick={() => changeTab(t)}>{t}{t === 'Review' && run.actions.length > 0 && <span className="tab-dot" />}</button>)}</div>
          {tab === 'Review' && <SplitWorkspace split={split} setSplit={setSplit}><div><Review run={run} /><Panel title="Run context" storageKey={`${id}:context`} defaultOpen={false}><DataView value={{ repository: run.state.repo_url, application_url: run.state.app_url, budget: run.state.budget, environment: run.state.environment }} /></Panel></div><Decision key={run.pending?.id || id} run={run} sending={sending} respond={response} /></SplitWorkspace>}
          {tab === 'Prompts' && <Prompts key={`${id}:${run.busy}`} run={run} />}
          {tab === 'Artifacts' && <Artifacts run={run} />}
          {tab === 'Generated tests' && <Generated run={run} />}
          {tab === 'QA report' && <QA run={run} />}
          {tab === 'Test reports' && <Reports key={`${id}:reports`} run={run} theme={theme} />}
          {tab === 'Logs' && <Reports key={`${id}:logs`} run={run} logs theme={theme} />}
          {tab === 'Observability' && <Evidence key={`${id}:obs`} id={id} kind="observability" busy={run.busy} />}
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
function Review({ run }: { run: Run }) {
  const p = run.pending;
  if (!p) return <Panel title="Review" storageKey={`${run.id}:review`}><p>No pending approval for this run.</p></Panel>;
  const value = p.kind === 'review' ? p.details.artifact : p.details;
  return <Panel title={p.title || label(p.stage)} storageKey={`${run.id}:review:${p.stage}`} actions={p.kind === 'review' ? <ExportButtons id={run.id} kind={p.stage} /> : undefined}>
    <div className="review-meta"><Badge value={p.kind === 'tool' ? 'Action approval' : p.kind === 'error' ? 'Error gate' : 'Artifact review'} /><span>{text(p.details.role || p.details.persona || '')}</span></div>
    {p.kind === 'tool' && p.details.effects ? <div className="notice">{text(p.details.effects)}</div> : null}
    {p.kind === 'tool' && p.stage !== 'repo_analysis' && <p className="bad">Commands execute locally as your user. Review all commands and download sources before approval.</p>}
    <DataView value={value} />
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
function Artifacts({ run }: { run: Run }) {
  const data = artifacts(run);
  return <>{!Object.keys(data).length && <p className="empty-inline">No artifacts yet.</p>}{stages.filter(s => data[s] !== undefined).map(stage => <Panel key={stage} title={`${label(stage)} · revision ${text(record(run.state.revisions)[stage])}`} storageKey={`${run.id}:artifact:${stage}`} defaultOpen={stage === run.pending?.stage} actions={<ExportButtons id={run.id} kind={stage} />}><DataView value={data[stage]} /><details className="raw-json"><summary>Raw JSON</summary><pre>{JSON.stringify(data[stage], null, 2)}</pre></details></Panel>)}</>;
}
function Generated({ run }: { run: Run }) {
  const data = record(artifacts(run).test_generation); const files = rows(data.files); const validation = record(data.validation);
  if (!Object.keys(data).length) return <p className="empty-inline">Generated test source is not available yet.</p>;
  return <><Metrics values={{ generated_files: validation.files || files.length, test_declarations: validation.test_declarations, covered_cases: Array.isArray(validation.covered_case_ids) ? validation.covered_case_ids.length : 0 }} /><Panel title="Case catalog" storageKey={`${run.id}:catalog`}><DataTable data={rows(data.case_catalog)} /></Panel>{files.map((file, index) => <Panel key={index} title={text(file.path)} storageKey={`${run.id}:source:${index}`} defaultOpen={index === 0} actions={<a className="button small" href={download(run.id, 'source', 'json', index)} download>Source ↓</a>}><p>Cases: {text(file.case_ids)}</p><pre className="source-code">{text(file.content)}</pre></Panel>)}<Panel title="Generation validation" storageKey={`${run.id}:validation`} defaultOpen={false}><DataView value={data.validation} /></Panel></>;
}
function QA({ run }: { run: Run }) {
  const execution = record(artifacts(run).test_execution); const qa = record(execution.qa_report);
  if (!Object.keys(qa).length) return <div className="empty-state"><span>◫</span><h2>Your results will appear here</h2><p>Approve execution to produce case results, requirement coverage and failure evidence.</p></div>;
  const updates = record(qa.updates);
  const failures = rows(qa.cases).filter(c => ['failed', 'error', 'not_executed'].includes(text(c.status)));
  const actions = [...rows(updates.generated_test_files_to_update), ...rows(updates.repository_files_to_review)];
  return <><div className="qa-summary"><div><span className="eyebrow">Saved execution verdict</span><h2><Badge value={qa.verdict} /></h2><p>Execution: {text(qa.execution_status)} · Runner: {text(qa.adapter)}</p></div><ExportButtons id={run.id} kind="qa" /></div>{qa.verdict === 'INCONCLUSIVE' && <p className="notice bad">Tests did not produce sufficient execution evidence. Inconclusive is never a pass.</p>}<Metrics values={record(qa.case_status_counts)} />
    <Panel title="Why tests failed & what to improve" storageKey={`${run.id}:qa:failures`}>
      <p className="field-help">Observed output and recommendations from the saved QA report. A failing assertion needs review of both application behavior and the test expectation.</p>
      {failures.length ? <div className="failure-cards">{failures.map((c, i) => {
        const related = actions.filter(a => a.case_id === c.id);
        return <article className="failure-card" key={text(c.id) || i}><header><div><span className="eyebrow">{text(c.id)} · {label(c.level)}</span><h3>{text(c.title)}</h3></div><Badge value={c.status} /></header><code className="failure-location">{text(c.file || 'No generated file recorded')}</code><div className="failure-reason"><b>Observed failure</b><pre>{text(c.message || 'No failure output was recorded. This case did not execute; inspect the execution logs and environment status.')}</pre></div><div className="failure-next"><span aria-hidden="true">↗</span><div><b>Recommended next step</b>{related.length ? related.map((a, n) => <div key={n}><span className="badge">{label(a.classification)}</span><p>{text(a.action)}</p><small>{text(a.file)}{a.lines ? ` · lines ${text(a.lines)}` : a.line ? `:${text(a.line)}` : ''}</small></div>) : <p>Inspect this case’s output and the execution logs. Confirm the environment and reproduce the behavior before changing the application or its test.</p>}</div></div></article>;
      })}</div> : <div className="success-note"><span>✓</span><div><b>No failed cases in the saved QA report</b><p>Review requirement coverage and warnings before treating this run as complete coverage.</p></div></div>}
    </Panel>
    <Panel title="Case results" storageKey={`${run.id}:qa:Case results`}><DataTable data={rows(qa.cases)} columns={['title', 'level', 'actor', 'priority', 'status', 'file']} /></Panel>
    <Panel title="Requirement coverage" storageKey={`${run.id}:qa:Requirement coverage`}><DataTable data={rows(qa.requirement_coverage)} columns={['state', 'category', 'actor', 'text']} /></Panel>
    <Panel title="Improvement worklist" storageKey={`${run.id}:qa:updates`}><DataTable data={[...actions, ...rows(updates.requirements_needing_tests), ...rows(updates.environment_actions)]} columns={['classification', 'file', 'case', 'detail', 'action']} /></Panel>
    <Panel title="Warnings & limitations" storageKey={`${run.id}:qa:limits`}><DataView value={{ warnings: qa.warnings, limitations: qa.limitations, unmapped_results: qa.unmapped_results }} /></Panel>
    <Panel title="Skill contract checks" storageKey={`${run.id}:qa:checks`}><DataView value={qa.skill_checks} /></Panel>
    <Panel title="Complete QA evidence" storageKey={`${run.id}:qa:full`} defaultOpen={false}><DataView value={qa} /></Panel>{run.actions.includes('revise') && run.pending?.stage === 'test_execution' && <p className="notice">Use the Review tab to regenerate failing tests. New source and execution require approval again.</p>}</>;
}
function Evidence({ id, kind, busy, run }: { id: string; kind: 'audit' | 'observability'; busy: boolean; run?: Run }) {
  const [data, setData] = useState<Row | null>(null); const [error, setError] = useState(''); const [event, setEvent] = useState(''); const [stage, setStage] = useState('');
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const load = async () => { try { setData(await api<Row>(`runs/${id}/${kind}`, undefined, controller.signal)); setError(''); } catch (e) { if (!controller.signal.aborted) setError((e as Error).message); } if (busy && !controller.signal.aborted) timer = setTimeout(load, 2500); };
    void load(); return () => { controller.abort(); clearTimeout(timer); };
  }, [id, kind, busy]);
  if (error) return <p className="notice bad" role="alert">{error} Other run views remain available.</p>;
  if (!data) return <p className="empty-inline">Loading persisted evidence…</p>;
  if (kind === 'observability') return <Observability id={id} report={data} />;
  const events = rows(data.events); const filtered = events.filter(e => (!event || e.event === event) && (!stage || e.stage === stage));
  return <><Panel title="Approval & activity audit" storageKey={`${id}:audit:events`} actions={<a className="button small" href={download(id, 'audit')} download>Audit JSONL ↓</a>}><div className="filters"><Select label="Event" value={event} set={setEvent} options={events.map(e => text(e.event))} /><Select label="Stage" value={stage} set={setStage} options={events.map(e => text(e.stage || ''))} /></div>{Number(data.malformed_lines) > 0 && <p className="notice">{text(data.malformed_lines)} malformed lines were omitted from this view.</p>}<div className="audit-feed">{filtered.slice().reverse().slice(0, 100).map((e, i) => <details key={i}><summary><span className="audit-icon">{e.decision === 'approve' ? '✓' : '·'}</span><span><b>{label(e.event)}</b><small>{label(e.stage)} · {text(e.at)}{e.decision ? ` · ${label(e.decision)}` : ''}</small></span></summary><DataView value={e} /></details>)}</div><details><summary>Search all audit records</summary><DataTable data={filtered} columns={['at', 'event', 'stage', 'decision', 'request_id']} /></details></Panel><Panel title="Runtime & skill provenance" storageKey={`${id}:audit:provenance`}><DataView value={{ runtime: run?.state.runtime_provenance, skills: run?.state.skill_provenance, skill_tool_trace: run?.state.skill_tool_trace, invocations: run?.state.invocations }} /></Panel></>;
}
