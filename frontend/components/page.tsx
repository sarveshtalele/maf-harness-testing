'use client';
import Link from 'next/link';
import { createContext, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { download, label, record, statusTone, text, type Row, type Tone } from '@/lib/harness';

/** The run whose records the page shows. Nested objects with an id link to their record page inside this run. */
export const RunScope = createContext<string | undefined>(undefined);
export const useRunId = () => useContext(RunScope);
export const recordHref = (runId: string, kind: string, recordId: string) => `/runs/${encodeURIComponent(runId)}/records/${encodeURIComponent(kind)}/${encodeURIComponent(recordId)}`;
/** Record kind from a field key: "requirements" and "requirement" both give "requirement". */
export const kindOf = (key: string) => key.replace(/s$/, '').replace(/_/g, '-');

export function StatusBadge({ value, tone }: { value: unknown; tone?: Tone }) {
  return <span className={`badge ${tone ?? statusTone(value)}`}>{label(value)}</span>;
}

export function Metrics({ values }: { values: Row }) {
  return <div className="metrics">{Object.entries(values).map(([key, value]) => <div className="metric" key={key}><span>{label(key)}</span><strong>{typeof value === 'number' ? value.toLocaleString() : text(value)}</strong></div>)}</div>;
}

/** Markdown and JSON are offered as downloads only; the page renders structured UI. */
export function Exports({ id, kind }: { id: string; kind: string }) {
  return <>
    <a className="button small" href={download(id, kind, 'md')} download>Markdown ↓</a>
    <a className="button small" href={download(id, kind, 'json')} download>JSON ↓</a>
  </>;
}

export function EmptyState({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return <div className="empty-state">{icon && <span aria-hidden="true">{icon}</span>}<h2>{title}</h2>{children}</div>;
}

/** A collapsible subsection: h2 > button[aria-expanded][aria-controls]. Children are created only after the first open. */
export type SectionProps = { id?: string; title: string; count?: number; summary?: ReactNode; defaultOpen?: boolean; open?: boolean; onToggle?: (open: boolean) => void; expandHref?: string; children: ReactNode | (() => ReactNode) };
export function Section({ id, title, count, summary, defaultOpen = false, open: controlled, onToggle, expandHref, children }: SectionProps) {
  const [inner, setInner] = useState(defaultOpen);
  const isOpen = controlled ?? inner;
  const [loaded, setLoaded] = useState(isOpen);
  const bodyId = useId();
  useEffect(() => { if (isOpen) setLoaded(true); }, [isOpen]);
  const toggle = () => { if (controlled === undefined) setInner(!isOpen); onToggle?.(!isOpen); };
  return <section className="section" id={id}>
    <h2 className="section-heading">
      <button type="button" aria-expanded={isOpen} aria-controls={bodyId} onClick={toggle}><span className="section-chevron" aria-hidden="true">{isOpen ? '▾' : '▸'}</span><span className="section-title">{title}</span>{count !== undefined && <span className="section-count">{count}</span>}</button>
      {expandHref && <Link className="section-expand" href={expandHref} aria-label={`Open ${title} as a page`} title={`Open ${title} as a page`}>⤢</Link>}
    </h2>
    {summary && <div className="section-summary">{summary}</div>}
    <div id={bodyId} className="section-body" hidden={!isOpen}>{loaded && (typeof children === 'function' ? children() : children)}</div>
  </section>;
}

export type Crumb = { label: string; href?: string };
export type Download = { label: string; href: string };
export type SectionSpec = { id: string; title: string; count?: number; summary?: ReactNode; defaultOpen?: boolean; expandHref?: string; render: () => ReactNode };
export type Meta = { label: string; value: ReactNode };

/** The one page template: breadcrumbs, title with ID and status, metadata strip, actions, downloads, notices and sections. */
export function DetailPage({ breadcrumbs, eyebrow, title, id, status, meta, actions, downloads, notices, sections, children }: {
  breadcrumbs?: Crumb[]; eyebrow?: string; title: string; id?: string; status?: unknown; meta?: Meta[]; actions?: ReactNode; downloads?: Download[]; notices?: ReactNode; sections?: SectionSpec[]; children?: ReactNode;
}) {
  const specs = useMemo(() => sections ?? [], [sections]);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const isOpen = (spec: SectionSpec) => open[spec.id] ?? !!spec.defaultOpen;
  const setAll = (value: boolean) => setOpen(Object.fromEntries(specs.map(spec => [spec.id, value])));
  const last = (breadcrumbs?.length ?? 0) - 1;
  return <article className="detail-page">
    {breadcrumbs && breadcrumbs.length > 0 && <nav aria-label="Breadcrumb" className="crumbs"><ol>{breadcrumbs.map((crumb, i) => <li key={`${crumb.label}:${i}`}>{crumb.href && i < last ? <Link href={crumb.href}>{crumb.label}</Link> : <span aria-current={i === last ? 'page' : undefined}>{crumb.label}</span>}</li>)}</ol></nav>}
    <header className="page-hero">
      <div className="page-hero-text">
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h1>{title}</h1>
        {(id || status !== undefined) && <div className="page-hero-meta">{id && <code className="page-id">{id}</code>}{status !== undefined && <StatusBadge value={status} />}</div>}
      </div>
      {actions && <div className="header-actions page-actions">{actions}</div>}
    </header>
    {meta && meta.length > 0 && <dl className="meta-strip">{meta.map((item, i) => <div key={`${item.label}:${i}`}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>}
    {downloads && downloads.length > 0 && <div className="downloads" aria-label="Downloads">{downloads.map(item => <a className="button small" key={item.label} href={item.href} download>{item.label} ↓</a>)}</div>}
    {notices}
    {children}
    {specs.length > 1 && <div className="section-tools"><button type="button" className="small" onClick={() => setAll(true)}>Expand all</button><button type="button" className="small" onClick={() => setAll(false)}>Collapse all</button></div>}
    {specs.map(spec => <Section key={spec.id} id={spec.id} title={spec.title} count={spec.count} summary={spec.summary} expandHref={spec.expandHref} open={isOpen(spec)} onToggle={value => setOpen(current => ({ ...current, [spec.id]: value }))}>{spec.render}</Section>)}
  </article>;
}

/** Renders one value: arrays join with ", ", objects with an id link to their record page, multi-line text as paragraphs. */
export function renderValue(key: string, value: unknown, runId?: string): ReactNode {
  if (value == null || value === '') return <span className="muted">Not specified</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">None recorded</span>;
    if (value.every(item => item && typeof item === 'object' && !Array.isArray(item))) return <Table caption={label(key)} rows={value as Row[]} />;
    return <span className="field-value">{value.map(item => text(item)).join(', ')}</span>;
  }
  if (typeof value === 'object') {
    const object = record(value);
    if (typeof object.id === 'string' && runId) return <Link href={recordHref(runId, kindOf(key), object.id)}>{text(object.title ?? object.text ?? object.id)} ›</Link>;
    return <Fields data={object} />;
  }
  if (typeof value === 'boolean') return <StatusBadge value={value} />;
  if (typeof value === 'string' && value.includes('\n')) return <div className="prose">{value.split(/\n+/).filter(Boolean).map((line, i) => <p key={i}>{line}</p>)}</div>;
  return <span className="field-value">{text(value)}</span>;
}

export type FieldSpec = { label?: string; render?: (value: unknown) => ReactNode };
/** Replaces DataView: label map applied, nested objects link to records, arrays of objects become tables. */
export function Fields({ data, schema = {}, skip = [] }: { data: unknown; schema?: Record<string, FieldSpec>; skip?: string[] }) {
  const runId = useRunId();
  const entries = Object.entries(record(data)).filter(([key, value]) => !skip.includes(key) && value !== undefined);
  if (!entries.length) return <p className="empty-inline">Nothing recorded.</p>;
  return <dl className="fields">{entries.map(([key, value]) => {
    const spec = schema[key];
    return <div className="field" key={key}><dt>{spec?.label ?? label(key)}</dt><dd>{spec?.render ? spec.render(value) : renderValue(key, value, runId)}</dd></div>;
  })}</dl>;
}

export type Column = { key: string; label?: string; width?: string; render?: (row: Row) => ReactNode };
const PAGE = 25;
/** One table renderer for the whole app: caption, column widths, optional search, sorting, paging, row links, focusable scroll region. */
export function Table({ caption, columns, rows: data, rowHref, searchable = false, empty = 'No records yet.', pageSize = PAGE }: {
  caption: string; columns?: Column[]; rows: Row[]; rowHref?: (row: Row) => string | undefined; searchable?: boolean; empty?: string; pageSize?: number;
}) {
  const runId = useRunId();
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<{ key: string; ascending: boolean } | null>(null);
  const cols = useMemo<Column[]>(() => columns ?? [...new Set(data.slice(0, 20).flatMap(row => Object.keys(row)))].slice(0, 6).map(key => ({ key })), [columns, data]);
  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const filtered = needle ? data.filter(row => Object.values(row).some(value => text(value).toLowerCase().includes(needle))) : data;
    if (!sort) return filtered;
    const sign = sort.ascending ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const x = a[sort.key]; const y = b[sort.key];
      return (typeof x === 'number' && typeof y === 'number' ? x - y : text(x).localeCompare(text(y), undefined, { numeric: true })) * sign;
    });
  }, [data, search, sort]);
  if (!data.length) return <p className="empty-inline">{empty}</p>;
  const pages = Math.max(1, Math.ceil(shown.length / pageSize));
  const current = Math.min(page, pages - 1);
  const visible = shown.slice(current * pageSize, current * pageSize + pageSize);
  return <div className="data-table">
    <div className="table-tools">
      {searchable && <input type="search" aria-label={`Search ${caption}`} placeholder="Search…" value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} />}
      <small>{shown.length} of {data.length} {data.length === 1 ? 'row' : 'rows'}</small>
    </div>
    <div className="table-scroll" role="region" aria-label={caption} tabIndex={0}>
      <table>
        <caption className="table-caption">{caption}</caption>
        <colgroup>{cols.map(col => <col key={col.key} style={col.width ? { width: col.width } : undefined} />)}{rowHref && <col style={{ width: '96px' }} />}</colgroup>
        <thead><tr>{cols.map(col => <th key={col.key} scope="col" aria-sort={sort?.key === col.key ? (sort.ascending ? 'ascending' : 'descending') : 'none'}><button type="button" className="column-sort" onClick={() => setSort({ key: col.key, ascending: sort?.key === col.key ? !sort.ascending : true })}>{col.label ?? label(col.key)}<span aria-hidden="true">{sort?.key === col.key ? (sort.ascending ? '↑' : '↓') : '↕'}</span></button></th>)}{rowHref && <th scope="col"><span className="sr-only">Open</span></th>}</tr></thead>
        <tbody>{visible.map((row, i) => {
          const href = rowHref?.(row);
          return <tr key={`${current}:${i}`}>{cols.map(col => <td key={col.key}>{col.render ? col.render(row) : renderCell(col.key, row[col.key], runId)}</td>)}{rowHref && <td>{href ? <Link className="row-link" href={href} aria-label={`Open ${caption} row ${current * pageSize + i + 1}`}>Open ›</Link> : null}</td>}</tr>;
        })}</tbody>
      </table>
    </div>
    {!shown.length && <p className="empty-inline">No matching rows. Try a different search.</p>}
    {shown.length > pageSize && <div className="table-tools table-pager"><button type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous</button><span>Page {current + 1} of {pages}</span><button type="button" disabled={current + 1 >= pages} onClick={() => setPage(current + 1)}>Next</button></div>}
  </div>;
}

function renderCell(key: string, value: unknown, runId?: string): ReactNode {
  if (['status', 'result', 'verdict', 'state', 'ok', 'decision', 'category', 'classification'].includes(key) && (typeof value === 'string' || typeof value === 'boolean')) return <StatusBadge value={value} />;
  return renderValue(key, value, runId);
}

export function ConfirmDialog({ open, title, children, confirmLabel, cancelLabel = 'Cancel', tone = 'primary', busy = false, disabled = false, onConfirm, onClose }: {
  open: boolean; title: string; children?: ReactNode; confirmLabel: string; cancelLabel?: string; tone?: 'primary' | 'danger'; busy?: boolean; disabled?: boolean; onConfirm: () => void; onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current; if (!dialog) return;
    if (open && !dialog.open) { opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; dialog.showModal(); }
    if (!open && dialog.open) dialog.close();
    if (!open && opener.current) { if (opener.current.isConnected) opener.current.focus(); opener.current = null; }
  }, [open]);
  return <dialog ref={ref} className="confirm-dialog" aria-labelledby={titleId} onClose={onClose} onCancel={e => { if (busy) e.preventDefault(); }}>
    {open && <form onSubmit={e => { e.preventDefault(); if (!busy && !disabled) onConfirm(); }}>
      <header className="confirm-head"><h2 id={titleId}>{title}</h2></header>
      <div className="confirm-body">{children}</div>
      <div className="header-actions confirm-actions"><button type="button" onClick={onClose} disabled={busy}>{cancelLabel}</button><button type="submit" className={tone === 'danger' ? 'danger-button' : 'primary'} disabled={busy || disabled}>{confirmLabel}</button></div>
    </form>}
  </dialog>;
}
