'use client';
import { useEffect, useState, type FormEvent } from 'react';
import { api, desktop, label } from '@/lib/harness';
import { Panel } from './data';

type View = { base_url: string; model: string; stage_models: Record<string, string>; key_configured: boolean; key_hint: string; source?: string; encrypted?: boolean; notice?: string };
type Desktop = { getModelSettings(): Promise<View>; saveModelSettings(input: Record<string, unknown>): Promise<View> };

/** Mirrors settings.validate_base_url so the error shows before any request is sent. */
export function baseUrlProblem(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return '';
  let url: URL;
  try { url = new URL(trimmed); } catch { return 'Enter a full URL, for example https://gateway.example.com/v1.'; }
  if (url.username || url.password) return 'The URL must not contain a username or password.';
  if (url.protocol === 'https:' || (url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname))) return '';
  return url.protocol === 'http:' ? 'Use https://. Plain http:// is allowed only for localhost or 127.0.0.1.' : 'The URL must start with https:// (or http:// for localhost).';
}

const presets: Record<string, { base_url: string; hint: string }> = {
  'OpenAI': { base_url: '', hint: 'Default OpenAI endpoint; leave Base URL empty.' },
  'Azure OpenAI / LiteLLM gateway': { base_url: 'https://YOUR-RESOURCE.openai.azure.com/openai/v1/', hint: 'Use your gateway or resource URL (https). Model is the deployment name.' },
  'Anthropic-compatible gateway': { base_url: 'https://api.anthropic.com/v1/', hint: 'An OpenAI-compatible endpoint in front of Anthropic models.' },
};

export default function ModelSettings({ stages, onSaved }: { stages: string[]; onSaved: () => void }) {
  const [view, setView] = useState<View | null>(null);
  const [form, setForm] = useState({ base_url: '', model: '', api_key: '' });
  const [perStage, setPerStage] = useState<Record<string, string>>({});
  const [show, setShow] = useState(false);
  const [toEnv, setToEnv] = useState(false);
  const [preset, setPreset] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string; detail?: string } | null>(null);
  const [urlTouched, setUrlTouched] = useState(false);
  const urlError = baseUrlProblem(form.base_url);
  const showUrlError = urlTouched && !!urlError;

  const load = (v: View) => { setView(v); setForm(f => ({ ...f, base_url: v.base_url, model: v.model, api_key: '' })); setPerStage(v.stage_models || {}); };
  useEffect(() => { (desktop<Desktop>()?.getModelSettings() ?? api<View>('settings/model')).then(v => { load(v); if (v.notice) setNotice({ ok: false, text: v.notice }); }).catch(e => setNotice({ ok: false, text: e.message })); }, []);

  const body = () => ({ base_url: form.base_url.trim(), model: form.model.trim(), stage_models: Object.fromEntries(Object.entries(perStage).filter(([, v]) => v.trim())), ...(form.api_key ? { api_key: form.api_key } : {}) });
  async function save(e: FormEvent) {
    e.preventDefault(); setUrlTouched(true);
    if (urlError) return;
    setBusy(true); setNotice(null);
    try {
      const d = desktop<Desktop>();
      const next = d ? await d.saveModelSettings(body()) : await api<View>('settings/model', { ...body(), save_to_env: toEnv });
      load(next); setShow(false); onSaved();
      setNotice({ ok: true, text: d && next.encrypted === false ? 'Saved for this session only: this system cannot encrypt secrets, so the key is not stored on disk.' : 'Saved.' });
    } catch (err) { setNotice({ ok: false, text: (err as Error).message }); } finally { setBusy(false); }
  }
  /** Tests what is typed in the form (not saved settings); the key is sent only when a new one was typed. */
  async function test() {
    setUrlTouched(true);
    if (urlError) return;
    setBusy(true); setNotice(null);
    try {
      const r = await api<{ ok: boolean; message: string; detail?: string }>('settings/model/test', { base_url: form.base_url.trim(), model: form.model.trim(), ...(form.api_key ? { api_key: form.api_key } : {}) });
      setNotice({ ok: r.ok, text: r.message, detail: r.detail });
    }
    catch (err) { setNotice({ ok: false, text: (err as Error).message }); } finally { setBusy(false); }
  }
  const pick = (name: string) => { setPreset(name); if (presets[name]) setForm(f => ({ ...f, base_url: presets[name].base_url })); };

  return <Panel title="Model & API key" storageKey="config:model"><form onSubmit={save} autoComplete="off">
    <p>{view?.key_configured ? <>API key set (ending <code>{view.key_hint}</code>) from <strong>{view.source === 'runtime' ? 'this app' : view.source || 'saved settings'}</strong>.</> : 'No API key configured yet.'} The key is write-only and never shown again.</p>
    <label>Provider preset<select value={preset} onChange={e => pick(e.target.value)}><option value="">Custom</option>{Object.keys(presets).map(p => <option key={p}>{p}</option>)}</select></label>
    {presets[preset] && <p className="field-help">{presets[preset].hint}</p>}
    <label>Base URL <span className="muted">optional</span><input value={form.base_url} onChange={e => setForm({ ...form, base_url: e.target.value })} onBlur={() => setUrlTouched(true)} placeholder="https://… (empty = provider default)" inputMode="url" aria-invalid={showUrlError} aria-describedby={showUrlError ? 'base-url-error' : undefined} /></label>
    {showUrlError && <p id="base-url-error" className="field-error" role="alert">{urlError}</p>}
    <label>Model<input value={form.model} onChange={e => setForm({ ...form, model: e.target.value })} maxLength={100} required /></label>
    <details><summary>Per-stage models (optional)</summary>{stages.map(s => <label key={s}>{label(s)}<input value={perStage[s] || ''} onChange={e => setPerStage({ ...perStage, [s]: e.target.value })} maxLength={100} placeholder="Default model" /></label>)}</details>
    <label>API key<input type={show ? 'text' : 'password'} value={form.api_key} onChange={e => setForm({ ...form, api_key: e.target.value })} autoComplete="new-password" spellCheck={false} placeholder={view?.key_configured ? 'Leave empty to keep the current key' : 'Paste your API key'} /></label>
    <label className="check"><input type="checkbox" checked={show} onChange={e => setShow(e.target.checked)} />Show key while typing</label>
    {!desktop<Desktop>() && <label className="check"><input type="checkbox" checked={toEnv} onChange={e => setToEnv(e.target.checked)} />Save to .env (developer)</label>}
    <p className="field-help">{desktop<Desktop>() ? 'Stored encrypted with your operating system keychain.' : 'Kept in the running local server only, unless you save to .env.'}</p>
    <div className="header-actions"><button className="primary" disabled={busy}>Save</button><button type="button" disabled={busy || (!view?.key_configured && !form.api_key)} onClick={test}>Test connection</button></div>
    {notice && <div role="status" className={notice.ok ? '' : 'bad'}><p>{notice.text}</p>{notice.detail && <details><summary>Details</summary><pre className="code-block">{notice.detail}</pre></details>}</div>}
  </form></Panel>;
}
