'use client';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApiError, api, artifacts, download, isConnectionError, label, minutes, record, rows, runTitle, text, type Row, type Run } from '@/lib/harness';
import { ConfirmDialog, EmptyState, RunScope, StatusBadge } from './page';
import { POLL_BUSY_MS, POLL_IDLE_MS, useShell } from './shell';
import Icon, { type IconName } from './icons';

export const STAGES = ['repo_analysis', 'requirements', 'test_planning', 'test_design', 'test_generation', 'environment_setup', 'test_execution'];
/** Run tabs, each a route under /runs/[id]. The Review tab is the run overview itself. */
export const RUN_TABS: { name: string; segment: string; icon: IconName }[] = [
  { name: 'Review', segment: '', icon: 'eye' },
  { name: 'Errors', segment: 'errors', icon: 'alert' },
  { name: 'Artifacts', segment: 'artifacts', icon: 'box' },
  { name: 'Generated tests', segment: 'tests', icon: 'code' },
  { name: 'QA report', segment: 'qa', icon: 'shield' },
  { name: 'Test reports', segment: 'evidence', icon: 'chart' },
  { name: 'Logs', segment: 'logs', icon: 'terminal' },
  { name: 'Observability', segment: 'observability', icon: 'activity' },
  { name: 'Audit', segment: 'audit', icon: 'clipboard' },
  { name: 'Prompts', segment: 'prompts', icon: 'message' },
];
export const RUN_ID = /^[A-Za-z0-9_-]{1,100}$/;

type RunValue = { id: string; run: Run; sending: boolean; act: (path: string, body: Row) => Promise<Row | undefined>; reload: () => Promise<void>; setError: (message: string) => void };
const RunContext = createContext<RunValue | null>(null);
export const useRun = () => { const value = useContext(RunContext); if (!value) throw new Error('useRun must be used inside a run page'); return value; };

/** Shared run header, stage stepper, notices and tab navigation. Deep pages (sections, records, files) get a compact header. */
export default function RunLayout({ id, children }: { id: string; children: ReactNode }) {
  const shell = useShell();
  // Stable callbacks and refs only: a change of shell state must not restart the run poll (item 10).
  const refreshShell = shell.refresh;
  const serviceBusyRef = useRef(false);
  serviceBusyRef.current = shell.serviceBusy;
  const router = useRouter();
  const pathname = usePathname();
  const [run, setRun] = useState<Run | null>(null);
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const [rerunStage, setRerunStage] = useState<string | null>(null);
  const [rerunError, setRerunError] = useState('');
  const [selectedStage, setSelectedStage] = useState('');
  const validId = RUN_ID.test(id);
  const busyRef = useRef(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    const next = await api<Run>(`runs/${encodeURIComponent(id)}`, undefined, signal);
    if (signal?.aborted) return;
    setRun(next); setLoadError(null); busyRef.current = next.busy || next.service_busy;
  }, [id]);
  // Polling backs off to 10 s when nothing runs; Evidence and the run stay in step (findings 7, 12).
  useEffect(() => {
    if (!validId) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await load(controller.signal); } catch (e) { if (!controller.signal.aborted) setLoadError(e as Error); }
      if (!controller.signal.aborted) timer = setTimeout(poll, busyRef.current || serviceBusyRef.current ? POLL_BUSY_MS : POLL_IDLE_MS);
    };
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [load, validId]);

  const reload = useCallback(async () => { try { await load(); } catch (e) { setLoadError(e as Error); } }, [load]);
  const act = useCallback(async (path: string, body: Row) => {
    setSending(true); setError('');
    try { const result = await api<Row>(path, body); await reload(); await refreshShell(); return result; }
    catch (e) { setError((e as Error).message); return undefined; }
    finally { setSending(false); }
  }, [reload, refreshShell]);

  const value = useMemo<RunValue | null>(() => (run ? { id, run, sending, act, reload, setError: setError } : null), [id, run, sending, act, reload]);
  const title = run ? runTitle(run.state.run_name, run.state.repo_url, id) : id;
  const base = `/runs/${encodeURIComponent(id)}`;
  const segments = pathname.split('/').filter(Boolean);
  const tabLevel = segments.length <= 3;
  const currentSegment = segments[2] ?? '';
  const isDeep = !tabLevel;

  if (!validId) return <EmptyState title="Run not found">The run ID is not valid. <Link href="/runs">Back to runs</Link></EmptyState>;
  if (!run) {
    if (loadError instanceof ApiError && loadError.status === 404) return <EmptyState title="Run not found">No saved run has the ID {id}. It may have been deleted. <Link href="/runs">Back to runs</Link></EmptyState>;
    if (loadError && !isConnectionError(loadError)) return <div className="notice bad" role="alert">{loadError.message} <Link href="/runs">Back to runs</Link></div>;
    return <p className="empty-inline" role="status">Loading run evidence…</p>;
  }

  const active = run.busy ? String(run.state.active_stage || run.pending?.stage || 'repo_analysis') : run.pending?.stage || String(run.state.active_stage || 'repo_analysis');
  const errorCount = run.errors?.items.length ?? 0;
  const genFailed = run.errors?.items.filter(e => e.source === 'generation_failures' || e.source === 'audit' && e.stage === 'test_generation').length ?? 0;
  const firstSentence = (v: string) => (v.split(/(?<=[.!?])\s|\n/)[0] || v).slice(0, 200);
  const finish = run.busy ? record(record(run.progress).eta).finish_estimate : null;
  const etaOf = (stage: string): Row => record(rows(record(run.progress).eta ? record(record(run.progress).eta).stages : []).find(r => r.stage === stage));
  const failedStage = run.pending?.kind === 'error' ? run.pending.stage : record(record(artifacts(run).test_execution).qa_report).verdict === 'FAIL' ? 'test_execution' : '';
  const stages = Array.isArray(run.state.stages) ? run.state.stages as string[] : STAGES;
  const percent = Number(record(run.progress).percent) || 0;
  const repoName = (url: string) => url.replace(/^https?:\/\/github\.com\//, '').replace(/\.git$/, '');
  const repo = text(run.state.repo_url);
  const cloning = record(run.operation).kind === 'clone' && record(run.operation).status === 'running';

  const confirmStop = async () => { setStopOpen(false); await act(`runs/${encodeURIComponent(id)}/stop`, {}); };
  const confirmRename = async () => { setRenameOpen(false); await act(`runs/${encodeURIComponent(id)}/rename`, { run_name: renameValue }); };
  // Errors stay inside the dialog so the user can read them and choose another step (item 11).
  const confirmRerun = async () => {
    if (!rerunStage) return;
    setSending(true); setRerunError('');
    try {
      await api<Row>(`runs/${encodeURIComponent(id)}/rerun`, { stage: rerunStage, request_id: run.pending?.id || null });
      await reload(); await refreshShell();
      setRerunStage(null); router.push(base);
    } catch (e) { setRerunError((e as Error).message); }
    finally { setSending(false); }
  };

  return <RunContext.Provider value={value}><RunScope.Provider value={id}>
    <section className={`run-hero${isDeep ? ' run-hero-compact' : ''}`} aria-label="Run summary">
      <div className="run-hero-text">
        <span className="eyebrow">Run {id}</span>
        {isDeep ? <p className="run-hero-name"><Link href={base}>{title}</Link> <StatusBadge value={run.busy && run.status !== 'stopping' ? 'working' : run.status} /></p> : <h1>{title}</h1>}
        {!isDeep && <p className="run-hero-meta">{repoName(repo) || 'No repository'} · {text(run.state.repo_ref || 'Default branch')} · <StatusBadge value={run.busy && run.status !== 'stopping' ? 'working' : run.status} /> <span className="badge">{text(run.state.run_mode || 'brownfield')}</span>{(Array.isArray(run.state.test_types) ? run.state.test_types : ['unit', 'integration']).map(t => <span className="badge" key={text(t)}>{text(t)}</span>)}</p>}
      </div>
      <div className="header-actions run-actions">
        {run.has_zip && <a className="button primary" href={download(id, 'zip')} download>Download deliverable ZIP ↓</a>}
        {run.busy && run.status !== 'stopping' && <button className="danger-button" disabled={sending} onClick={() => setStopOpen(true)}>Stop run</button>}
        <button disabled={sending || run.busy || run.service_busy} onClick={() => { setRenameValue(typeof run.state.run_name === 'string' ? run.state.run_name : ''); setRenameOpen(true); }}>Rename run</button>
        <button disabled={sending || run.service_busy} onClick={() => void act(`runs/${encodeURIComponent(id)}/reconnect`, {})}>Reconnect</button>
        <button disabled={sending || run.service_busy} onClick={() => { setRerunError(''); setRerunStage(selectedStage || active || 'repo_analysis'); }}>Rerun from step <Icon name="plus" /></button>
      </div>
    </section>
    {tabLevel && <>
      <div className="run-completion"><span>Workflow progress · {percent}%</span><progress aria-label="Workflow completion" max={100} value={percent} /><small>Approved stage milestones; test verdict is reported separately.</small></div>
      <p className="stage-current">Current stage: <strong>{label(active)}</strong></p>
      <ol className="stage-progress" aria-label="Workflow stages">{stages.map((s, i) => <li key={s}><Link href={`${base}/artifacts/${encodeURIComponent(s)}`} aria-label={`View ${label(s)} evidence`} aria-current={selectedStage === s ? 'true' : undefined} onClick={() => setSelectedStage(s)} className={`${active === s ? 'current' : ''} ${failedStage === s ? 'failed' : record(run.state.approved)[s] ? 'done' : ''}`}><span aria-hidden="true">{failedStage === s ? '!' : record(run.state.approved)[s] ? '✓' : i + 1}</span><small>{label(s)}</small>{etaOf(s).predicted_s != null ? <small>{etaOf(s).status === 'done' ? 'took ' : '~'}{minutes(Number(etaOf(s).predicted_s))}</small> : null}</Link></li>)}</ol>
      {finish ? <p className="muted">Estimated completion ~{new Date(String(finish)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} (≈{minutes(Number(record(record(run.progress).eta).total_remaining_s))} left)</p> : null}
    </>}
    {(run.busy || sending) && <WorkingStatus stage={active} eta={etaOf(active)} operation={record(run.operation)} directory={run.run_directory} cancel={run.busy && cloning ? () => void act(`runs/${encodeURIComponent(id)}/cancel-clone`, {}) : undefined} sending={sending} />}
    {error && <div className="notice bad" role="alert">{error}</div>}
    {errorCount > 0 && tabLevel && <div className="notice bad" role="alert">{errorCount} errors in this run{genFailed > 0 ? ` (${genFailed} generation batches failed)` : ''} <Link href={`${base}/errors`}>Open Errors tab</Link></div>}
    {!run.busy && run.pending?.error && tabLevel && <div className="notice bad" role="alert">{firstSentence(run.pending.error)} Details in Errors tab.</div>}
    {run.status === 'interrupted' && tabLevel && <div className="notice bad">An approved operation was interrupted. Automatic replay is blocked. Inspect the audit and retained artifacts; reconnecting does not repeat the action.</div>}
    {run.status === 'stopping' && tabLevel && <div className="notice" role="status">Stopping… waiting for running work to end. Approved evidence is kept. If this takes more than a minute, restart the app; the run then shows as interrupted and can be resumed with Reconnect.</div>}
    {run.status === 'stopped' && tabLevel && <div className="notice">Workflow stopped. Approved evidence remains available.</div>}
    {run.state.completed_reason && tabLevel ? <div className="notice"><StatusBadge value={record(record(artifacts(run).test_execution).qa_report).verdict} /> {text(run.state.completed_reason)}</div> : null}
    <nav className="tabs" aria-label="Run views">{RUN_TABS.map(tab => {
      const href = tab.segment ? `${base}/${tab.segment}` : base;
      const current = tab.segment === currentSegment;
      return <Link key={tab.name} href={href} className={current ? 'selected' : ''} aria-current={current ? 'page' : undefined}><Icon name={tab.icon} />{tab.name === 'Errors' ? `Errors${errorCount ? ` ${errorCount}` : ''}` : tab.name}{tab.name === 'Review' && run.actions.length > 0 && <span className="tab-dot" />}</Link>;
    })}</nav>
    {children}
    <ConfirmDialog open={stopOpen} title="Stop this run?" confirmLabel="Stop run" tone="danger" busy={sending} onConfirm={() => void confirmStop()} onClose={() => setStopOpen(false)}>
      <p>Completed stages and evidence are kept.</p>
    </ConfirmDialog>
    <ConfirmDialog open={renameOpen} title="Rename run" confirmLabel="Save" busy={sending} onConfirm={() => void confirmRename()} onClose={() => setRenameOpen(false)}>
      <label htmlFor="rename-run-input">Run name (max 80 characters; empty restores the default)</label>
      <input id="rename-run-input" maxLength={80} value={renameValue} onChange={e => setRenameValue(e.target.value)} />
    </ConfirmDialog>
    <ConfirmDialog open={rerunStage !== null} title="Rerun from a step" confirmLabel={`Rerun ${label(rerunStage)}`} busy={sending} disabled={run.service_busy} onConfirm={() => void confirmRerun()} onClose={() => setRerunStage(null)}>
      {rerunError && <p className="notice bad" role="alert">{rerunError}</p>}
      <p>Previous evidence is retained in the run’s attempts folder. This step and all later approvals are reset. Clone, installation and execution still require their normal review gates.</p>
      <div className="step-picker" role="group" aria-label="Step to rerun">{stages.map((stage, index, all) => <button type="button" key={stage} aria-pressed={rerunStage === stage} disabled={sending || run.service_busy || all.slice(0, index).some(s => !record(run.state.approved)[s])} onClick={() => setRerunStage(stage)}>{label(stage)}</button>)}</div>
    </ConfirmDialog>
  </RunScope.Provider></RunContext.Provider>;
}

/** Live status for a running stage. Only the stage text is a live region; the elapsed timer is hidden from assistive tech. */
function WorkingStatus({ stage, eta, operation, directory, cancel, sending }: { stage: string; eta: Row; operation: Row; directory?: string; cancel?: () => void; sending: boolean }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => { const start = Date.now(); const timer = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000); return () => clearInterval(timer); }, [stage]);
  const current = operation.stage === stage ? operation : {};
  const batch = eta.batch ? record(eta.batch) : null;
  const cloning = current.kind === 'clone' && current.status === 'running';
  const headline = cloning ? 'Cloning repository from GitHub…' : stage === 'repo_analysis' ? 'Inspecting repository source…' : stage === 'test_generation' ? 'Generating tests…' : `${label(stage || 'workflow')} is running…`;
  // Live region: the headline and progress only. The elapsed timer changes every second, so it sits outside the region.
  const progress = cloning ? `Git attempt ${text(current.attempt)} · ${text(current.elapsed_seconds)}s elapsed · timeout ${text(current.timeout_seconds)}s` : batch ? `Batch ${text(batch.done)} of ${text(batch.total)}${eta.remaining_s != null ? ` · ~${minutes(Number(eta.remaining_s))} left of ~${minutes(Number(eta.predicted_s))}` : ''}` : eta.remaining_s != null ? `~${minutes(Number(eta.remaining_s))} left of ~${minutes(Number(eta.predicted_s))}` : 'Waiting for the next saved output';
  const active = minutes(eta.elapsed_s != null ? Number(eta.elapsed_s) : elapsed);
  return <div className="notice working"><span className="spinner" aria-hidden="true" /><div><div role="status" aria-live="polite"><strong>{headline}</strong><div>{progress}</div></div><div className="working-timer" aria-hidden="true">{active} active. Updates refresh automatically.</div>{current.latest_output ? <pre className="live-clone-output">{text(current.latest_output)}</pre> : null}{directory && <small>Actual run directory: <code>{directory}</code></small>}{cancel && <div><button disabled={sending} onClick={cancel}>{sending ? 'Cancelling…' : 'Cancel clone'}</button><small>Stops this run’s Git process tree; preserves output for retry.</small></div>}</div></div>;
}
