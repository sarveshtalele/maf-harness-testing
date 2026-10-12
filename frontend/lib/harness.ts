export type Row = Record<string, unknown>;
export type Pending = { id: string; stage: string; kind: string; title: string; details: Row; error?: string };
export type Run = { id: string; progress?: Row; status: string; state: Row; pending: Pending | null; actions: string[]; busy: boolean; service_busy: boolean; pending_consumed?: boolean; operation?: Row; run_directory?: string; operation_error?: string; error?: string; has_zip: boolean; errors?: { items: Row[]; counts: Record<string, number> } };
export type RunSummary = { id: string; run_name?: string; repo_url?: string; status: string; stage?: string; verdict?: string; busy?: boolean; pending?: boolean; updated: number };
export type Config = { case_budget_default?: number; model: string; base_url: string; key_configured: boolean; key_source?: string; daily: { used: number; remaining: number; limit: number }; stages: string[]; personas: Record<string, { name: string; role: string; goal: string }> };
export const record = (value: unknown): Row => value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
export const rows = (value: unknown): Row[] => Array.isArray(value) ? value.map(record) : [];
/** Words whose display form is fixed. Applied per word before title-casing, so "text" reads "Description". */
export const LABELS: Record<string, string> = { text: 'Description', id: 'ID', sha256: 'SHA-256', api: 'API' };
export const label = (value: unknown): string => {
  const raw = String(value ?? '—');
  if (raw === 'environment_setup') return 'Test execution · Setup';
  return raw.replace(/_/g, ' ').split(' ').map(word => LABELS[word.toLowerCase()] ?? (word.charAt(0).toUpperCase() + word.slice(1))).join(' ');
};
/** Display text for one value. Arrays join with ", "; objects are summarised, never serialised as JSON. */
export const text = (value: unknown): string => {
  if (value == null) return '—';
  if (Array.isArray(value)) return value.length ? value.map(item => text(item)).join(', ') : '—';
  if (typeof value === 'object') { const count = Object.keys(value).length; return count ? `${count} ${count === 1 ? 'field' : 'fields'}` : '—'; }
  return String(value);
};
export type Tone = '' | 'good' | 'warn' | 'bad';
/** The one status tone map. Failures are bad, pending and unverified work is warn, passes are good, everything else neutral. */
export function statusTone(value: unknown): Tone {
  const s = text(value).toLowerCase().trim();
  if (!s || s === '—') return '';
  if (/fail|error|interrupted|blocked|stopped_with_error|not_verified_failed/.test(s)) return 'bad';
  if (/unverified|inconclusive|not_executed|pass_with_skips|wait|review|pending|stopp|working|pass(es)?_?warn/.test(s)) return 'warn';
  if (/^(pass|passed|verified|approved|complete|completed|ready|true|done|supported|good)$/.test(s) || /^pass/.test(s)) return s === 'supported' ? '' : 'good';
  return '';
}
export const number = (value: unknown) => typeof value === 'number' ? value.toLocaleString() : text(value);
export const artifacts = (run: Run) => record(run.state.artifacts);
export const download = (id: string, kind: string, format = 'json', index?: number) => `/api/harness/runs/${encodeURIComponent(id)}/download?${new URLSearchParams({ kind, format, ...(index == null ? {} : { index: String(index) }) })}`;
/** An HTTP error answer from the local API (validation 400, conflict 409, ...). */
export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}
/** The fetch itself failed (network down, proxy unreachable): no HTTP answer was received. */
export class NetworkError extends Error {}
/** Connection failures only: a network failure or an HTTP 503/504 from the proxy. Other errors are not connection errors. */
export const isConnectionError = (error: unknown) => error instanceof NetworkError || (error instanceof ApiError && (error.status === 503 || error.status === 504));
async function send(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(`/api/harness/${path}`, { ...init, cache: 'no-store' });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new NetworkError(error instanceof Error ? error.message : 'Network request failed');
  }
}
/** The window bridge exposed by the desktop shell; undefined in the browser. */
export function desktop<T>(): T | undefined { return typeof window === 'undefined' ? undefined : (window as unknown as { desktop?: T }).desktop; }

async function failure(response: Response): Promise<never> {
  const error = await response.json().catch(() => ({}));
  throw new ApiError(error.error || `Request failed (${response.status})`, response.status);
}

export async function api<T>(path: string, body?: Row, signal?: AbortSignal): Promise<T> {
  const response = await send(path, { method: body ? 'POST' : 'GET', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined, signal });
  if (!response.ok) await failure(response);
  return response.json();
}

export async function apiDelete<T>(path: string): Promise<T> {
  const response = await send(path, { method: 'DELETE' });
  if (!response.ok) await failure(response);
  return response.json();
}

export const minutes = (s: number) => (s < 90 ? `${Math.max(1, Math.round(s))} s` : `${Math.round(s / 60)} min`);
export const runTitle = (name: unknown, repo: unknown, id: string) => (typeof name === 'string' && name) || (typeof repo === 'string' && repo ? repo.replace(/^https?:\/\/github\.com\//, '').replace(/\.git$/, '') : id);
