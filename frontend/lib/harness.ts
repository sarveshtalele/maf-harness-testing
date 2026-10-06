export type Row = Record<string, unknown>;
export type Pending = { id: string; stage: string; kind: string; title: string; details: Row; error?: string };
export type Run = { id: string; status: string; state: Row; pending: Pending | null; actions: string[]; busy: boolean; service_busy: boolean; pending_consumed?: boolean; operation?: Row; run_directory?: string; operation_error?: string; error?: string; has_zip: boolean };
export type RunSummary = { id: string; repo_url?: string; status: string; stage?: string; verdict?: string; busy?: boolean; pending?: boolean; updated: number };
export type Config = { model: string; base_url: string; key_configured: boolean; daily: { used: number; remaining: number; limit: number }; stages: string[]; personas: Record<string, { name: string; role: string; goal: string }> };
export const record = (value: unknown): Row => value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
export const rows = (value: unknown): Row[] => Array.isArray(value) ? value.map(record) : [];
export const label = (value: unknown) => (value === 'environment_setup' ? 'Test execution · Setup' : String(value ?? '—')).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
export const text = (value: unknown): string => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
export const number = (value: unknown) => typeof value === 'number' ? value.toLocaleString() : text(value);
export const artifacts = (run: Run) => record(run.state.artifacts);
export const download = (id: string, kind: string, format = 'json', index?: number) => `/api/harness/runs/${encodeURIComponent(id)}/download?${new URLSearchParams({ kind, format, ...(index == null ? {} : { index: String(index) }) })}`;
export async function api<T>(path: string, body?: Row, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/harness/${path}`, { method: body ? 'POST' : 'GET', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined, cache: 'no-store', signal });
  if (!response.ok) { const error = await response.json(); throw new Error(error.error || `Request failed (${response.status})`); }
  return response.json();
}
