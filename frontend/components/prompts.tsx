'use client';
import { useEffect, useState } from 'react';
import { api, label, record, text, type Row, type Run } from '@/lib/harness';
import { DetailPage, Fields, Section } from './page';
import { Panel } from './data';

type Settings = { repository_prompt: string; personas: Record<string, string>; overrides: Record<string, string> };
type PromptRecord = { id: string; stage: string; batch?: number; override_key: string; sha256: string };
type Catalog = { settings: Settings; defaults: Settings; records: PromptRecord[]; host_stages: string[]; persona_labels?: Record<string, string> };

/** Agent prompts: the workspace page (no run) and a run's own prompts with its model-request history. */
export default function Prompts({ run }: { run?: Run }) {
  const endpoint = run ? `runs/${encodeURIComponent(run.id)}/prompts` : 'prompts';
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [stage, setStage] = useState('requirements');
  const [selected, setSelected] = useState('');
  const [request, setRequest] = useState<Row | null>(null);
  const [edited, setEdited] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    void api<Catalog>(endpoint, undefined, abort.signal).then(value => { setCatalog(value); setSettings(value.settings); }).catch(e => { if (!abort.signal.aborted) setMessage((e as Error).message); });
    return () => abort.abort();
  }, [endpoint]);
  const save = async (next: Settings, override?: { override_key: string; override_prompt: string | null }) => {
    setSaving(true); setMessage('');
    try { await api(endpoint, override || { repository_prompt: next.repository_prompt, personas: next.personas }); setSettings(next); setMessage(run ? 'Saved for future calls in this run. Historical requests remain unchanged.' : 'Saved as defaults for new runs.'); }
    catch (e) { setMessage((e as Error).message); }
    finally { setSaving(false); }
  };
  const inspect = async (recordId: string) => {
    setSelected(recordId); setMessage('');
    try { const result = await api<Row>(`${endpoint}/${encodeURIComponent(recordId)}`); setRequest(result); setEdited(text(result.user_prompt)); }
    catch (e) { setMessage((e as Error).message); }
  };
  if (!settings || !catalog) return <p role="status">{message || 'Loading saved prompts…'}</p>;
  const disabled = saving || !!run?.service_busy;
  const selectedRecord = catalog.records.find(item => item.id === selected);
  const { user_prompt: _omit, ...requestMeta } = request ?? {};
  void _omit;
  const title = run ? 'Run prompts' : 'Agent prompts';
  return <DetailPage level={run ? 2 : 1} title={title} eyebrow={run ? 'Run model requests' : 'Workspace'} notices={message ? <p role="status" className="notice">{message}</p> : undefined}
    children={<div className="prompt-editor">
      <p>{run ? 'This run has its own saved templates and complete model-request history.' : 'Customize persona instructions and repository review guidance for new runs.'}</p>
      <Panel title="Repository review prompt" storageKey={`${run ? run.id : 'workspace'}:prompts:repository`}><p>Default end-to-end guidance is also available in prompts/repository-review.md.</p><label>Review guidance<textarea aria-label="Review guidance" rows={14} value={settings.repository_prompt} onChange={e => setSettings({ ...settings, repository_prompt: e.target.value })} /></label><div className="header-actions"><button disabled={disabled} className="primary" onClick={() => void save(settings)}>Save prompts</button><button disabled={disabled} onClick={() => setSettings({ ...settings, repository_prompt: catalog.defaults.repository_prompt })}>Restore example</button></div></Panel>
      <Panel title="Agent persona instructions" storageKey={`${run ? run.id : 'workspace'}:prompts:personas`}><fieldset className="filter-chips"><legend>Workflow stage</legend>{Object.keys(settings.personas).map(value => <button type="button" key={value} aria-pressed={stage === value} onClick={() => setStage(value)}>{catalog.persona_labels?.[value] ?? label(value)}</button>)}</fieldset>{catalog.host_stages.includes(stage) && <p className="notice">This stage is executed by the host. Its saved persona is descriptive; it does not change install commands, approvals or test verdicts.</p>}<label>Complete persona prompt<textarea aria-label="Complete persona prompt" rows={18} value={settings.personas[stage] || ''} onChange={e => setSettings({ ...settings, personas: { ...settings.personas, [stage]: e.target.value } })} /></label><div className="header-actions"><button disabled={disabled} className="primary" onClick={() => void save(settings)}>Save persona</button><button disabled={disabled} onClick={() => setSettings({ ...settings, personas: { ...settings.personas, [stage]: catalog.defaults.personas[stage] } })}>Restore stage default</button></div></Panel>
      {run && <Panel title="Complete model request history" storageKey={`${run.id}:prompts:history`}>
        <p>Inspect the instructions, input/schema and assigned skills recorded for every model call. Changes apply to the next attempt of the same stage or generation batch.</p>
        {catalog.records.length ? <fieldset className="filter-chips"><legend>Recorded model call</legend>{catalog.records.map((r, i) => <button type="button" key={r.id} aria-pressed={selected === r.id} onClick={() => void inspect(r.id)}>{i + 1}. {label(r.stage)}{r.batch == null ? '' : ` · batch ${r.batch}`}</button>)}</fieldset> : <p>No model calls have been recorded yet.</p>}
        {request && selectedRecord && <>
          <Section title="Request details" defaultOpen><Fields data={requestMeta} /></Section>
          <p className="muted">Request ID: <code>{selectedRecord.id}</code> · hash <code>{selectedRecord.sha256}</code></p>
          <a className="button small" href={`/api/harness/${endpoint}/${encodeURIComponent(selected)}`} download={`prompt-${selectedRecord.stage}-${selected}.json`}>Request JSON ↓</a>
          <label>Complete user prompt and output contract<textarea aria-label="Complete user prompt and output contract" rows={24} value={edited} onChange={e => setEdited(e.target.value)} /></label>
          <p className="field-help">Keep source facts, approved case IDs and the output contract accurate. Host validation and approval gates still apply.</p>
          <div className="header-actions"><button disabled={disabled || !edited.trim()} className="primary" onClick={() => void save({ ...settings, overrides: { ...settings.overrides, [selectedRecord.override_key]: edited } }, { override_key: selectedRecord.override_key, override_prompt: edited })}>Save for next attempt</button><button disabled={disabled} onClick={() => { const overrides = { ...settings.overrides }; delete overrides[selectedRecord.override_key]; void save({ ...settings, overrides }, { override_key: selectedRecord.override_key, override_prompt: null }); }}>Use assembled default</button></div>
        </>}
      </Panel>}
      {run?.service_busy && <p className="field-help">Prompt saves are available when the active operation reaches a gate.</p>}
    </div>} />;
}
