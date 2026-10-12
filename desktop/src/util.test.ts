import test from 'node:test';
import assert from 'node:assert/strict';
import { canOpenExternally, isAppOrigin, isEvidenceUrl, makeToken, mergePath, nonceMatches, pickFreePort, pickTwoPorts, readLoginShellPath, sessionCookieValue } from './util';

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

test('F13: only this launch nonce matches', () => {
  assert.ok(nonceMatches('n-0123456789abcdef', 'n-0123456789abcdef'));
  assert.ok(!nonceMatches('n-0123456789abcdeX', 'n-0123456789abcdef'));
  assert.ok(!nonceMatches('short', 'n-0123456789abcdef'));
  assert.ok(!nonceMatches(undefined, 'n-0123456789abcdef'));
  assert.ok(!nonceMatches('', ''));
});

test('session cookie value is read from Set-Cookie lines only', () => {
  assert.equal(sessionCookieValue(['aqe_session=abc_DEF-123; Path=/; HttpOnly; SameSite=Strict']), 'abc_DEF-123');
  assert.equal(sessionCookieValue(['other=1', 'x=aqe_session=bad value']), null);
  assert.equal(sessionCookieValue([]), null);
});

test('evidence report URLs are recognised', () => {
  assert.ok(isEvidenceUrl('http://127.0.0.1:4000/api/harness/runs/r1/evidence/a.html/view.html'));
  assert.ok(!isEvidenceUrl('http://127.0.0.1:4000/runs/r1'));
  assert.ok(!isEvidenceUrl('http://127.0.0.1:4000/api/harness/skills/evidence/x'));
  assert.ok(!isEvidenceUrl('not a url'));
});

test('macOS service PATH: login shell first, inherited next, fallback dirs last, no duplicates', () => {
  assert.equal(mergePath('/a:/b', '/b:/c'), '/a:/b:/c:/opt/homebrew/bin:/usr/local/bin');
  assert.equal(mergePath(null, '/usr/bin:/bin'), '/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin');
  assert.equal(mergePath('/opt/homebrew/bin', ''), '/opt/homebrew/bin:/usr/local/bin');
});

test('login shell PATH is read through the marker; a failing shell yields null', { skip: process.platform === 'win32' }, async () => {
  const p = await readLoginShellPath('/bin/sh');
  assert.ok(p === null || p.includes('/'), 'a PATH with directories, or null');
  assert.equal(await readLoginShellPath('/nonexistent/shell-for-test'), null);
});
