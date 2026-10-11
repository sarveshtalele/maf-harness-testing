'use client';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { label, number, record, rows, text, type Row } from '@/lib/harness';

const RUBRIC_MAX: Record<string, number> = { source_and_api_match: 25, assertion_checks_real_behavior: 30, fixture_validity: 20, isolation_and_determinism: 15, traceability: 10 };

// Host-computed confidence: the rubric sum and any evidence caps, shown on hover when the record carries them.
function confidenceTitle(item: Row): string {
  const dimensions = Object.entries(record(item.scores)).map(([key, value]) => `${label(key)} ${text(value)}/${RUBRIC_MAX[key] ?? '?'}`).join(' · ');
  const caps = rows(item.caps).map(cap => `${text(cap.rule)} ≤ ${text(cap.cap)}`).join(' · ');
  return [`Confidence ${text(item.confidence)}${item.model_score != null ? ` (rubric ${text(item.model_score)})` : ''}`, dimensions, caps ? `Caps: ${caps}` : ''].filter(Boolean).join('\n');
}

export function ResizeHandle({ label: name, axis = 'horizontal', start, reset }: { label: string; axis?: 'horizontal' | 'both'; reset?: () => void; start: () => (dx: number, dy: number) => void }) {
  const drag = useRef<{ x: number; y: number; move: (dx: number, dy: number) => void } | null>(null);
  return <button className={`resize-handle resize-${axis}`} aria-label={name} title={`${name}: drag or use arrow keys${reset ? "; double-click or Home to restore size" : ""}`} onDoubleClick={reset} onPointerDown={e => {
    if (e.button !== 0) return;
    e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, move: start() };
  }} onPointerMove={e => { const d = drag.current; if (d) d.move(e.clientX - d.x, e.clientY - d.y); }} onPointerUp={e => {
    drag.current = null; if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  }} onLostPointerCapture={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onKeyDown={e => {
    if (e.key === 'Home' && reset) { e.preventDefault(); reset(); return; }
    const delta: Record<string, [number, number]> = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] };
    if (delta[e.key] && (axis === 'both' || e.key === 'ArrowLeft' || e.key === 'ArrowRight')) { e.preventDefault(); start()(...delta[e.key]); }
  }}><span aria-hidden="true">{axis === 'both' ? '◢' : '⋮'}</span></button>;
}

export function usePreference<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState(initial);
  useEffect(() => { try { const stored = localStorage.getItem(key); setValue(stored == null ? initial : JSON.parse(stored)); } catch { setValue(initial); } }, [key]); // initial is deliberately a stable fallback, not a storage dependency.
  const save = (next: T) => { setValue(next); try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* Layout still works without storage. */ } };
  return [value, save];
}

export function Panel({ title, children, storageKey, actions, defaultOpen = true, revealKey }: { title: string; children: ReactNode; storageKey: string; actions?: ReactNode; defaultOpen?: boolean; revealKey?: string }) {
  const [height, setHeight] = usePreference<number>(`panel-height:${storageKey}`, 0);
  const [width, setWidth] = usePreference<number>(`panel-width:${storageKey}`, 100);
  const [collapsed, setCollapsed] = usePreference<boolean>(`panel-closed:${storageKey}`, !defaultOpen);
  useEffect(() => { if (revealKey) setCollapsed(false); }, [revealKey]);
  const [expanded, setExpanded] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const panel = useRef<HTMLElement>(null);
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => { if (expanded) dialog.current?.showModal(); }, [expanded]);
  const safeHeight = Number.isFinite(height) ? Math.max(0, Math.min(1200, height)) : 0;
  return <section ref={panel} className="panel" style={{ width: `${Math.max(50, Math.min(100, Number(width) || 100))}%` }}>
    <header className="panel-heading"><button className="panel-title" aria-expanded={!collapsed} onClick={() => setCollapsed(!collapsed)}><span aria-hidden="true">{collapsed ? '▸' : '▾'}</span> {title}</button><div className="panel-actions">{actions}<button title={`Expand ${title}`} aria-label={`Expand ${title}`} onClick={() => setExpanded(true)}>⤢</button></div></header>
    {!collapsed && <><div ref={body} className="panel-body resizable" style={safeHeight ? { height: safeHeight } : {}}>{children}</div><ResizeHandle label={`Resize ${title}`} axis="both" reset={() => { setWidth(100); setHeight(0); }} start={() => {
      const box = panel.current!.getBoundingClientRect(); const parentWidth = box.width / (Math.max(50, Math.min(100, Number(width) || 100)) / 100);
      const bodyHeight = body.current!.getBoundingClientRect().height;
      return (dx, dy) => { setWidth(Math.max(50, Math.min(100, (box.width + dx) / parentWidth * 100))); setHeight(Math.round(Math.max(100, Math.min(1200, bodyHeight + dy)))); };
    }} /></>}
    {expanded && <dialog ref={dialog} aria-label={title} className="expanded-panel" onCancel={() => setExpanded(false)} onClose={() => setExpanded(false)}><header className="panel-heading"><h2>{title}</h2><button autoFocus onClick={() => { dialog.current?.close(); setExpanded(false); }}>Close expanded view</button></header><div className="panel-body">{children}</div></dialog>}
  </section>;
}

export function Badge({ value }: { value: unknown }) {
  const str = text(value); const lower = str.toLowerCase();
  const kind = /fail|error|interrupted|inconclusive|blocked/.test(lower) ? 'bad' : /unverified/.test(lower) ? 'warn' : /pass|verified|approved|complete|ready|true/.test(lower) ? 'good' : /wait|review|skip|stopp|pending/.test(lower) ? 'warn' : '';
  return <span className={`badge ${kind}`}>{label(str)}</span>;
}
export function Metrics({ values }: { values: Row }) {
  return <div className="metrics">{Object.entries(values).map(([key, value]) => <div className="metric" key={key}><span>{label(key)}</span><strong>{number(value)}</strong></div>)}</div>;
}
export function ExportButtons({ id, kind }: { id: string; kind: string }) {
  const base = `/api/harness/runs/${encodeURIComponent(id)}/download?kind=${encodeURIComponent(kind)}`;
  return <><a className="button small" href={`${base}&format=md`} download>Markdown ↓</a><a className="button small" href={`${base}&format=json`} download>JSON ↓</a></>;
}

export function DataView({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value == null) return <span className="muted">Not specified</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <p className="muted">None recorded.</p>;
    if (value.every(item => item && typeof item === 'object' && !Array.isArray(item))) return <DataTable data={value as Row[]} />;
    return <ul className="data-list">{value.map((item, i) => <li key={i}><DataView value={item} depth={depth + 1} /></li>)}</ul>;
  }
  if (typeof value === 'object') return <dl className="data-fields">{Object.entries(record(value)).map(([key, item]) => <div key={key}>
    <dt>{label(key)}</dt><dd>{item && typeof item === 'object' ? depth < 2 ? <div className="field-section"><DataView value={item} depth={depth + 1} /></div> : <details><summary>Explore {label(key).toLowerCase()}</summary><DataView value={item} depth={depth + 1} /></details> : key === 'content' || (typeof item === 'string' && item.includes('\n')) ? <pre>{text(item)}</pre> : <span>{text(item)}</span>}</dd>
  </div>)}</dl>;
  return <span className="data-text">{text(value)}</span>;
}

export function DataTable({ data, columns }: { data: Row[]; columns?: string[] }) {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Row | null>(null);
  const [sort, setSort] = useState<{ key: string; ascending: boolean } | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (selected) dialog.current?.showModal(); }, [selected]);
  const keys = columns || [...new Set(data.flatMap(item => Object.keys(item)))].slice(0, 7);
  const shown = data.filter(item => JSON.stringify(item).toLowerCase().includes(search.toLowerCase())).sort((a, b) => sort ? (typeof a[sort.key] === 'number' && typeof b[sort.key] === 'number' ? Number(a[sort.key]) - Number(b[sort.key]) : text(a[sort.key]).localeCompare(text(b[sort.key]), undefined, { numeric: true })) * (sort.ascending ? 1 : -1) : 0);
  const current = Math.min(page, Math.max(0, Math.ceil(shown.length / 30) - 1));
  if (!data.length) return <p className="empty-inline">No records yet.</p>;
  return <div className="data-table"><div className="table-tools"><input aria-label="Search table records" type="search" placeholder="Search records…" value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} /><small>{shown.length} of {data.length} records</small></div><div className="table-scroll"><table><thead><tr>{keys.map(key => <th key={key} aria-sort={sort?.key === key ? sort.ascending ? 'ascending' : 'descending' : 'none'}><button className="column-sort" onClick={() => setSort({ key, ascending: sort?.key === key ? !sort.ascending : true })}>{label(key)}<span aria-hidden="true">{sort?.key === key ? sort.ascending ? '↑' : '↓' : '↕'}</span></button></th>)}<th>Details</th></tr></thead><tbody>{shown.slice(current * 30, current * 30 + 30).map((item, i) => <tr key={i}>{keys.map(key => <td key={key}>{['status', 'result', 'verdict', 'state', 'ok'].includes(key) ? <Badge value={item[key]} /> : <span className="cell-text" title={key === 'confidence' && item.scores ? confidenceTitle(item) : text(item[key])}>{Array.isArray(item[key]) ? item[key].every(v => typeof v !== 'object') ? item[key].join(' · ') : `${item[key].length} entries` : item[key] && typeof item[key] === 'object' ? `${Object.keys(record(item[key])).length} fields` : text(item[key])}</span>}</td>)}<td><button className="small row-inspect" aria-label={`Inspect record ${current * 30 + i + 1}`} onClick={() => setSelected(item)}>Inspect ↗</button></td></tr>)}</tbody></table></div>{!shown.length && <p className="empty-inline">No matching records. Try a different search.</p>}{shown.length > 30 && <div className="table-tools"><button disabled={current === 0} onClick={() => setPage(current - 1)}>Previous</button><span>Page {current + 1} / {Math.ceil(shown.length / 30)}</span><button disabled={(current + 1) * 30 >= shown.length} onClick={() => setPage(current + 1)}>Next</button></div>}{selected && <dialog ref={dialog} className="record-dialog" aria-label="Record details" onClose={() => setSelected(null)} onCancel={() => setSelected(null)}><header className="panel-heading"><h2>Record details</h2><button autoFocus onClick={() => { dialog.current?.close(); setSelected(null); }}>Close details</button></header><div className="panel-body"><DataView value={selected} depth={1} /></div></dialog>}</div>;
}
