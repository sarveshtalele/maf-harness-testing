import test from 'node:test';
import assert from 'node:assert/strict';
import { canOpenExternally, csp, isAppOrigin, isEvidenceUrl, makeToken, pickFreePort, pickTwoPorts } from './util';

test('free ports are loopback-bindable and distinct', async () => {
  const [a, b] = await pickTwoPorts();
  assert.ok(a > 0 && b > 0 && a !== b);
  assert.ok((await pickFreePort()) > 0);
});

test('token is long, url-safe and unique', () => {
  const t = makeToken();
  assert.ok(t.length >= 64 && /^[A-Za-z0-9_-]+$/.test(t));
  assert.notEqual(t, makeToken());
});

test('navigation guard', () => {
  assert.ok(isAppOrigin('http://127.0.0.1:4000/runs', 4000));
  assert.ok(isAppOrigin('http://localhost:4000/', 4000));
  assert.ok(!isAppOrigin('http://127.0.0.1:4001/', 4000));
  assert.ok(!isAppOrigin('https://127.0.0.1:4000/', 4000));
  assert.ok(!isAppOrigin('http://127.0.0.1.evil.com:4000/', 4000));
  assert.ok(!isAppOrigin('http://evil.com@127.0.0.1:4001/', 4000));
  assert.ok(!isAppOrigin('file:///etc/passwd', 4000));
  assert.ok(!isAppOrigin('not a url', 4000));
});

test('external links: https only', () => {
  assert.ok(canOpenExternally('https://github.com/x'));
  assert.ok(!canOpenExternally('http://github.com/x'));
  assert.ok(!canOpenExternally('file:///x'));
  assert.ok(!canOpenExternally('javascript:alert(1)'));
});

test('csp restricts framing and objects', () => {
  const c = csp(4000);
  assert.match(c, /frame-ancestors 'self'/);
  assert.match(c, /object-src 'none'/);
});

test('evidence report URLs are recognised', () => {
  assert.ok(isEvidenceUrl('http://127.0.0.1:4000/api/harness/runs/r1/evidence/a.html/view.html'));
  assert.ok(!isEvidenceUrl('http://127.0.0.1:4000/runs/r1'));
  assert.ok(!isEvidenceUrl('not a url'));
});
