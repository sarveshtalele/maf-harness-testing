import { strict as assert } from 'node:assert';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { test } from 'node:test';
import { KeyStore, SafeStorageLike, keyHost } from './keystore';

const fake = (available = true): SafeStorageLike => ({
  isEncryptionAvailable: () => available,
  encryptString: (s) => Buffer.from(Buffer.from(s).toString('base64') + '!enc'),
  decryptString: (b) => Buffer.from(b.toString().replace(/!enc$/, ''), 'base64').toString(),
});
const tmp = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'ks-')), 'secrets.bin');
const KEY = 'fake-test-0123456789abcdef';

test('saves encrypted, round-trips, view hides the key', () => {
  const f = tmp();
  const v = new KeyStore(f, fake()).save({ api_key: KEY, model: 'm', base_url: 'https://gw.example.com/v1', stage_models: { a: 'b' } });
  assert.equal(v.key_hint, KEY.slice(-4));
  assert.ok(!JSON.stringify(v).includes(KEY));
  assert.ok(!fs.readFileSync(f).toString().includes(KEY));
  if (process.platform !== 'win32') assert.equal(fs.statSync(f).mode & 0o777, 0o600);
  const again = new KeyStore(f, fake());
  assert.equal(again.get().api_key, KEY);
  assert.deepEqual(again.env(), {
    OPENAI_API_KEY: KEY, OPENAI_BASE_URL: 'https://gw.example.com/v1', OPENAI_MODEL: 'm', HARNESS_STAGE_MODELS: '{"a":"b"}',
  });
});

test('empty api_key keeps the stored one; omitted fields are not exported', () => {
  const ks = new KeyStore(tmp(), fake());
  ks.save({ api_key: KEY });
  ks.save({ api_key: '', model: 'next' });
  assert.equal(ks.get().api_key, KEY);
  assert.deepEqual(Object.keys(ks.env()).sort(), ['OPENAI_API_KEY', 'OPENAI_MODEL']);
});

test('without OS encryption the key stays in memory only', () => {
  const f = tmp();
  const ks = new KeyStore(f, fake(false));
  const v = ks.save({ api_key: KEY });
  assert.equal(v.encrypted, false);
  assert.equal(v.key_configured, true);
  assert.ok(!fs.existsSync(f));
  assert.equal(new KeyStore(f, fake(false)).view().key_configured, false);
});

test('Linux basic_text keyring is treated as unencrypted (in memory only)', () => {
  const basic: SafeStorageLike = { ...fake(), getSelectedStorageBackend: () => 'basic_text' };
  const f = tmp();
  const v = new KeyStore(f, basic, 'linux').save({ api_key: KEY });
  assert.equal(v.encrypted, false);
  assert.ok(!fs.existsSync(f));
  assert.equal(new KeyStore(f, basic, 'linux').view().key_configured, false);
  // Other platforms and real keychain backends keep using safeStorage.
  const kwallet: SafeStorageLike = { ...fake(), getSelectedStorageBackend: () => 'kwallet5' };
  assert.equal(new KeyStore(tmp(), kwallet, 'linux').save({ api_key: KEY }).encrypted, true);
  assert.equal(new KeyStore(tmp(), basic, 'darwin').save({ api_key: KEY }).encrypted, true);
});

test('Linux basic_text: a secrets file from an earlier build is deleted and reported', () => {
  const f = tmp();
  new KeyStore(f, fake()).save({ api_key: KEY }); // written under the OS keychain earlier
  assert.ok(fs.existsSync(f));
  const basic: SafeStorageLike = { ...fake(), getSelectedStorageBackend: () => 'basic_text' };
  const v = new KeyStore(f, basic, 'linux').view();
  assert.equal(v.key_configured, false);
  assert.match(v.notice ?? '', /Saved key was discarded/);
  assert.ok(!fs.existsSync(f));
  assert.equal(new KeyStore(f, basic, 'linux').view().notice, undefined);
});

test('rejects insecure urls, bad models and keys; corrupt file reads empty', () => {
  const ks = new KeyStore(tmp(), fake());
  assert.throws(() => ks.save({ base_url: 'http://example.com' }));
  assert.throws(() => ks.save({ base_url: 'https://u:p@example.com' }));
  assert.throws(() => ks.save({ model: '' }));
  assert.throws(() => ks.save({ model: 'x'.repeat(101) }));
  assert.throws(() => ks.save({ api_key: 'a b' }));
  assert.doesNotThrow(() => ks.save({ base_url: 'http://localhost:4000/v1' }));
  const f = tmp();
  fs.writeFileSync(f, 'garbage');
  assert.equal(new KeyStore(f, fake()).view().key_configured, false);
});

const TARGET_A = 'a'.repeat(64);
const TARGET_B = 'b'.repeat(64);

test('MCP secrets: encrypted at rest, view has booleans only, clears', () => {
  const f = tmp();
  const ks = new KeyStore(f, fake());
  assert.deepEqual(ks.saveMcpSecrets('atlassian-jira', { BEARER_TOKEN: 'tok-0123456789' }, TARGET_A), { BEARER_TOKEN: true });
  ks.saveMcpSecrets('playwright', { API_KEY: 'pw-secret-value' }, TARGET_B);
  assert.ok(!fs.readFileSync(f).toString().includes('tok-0123456789'));
  const again = new KeyStore(f, fake());
  assert.equal(again.get().mcp['atlassian-jira']?.BEARER_TOKEN, 'tok-0123456789');
  assert.ok(!JSON.stringify(again.mcpView()).includes('tok-'));
  assert.deepEqual(again.mcpValues().sort(), ['pw-secret-value', 'tok-0123456789']);
  again.saveMcpSecrets('playwright', { API_KEY: '' }, TARGET_B);
  assert.deepEqual(again.mcpView(), { 'atlassian-jira': { BEARER_TOKEN: true } });
  assert.throws(() => again.saveMcpSecrets('Bad Name', { A: 'x' }, TARGET_A));
  assert.throws(() => again.saveMcpSecrets('ok', { 'bad key': 'x' }, TARGET_A));
  assert.throws(() => again.saveMcpSecrets('ok', { A: 'line\nbreak' }, TARGET_A));
  assert.throws(() => again.saveMcpSecrets('ok', { A: 'x' }, 'not-a-fingerprint'));
  assert.deepEqual(again.checkMcpSecrets('ok', { A: ' x ' }), { A: 'x' }); // validates and trims, stores nothing
  assert.deepEqual(again.mcpView(), { 'atlassian-jira': { BEARER_TOKEN: true } });
  again.removeMcpServer('atlassian-jira');
  assert.deepEqual(again.mcpView(), {});
});

test('MCP secrets never enter the spawn environment', () => {
  const ks = new KeyStore(tmp(), fake());
  ks.saveMcpSecrets('atlassian-jira', { BEARER_TOKEN: 'tok-0123456789' }, TARGET_A);
  assert.ok(!Object.keys(ks.env()).some((k) => k.startsWith('HARNESS_MCP_SECRET_')));
  assert.ok(!Object.values(ks.env()).includes('tok-0123456789'));
});

test('F4: reconcile drops secrets whose target changed or disappeared, and keeps the rest', () => {
  const f = tmp();
  const ks = new KeyStore(f, fake());
  ks.saveMcpSecrets('jira', { BEARER_TOKEN: 'jira-token-value' }, TARGET_A);
  ks.saveMcpSecrets('gone', { BEARER_TOKEN: 'gone-token-value' }, TARGET_A);
  ks.saveMcpSecrets('moved', { BEARER_TOKEN: 'moved-token-value' }, TARGET_A);
  const dropped = ks.reconcileMcp({ jira: TARGET_A, moved: TARGET_B }); // "gone" no longer exists in the backend
  assert.deepEqual(dropped.sort(), ['gone', 'moved']);
  assert.deepEqual(ks.mcpView(), { jira: { BEARER_TOKEN: true } });
  assert.deepEqual(ks.mcpValues(), ['jira-token-value']);
  // The drop is persisted: a restart does not bring the old values back.
  assert.deepEqual(new KeyStore(f, fake()).mcpValues(), ['jira-token-value']);
});

test('F4: a secret saved for a new target never keeps values from the old one', () => {
  const ks = new KeyStore(tmp(), fake());
  ks.saveMcpSecrets('jira', { BEARER_TOKEN: 'old-token-value', OTHER: 'old-other-value' }, TARGET_A);
  ks.saveMcpSecrets('jira', { BEARER_TOKEN: 'new-token-value' }, TARGET_B);
  assert.deepEqual(ks.get().mcp.jira, { BEARER_TOKEN: 'new-token-value' });
  assert.equal(ks.get().mcp_fp.jira, TARGET_B);
  // Secrets stored from before fingerprints existed have no fingerprint, so they are dropped on the next reconcile.
  const legacy = new KeyStore(tmp(), fake());
  legacy.save({});
  (legacy as unknown as { cache: { mcp: Record<string, Record<string, string>> } }).cache.mcp = { jira: { BEARER_TOKEN: 'legacy-value' } };
  assert.deepEqual(legacy.reconcileMcp({ jira: TARGET_A }), ['jira']);
});

test('F3: a base URL host change needs a new API key in the same call', () => {
  const ks = new KeyStore(tmp(), fake());
  ks.save({ api_key: KEY, base_url: 'https://gw.example.com/v1' });
  assert.throws(() => ks.save({ base_url: 'https://attacker.example.net/v1' }), /new API key in the same request/);
  assert.equal(ks.get().base_url, 'https://gw.example.com/v1'); // nothing changed
  assert.equal(ks.get().api_key, KEY);
  ks.save({ base_url: 'https://gw.example.com:8443/v2' }); // same host, other path and port: allowed
  ks.save({ base_url: 'https://attacker.example.net/v1', api_key: 'other-key-0123456789' }); // new host with a new key
  assert.equal(ks.get().api_key, 'other-key-0123456789');
  const noKey = new KeyStore(tmp(), fake());
  noKey.save({ base_url: 'https://gw.example.com/v1' }); // no saved key yet: nothing to protect
  noKey.save({ base_url: 'https://other.example.org/v1' });
});

test('F3: the empty base URL means the provider host', () => {
  assert.equal(keyHost(''), 'api.openai.com');
  assert.equal(keyHost('https://GW.Example.com:443/v1'), 'gw.example.com');
  assert.equal(keyHost('http://[::1]:4000/v1'), '::1');
  const ks = new KeyStore(tmp(), fake());
  ks.save({ api_key: KEY });
  assert.throws(() => ks.save({ base_url: 'https://gw.example.com/v1' }), /new API key/);
});
