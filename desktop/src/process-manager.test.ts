import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, ChildProcess } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { killTree, reapOrphans } from './process-manager';

// POSIX only: the tests rely on process groups and kill(-pid). Windows uses taskkill /T, which has no such check here.
const posixOnly = process.platform === 'win32' ? 'process groups are POSIX-only' : false;

/** Fake service: a leader that spawns a grandchild ignoring SIGTERM. The leader either stays up or exits at once. */
function spawnFakeTree(mode: 'stay' | 'exit'): ChildProcess {
  const code = `
    const { spawn } = require('node:child_process');
    const g = spawn(process.execPath, ['-e', "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);"], { stdio: 'ignore' });
    const done = () => ${mode === 'exit' ? 'process.exit(0);' : 'void 0;'};
    process.stdout.write(g.pid + '\\n', done);
    ${mode === 'stay' ? 'setInterval(() => {}, 1000);' : ''}
  `;
  return spawn(process.execPath, ['-e', code], { detached: true, stdio: ['pipe', 'pipe', 'inherit'] });
}

function readGrandchildPid(child: ChildProcess): Promise<number> {
  return new Promise((resolve) => child.stdout!.once('data', (b: Buffer) => resolve(Number(String(b).trim()))));
}

function groupOrPidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === 'EPERM';
  }
}

async function eventuallyGone(pid: number, ms = 3000): Promise<boolean> {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (!groupOrPidAlive(pid)) return true;
    await new Promise((r) => setTimeout(r, 50));
  }
  return !groupOrPidAlive(pid);
}

test('killTree stops a live leader and a grandchild that ignores SIGTERM', { skip: posixOnly }, async () => {
  const child = spawnFakeTree('stay');
  const grandchild = await readGrandchildPid(child);
  assert.ok(groupOrPidAlive(grandchild));
  await killTree(child, 1000);
  assert.ok(await eventuallyGone(grandchild), 'grandchild survived the group kill');
});

test('killTree still reaps the group when the leader already exited', { skip: posixOnly }, async () => {
  const child = spawnFakeTree('exit');
  const grandchild = await readGrandchildPid(child);
  await new Promise((r) => child.once('exit', r));
  assert.ok(groupOrPidAlive(grandchild));
  await killTree(child, 1000);
  assert.ok(await eventuallyGone(grandchild), 'grandchild outlived its exited leader');
});

test('reapOrphans leaves a record alone when its command line lacks the marker', { skip: posixOnly }, async () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'pm-test-')), 'children.json');
  fs.writeFileSync(file, JSON.stringify([{ name: 'backend', pid: process.pid, port: 1, marker: 'no-such-marker-xyz' }]));
  assert.deepEqual(await reapOrphans(file), []);
  assert.ok(groupOrPidAlive(process.pid));
  assert.ok(!fs.existsSync(file), 'the stale record file is removed');
});
