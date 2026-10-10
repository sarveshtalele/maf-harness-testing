'use client';
import { useState } from 'react';
import { label, runTitle, text, type Run } from '@/lib/harness';
import { Panel } from './data';

export default function RunErrors({ run, id }: { run: Run; id: string }) {
  const [cat, setCat] = useState('');
  const [q, setQ] = useState('');
  const items = run.errors?.items ?? [];
  const counts = run.errors?.counts ?? {};
  const shown = items.filter(e => (!cat || e.category === cat) && `${e.summary} ${e.detail} ${text(e.stage)} ${(e.case_ids as string[]).join(' ')}`.toLowerCase().includes(q.toLowerCase()));
  return <Panel title={`Errors for ${runTitle(run.state.run_name, run.state.repo_url, id)} · ${id}`} storageKey={`${id}:errors`}>
    {!items.length ? <p className="empty-inline">No errors recorded for this run.</p> : <>
      <div className="table-tools"><div className="header-actions">{Object.entries(counts).map(([c, n]) => <button key={c} className="small" aria-pressed={cat === c} onClick={() => setCat(cat === c ? '' : c)}>{label(c)} {n}</button>)}</div>
        <input aria-label="Search errors" type="search" placeholder="Search errors…" value={q} onChange={e => setQ(e.target.value)} /><small>{shown.length} of {items.length}</small></div>
      <div className="data-table"><div className="table-scroll"><table><thead><tr><th>Time</th><th>Stage</th><th>Batch</th><th>Cases</th><th>Category</th><th>Summary</th></tr></thead>
        <tbody>{shown.map(e => { const ids = e.case_ids as string[]; return <tr key={text(e.id)}>
          <td>{e.at ? new Date(text(e.at)).toLocaleString() : '—'}</td><td>{label(e.stage)}</td><td>{e.batch == null ? '—' : text(e.batch)}</td>
          <td>{ids.slice(0, 2).map(c => <code key={c} title={c}>{c.length > 28 ? `${c.slice(0, 28)}…` : c}</code>)}{ids.length > 2 && <small> +{ids.length - 2}</small>}</td>
          <td><span className="badge bad">{label(e.category)}</span></td>
          <td><details><summary>{text(e.summary)}</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{text(e.detail)}</pre><button className="small" onClick={() => void navigator.clipboard?.writeText(text(e.detail))}>Copy</button></details></td></tr>; })}</tbody></table></div>
        {!shown.length && <p className="empty-inline">No matching errors.</p>}</div></>}
  </Panel>;
}
