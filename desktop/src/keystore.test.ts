import { strict as assert } from 'node:assert';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { test } from 'node:test';
import { KeyStore, SafeStorageLike, mcpEnvName } from './keystore';

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

test('MCP secrets: encrypted at rest, env names match Python, view has booleans only, clears', () => {
  const f = tmp();
  const ks = new KeyStore(f, fake());
  assert.deepEqual(ks.saveMcpSecrets('atlassian-jira', { BEARER_TOKEN: 'tok-0123456789' }), { BEARER_TOKEN: true });
  ks.saveMcpSecrets('playwright', { API_KEY: 'pw-secret-value' });
  assert.ok(!fs.readFileSync(f).toString().includes('tok-0123456789'));
  const again = new KeyStore(f, fake());
  assert.equal(again.env()['HARNESS_MCP_SECRET_ATLASSIAN__JIRA_BEARER_TOKEN'], 'tok-0123456789');
  assert.equal(again.env()['HARNESS_MCP_SECRET_PLAYWRIGHT_API_KEY'], 'pw-secret-value');
  assert.ok(!JSON.stringify(again.mcpView()).includes('tok-'));
  assert.deepEqual(again.mcpValues().sort(), ['pw-secret-value', 'tok-0123456789']);
  again.saveMcpSecrets('playwright', { API_KEY: '' });
  assert.deepEqual(again.mcpView(), { 'atlassian-jira': { BEARER_TOKEN: true } });
  assert.throws(() => again.saveMcpSecrets('Bad Name', { A: 'x' }));
  assert.throws(() => again.saveMcpSecrets('ok', { 'bad key': 'x' }));
  assert.throws(() => again.saveMcpSecrets('ok', { A: 'line\nbreak' }));
  assert.deepEqual(again.checkMcpSecrets('ok', { A: ' x ' }), { A: 'x' }); // validates and trims, stores nothing
  assert.deepEqual(again.mcpView(), { 'atlassian-jira': { BEARER_TOKEN: true } });
  again.removeMcpServer('atlassian-jira');
  assert.deepEqual(again.mcpView(), {});
});

test('MCP env names encode - as __ (same as Python) so names cannot collide', () => {
  assert.equal(mcpEnvName('a-b', 'C'), 'HARNESS_MCP_SECRET_A__B_C');
  assert.equal(mcpEnvName('a', 'B_C'), 'HARNESS_MCP_SECRET_A_B_C');
});
