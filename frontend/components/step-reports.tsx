'use client';
import { useEffect, useState } from 'react';
import { api, download, type Run } from '@/lib/harness';
import Markdown from './markdown';
import { ExportButtons, Panel } from './data';

type Step = { name: string; title: string; status: string };

// Deterministic per-step reports (summary, then one report for each workflow step) rendered from the saved run.
export default function StepReports({ run }: { run: Run }) {
  const [steps, setSteps] = useState<Step[]>([]);
  const [name, setName] = useState('00-summary');
  const [body, setBody] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void api<{ steps: Step[] }>(`runs/${run.id}/download?kind=step&format=json`, undefined, controller.signal).then(r => setSteps(r.steps)).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [run.id, run.busy]);
  useEffect(() => {
    const controller = new AbortController();
    void fetch(`${download(run.id, 'step', 'md')}&name=${encodeURIComponent(name)}`, { cache: 'no-store', signal: controller.signal }).then(r => r.ok ? r.text() : Promise.reject(new Error('Step report unavailable.'))).then(setBody).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [run.id, run.busy, name]);
  return <Panel title="Step reports" storageKey={`${run.id}:qa:steps`} actions={<ExportButtons id={run.id} kind="qa" />}>
    {error && <p className="notice bad" role="alert">{error}</p>}
    <div className="evidence-types" role="tablist" aria-label="Step reports">{steps.map(s => <button key={s.name} role="tab" aria-selected={name === s.name} aria-pressed={name === s.name} onClick={() => setName(s.name)}>{s.title.replace(/^Step \d+: /, '')}{s.status === 'Not run' ? ' (not run)' : ''}</button>)}</div>
    {body ? <Markdown text={body} /> : <p className="empty-inline">Loading report…</p>}
  </Panel>;
}
