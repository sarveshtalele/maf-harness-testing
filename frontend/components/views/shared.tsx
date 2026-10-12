'use client';
import Link from 'next/link';
import { Fragment, type ReactNode } from 'react';
import { artifacts, label, record, rows, runTitle, text, type Row, type Run, type Tone } from '@/lib/harness';
import { type Block, plain } from '@/lib/report';
import { Fields, Table, recordHref, useRunId, type Column } from '../page';

const VERDICT_TONE: Record<string, Tone> = { PASS: 'good', PASS_WITH_SKIPS: 'warn', FAIL: 'bad', ERROR: 'bad', INCONCLUSIVE: 'warn' };
/** The verdict banner used for every saved verdict (QA report, step reports). */
export function VerdictBanner({ name, meta, label: heading = 'Verdict' }: { name: unknown; meta?: ReactNode; label?: string }) {
  const value = text(name).toUpperCase();
  const tone = VERDICT_TONE[value] || 'neutral';
  return <div role="status" className={`report-verdict ${tone}`}><span className="eyebrow">{heading}</span><strong>{value}</strong>{meta && <span className="report-verdict-meta">{meta}</span>}</div>;
}

/** Inline text: `code`, **strong** and http(s) links. Anything else is plain text. */
export function inline(value: string): ReactNode {
  return value.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g).map((part, i) => {
    if (part.startsWith('`') && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>;
    if (part.startsWith('**') && part.length > 4) return <strong key={i}>{part.slice(2, -2)}</strong>;
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link && /^https?:\/\//i.test(link[2])) return <a key={i} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

const parseJson = (value: string): Row | null => { try { const parsed = JSON.parse(value); return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Row : null; } catch { return null; } };

/** Structured report blocks. Tables become Table, lists stay lists, JSON snippets become fields and command snippets stay preformatted. */
export function ReportBlocks({ blocks, caption }: { blocks: Block[]; caption: string }) {
  return <div className="report-blocks">{blocks.map((block, i) => {
    switch (block.type) {
      case 'p': return <p key={i}>{inline(block.text)}</p>;
      case 'h3': return <h3 key={i}>{block.text}</h3>;
      case 'quote': return <blockquote key={i}>{inline(block.text)}</blockquote>;
      case 'verdict': return <VerdictBanner key={i} name={block.name} meta={block.meta ? inline(block.meta) : undefined} />;
      case 'list': { const Tag = block.ordered ? 'ol' : 'ul'; return <Tag key={i}>{block.items.map((item, j) => <li key={j}>{inline(item)}</li>)}</Tag>; }
      case 'table': {
        const columns: Column[] = block.headers.map((header, j) => ({ key: `c${j}`, label: header }));
        const data = block.rows.map(cells => Object.fromEntries(cells.map((cell, j) => [`c${j}`, cell])) as Row);
        return <Table key={i} caption={`${caption} · ${plain(block.headers[0] || 'table')}`} columns={columns} rows={data} empty="No rows." />;
      }
      case 'code': {
        const json = parseJson(block.text.trim());
        if (json) return <Fields key={i} data={json} />;
        return <figure key={i} className="report-code">{block.language && <figcaption>{label(block.language)}</figcaption>}<pre><code>{block.text}</code></pre></figure>;
      }
    }
  })}</div>;
}

/** Case table: one row per case, Sno restarts in each table. "Open case" goes to the case record page. */
export function CaseTable({ cases, requirements, caption }: { cases: Row[]; requirements?: Row; caption: string }) {
  const runId = useRunId();
  const view = cases.map((item, index) => ({
    sno: index + 1,
    reqs: caseRequirementIds(item, requirements).join(', ') || '—',
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
  return <Table caption={caption} columns={columns} rows={view as unknown as Row[]} searchable empty="No test cases recorded." />;
}

// Mirrors qa/report.py case_requirement_ids: direct requirement links, then the parent requirement of each linked
// acceptance criterion, and only when neither exists the raw link IDs (validation, api_contract).
export function caseRequirementIds(item: Row, requirements?: Row): string[] {
  const links = rows(item.traceability_links);
  const parents = new Map(rows(record(record(requirements).requirement_model).acceptance_criteria).filter(c => c.requirement_id).map(c => [text(c.id), text(c.requirement_id)]));
  let ids = links.filter(link => link.type === 'requirement').map(link => text(link.id));
  ids = ids.concat(links.filter(link => link.type === 'acceptance_criterion' && parents.has(text(link.id))).map(link => parents.get(text(link.id)) as string));
  if (!ids.length) ids = links.map(link => text(link.id));
  return [...new Set(ids.filter(id => id && id !== '—' && id !== 'None'))];
}

/** Finds a record by ID in the run's saved artifacts and QA report, or in the run's error list. Every match is merged, so
 *  a case shows its design, its generated catalog entry and its execution result together (later matches win). */
export function findRecord(run: Run, recordId: string): Row | null {
  const errorItem = (run.errors?.items ?? []).find(item => text(item.id) === recordId);
  if (errorItem) return errorItem;
  const matches: Row[] = [];
  const visit = (value: unknown, depth: number) => {
    if (depth > 7 || !value || typeof value !== 'object') return;
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item && typeof item === 'object' && (text((item as Row).id) === recordId || text((item as Row).case_id) === recordId)) matches.push(item as Row);
        else visit(item, depth + 1);
      }
      return;
    }
    for (const item of Object.values(value)) visit(item, depth + 1);
  };
  visit(artifacts(run), 0);
  visit(run.state.test_reviews, 0);
  return matches.length ? Object.assign({}, ...matches) as Row : null;
}

/** Audit or event feed: one expandable row per event, the same structure for audit and trace JSONL. */
export function EventFeed({ events }: { events: Row[] }) {
  if (!events.length) return <p className="empty-inline">No events recorded.</p>;
  return <div className="audit-feed">{events.map((event, i) => <details key={i}><summary><span className="audit-icon" aria-hidden="true">{event.decision === 'approve' ? '✓' : '·'}</span><span><b>{label(event.event ?? event.name ?? 'event')}</b><small>{label(event.stage ?? '')}{event.at ? ` · ${text(event.at)}` : ''}{event.decision ? ` · ${label(event.decision)}` : ''}</small></span></summary><Fields data={event} /></details>)}</div>;
}

export const runName = (run: Run, id: string) => runTitle(run.state.run_name, run.state.repo_url, id);
