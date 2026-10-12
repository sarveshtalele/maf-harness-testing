import test from 'node:test';
import assert from 'node:assert/strict';
import { denyPermissionCheck, denyPermissionRequest, isFrameNavigationAllowed, isTrustedSender } from './guards';

const UI = 4000;
const app = (path = '/runs') => `http://127.0.0.1:${UI}${path}`;

test('IPC: only the app page is a trusted sender', () => {
  assert.equal(isTrustedSender({ senderFrame: { url: app() } }, UI), true);
  assert.equal(isTrustedSender({ senderFrame: { url: app('/runs/r1/evidence') } }, UI), true);
  // Evidence report frames are never trusted, even on the app origin.
  assert.equal(isTrustedSender({ senderFrame: { url: app('/api/harness/runs/r1/evidence/h/view.html') } }, UI), false);
  assert.equal(isTrustedSender({ senderFrame: { url: 'https://evil.example/' } }, UI), false);
  assert.equal(isTrustedSender({ senderFrame: { url: 'http://127.0.0.1:4001/' } }, UI), false); // other port
  assert.equal(isTrustedSender({ senderFrame: null }, UI), false); // detached frame
  assert.equal(isTrustedSender(undefined, UI), false);
  assert.equal(isTrustedSender({ senderFrame: { url: app() } }, null), false); // before the UI is up
});

test('frames: navigation stays on the app origin', () => {
  assert.equal(isFrameNavigationAllowed(app('/runs/x'), UI), true);
  assert.equal(isFrameNavigationAllowed('https://attacker.example/', UI), false);
  assert.equal(isFrameNavigationAllowed('http://127.0.0.1:4001/', UI), false);
  assert.equal(isFrameNavigationAllowed('data:text/html,<script>1</script>', UI), false);
  assert.equal(isFrameNavigationAllowed(app(), null), false);
});

test('permissions: every request and every check is refused', () => {
  let answer: boolean | undefined;
  denyPermissionRequest({}, 'media', granted => { answer = granted; });
  assert.equal(answer, false);
  assert.equal(denyPermissionCheck(), false);
});
