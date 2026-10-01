'use client';
import { useState } from 'react';
import { DataTable, DataView, ExportButtons, Metrics, Panel } from './data';
import { label, number, record, rows, text, type Row } from '@/lib/harness';

export default function Observability({ id, report }: { id: string; report: Row }) {
  const [tab, setTab] = useState('Overview');
  const [stage, setStage] = useState('');
  const [category, setCategory] = useState('');
  const [failed, setFailed] = useState(false);
  const [lane, setLane] = useState('');
  const [reqState, setReqState] = useState('');
  const tabs = ['Overview', 'Skills', 'Tools', 'Automation', 'Timeline', 'Traceability', 'Integrity', 'Export'];
  const calls = rows(report.tool_calls);
  const timeline = rows(report.timeline);
  const chain = record(report.traceability);
  const totals = record(report.totals);
  const scope = `${id}:obs:${tab}`;
  return <div>
    <Metrics values={{ provider_calls: totals.provider_calls, reported_tokens: totals.tokens, tool_calls: totals.tool_calls, human_gates: totals.human_gates }} />
    {report.trace_level !== 'full' && <p className="notice">Summary trace: this saved run has no full trace file. Some timings and arguments are unavailable.</p>}
    <div className="tabs sub-tabs" aria-label="Observability views">{tabs.map(t => <button key={t} aria-pressed={tab === t} className={tab === t ? 'selected' : ''} onClick={() => setTab(t)}>{t}</button>)}</div>
    {tab === 'Overview' && <>
      <Panel title="Stage activity & token usage" storageKey={`${scope}:stages`}><div className="bars">{rows(report.stages).map(s => <div key={text(s.stage)}><span>{label(s.stage)}</span><meter min={0} max={Math.max(1, ...rows(report.stages).map(r => Number(r.tokens) || 0))} value={Number(s.tokens) || 0} /><b>{number(s.tokens)}</b></div>)}</div><DataTable data={rows(report.stages)} columns={['stage', 'actor', 'assigned_skills', 'loaded_skills', 'tokens', 'repairs', 'errors']} /></Panel>
      <Panel title="Prompt cache" storageKey={`${scope}:cache`}><DataView value={report.cache} /></Panel>
      <Panel title="Model runs" storageKey={`${scope}:models`}><DataTable data={rows(report.model_runs)} columns={['seq', 'stage', 'phase', 'status', 'duration_ms', 'tokens', 'tool_calls']} /></Panel>
      <Panel title="All run metrics" storageKey={`${scope}:totals`} defaultOpen={false}><DataView value={report.totals} /></Panel>
    </>}
    {tab === 'Skills' && <><Panel title="Skills invoked" storageKey={`${scope}:skills`}><DataTable data={rows(report.skills)} /></Panel><Panel title="Skill tool calls" storageKey={`${scope}:calls`}><DataTable data={calls.filter(c => text(c.category).startsWith('skill'))} /></Panel></>}
    {tab === 'Tools' && <><Panel title="Tool usage" storageKey={`${scope}:summary`}><DataTable data={rows(report.tool_summary)} /></Panel><Panel title="Tool call log" storageKey={`${scope}:calls`}><div className="filters"><Select label="Stage" value={stage} set={setStage} options={calls.map(c => text(c.stage))} /><Select label="Category" value={category} set={setCategory} options={calls.map(c => text(c.category))} /><label className="check"><input type="checkbox" checked={failed} onChange={e => setFailed(e.target.checked)} />Only failures</label></div><DataTable data={calls.filter(c => (!stage || c.stage === stage) && (!category || c.category === category) && (!failed || !c.ok))} /></Panel></>}
    {tab === 'Automation' && Object.entries(record(report.automation)).map(([key, value]) => <Panel key={key} title={label(key)} storageKey={`${scope}:${key}`}><DataView value={value} /></Panel>)}
    {tab === 'Timeline' && <Panel title="Run event timeline" storageKey={`${scope}:timeline`}><Select label="Lane" value={lane} set={setLane} options={timeline.map(t => text(t.lane))} /><div className="timeline">{timeline.filter(t => !lane || lane === t.lane).slice(-150).map((t, i) => <details key={i}><summary><span>{t.t == null ? '—' : `${t.t}s`}</span><b>{text(t.label)}</b><small>{label(t.stage)} · {text(t.lane)}</small></summary><DataView value={t} /></details>)}</div><DataTable data={timeline.filter(t => !lane || lane === t.lane)} /></Panel>}
    {tab === 'Traceability' && <><Panel title="Requirement → case → file → result" storageKey={`${scope}:chain`}><Select label="Requirement state" value={reqState} set={setReqState} options={rows(chain.chain).map(r => text(r.state))} /><DataTable data={rows(chain.chain).filter(r => !reqState || r.state === reqState).flatMap(r => (rows(r.cases).length ? rows(r.cases) : [{}]).map(c => ({ requirement: r.requirement, text: r.text, state: r.state, actor: r.actor, case: c.case, file: c.file, status: c.status })))} /><DataView value={{ produced_by: chain.produced_by, case_status_counts: chain.case_status_counts, requirements_without_cases: chain.requirements_without_cases, orphan_cases: chain.orphan_cases }} /></Panel></>}
    {tab === 'Integrity' && <><Panel title="Integrity checks" storageKey={`${scope}:checks`}><DataTable data={rows(report.checks)} /></Panel><Panel title="Telemetry" storageKey={`${scope}:telemetry`}><DataView value={record(report.run).telemetry} /></Panel></>}
    {tab === 'Export' && <Panel title="Export observability" storageKey={`${scope}:export`} actions={<ExportButtons id={id} kind="observability" />}><p>Reports are rebuilt from this run’s persisted evidence. Prompts and model sessions are excluded.</p><details><summary>Complete report JSON</summary><pre>{JSON.stringify(report, null, 2)}</pre></details></Panel>}
  </div>;
}
export function Select({ label: name, value, set, options }: { label: string; value: string; set: (value: string) => void; options: string[] }) {
  return <fieldset className="filter-chips"><legend>{name}</legend>{['', ...new Set(options.filter(Boolean).sort())].map(o => <button type="button" key={o} aria-pressed={value === o} onClick={() => set(o)}>{o ? label(o) : 'All'}</button>)}</fieldset>;
}
