'use client';
import Link from 'next/link';
import { artifacts, download, label, record, rows, text, type Row } from '@/lib/harness';
import { DetailPage, EmptyState, Fields, Metrics, Section, StatusBadge, Table, recordHref, useRunId, type Column, type SectionSpec } from '../page';
import Icon from '../icons';
import { useRun } from '../run-shell';
import { StepList, base } from './run-pages';
import { VerdictBanner, runName } from './shared';

const isObject = (value: unknown): value is Row => !!value && typeof value === 'object' && !Array.isArray(value);
const list = (value: unknown): string => (Array.isArray(value) ? value.map(item => text(item)).join(', ') : text(value));
const dash = (value: unknown) => (value == null || value === '' ? '—' : text(value));

/** Summary line counts: controls, each final status, and the step where controls failed. */
function traceCounts(summary: Row): Row {
  const out: Row = { controls: summary.controls ?? 0 };
  for (const [key, value] of Object.entries(record(summary.final_status))) out[key] = value;
  for (const [step, value] of Object.entries(record(summary.failed_at))) out[`failed at ${label(step)}`] = value;
  return out;
}

const TRACE_COLUMNS: Column[] = [
  { key: 'control_id', label: 'Control', width: '110px', render: row => <span className="cell-id">{dash(row.control_id)}</span> },
  { key: 'requirement_id', label: 'Requirement', width: '120px', render: row => <span className="cell-id">{dash(row.requirement_id)}</span> },
  { key: 'plan', label: 'Plan area', width: '180px', render: row => <>{dash(row.plan_area_id)} {dash(row.plan_area_name)}</> },
  { key: 'case_ids', label: 'Test case(s)', width: '170px', render: row => <span className="cell-id">{list(row.case_ids) || '—'}</span> },
  { key: 'test_files', label: 'Test file(s)', width: '170px', render: row => <span className="cell-id">{list(row.test_files) || '—'}</span> },
  { key: 'generation', label: 'Generation', width: '120px', render: row => <StatusBadge value={row.generation} /> },
  { key: 'execution', label: 'Execution', width: '120px', render: row => <StatusBadge value={row.execution} /> },
  { key: 'final_status', label: 'Status', width: '130px', render: row => <StatusBadge value={row.final_status} /> },
  { key: 'failed_at', label: 'Failed at', width: '120px', render: row => dash(row.failed_at ? label(row.failed_at) : '') },
  { key: 'reason', label: 'Reason' },
];

const CASE_COLUMNS: Column[] = [
  { key: 'id', label: 'Test case ID', width: '150px', render: row => <span className="cell-id">{text(row.id)}</span> },
  { key: 'control_ids', label: 'Control', width: '130px', render: row => <span className="cell-id">{list(row.control_ids) || '—'}</span> },
  { key: 'title', label: 'Case' },
  { key: 'level', label: 'Level', width: '90px' },
  { key: 'actor', label: 'Actor', width: '110px' },
  { key: 'priority', label: 'Priority', width: '90px' },
  { key: 'confidence', label: 'Confidence', width: '110px' },
  { key: 'status', label: 'Status', width: '130px', render: row => <StatusBadge value={row.status} /> },
  { key: 'file', label: 'File' },
];

/** The six-column test-case table for one category (Sno restarts in each table), with per-case details below. */
function CategoryCases({ cases, caption }: { cases: Row[]; caption: string }) {
  const runId = useRunId();
  const view = cases.map((item, index) => ({
    sno: index + 1,
    reqs: list(item.requirement_ids) || '—',
    caseId: text(item.id),
    title: text(item.title),
    description: text(item.description),
    meta: [item.priority, item.level, item.actor, item.target].map(value => text(value)).filter(value => value !== '—').join(' · '),
    steps: Array.isArray(item.steps) ? item.steps.map(step => text(step)) : [],
    expected: text(item.expected_result),
  }));
  const columns: Column[] = [
    { key: 'sno', label: 'Sno', width: '64px' },
    { key: 'reqs', label: 'Requirement ID', width: '170px', render: row => <span className="cell-id">{text(row.reqs)}</span> },
    { key: 'caseId', label: 'Test Case ID', width: '170px', render: row => <span className="cell-id">{text(row.caseId)}</span> },
    { key: 'title', label: 'Test Case Description', width: '260px', render: row => <div className="case-description"><strong>{text(row.title)}</strong>{text(row.description) !== '—' && text(row.description) !== text(row.title) && <small>{text(row.description)}</small>}{text(row.meta) !== '—' && <small className="case-meta">{text(row.meta)}</small>}{runId && text(row.caseId) !== '—' && <Link href={recordHref(runId, 'case', text(row.caseId))} className="case-open">Open case ›</Link>}</div> },
    { key: 'steps', label: 'Steps', width: '240px', render: row => Array.isArray(row.steps) && row.steps.length ? <ol className="case-steps">{(row.steps as string[]).map((step, i) => <li key={i}>{step}</li>)}</ol> : <span className="muted">—</span> },
    { key: 'expected', label: 'Expected Result', width: '220px' },
  ];
  return <>
    <Table caption={caption} columns={columns} rows={view as unknown as Row[]} searchable empty="No test cases recorded." />
    <details className="case-details"><summary>Case details</summary>{cases.map((item, i) => <Section key={i} title={`${text(item.id)} · ${text(item.title)}`}><Fields data={{ priority: item.priority, level: item.level, actor: item.actor, target: item.target, preconditions: item.preconditions, data_notes: item.data_notes, traceability_links: item.traceability_links }} /></Section>)}</details>
  </>;
}

const failureCards = (run: { id: string }, failures: Row[], updates: Row[]) => (
  failures.length ? <div className="failure-cards">{failures.map((c, i) => {
    const related = updates.filter(a => a.case_id === c.id || a.case === c.title);
    return <article className="failure-card" key={text(c.id) || i}><header><div><span className="eyebrow">{text(c.id)} · {label(c.level)}</span><h3>{text(c.title)}</h3></div><StatusBadge value={c.status} /></header><code className="failure-location">{text(c.file || 'No generated file recorded')}</code><div className="failure-reason"><b>Observed failure</b><p className="failure-message">{text(c.message || 'No failure output was recorded. This case did not execute; inspect the execution logs and environment status.')}</p></div><div className="failure-next"><span aria-hidden="true">↗</span><div><b>Recommended next step</b>{related.length ? related.map((a, n) => <div key={n}><StatusBadge value={a.classification} tone="" /><p>{text(a.action)}</p><small>{text(a.file)}{a.lines ? ` · lines ${list(a.lines)}` : a.line ? `:${text(a.line)}` : ''}</small></div>) : <p>Inspect this case’s output and the execution logs. Confirm the environment and reproduce the behavior before changing the application or its test.</p>}</div></div></article>;
  })}</div> : <div className="success-note"><span aria-hidden="true">✓</span><div><b>No failed cases in the saved QA report</b><p>Review requirement coverage and warnings before treating this run as complete coverage.</p></div></div>
);

/** The QA report as structured sections, in the same order as the Markdown export (qa/report.py render_markdown). */
export function QaView() {
  const { id, run } = useRun();
  const execution = record(artifacts(run).test_execution); const qa = record(execution.qa_report);
  const crumbs = [{ label: 'Runs', href: '/runs' }, { label: runName(run, id), href: base(id) }, { label: 'QA report' }];
  if (!Object.keys(qa).length) return <DetailPage level={2} title="QA report" eyebrow="Saved execution verdict" children={<EmptyState title="Your results will appear here" icon={<Icon name="shield" />}>Approve execution to produce case results, requirement coverage and failure evidence.</EmptyState>} />;
  const repo = record(qa.repository);
  const updates = record(qa.updates);
  const generatedUpdates = rows(updates.generated_test_files_to_update);
  const repoUpdates = rows(updates.repository_files_to_review);
  const requirementUpdates = rows(updates.requirements_needing_tests);
  const envUpdates = rows(updates.environment_actions);
  const anyUpdates = generatedUpdates.length + repoUpdates.length + requirementUpdates.length + envUpdates.length > 0;
  const failures = rows(qa.failures).length ? rows(qa.failures) : rows(qa.cases).filter(c => ['failed', 'error', 'not_executed', 'generation_failed'].includes(text(c.status)));
  const verification = record(qa.verification);
  const reviewMiss = rows(qa.review_miss);
  const sections: SectionSpec[] = [
    { id: 'verdict', title: 'Verdict and case counts', defaultOpen: true, render: () => <>
      <VerdictBanner name={qa.verdict} meta={<>Execution: {text(qa.execution_status)} · Runner: {text(qa.adapter)}</>} label="Saved execution verdict" />
      <Metrics values={record(qa.case_status_counts)} />
      <p className="field-help">Line, branch and mutation coverage: not measured. This verdict does not certify the complete application. Inconclusive is never a pass.</p>
    </> },
    { id: 'scope', title: 'Evidence scope', defaultOpen: true, render: () => <>
      <p>Generated suite: <StatusBadge value={qa.generated_verdict || qa.verdict} />. A suite verdict does not qualify the complete application.</p>
      {Object.keys(verification).length > 0 && <p className="verification-line"><strong>Verification:</strong> source-review accepted {text(verification.verified ?? 0)}/{text(verification.total ?? 0)} cases · threshold {text(verification.threshold)} · lowest accepted confidence {verification.min_confidence == null ? 'n/a' : text(verification.min_confidence)} · {verification.enforced ? 'full-accuracy gate' : 'fail-soft (HARNESS_REQUIRE_FULL_ACCURACY=0)'}</p>}
      <Fields data={{ source_intake: qa.source_intake, runtime_coverage: qa.runtime_coverage }} />
      <h3>Existing regression scopes</h3>
      <Table caption="Existing regression scopes" columns={[{ key: 'scope', label: 'Scope', render: row => label(row.scope) }, { key: 'verdict', label: 'Verdict', render: row => <StatusBadge value={row.verdict} /> }, { key: 'tests', label: 'Tests', width: '90px' }, { key: 'reason', label: 'Reason' }]} rows={rows(qa.baseline_regression)} />
      {rows(qa.baseline_regression).map((scope, i) => <Section key={i} title={`${label(scope.scope)} · ${text(scope.verdict)} cases`} defaultOpen={false}><Table caption={`${label(scope.scope)} cases`} columns={[{ key: 'classname', label: 'Class' }, { key: 'name', label: 'Name' }, { key: 'status', label: 'Status', render: row => <StatusBadge value={row.status} /> }, { key: 'message', label: 'Message' }]} rows={rows(scope.cases)} searchable /></Section>)}
    </> },
    { id: 'traceability', title: 'End-to-end traceability', count: rows(qa.traceability).length, render: () => <>
      <p>Each control follows one requirement from planning (PLN) through design, generation, environment setup and execution. Failed at names the first step that did not pass.</p>
      <Metrics values={traceCounts(record(qa.traceability_summary))} />
      <Table caption="End-to-end traceability" columns={TRACE_COLUMNS} rows={rows(qa.traceability)} searchable empty="No controls recorded." />
    </> },
    { id: 'cases', title: 'Case results', count: rows(qa.cases).length, render: () => <Table caption="Case results" columns={CASE_COLUMNS} rows={rows(qa.cases)} searchable rowHref={row => recordHref(id, 'case', text(row.id))} /> },
    { id: 'test-cases', title: 'Test cases', count: rows(qa.test_cases).length, render: () => {
      const groups = ['positive', 'negative', 'boundary', 'validation', 'contract'];
      const all = rows(qa.test_cases);
      return <>{groups.filter(g => all.some(c => c.group === g)).map(g => <Section key={g} title={`${label(g)} cases`} count={all.filter(c => c.group === g).length} defaultOpen={g === 'positive' || g === 'negative'}><CategoryCases cases={all.filter(c => c.group === g)} caption={`${label(g)} cases`} /></Section>)}{!all.length && <p className="empty-inline">No test cases recorded.</p>}</>;
    } },
    { id: 'requirements', title: 'Requirement states', render: () => <Metrics values={record(qa.requirement_state_counts)} /> },
    { id: 'requirement-traceability', title: 'Requirement traceability', count: rows(qa.requirement_coverage).length, render: () => <>
      <p>Linked generated tests are not measured line, branch or complete behavior coverage. A requirement counts as passed only when its linked tests passed execution. Source-review acceptance is not execution.</p>
      <Table caption="Requirement traceability" columns={[{ key: 'id', label: 'ID', width: '120px', render: row => <span className="cell-id">{text(row.id)}</span> }, { key: 'state', label: 'State', width: '140px', render: row => <StatusBadge value={row.state} /> }, { key: 'requirement_state', label: 'Basis', width: '170px' }, { key: 'text', label: 'Description' }]} rows={rows(qa.requirement_coverage)} searchable />
    </> },
    { id: 'failures', title: 'Why tests failed & what to improve', count: failures.length, defaultOpen: true, render: () => <>
      <p className="field-help">Observed output and recommendations from the saved QA report. A failing assertion needs review of both application behavior and the test expectation.</p>
      {failureCards(run, failures, [...generatedUpdates, ...repoUpdates])}
    </> },
    { id: 'skills', title: 'Skill contract checks', count: Object.keys(record(qa.skill_checks)).length, render: () => <Table caption="Skill contract checks" columns={[{ key: 'skill', label: 'Skill', render: row => <span className="cell-id">{text(row.skill)}</span> }, { key: 'check', label: 'Check', width: '200px' }, { key: 'result', label: 'Result', width: '120px', render: row => <StatusBadge value={row.result} /> }, { key: 'output', label: 'Output' }]} rows={Object.entries(record(qa.skill_checks)).map(([check, value]) => ({ check, skill: text(record(value).skill), result: record(value).passed ? 'PASS' : 'FAIL', output: text(record(value).output) }))} searchable empty="No skill checks recorded." /> },
    { id: 'worklist', title: 'Improvement worklist', count: generatedUpdates.length + repoUpdates.length + requirementUpdates.length + envUpdates.length, defaultOpen: true, render: () => anyUpdates ? <>
      {generatedUpdates.length > 0 && <Section title="Generated test files" count={generatedUpdates.length} defaultOpen><Table caption="Generated test files to update" columns={[{ key: 'file', label: 'File', render: row => <span className="cell-id">{text(row.file)}</span> }, { key: 'lines', label: 'Line(s)', width: '110px', render: row => list(row.lines) || 'whole file' }, { key: 'case', label: 'Case' }, { key: 'classification', label: 'Class', width: '150px' }, { key: 'what', label: 'What to change', render: row => <>{text(row.detail)}<small className="muted"> — {text(row.action)}</small></> }]} rows={generatedUpdates} searchable /></Section>}
      {repoUpdates.length > 0 && <Section title="Repository source files to review" count={repoUpdates.length} defaultOpen><Table caption="Repository source files to review" columns={[{ key: 'file', label: 'File:line', render: row => <span className="cell-id">{`${text(row.file)}:${text(row.line)}`}</span> }, { key: 'case', label: 'Case' }, { key: 'classification', label: 'Class', width: '150px' }, { key: 'detail', label: 'Evidence' }]} rows={repoUpdates} searchable /></Section>}
      {requirementUpdates.length > 0 && <Section title="Requirements that still need a passing test" count={requirementUpdates.length} defaultOpen><Table caption="Requirements that still need a passing test" columns={[{ key: 'requirement', label: 'Requirement' }, { key: 'state', label: 'State', width: '130px', render: row => <StatusBadge value={row.state} /> }, { key: 'cover', label: 'Cover these files', render: row => <span className="cell-id">{list(row.source_files_to_cover)}</span> }]} rows={requirementUpdates} searchable /></Section>}
      {envUpdates.length > 0 && <Section title="Environment actions" count={envUpdates.length} defaultOpen><ul>{envUpdates.map((u, i) => <li key={i}>{text(u.action)}</li>)}</ul></Section>}
    </> : <p className="success-note">Nothing to update: every executed case passed and every requirement has a passing test.</p> },
    { id: 'review-vs-execution', title: 'Review vs execution', count: reviewMiss.length || undefined, render: () => <>
      <p className="field-help">Cases that the source review accepted but execution did not pass, and cases that execution showed to need a review. Shown only when the report records them.</p>
      {reviewMiss.length ? <Table caption="Review misses" columns={[{ key: 'id', label: 'Test case ID', render: row => <span className="cell-id">{text(row.id)}</span> }, { key: 'flag', label: 'Flag' }, { key: 'detail', label: 'Detail' }]} rows={reviewMiss} /> : <p className="empty-inline">No review misses recorded.</p>}
    </> },
    { id: 'unmapped', title: 'Executed tests not mapped to an approved case', count: Array.isArray(qa.unmapped_results) ? (qa.unmapped_results as unknown[]).length : undefined, render: () => (Array.isArray(qa.unmapped_results) && qa.unmapped_results.length ? <ul>{(qa.unmapped_results as unknown[]).map((n, i) => <li key={i}>{text(n)}</li>)}</ul> : <p className="empty-inline">Every executed test maps to an approved case.</p>) },
    { id: 'limits', title: 'Warnings & limitations', render: () => <Fields data={{ warnings: qa.warnings, limitations: qa.limitations }} /> },
  ];
  return <DetailPage level={2} eyebrow="Saved execution verdict" title="QA report" status={qa.verdict} breadcrumbs={crumbs}
    meta={[{ label: 'Repository', value: dash(repo.name || repo.url) }, { label: 'Commit', value: <span className="cell-id">{text(repo.commit ? String(repo.commit).slice(0, 12) : '—')}</span> }, { label: 'Execution', value: text(qa.execution_status) }, { label: 'Runner', value: text(qa.adapter) }]}
    downloads={[{ label: 'Markdown', href: download(id, 'qa', 'md') }, { label: 'JSON', href: download(id, 'qa', 'json') }]}
    notices={qa.verdict === 'INCONCLUSIVE' ? <p className="notice bad">Tests did not produce sufficient execution evidence. Inconclusive is never a pass.</p> : undefined}
    sections={sections}>
    {run.actions.includes('revise') && run.pending?.stage === 'test_execution' && <p className="notice">Use the Review tab to regenerate failing tests. New source and execution require approval again.</p>}
    <Section title="Step reports" defaultOpen><StepList runId={id} /></Section>
  </DetailPage>;
}
