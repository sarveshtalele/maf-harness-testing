import * as fs from 'node:fs';
import * as path from 'node:path';

export interface Secrets {
  api_key: string;
  base_url: string;
  model: string;
  stage_models: Record<string, string>;
}
export interface ModelSettingsInput extends Partial<Secrets> {}
/** Renderer-visible view: never contains the key. */
export interface ModelSettingsView {
  base_url: string;
  model: string;
  stage_models: Record<string, string>;
  key_configured: boolean;
  key_hint: string;
  encrypted: boolean; // false: held in memory only for this session
}
export interface SafeStorageLike {
  isEncryptionAvailable(): boolean;
  encryptString(s: string): Buffer;
  decryptString(b: Buffer): string;
}

const EMPTY: Secrets = { api_key: '', base_url: '', model: '', stage_models: {} };

export function validateBaseUrl(url: string): string {
  const v = url.trim();
  if (!v) return '';
  let u: URL;
  try { u = new URL(v); } catch { throw new Error('Base URL is not a valid URL.'); }
  const local = u.hostname === 'localhost' || u.hostname === '127.0.0.1';
  if (u.protocol !== 'https:' && !(u.protocol === 'http:' && local)) throw new Error('Base URL must use https (http is allowed only for localhost).');
  if (u.username || u.password) throw new Error('Base URL must not contain credentials.');
  return v;
}

export function validate(input: ModelSettingsInput): ModelSettingsInput {
  const out: ModelSettingsInput = {};
  if (input.api_key !== undefined && input.api_key !== '') {
    if (typeof input.api_key !== 'string' || /\s/.test(input.api_key.trim()) || input.api_key.length > 500) throw new Error('API key must be a single non-empty string.');
    out.api_key = input.api_key.trim();
  }
  if (input.base_url !== undefined) out.base_url = validateBaseUrl(String(input.base_url));
  if (input.model !== undefined) {
    const m = String(input.model).trim();
    if (!m || m.length > 100) throw new Error('Model must be 1-100 characters.');
    out.model = m;
  }
  if (input.stage_models !== undefined) {
    const sm: Record<string, string> = {};
    for (const [k, v] of Object.entries(input.stage_models ?? {})) {
      const val = String(v).trim();
      if (val.length > 100) throw new Error('Stage model names are limited to 100 characters.');
      if (val) sm[String(k)] = val;
    }
    out.stage_models = sm;
  }
  return out;
}

/** Secrets at rest: safeStorage-encrypted file, or session memory when the OS cannot encrypt. */
export class KeyStore {
  private cache: Secrets | null = null;

  constructor(private file: string, private ss: SafeStorageLike) {}

  get(): Secrets {
    if (this.cache) return this.cache;
    let s = { ...EMPTY };
    if (this.ss.isEncryptionAvailable() && fs.existsSync(this.file)) {
      try {
        s = { ...EMPTY, ...JSON.parse(this.ss.decryptString(fs.readFileSync(this.file))) };
      } catch { /* unreadable or from another user/machine: start empty */ }
    }
    return (this.cache = s);
  }

  /** Merge validated input (empty api_key keeps the stored one) and persist when encryption is available. */
  save(input: ModelSettingsInput): ModelSettingsView {
    this.cache = { ...this.get(), ...validate(input) };
    if (this.ss.isEncryptionAvailable()) {
      fs.mkdirSync(path.dirname(this.file), { recursive: true, mode: 0o700 });
      const tmp = `${this.file}.tmp`;
      fs.writeFileSync(tmp, this.ss.encryptString(JSON.stringify(this.cache)), { mode: 0o600 });
      fs.renameSync(tmp, this.file);
      try { fs.chmodSync(this.file, 0o600); } catch { /* Windows */ }
    }
    return this.view();
  }

  view(): ModelSettingsView {
    const s = this.get();
    return {
      base_url: s.base_url, model: s.model, stage_models: s.stage_models,
      key_configured: !!s.api_key,
      key_hint: s.api_key.length >= 8 ? s.api_key.slice(-4) : s.api_key ? '****' : '',
      encrypted: this.ss.isEncryptionAvailable(),
    };
  }

  /** Env for the backend spawn. Unset fields are omitted so the .env/environment fallback still applies. */
  env(): NodeJS.ProcessEnv {
    const s = this.get();
    const e: NodeJS.ProcessEnv = {};
    if (s.api_key) e.OPENAI_API_KEY = s.api_key;
    if (s.base_url) e.OPENAI_BASE_URL = s.base_url;
    if (s.model) e.OPENAI_MODEL = s.model;
    if (Object.keys(s.stage_models).length) e.HARNESS_STAGE_MODELS = JSON.stringify(s.stage_models);
    return e;
  }
}
