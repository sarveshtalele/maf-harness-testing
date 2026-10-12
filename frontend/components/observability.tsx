'use client';
import { useEffect, useState } from 'react';
import { api, download, label, number, record, rows, text, type Row, type Run } from '@/lib/harness';
import { DetailPage, Fields, Metrics, Section, Table, type Column } from './page';
import Icon, { type IconName } from './icons';
import { useRun } from './run-shell';

const TABS = ['Overview', 'Skills', 'Tools', 'Automation', 'Timeline', 'Traceability', 'Integrity', 'Export'];
const TAB_ICONS: Record<string, IconName> = { Overview: 'grid', Skills: 'spark', Tools: 'wrench', Automation: 'bolt', Timeline: 'clock', Traceability: 'branch', Integrity: 'lock', Export: 'download' };

/** Observability for a saved run. Polls only while the run is busy (finding 12). */
export function ObservabilityView() {
  const { id, run } = useRun();
  const [report, setReport] = useState<Row | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try { const next = await api<Row>(`runs/${encodeURIComponent(id)}/observability`, undefined, controller.signal); if (!controller.signal.aborted) setReport(next); setError(''); }
      catch (e) { if (!controller.signal.aborted) setError((e as Error).message); }
      if (run.busy && !controller.signal.aborted) timer = setTimeout(load, 2500);
    };
    void load(); return () => { controller.abort(); clearTimeout(timer); };
  }, [id, run.busy]);
  if (error) return <p className="notice bad" role="alert">{error} Other run views remain available.</p>;
  if (!report) return <p className="empty-inline" role="status">Loading persisted evidence…</p>;
  return <ObservabilityReport id={id} report={report} run={run} />;
}

export function ObservabilityReport({ id, report, run }: { id: string; report: Row; run: Run }) {
  const [tab, setTab] = useState('Overview');
  const [stage, setStage] = useState('');
  const [category, setCategory] = useState('');
  const [failed, setFailed] = useState(false);
  const [lane, setLane] = useState('');
  const [reqState, setReqState] = useState('');
  const calls = rows(report.tool_calls);
  const stageRows = rows(report.stages).filter(row => !stage || row.stage === stage);
  const timeline = rows(report.timeline);
  const chain = record(report.traceability);
  const totals = stage ? stageRows[0] || {} : record(report.totals);
  const subtabs = (
    <div className="tabs sub-tabs" role="group" aria-label="Observability views">{TABS.map(t => <button type="button" key={t} aria-pressed={tab === t} className={tab === t ? 'selected' : ''} onClick={() => setTab(t)}><Icon name={TAB_ICONS[t]} />{t}</button>)}</div>
  );
  const stageSelect = <FilterChips label="Workflow step" value={stage} set={setStage} options={rows(report.stages).map(s => text(s.stage))} />;
  const stageColumns: Column[] = [
    { key: 'stage', label: 'Stage', render: row => label(row.stage) }, { key: 'actor', label: 'Actor' }, { key: 'assigned_skills', label: 'Assigned skills' },
    { key: 'loaded_skills', label: 'Loaded skills' }, { key: 'execution_mode', label: 'Execution mode' }, { key: 'script_runs', label: 'Script runs', width: '110px' },
    { key: 'tokens', label: 'Tokens', width: '110px' }, { key: 'repairs', label: 'Repairs', width: '100px' }, { key: 'errors', label: 'Errors', width: '100px' },
  ];
  return <DetailPage level={2} title="Observability" eyebrow="Run telemetry" children={<>
    {stageSelect}
    <Metrics values={{ provider_calls: totals.provider_calls, reported_tokens: totals.tokens, tool_calls: totals.tool_calls, human_gates: totals.human_gates }} />
    {report.trace_level !== 'full' && <p className="notice">Summary trace: this saved run has no full trace file. Some timings and arguments are unavailable.</p>}
    {subtabs}
    {tab === 'Overview' && <>
      <Section title="Stage activity & token usage" defaultOpen><div className="bars">{stageRows.map(s => <div key={text(s.stage)}><span>{label(s.stage)}</span><meter min={0} max={Math.max(1, ...rows(report.stages).map(r => Number(r.tokens) || 0))} value={Number(s.tokens) || 0} /><b>{number(s.tokens)}</b></div>)}</div><Table caption="Stage activity" columns={stageColumns} rows={stageRows} /></Section>
      <Section title="Prompt cache"><Fields data={report.cache} /></Section>
      <Section title="Model runs"><Table caption="Model runs" columns={[{ key: 'seq', label: 'Seq', width: '80px' }, { key: 'stage', label: 'Stage' }, { key: 'phase', label: 'Phase' }, { key: 'status', label: 'Status' }, { key: 'duration_ms', label: 'Duration (ms)' }, { key: 'tokens', label: 'Tokens' }, { key: 'tool_calls', label: 'Tool calls' }]} rows={rows(report.model_runs).filter(r => !stage || r.stage === stage)} searchable /></Section>
      <Section title="All run metrics"><Fields data={report.totals} /></Section>
    </>}
    {tab === 'Skills' && <><Section title="Skills invoked" defaultOpen><Table caption="Skills invoked" rows={rows(report.skills).filter(r => !stage || Array.isArray(r.stages) && r.stages.includes(stage))} searchable /></Section><Section title="Skill tool calls"><Table caption="Skill tool calls" rows={calls.filter(c => (!stage || c.stage === stage) && text(c.category).startsWith('skill'))} searchable /></Section></>}
    {tab === 'Tools' && <><Section title="Tool usage" defaultOpen><Table caption="Tool usage" rows={rows(report.tool_summary)} searchable /></Section><Section title="Tool call log" defaultOpen><div className="filters"><FilterChips label="Category" value={category} set={setCategory} options={calls.map(c => text(c.category))} /><label className="check"><input type="checkbox" checked={failed} onChange={e => setFailed(e.target.checked)} />Only failures</label></div><Table caption="Tool call log" rows={calls.filter(c => (!stage || c.stage === stage) && (!category || c.category === category) && (!failed || !c.ok))} searchable /></Section></>}
    {tab === 'Automation' && Object.entries(record(report.automation)).map(([key, value]) => <Section key={key} title={label(key)}><Fields data={value} /></Section>)}
    {tab === 'Timeline' && <Section title="Run event timeline" defaultOpen><FilterChips label="Lane" value={lane} set={setLane} options={timeline.map(t => text(t.lane))} /><ol className="timeline">{timeline.filter(t => (!stage || t.stage === stage) && (!lane || lane === t.lane)).slice(-150).map((t, i) => <li key={i}><details><summary><span>{t.t == null ? '—' : `${t.t}s`}</span><b>{text(t.label)}</b><small>{label(t.stage)} · {text(t.lane)}</small></summary><Fields data={t} /></details></li>)}</ol></Section>}
    {tab === 'Traceability' && <><Section title="Requirement → case → file → result" defaultOpen><FilterChips label="Requirement state" value={reqState} set={setReqState} options={rows(chain.chain).map(r => text(r.state))} /><Table caption="Requirement to case chain" searchable rows={rows(chain.chain).filter(r => !reqState || r.state === reqState).flatMap(r => (rows(r.cases).length ? rows(r.cases) : [{}]).map(c => ({ requirement: r.requirement, text: r.text, state: r.state, actor: r.actor, case: c.case, file: c.file, status: c.status })))} columns={[{ key: 'requirement', label: 'Requirement' }, { key: 'text', label: 'Description' }, { key: 'state', label: 'State' }, { key: 'actor', label: 'Actor' }, { key: 'case', label: 'Case' }, { key: 'file', label: 'File' }, { key: 'status', label: 'Status' }]} /></Section><Section title="Coverage summary"><Fields data={{ produced_by: chain.produced_by, case_status_counts: chain.case_status_counts, requirements_without_cases: chain.requirements_without_cases, orphan_cases: chain.orphan_cases }} /></Section></>}
    {tab === 'Integrity' && <><Section title="Integrity checks" defaultOpen><Table caption="Integrity checks" rows={rows(report.checks)} searchable /></Section><Section title="Telemetry"><Fields data={record(report.run).telemetry} /></Section></>}
    {tab === 'Export' && <Section title="Export observability" defaultOpen actions={<><a className="button small" href={download(id, 'observability', 'md')} download>Markdown ↓</a><a className="button small" href={download(id, 'observability', 'json')} download>JSON ↓</a></>}><p>Reports are rebuilt from this run’s persisted evidence. Prompts and model sessions are excluded.</p></Section>}
  </>} />;
}

function FilterChips({ label: name, value, set, options }: { label: string; value: string; set: (value: string) => void; options: string[] }) {
  return <fieldset className="filter-chips"><legend>{name}</legend>{['', ...new Set(options.filter(Boolean).sort())].map(o => <button type="button" key={o || 'all'} aria-pressed={value === o} onClick={() => set(o)}>{o ? label(o) : 'All'}</button>)}</fieldset>;
}
