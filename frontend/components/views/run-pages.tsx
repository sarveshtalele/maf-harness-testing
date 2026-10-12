'use client';
import Link from 'next/link';
import { useParams, usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { api, artifacts, download, label, record, rows, text, type Row, type Run } from '@/lib/harness';
import { parseReport, type Report } from '@/lib/report';
import { DetailPage, EmptyState, Exports, Fields, JsonDownload, Metrics, Section, StatusBadge, Table, renderValue, recordHref, useRunId, type Column, type SectionSpec } from '../page';
import { Panel, usePreference, ResizeHandle } from '../data';
import { useRun, STAGES } from '../run-shell';
import Prompts from '../prompts';
import Icon from '../icons';
import { CaseTable, EventFeed, ReportBlocks, VerdictBanner, findRecord, runName } from './shared';

export const base = (id: string) => `/runs/${encodeURIComponent(id)}`;
const isPlainObject = (value: unknown): value is Row => !!value && typeof value === 'object' && !Array.isArray(value);
const isCaseKey = (key: string) => key.endsWith('_tests');
/** "positive_tests" is captioned "Positive cases". */
const caseCaption = (key: string) => `${label(key.replace(/_tests$/, ''))} cases`;
const describe = (value: unknown): string => {
  if (Array.isArray(value)) return `${value.length} ${value.length === 1 ? 'entry' : 'entries'}`;
  if (isPlainObject(value)) { const n = Object.keys(value).length; return `${n} ${n === 1 ? 'field' : 'fields'}`; }
  if (typeof value === 'string') return value.length > 240 ? `${value.slice(0, 240)}…` : value;
  return text(value);
};

/** One value of an artifact section: cases as tables, objects as fields, lists and text through the shared renderer. */
export function SectionBody({ value, keyName }: { value: unknown; keyName: string }) {
  if (isPlainObject(value)) return <Fields data={value} />;
  return <>{renderValue(keyName, value)}</>;
}

// ---------- Review (run overview and pending gate) ----------

function useSplit(): [number, (value: number) => void] { return usePreference<number>('maf:split', 68); }

export function SplitWorkspace({ children, split, setSplit }: { children: [ReactNode, ReactNode]; split: number; setSplit: (value: number) => void }) {
  const grid = useRef<HTMLDivElement>(null);
  return <div ref={grid} className="workspace-grid" style={{ '--main-split': `${Math.max(45, Math.min(80, Number(split) || 68))}%` } as CSSProperties}>{children[0]}<ResizeHandle label="Resize workspace columns" start={() => {
    const width = grid.current!.clientWidth; const initial = split;
    return dx => setSplit(Math.max(45, Math.min(80, initial + dx / width * 100)));
  }} />{children[1]}</div>;
}

export function ReviewView() {
  const { id, run, sending, act } = useRun();
  const [split, setSplit] = useSplit();
  return <>
    {!record(run.state.approved).test_design && Array.isArray(run.state.stages) && (run.state.stages as string[]).includes('test_design') && record(record(run.progress).case_budget).rows ? <CaseBudget budget={record(record(run.progress).case_budget)} id={id} disabled={sending || run.busy || run.service_busy} act={act} /> : null}
    <SplitWorkspace split={split} setSplit={setSplit}>
      <div><ReviewPanel run={run} /><Panel title="Run context" storageKey={`${id}:context`} defaultOpen={false}><Fields data={{ repository: run.state.repo_url, application_url: run.state.app_url, budget: run.state.budget, repository_knowledge: run.state.repo_knowledge, environment: run.state.environment }} /></Panel></div>
      <DecisionPanel key={run.pending?.id || id} run={run} sending={sending} respond={(decision, feedback) => run.pending && act(`runs/${encodeURIComponent(id)}/respond`, { request_id: run.pending.id, decision, feedback })} />
    </SplitWorkspace>
  </>;
}

function ReviewPanel({ run }: { run: Run }) {
  const p = run.pending;
  if (!p) return <Panel title="Review" storageKey={`${run.id}:review`}><p>No pending approval for this run.</p></Panel>;
  const value = record(p.kind === 'review' ? p.details.artifact : p.details);
  const executing = run.busy && run.pending_consumed && p.kind === 'tool';
  const caseKeys = p.stage === 'test_design' ? Object.keys(value).filter(isCaseKey) : [];
  const rest = Object.fromEntries(Object.entries(value).filter(([key]) => key !== 'setup_guide' && !isCaseKey(key)));
  return <Panel title={executing ? p.title.replace(/^Approve /, '') : p.title || label(p.stage)} storageKey={`${run.id}:review:${p.stage}`} actions={p.kind === 'review' ? <Exports id={run.id} kind={p.stage} /> : undefined} expandHref={p.kind === 'review' ? `${base(run.id)}/artifacts/${p.stage}` : undefined}>
    <div className="review-meta"><StatusBadge value={executing ? 'Approved action in progress' : p.kind === 'tool' ? 'Action approval' : p.kind === 'error' ? 'Error gate' : 'Artifact review'} /><span>{text(p.details.role || p.details.persona || '')}</span></div>
    {p.kind === 'tool' && p.details.effects ? <div className="notice">{text(p.details.effects)}</div> : null}
    {p.kind === 'tool' && p.stage !== 'repo_analysis' && <p className="bad">Commands execute locally as your user. Review all commands and download sources before approval.</p>}
    {value.setup_guide ? <SetupGuide guide={record(value.setup_guide)} /> : null}
    {caseKeys.map((key, i) => <Section key={key} id={`cases-${key}`} title={label(key)} count={rows(value[key]).length} defaultOpen={i < 2}><CaseTable cases={rows(value[key])} requirements={record(artifacts(run).requirements)} caption={caseCaption(key)} /></Section>)}
    <Fields data={rest} />
  </Panel>;
}

function DecisionPanel({ run, sending, respond }: { run: Run; sending: boolean; respond: (decision: string, feedback: string) => unknown }) {
  const [feedback, setFeedback] = useState(''); const p = run.pending;
  const revisionRequired = p && !['environment_setup', 'test_execution'].includes(p.stage);
  const names: Record<string, string> = { approve: p?.kind === 'tool' ? p.stage === 'environment_setup' ? 'Install in isolated environment' : p.stage === 'test_execution' ? 'Run tests here' : 'Approve repository inspection' : 'Approve and proceed', revise: p?.stage === 'test_execution' ? 'Regenerate failing tests' : p?.stage === 'environment_setup' ? 'Re-check environment' : 'Request revision', stop: 'Stop workflow', retry: 'Retry stage', deny: 'Deny action', skip: 'Skip action' };
  return <div><Panel title="Your decision" storageKey={`${run.id}:decision`}>
    {run.actions.length ? <><p>{p?.kind === 'tool' ? 'Approval authorizes only the action shown in this request.' : 'Review the complete evidence before continuing.'}</p>{run.actions.includes('revise') && <label>Modification instructions<textarea maxLength={20000} value={feedback} onChange={e => setFeedback(e.target.value)} placeholder={revisionRequired ? 'Required when requesting revision…' : 'Optional instructions…'} /></label>}<div className="decision-buttons">{run.actions.map(action => <button key={action} className={action === 'approve' ? 'primary' : action === 'stop' ? 'danger-button' : ''} disabled={sending || run.service_busy || (action === 'revise' && !!revisionRequired && !feedback.trim())} onClick={() => respond(action, feedback)}>{names[action]}</button>)}</div><small>Request {p?.id.slice(0, 12)} · revision {text(p?.details.revision || '—')}</small></> : <p>{run.busy ? 'Decisions become available when the operation reaches its next gate.' : run.service_busy ? 'Another run is working. Wait before responding.' : 'No available approval. Inspect artifacts and audit for this run.'}</p>}
  </Panel><Panel title="Agent & usage" storageKey={`${run.id}:agent`}><Fields data={{ persona: p?.details.persona, goal: p?.details.goal, tokens: record(run.state.budget).reported_tokens, provider_calls: record(run.state.budget).calls }} /></Panel></div>;
}

function SetupGuide({ guide }: { guide: Row }) {
  return <section aria-label="Repository setup guide" className="setup-guide-block"><h3>Step-by-step repository setup</h3><p>{text(guide.platform)} · {text(guide.status)}</p><ol className="setup-guide">{(Array.isArray(guide.steps) ? guide.steps : []).map((item, index) => { const step = record(item); return <li key={index}><h4>{text(step.title)}</h4>{step.working_directory ? <p>Working directory: <code>{text(step.working_directory)}</code></p> : null}{step.command ? <code className="command-line">{text(step.command)}</code> : null}{Object.keys(record(step.environment)).length ? <Fields data={{ required_environment: step.environment }} /> : null}{step.instructions ? <p>{text(step.instructions)}</p> : null}</li>; })}</ol>{guide.repository_knowledge ? <Section title="Pasted repository knowledge"><p>{text(guide.knowledge_notice)}</p><div className="prose">{text(guide.repository_knowledge).split(/\n+/).map((line, i) => <p key={i}>{line}</p>)}</div></Section> : null}</section>;
}

function CaseBudget({ budget, id, disabled, act }: { budget: Row; id: string; disabled: boolean; act: (path: string, body: Row) => Promise<unknown> }) {
  const [value, setValue] = useState(Number(budget.current) || 2);
  const recommended = Number(budget.recommended);
  const minutes = (s: number) => (s < 90 ? `${Math.max(1, Math.round(s))} s` : `${Math.round(s / 60)} min`);
  const columns: Column[] = [
    { key: 'k', label: 'Cases per requirement', render: row => <>{text(row.k)}{Number(row.k) === recommended ? ' (recommended)' : ''}</> },
    { key: 'cases', label: 'Approx. cases' }, { key: 'batches', label: 'Approx. batches' },
    { key: 'generation', label: 'Approx. generation time' }, { key: 'design', label: 'Approx. design time' },
  ];
  const data = rows(budget.rows).map(r => ({ k: r.per_requirement, cases: r.cases, batches: r.batches, generation: `~${minutes(Number(r.generation_min) * 60)}`, design: `~${minutes(Number(r.design_min) * 60)}` }));
  return <Panel title="Case budget" storageKey={`${id}:case-budget`}>
    <p>Current: <strong>{text(budget.current)}</strong> test case(s) per requirement. Recommended for ~1 hour of generation: <strong>{recommended}</strong>. Re-run test design to apply a changed budget.</p>
    <div className="header-actions"><label>Cases per requirement<select value={value} disabled={disabled} onChange={e => setValue(Number(e.target.value))}>{Array.from({ length: 10 }, (_, i) => i + 1).map(k => <option key={k} value={k}>{k}</option>)}</select></label><button disabled={disabled || value === Number(budget.current)} onClick={() => void act(`runs/${encodeURIComponent(id)}/case-budget`, { per_requirement: value })}>Save</button></div>
    {!rows(budget.rows).some(r => Number(r.cases) > 0) ? <p className="notice">Time estimates appear after the requirements step, when the requirement count is known.</p> : <Table caption="Case budget estimates" columns={columns} rows={data} />}
    <p className="field-help">Estimates use measured or historical batch time. Cases beyond the budget are dropped by priority (P0 first).</p>
  </Panel>;
}

// ---------- Errors ----------

export function ErrorsView() {
  const { id, run } = useRun();
  const [category, setCategory] = useState('');
  const items = run.errors?.items ?? [];
  const counts = run.errors?.counts ?? {};
  const data = items.filter(e => !category || e.category === category).map(e => {
    const ids = Array.isArray(e.case_ids) ? e.case_ids.map(value => text(value)) : [];
    return { time: e.at ? new Date(text(e.at)).toLocaleString() : '—', stage: label(e.stage), batch: e.batch == null ? '—' : text(e.batch), cases: ids.slice(0, 2).join(', ') + (ids.length > 2 ? ` +${ids.length - 2}` : ''), category: e.category, summary: text(e.summary), detail: text(e.detail), id: text(e.id) };
  });
  const columns: Column[] = [
    { key: 'time', label: 'Time', width: '150px' }, { key: 'stage', label: 'Stage', width: '130px' }, { key: 'batch', label: 'Batch', width: '70px' },
    { key: 'cases', label: 'Cases', width: '150px', render: row => <span className="cell-id">{text(row.cases)}</span> },
    { key: 'category', label: 'Category', width: '130px', render: row => <StatusBadge value={row.category} tone="bad" /> },
    { key: 'summary', label: 'Summary', render: row => <details><summary>{text(row.summary)}</summary><pre className="error-detail">{text(row.detail)}</pre></details> },
  ];
  return <DetailPage level={2} title="Errors" eyebrow="Run errors" children={<>
    {!items.length ? <p className="empty-inline">No errors recorded for this run.</p> : <>
      <div className="header-actions chip-row" role="group" aria-label="Filter by category">{Object.entries(counts).map(([c, n]) => <button key={c} className="small" aria-pressed={category === c} onClick={() => setCategory(category === c ? '' : c)}>{label(c)} {n}</button>)}</div>
      <Table caption={`Errors for ${runName(run, id)} · ${id}`} columns={columns} rows={data} searchable empty="No errors." />
    </>}
  </>} />;
}

// ---------- Artifacts ----------

export function ArtifactsIndexView() {
  const { id, run } = useRun();
  const data = artifacts(run);
  const available = STAGES.filter(stage => data[stage] !== undefined);
  const columns: Column[] = [
    { key: 'stage', label: 'Stage', render: row => <Link href={`${base(id)}/artifacts/${text(row.stageId)}`}>{text(row.stage)}</Link> },
    { key: 'revision', label: 'Revision', width: '120px' }, { key: 'sections', label: 'Sections', width: '120px' },
  ];
  const rowsData = available.map(stage => ({ stageId: stage, stage: label(stage), revision: text(record(run.state.revisions)[stage]), sections: isPlainObject(data[stage]) ? Object.keys(data[stage] as Row).length : 1 }));
  return <DetailPage level={2} title="Artifacts" eyebrow="Saved stage outputs" children={<>
    <p>Each stage saves its output as sections. Open a stage to read its sections; each section also opens as its own page.</p>
    {!available.length ? <p className="empty-inline">No artifacts yet.</p> : <Table caption="Saved stage artifacts" columns={columns} rows={rowsData} rowHref={row => `${base(id)}/artifacts/${text(row.stageId)}`} />}
  </>} />;
}

export function StageView() {
  const { id, run } = useRun();
  const { stage = '' } = useParams<{ stage: string }>();
  const data = artifacts(run);
  const value = data[stage];
  const runId = id;
  if (value === undefined) return <DetailPage breadcrumbs={[{ label: 'Runs', href: '/runs' }, { label: runName(run, id), href: base(id) }, { label: 'Artifacts', href: `${base(id)}/artifacts` }, { label: label(stage) }]} title={label(stage)} id={stage} children={<EmptyState title="No saved artifact yet">{label(stage)} has no saved output. You can revisit this stage while the run continues.</EmptyState>} />;
  const keys = isPlainObject(value) ? Object.keys(value) : [];
  const sections: SectionSpec[] = keys.length ? keys.map((key, i) => ({
    id: `section-${key}`, title: label(key), count: Array.isArray(record(value)[key]) ? (record(value)[key] as unknown[]).length : undefined,
    defaultOpen: i < 2, expandHref: `${base(runId)}/artifacts/${encodeURIComponent(stage)}/${encodeURIComponent(key)}`,
    render: () => isCaseKey(key) ? <CaseTable cases={rows(record(value)[key])} requirements={record(data.requirements)} caption={caseCaption(key)} /> : <SectionBody value={record(value)[key]} keyName={key} />,
  })) : [{ id: 'section-content', title: 'Content', defaultOpen: true, render: () => <SectionBody value={value} keyName={stage} /> }];
  return <DetailPage
    breadcrumbs={[{ label: 'Runs', href: '/runs' }, { label: runName(run, id), href: base(id) }, { label: 'Artifacts', href: `${base(id)}/artifacts` }, { label: label(stage) }]}
    eyebrow="Stage artifact" title={label(stage)} id={stage} status={record(run.state.approved)[stage] ? 'approved' : 'not approved'}
    meta={[{ label: 'Revision', value: text(record(run.state.revisions)[stage]) }, { label: 'Sections', value: keys.length || 1 }]}
    downloads={[{ label: 'Markdown', href: download(id, stage, 'md') }, { label: 'JSON', href: download(id, stage, 'json') }]}
    sections={sections} />;
}

export function SectionView() {
  const { id, run } = useRun();
  const { stage = '', section = '' } = useParams<{ stage: string; section: string }>();
  const data = artifacts(run);
  const container = record(data[stage]);
  const value = container[section];
  const crumbs = [{ label: 'Runs', href: '/runs' }, { label: runName(run, id), href: base(id) }, { label: 'Artifacts', href: `${base(id)}/artifacts` }, { label: label(stage), href: `${base(id)}/artifacts/${encodeURIComponent(stage)}` }, { label: label(section) }];
  if (value === undefined) return <DetailPage breadcrumbs={crumbs} title={label(section)} id={`${stage}.${section}`} children={<EmptyState title="Section not found">This stage has no section named {section}.</EmptyState>} />;
  return <DetailPage breadcrumbs={crumbs} eyebrow={`${label(stage)} · section`} title={label(section)} id={`${stage}.${section}`} status={undefined}
    meta={[{ label: 'Stage', value: label(stage) }, ...(typeof value === 'string' ? [] : [{ label: 'Contents', value: describe(value) }])]}
    actions={<JsonDownload value={value} filename={`${stage}-${section}.json`} label="Section JSON" />}>
    {isCaseKey(section) ? <CaseTable cases={rows(value)} requirements={record(data.requirements)} caption={`${label(section)} cases`} /> : <SectionBody value={value} keyName={section} />}
  </DetailPage>;
}

// ---------- Generated tests ----------

export function TestsView() {
  const { id, run } = useRun();
  const data = record(artifacts(run).test_generation);
  const files = rows(data.files); const validation = record(data.validation);
  const latestReviews = new Map<string, Row>(); rows(run.state.test_reviews).forEach(batch => rows(batch.reviews).forEach(review => latestReviews.set(text(review.case_id), review)));
  const reviews = rows(data.case_reviews).length ? rows(data.case_reviews) : [...latestReviews.values()].sort((a, b) => Number(b.confidence) - Number(a.confidence));
  const fileColumns: Column[] = [
    { key: 'path', label: 'File', render: row => <Link href={`${base(id)}/tests/${text(row.index)}`}>{text(row.path)}</Link> },
    { key: 'cases', label: 'Cases', width: '260px', render: row => <span className="cell-id">{text(row.cases)}</span> },
    { key: 'source', label: 'Source', width: '110px', render: row => <a className="button small" href={download(id, 'source', 'json', Number(row.index))} download>Source ↓</a> },
  ];
  const fileRows = files.map((file, index) => ({ index, path: text(file.path), cases: text(file.case_ids) }));
  if (!Object.keys(data).length) return <DetailPage level={2} title="Generated tests" eyebrow="Test generation" children={<>
    <p className="empty-inline">Generated test source is not available yet.</p>
    {reviews.length > 0 && <Panel title="Independent test review" storageKey={`${id}:test-review`}><p>Latest source-based review per case. These scores are judgments, not pass probabilities.</p><Table caption="Independent test review" columns={reviewColumns} rows={reviews} searchable /></Panel>}
  </>} />;
  return <DetailPage level={2} title="Generated tests" eyebrow="Test generation" children={<>
    <Metrics values={{ generated_files: validation.files || files.length, test_declarations: validation.test_declarations, covered_cases: Array.isArray(validation.covered_case_ids) ? validation.covered_case_ids.length : 0 }} />
    <p>Independent reviewer scores are source-based judgments, not pass probabilities. Cases are ranked by confidence; execution results remain authoritative.</p>
  </>} sections={[
    { id: 'files', title: 'Generated files', count: files.length, defaultOpen: true, render: () => <Table caption="Generated files" columns={fileColumns} rows={fileRows} searchable /> },
    { id: 'catalog', title: 'Case catalog', count: rows(data.case_catalog).length, defaultOpen: true, render: () => <Table caption="Case catalog" columns={catalogColumns} rows={rows(data.case_catalog)} searchable rowHref={row => recordHref(id, 'case', text(row.id))} /> },
    { id: 'review', title: 'Independent test review', count: reviews.length, render: () => <Table caption="Independent test review" columns={reviewColumns} rows={reviews} searchable /> },
    { id: 'validation', title: 'Generation validation', render: () => <Fields data={data.validation} /> },
  ]} />;
}
const reviewColumns: Column[] = [
  { key: 'confidence', label: 'Confidence', width: '110px' }, { key: 'case_id', label: 'Case ID', width: '150px', render: row => <span className="cell-id">{text(row.case_id)}</span> },
  { key: 'verdict', label: 'Verdict', width: '120px', render: row => <StatusBadge value={row.verdict} /> }, { key: 'reason', label: 'Reason' }, { key: 'source_files', label: 'Source files', width: '200px' },
];
const catalogColumns: Column[] = [
  { key: 'confidence', label: 'Confidence', width: '110px' }, { key: 'title', label: 'Title' }, { key: 'reviewer_verdict', label: 'Reviewer verdict', width: '140px', render: row => <StatusBadge value={row.reviewer_verdict} /> },
  { key: 'reviewer_reason', label: 'Reviewer reason' }, { key: 'priority', label: 'Priority', width: '90px' }, { key: 'expected_result', label: 'Expected result' },
];

export function TestFileView() {
  const { id, run } = useRun();
  const { file = '' } = useParams<{ file: string }>();
  const files = rows(record(artifacts(run).test_generation).files);
  const index = Number(file);
  const item = Number.isInteger(index) && index >= 0 && index < files.length ? files[index] : null;
  const crumbs = [{ label: 'Runs', href: '/runs' }, { label: runName(run, id), href: base(id) }, { label: 'Generated tests', href: `${base(id)}/tests` }, { label: item ? text(item.path).split(/[\\/]/).pop() || 'File' : 'File' }];
  if (!item) return <DetailPage breadcrumbs={crumbs} title="Generated file" id={file} children={<EmptyState title="Generated file not found">This run has no generated file with that number.</EmptyState>} />;
  return <DetailPage breadcrumbs={crumbs} eyebrow="Generated test file" title={text(item.path).split(/[\\/]/).pop() || 'File'} id={text(item.path)}
    meta={[{ label: 'Cases', value: text(item.case_ids) }]} downloads={[{ label: 'Source', href: download(id, 'source', 'json', index) }]}>
    <pre className="source-code" tabIndex={0} aria-label="Generated source">{text(item.content)}</pre>
  </DetailPage>;
}

// ---------- QA report and steps ----------

export function StepList({ runId }: { runId: string }) {
  const [steps, setSteps] = useState<Row[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void api<{ steps: Row[] }>(`runs/${encodeURIComponent(runId)}/download?kind=step&format=json`, undefined, controller.signal).then(r => setSteps(rows(r.steps))).catch(e => { if (!controller.signal.aborted) setError((e as Error).message); });
    return () => controller.abort();
  }, [runId]);
  if (error) return <p className="notice bad" role="alert">{error}</p>;
  if (!steps) return <p className="empty-inline" role="status">Loading step reports…</p>;
  return <Table caption="Step reports" columns={[{ key: 'title', label: 'Step', render: row => text(row.title).replace(/^Step \d+: /, '') }, { key: 'status', label: 'Status', width: '150px', render: row => <StatusBadge value={row.status} /> }]} rows={steps.map(step => ({ ...step, title: text(step.title), status: text(step.status), name: text(step.name) }))} rowHref={row => `${base(runId)}/qa/steps/${encodeURIComponent(text(row.name))}`} />;
}

export function StepView() {
  const { id, run } = useRun();
  const { step = '' } = useParams<{ step: string }>();
  const [report, setReport] = useState<Report | null>(null);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  // The step list is read once for the status badge; the report is read once per step (no refetch on busy flips).
  useEffect(() => {
    const controller = new AbortController();
    void api<{ steps: Row[] }>(`runs/${encodeURIComponent(id)}/download?kind=step&format=json`, undefined, controller.signal).then(r => setStatus(text(rows(r.steps).find(s => s.name === step)?.status || ''))).catch(() => undefined);
    return () => controller.abort();
  }, [id, step]);
  useEffect(() => {
    const controller = new AbortController();
    setReport(null); setError('');
    const source = `${download(id, 'step', 'md')}&name=${encodeURIComponent(step)}`;
    void fetch(source, { cache: 'no-store', signal: controller.signal }).then(r => (r.ok ? r.text() : Promise.reject(new Error('Step report unavailable.')))).then(body => setReport(parseReport(body))).catch(e => { if (!controller.signal.aborted) setError((e as Error).message); });
    return () => controller.abort();
  }, [id, step]);
  const crumbs = [{ label: 'Runs', href: '/runs' }, { label: runName(run, id), href: base(id) }, { label: 'QA report', href: `${base(id)}/qa` }, { label: step }];
  if (error) return <DetailPage breadcrumbs={crumbs} title="Step report" id={step} children={<p className="notice bad" role="alert">{error}</p>} />;
  if (!report) return <DetailPage breadcrumbs={crumbs} title="Step report" id={step} children={<p className="empty-inline" role="status">Loading report…</p>} />;
  const preamble = report.sections.find(s => !s.title);
  const sections: SectionSpec[] = report.sections.filter(s => s.title).map((s, i) => ({ id: `step-section-${i}`, title: s.title, defaultOpen: i < 2, render: () => <ReportBlocks blocks={s.blocks} caption={s.title} /> }));
  return <DetailPage breadcrumbs={crumbs} eyebrow="Step report" title={report.title || step} id={step} status={status || undefined}
    downloads={[{ label: 'Markdown', href: `${download(id, 'step', 'md')}&name=${encodeURIComponent(step)}` }]}
    sections={sections}>
    {preamble && <ReportBlocks blocks={preamble.blocks} caption={report.title || step} />}
  </DetailPage>;
}

// ---------- Test reports and logs ----------

type EvidenceFile = { id: string; path: string; name: string; kind: string; bytes: number; updated: number };
export function useEvidenceFiles(id: string, busy: boolean) {
  const [files, setFiles] = useState<EvidenceFile[] | null>(null);
  const [error, setError] = useState('');
  const [truncated, setTruncated] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try { const data = await api<{ files: EvidenceFile[]; truncated: boolean }>(`runs/${encodeURIComponent(id)}/evidence`, undefined, controller.signal); setFiles(data.files); setTruncated(data.truncated); setError(''); }
      catch (e) { if (!controller.signal.aborted) setError((e as Error).message); }
      if (busy && !controller.signal.aborted) timer = setTimeout(load, 4000);
    };
    void load(); return () => { controller.abort(); clearTimeout(timer); };
  }, [id, busy]);
  return { files: files ?? [], error, truncated, loaded: files !== null };
}

const kindLabel = (kind: string) => (kind === 'test-results' ? 'Test results (XML)' : label(kind));
const fileIcon = (kind: string) => (kind === 'report' ? '◫' : kind === 'screenshot' ? '▧' : kind === 'trace' ? '◈' : '≡');

export function EvidenceView() {
  const { id, run } = useRun();
  const { files, error, truncated } = useEvidenceFiles(id, run.busy);
  const [kind, setKind] = useState('');
  const [search, setSearch] = useState('');
  const router = useRouter(); const pathname = usePathname(); const selected = useSearchParams().get('file') || '';
  const available = files.filter(f => f.kind !== 'log');
  const shown = available.filter(f => (!kind || f.kind === kind) && f.path.toLowerCase().includes(search.toLowerCase()));
  const file = shown.find(f => f.id === selected) || shown.find(f => f.kind === 'report') || shown[0];
  const url = file ? `/api/harness/runs/${encodeURIComponent(id)}/evidence/${file.id}` : '';
  const pick = (value: string) => router.replace(`${pathname}?file=${encodeURIComponent(value)}`, { scroll: false });
  return <DetailPage level={2} eyebrow="Browser evidence" title="Test reports" children={<>
    <p>Open saved Playwright reports, inspect screenshots and download traces.</p>
    {error && <p className="notice bad" role="alert">{error}</p>}
    {truncated && <p className="notice">The evidence scan reached its file limit. Additional files remain in the run directory.</p>}
    <div className="evidence-layout">
      <Panel title="Reports & attachments" storageKey={`${id}:files:reports`}>
        <input type="search" aria-label="Search evidence files" placeholder="Search files or attempts…" value={search} onChange={e => setSearch(e.target.value)} />
        <div className="evidence-types" role="group" aria-label="Evidence type">{['', 'report', 'screenshot', 'trace', 'test-results'].map(k => <button type="button" key={k || 'all'} aria-pressed={kind === k} onClick={() => setKind(k)}>{k ? kindLabel(k) : 'All'}</button>)}</div>
        <div className="evidence-files" tabIndex={0} role="region" aria-label="Evidence files">{shown.map(f => <button type="button" className={file?.id === f.id ? 'selected' : ''} key={f.id} aria-pressed={file?.id === f.id} onClick={() => pick(f.id)}><span className={`file-icon ${f.kind}`} aria-hidden="true">{fileIcon(f.kind)}</span><span><b>{f.name}</b><small>{f.path}</small><small>{kindLabel(f.kind)} · {(f.bytes / 1024).toFixed(1)} KB</small></span></button>)}</div>
        {!shown.length && <EmptyState title={available.length ? 'No matching files' : 'No browser evidence yet'}>Reports and screenshots appear when a browser suite produces them. A run with only unit tests has no browser report.</EmptyState>}
      </Panel>
      <Panel title={file?.name || 'Evidence preview'} storageKey={`${id}:preview`} actions={file ? <a className="button small" href={`${url}?download=1`} download>Download ↓</a> : undefined}>
        {file ? <>
          <p className="evidence-path">{file.path}</p>
          {file.kind === 'report' ? <iframe key={file.id} className="report-frame" title="Playwright report" src={`${url}/view.html`} sandbox="allow-scripts" /> : file.kind === 'screenshot' ? <a href={url} target="_blank" rel="noreferrer"><img className="evidence-image" alt={`Screenshot ${file.path}`} src={url} /></a> : file.kind === 'trace' ? <EmptyState title="Playwright trace" icon="◈">Download this trace and open it with your local Playwright trace viewer.<code>npx playwright show-trace trace.zip</code></EmptyState> : <p className="empty-inline">This file has no inline preview. Use Download.</p>}
        </> : <p className="empty-inline">Select a file to inspect its evidence.</p>}
      </Panel>
    </div>
  </>} />;
}

export function LogsView() {
  const { id, run } = useRun();
  const { files, error, loaded } = useEvidenceFiles(id, run.busy);
  const logs = files.filter(f => f.kind === 'log');
  const columns: Column[] = [
    { key: 'name', label: 'File', render: row => <Link href={`${base(id)}/logs/${encodeURIComponent(text(row.fileId))}`}>{text(row.name)}</Link> },
    { key: 'path', label: 'Path' }, { key: 'size', label: 'Size', width: '110px' }, { key: 'updated', label: 'Updated', width: '190px' },
  ];
  const data = logs.map(f => ({ fileId: f.id, name: f.name, path: f.path, size: `${(f.bytes / 1024).toFixed(1)} KB`, updated: f.updated ? new Date(f.updated * 1000).toLocaleString() : '—' }));
  return <DetailPage level={2} eyebrow="Execution evidence" title="Logs" children={<>
    <p>Find the exact output from installation, generation and test execution. Audit and trace files open as structured events; raw files are available as downloads.</p>
    {error && <p className="notice bad" role="alert">{error}</p>}
    {!loaded ? <p className="empty-inline" role="status">Loading log files…</p> : <Table caption="Log files" columns={columns} rows={data} searchable empty="No logs recorded yet. Files appear when approved commands execute." />}
    <p className="field-help"><Link href={`${base(id)}/audit`}>Approval and activity audit</Link></p>
  </>} />;
}

type TextFile = { text: string; truncated: boolean };
export function LogFileView() {
  const { id, run } = useRun();
  const { fileId = '' } = useParams<{ fileId: string }>();
  const { files } = useEvidenceFiles(id, run.busy);
  const meta = files.find(f => f.id === fileId);
  const [content, setContent] = useState<TextFile | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setContent(null); setError('');
    void api<TextFile>(`runs/${encodeURIComponent(id)}/evidence/${encodeURIComponent(fileId)}`, undefined, controller.signal).then(setContent).catch(e => { if (!controller.signal.aborted) setError((e as Error).message); });
    return () => controller.abort();
  }, [id, fileId]);
  const debouncedQuery = useDebounced(query, 300);
  const [limit, setLimit] = useState(LOG_WINDOW);
  useEffect(() => { setLimit(LOG_WINDOW); }, [debouncedQuery, fileId]);
  const crumbs = [{ label: 'Runs', href: '/runs' }, { label: runName(run, id), href: base(id) }, { label: 'Logs', href: `${base(id)}/logs` }, { label: meta?.name || 'Log' }];
  const title = meta?.name || 'Log file';
  const download_ = `/api/harness/runs/${encodeURIComponent(id)}/evidence/${encodeURIComponent(fileId)}?download=1`;
  const structured = /\.jsonl$/i.test(meta?.name || '');
  const events = useMemo(() => {
    if (!content || !structured) return [] as Row[];
    return content.text.split('\n').filter(Boolean).map((line, i) => { try { const v = JSON.parse(line); return isPlain(v) ? { ...v, line: i + 1 } : { line: i + 1, message: String(v) }; } catch { return { line: i + 1, message: line }; } });
  }, [content, structured]);
  const lines = useMemo(() => (content ? content.text.replace(/\u001b\[[0-9;]*m/g, '').split('\n').map((line, i) => ({ line, i })) : []), [content]);
  const matchingLines = useMemo(() => lines.filter(r => !debouncedQuery || r.line.toLowerCase().includes(debouncedQuery.toLowerCase())), [lines, debouncedQuery]);
  if (error) return <DetailPage breadcrumbs={crumbs} title={title} id={fileId} children={<p className="notice bad" role="alert">{error}</p>} />;
  return <DetailPage breadcrumbs={crumbs} eyebrow="Log file" title={title} id={meta?.path || fileId} meta={meta ? [{ label: 'Size', value: `${(meta.bytes / 1024).toFixed(1)} KB` }] : undefined} downloads={[{ label: 'Raw file', href: download_ }]}>
    {!content ? <p className="empty-inline" role="status">Loading output…</p> : structured ? <>
      <p className="field-help">{events.length} entries{events.length > limit ? `; showing the first ${limit}` : ''}. Each row opens to its fields.</p>
      <EventFeed events={events.slice(0, limit)} />
      {events.length > limit && <button type="button" onClick={() => setLimit(value => value + LOG_WINDOW)}>Show {Math.min(LOG_WINDOW, events.length - limit)} more</button>}
    </> : <>
      {content.truncated && <p className="notice">Showing the first 512 KB. Download the raw file for the full log.</p>}
      <label>Find in output<input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search errors, test names or paths…" /></label>
      <div className="log-output" role="region" aria-label="Log output" tabIndex={0}>{matchingLines.slice(0, limit).map(({ line, i }) => <div className={/\b(ERROR|FAILED|Traceback|Exception)\b|"ok": false|"exit_code": [1-9]/.test(line) ? 'log-error' : ''} key={i}><span>{i + 1}</span><code>{line || ' '}</code></div>)}</div>
      {matchingLines.length > limit && <button type="button" onClick={() => setLimit(value => value + LOG_WINDOW)}>Show {Math.min(LOG_WINDOW, matchingLines.length - limit)} more lines</button>}
    </>}
  </DetailPage>;
}
/** Log and event views show this many rows at first; "Show more" adds the next window. */
const LOG_WINDOW = 1000;
/** A value that settles after `delay` ms without change: keeps search from re-rendering large logs on every key. */
function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => { const timer = setTimeout(() => setSettled(value), delay); return () => clearTimeout(timer); }, [value, delay]);
  return settled;
}
const isPlain = (value: unknown): value is Row => !!value && typeof value === 'object' && !Array.isArray(value);

// ---------- Audit ----------

export function AuditView() {
  const { id, run } = useRun();
  const [data, setData] = useState<Row | null>(null);
  const [error, setError] = useState('');
  const [event, setEvent] = useState(''); const [stage, setStage] = useState('');
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const load = async () => { try { const next = await api<Row>(`runs/${encodeURIComponent(id)}/audit`, undefined, controller.signal); if (!controller.signal.aborted) setData(next); setError(''); } catch (e) { if (!controller.signal.aborted) setError((e as Error).message); } if (run.busy && !controller.signal.aborted) timer = setTimeout(load, 2500); };
    void load(); return () => { controller.abort(); clearTimeout(timer); };
  }, [id, run.busy, run.pending?.id]);
  const events = rows(data?.events);
  const filtered = events.filter(e => (!event || e.event === event) && (!stage || e.stage === stage));
  const eventOptions = [...new Set(events.map(e => text(e.event)))].sort();
  const stageOptions = [...new Set(events.map(e => text(e.stage || '')).filter(Boolean))].sort();
  if (error) return <DetailPage level={2} title="Audit" children={<p className="notice bad" role="alert">{error} Other run views remain available.</p>} />;
  if (!data) return <DetailPage level={2} title="Audit" children={<p className="empty-inline" role="status">Loading persisted evidence…</p>} />;
  return <DetailPage level={2} eyebrow="Approval and activity" title="Audit" downloads={[{ label: 'Audit JSONL', href: download(id, 'audit') }]}
    notices={Number(data.malformed_lines) > 0 ? <p className="notice">{text(data.malformed_lines)} malformed lines were omitted from this view.</p> : undefined}
    sections={[
      { id: 'events', title: 'Approval & activity events', count: events.length, defaultOpen: true, render: () => <>
        <div className="filters" role="group" aria-label="Filter events"><FilterChips label="Event" value={event} set={setEvent} options={eventOptions} /><FilterChips label="Stage" value={stage} set={setStage} options={stageOptions} /></div>
        {filtered.length > 100 && <p className="field-help">Showing the last 100 of {filtered.length} events.</p>}
        <EventFeed events={filtered.slice().reverse().slice(0, 100)} />
      </> },
      { id: 'search', title: 'Search all audit records', count: filtered.length, render: () => <Table caption="Audit records" columns={[{ key: 'at', label: 'At', width: '200px' }, { key: 'event', label: 'Event' }, { key: 'stage', label: 'Stage', width: '150px' }, { key: 'decision', label: 'Decision', width: '120px' }, { key: 'request_id', label: 'Request ID', width: '170px', render: row => <span className="cell-id">{text(row.request_id)}</span> }]} rows={filtered} searchable /> },
      { id: 'provenance', title: 'Runtime & skill provenance', render: () => <Fields data={{ runtime: run.state.runtime_provenance, skills: run.state.skill_provenance, skill_tool_trace: run.state.skill_tool_trace, invocations: run.state.invocations }} /> },
    ]} />;
}

function FilterChips({ label: name, value, set, options }: { label: string; value: string; set: (value: string) => void; options: string[] }) {
  return <fieldset className="filter-chips"><legend>{name}</legend>{['', ...options].map(o => <button type="button" key={o || 'all'} aria-pressed={value === o} onClick={() => set(o)}>{o ? label(o) : 'All'}</button>)}</fieldset>;
}

// ---------- Record pages ----------

export function RecordView() {
  const { id, run } = useRun();
  const { kind = '', recordId = '' } = useParams<{ kind: string; recordId: string }>();
  const found = useMemo(() => findRecord(run, recordId), [run, recordId]);
  const errorItem = (run.errors?.items ?? []).find(item => text(item.id) === recordId);
  const item = found ?? (kind === 'error' ? errorItem ?? null : null);
  const crumbs = [{ label: 'Runs', href: '/runs' }, { label: runName(run, id), href: base(id) }, { label: label(kind) }, { label: recordId }];
  if (!item) return <DetailPage breadcrumbs={crumbs} eyebrow={label(kind)} title={recordId} id={recordId} children={<EmptyState title="Record not found">This run has no {label(kind).toLowerCase()} with ID {recordId}.</EmptyState>} />;
  const title = text(item.title ?? item.text ?? item.summary ?? recordId);
  const description = typeof item.description === 'string' ? item.description : typeof item.text === 'string' ? item.text : '';
  return <DetailPage breadcrumbs={crumbs} eyebrow={label(kind)} title={title} id={recordId} status={item.status ?? item.verdict ?? item.state ?? item.category}
    meta={[{ label: 'Kind', value: label(kind) }, ...(item.priority ? [{ label: 'Priority', value: text(item.priority) }] : [])]}>
    {description && <p className="page-description">{description}</p>}
    <Section title="Details" defaultOpen>{<Fields data={item} skip={['description', 'text', 'title']} />}</Section>
    {Array.isArray(item.steps) && item.steps.length ? <Section title="Steps" count={item.steps.length} defaultOpen><ol className="case-steps">{(item.steps as unknown[]).map((step, i) => <li key={i}>{text(step)}</li>)}</ol></Section> : null}
    {Array.isArray(item.traceability_links) && item.traceability_links.length ? <Section title="Traceability links" count={item.traceability_links.length}><Table caption="Traceability links" columns={[{ key: 'type', label: 'Type' }, { key: 'id', label: 'ID', render: row => <span className="cell-id">{text(row.id)}</span> }]} rows={rows(item.traceability_links)} /></Section> : null}
  </DetailPage>;
}


export function RunPromptsView() {
  const { run } = useRun();
  return <Prompts run={run} />;
}
