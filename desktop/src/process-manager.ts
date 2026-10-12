import { ChildProcess, spawn, spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

export interface ServiceSpec {
  name: string;
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
  port: number;
  /** Text that must appear in the process command line; orphan cleanup only kills processes that carry it. */
  marker: string;
}

/** One live service, as recorded in <userData>/children.json so a later launch can reap it after a crash. */
export interface ChildRecord {
  name: string;
  pid: number;
  port: number;
  marker: string;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const OUTPUT_KEEP = 64 * 1024;

/** Starts services in their own process group (POSIX) and kills the whole tree. Each child keeps its stdin as a pipe:
 *  the child exits on EOF (see the parent-exit preload and desktop_backend.py), so a crashed shell takes them along. */
export class ProcessManager {
  private extraRedact = new Set<string>();
  private procs = new Map<string, { child: ChildProcess; log: fs.WriteStream; output: string }>();
  private spawnErrors = new WeakSet<ChildProcess>();

  constructor(private logDir: string, private childrenFile: string) {
    fs.mkdirSync(logDir, { recursive: true, mode: 0o700 });
  }

  start(spec: ServiceSpec, redact: string[] = []): ChildProcess {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const log = fs.createWriteStream(path.join(this.logDir, `desktop-${spec.name}-${stamp}.log`), { mode: 0o600 });
    const child = spawn(spec.command, spec.args, {
      cwd: spec.cwd,
      env: spec.env,
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: process.platform !== 'win32', // own process group so killpg reaches grandchildren
      windowsHide: true,
    });
    const entry = { child, log, output: '' };
    log.on('error', () => {}); // a failed log write must not crash the main process
    const write = (text: string) => { if (!log.writableEnded) log.write(text); };
    const pump = (buf: Buffer) => {
      let text = buf.toString('utf8');
      for (const s of [...redact, ...this.extraRedact]) if (s) text = text.split(s).join('[REDACTED]');
      entry.output = (entry.output + text).slice(-OUTPUT_KEEP);
      write(text);
    };
    child.stdout?.on('data', pump);
    child.stderr?.on('data', pump);
    child.stdin?.on('error', () => {}); // the child may exit first; a write error here is not a failure
    child.on('error', (e) => {
      this.spawnErrors.add(child);
      write(`spawn error: ${e.message}\n`);
    });
    child.on('exit', (code, sig) => write(`\n[exit code=${code} signal=${sig}]\n`));
    this.procs.set(spec.name, entry);
    if (child.pid) this.record({ name: spec.name, pid: child.pid, port: spec.port, marker: spec.marker });
    return child;
  }

  /** Values to hide from every service log from now on (e.g. a secret entered while services run). */
  addRedactions(values: string[]): void {
    for (const v of values) if (v) this.extraRedact.add(v);
  }

  /** True when the child emitted 'error' (spawn failure such as ENOENT); it may never emit 'exit'. */
  hasSpawnError(child: ChildProcess): boolean {
    return this.spawnErrors.has(child);
  }

  isRunning(name: string): boolean {
    const p = this.procs.get(name)?.child;
    return !!p && p.exitCode === null && p.signalCode === null && !this.spawnErrors.has(p);
  }

  /** Resolves with the regex match once the named service prints it; fails if it exits first or time runs out. */
  async waitForOutput(name: string, pattern: RegExp, timeoutMs: number, alive: () => boolean): Promise<RegExpMatchArray> {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const match = this.procs.get(name)?.output.match(pattern);
      if (match) return match;
      if (!alive()) throw new Error(`${name} exited before it reported ready`);
      if (Date.now() > deadline) throw new Error(`timed out waiting for ${name} to report ready`);
      await sleep(100);
    }
  }

  /** Stop then start again, e.g. when the API key changes and the backend must re-read its env. */
  async restart(spec: ServiceSpec, redact: string[] = []): Promise<ChildProcess> {
    await this.stop(spec.name);
    return this.start(spec, redact);
  }

  async stop(name: string): Promise<void> {
    const entry = this.procs.get(name);
    if (!entry) return;
    this.procs.delete(name);
    this.unrecord(name);
    await killTree(entry.child);
    entry.log.end();
  }

  async stopAll(): Promise<void> {
    await Promise.all([...this.procs.keys()].map((n) => this.stop(n)));
  }

  private records(): ChildRecord[] {
    try {
      return JSON.parse(fs.readFileSync(this.childrenFile, 'utf8')) as ChildRecord[];
    } catch {
      return [];
    }
  }

  private save(list: ChildRecord[]): void {
    const tmp = `${this.childrenFile}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(list, null, 2), { encoding: 'utf8', mode: 0o600 });
    fs.renameSync(tmp, this.childrenFile);
  }

  private record(rec: ChildRecord): void {
    this.save([...this.records().filter((r) => r.name !== rec.name), rec]);
  }

  private unrecord(name: string): void {
    this.save(this.records().filter((r) => r.name !== name));
  }
}

/** Is any process still in the group? kill(-pid, 0) also sees a zombie leader, so the group counts until reaped. */
function groupAlive(pid: number): boolean {
  try {
    process.kill(-pid, 0);
    return true;
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === 'EPERM';
  }
}

/** Stops a service tree. POSIX: SIGTERM to the whole group, poll until it is empty, SIGKILL any survivor.
 *  The group is signalled even when the leader already exited, so a grandchild that ignores SIGTERM is still reaped.
 *  Windows: `taskkill /T` (graceful), wait, then `/T /F`. Grandchildren orphaned by a dead parent are not in the
 *  tree any more, so only the parent-exit preload and the stdin watcher cover that case. */
export async function killTree(child: ChildProcess, graceMs = 5000): Promise<void> {
  if (child.pid) await killProcessTree(child.pid, graceMs);
}

export async function killProcessTree(pid: number, graceMs = 5000): Promise<void> {
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(pid), '/T'], { windowsHide: true });
    const deadline = Date.now() + graceMs;
    while (Date.now() < deadline && isPidAlive(pid)) await sleep(100);
    spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true });
    return;
  }
  const signal = (s: NodeJS.Signals) => {
    try { process.kill(-pid, s); } catch { /* group already gone */ }
  };
  signal('SIGTERM');
  const deadline = Date.now() + graceMs;
  while (Date.now() < deadline && groupAlive(pid)) await sleep(100);
  if (groupAlive(pid)) signal('SIGKILL');
}

function isPidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === 'EPERM';
  }
}

/** Full command line of a pid, or null when it cannot be read (the process is gone or the lookup failed). */
export function commandLine(pid: number): string | null {
  const out = process.platform === 'win32'
    ? spawnSync('powershell', ['-NoProfile', '-Command', `(Get-CimInstance Win32_Process -Filter 'ProcessId=${pid}').CommandLine`], { windowsHide: true, encoding: 'utf8', timeout: 10_000 })
    : spawnSync('ps', ['-o', 'command=', '-p', String(pid)], { encoding: 'utf8', timeout: 10_000 });
  return out.status === 0 && out.stdout.trim() ? out.stdout.trim() : null;
}

/** Kills services a previous app instance left behind (crash, End task, SIGKILL). A record is acted on only when its
 *  pid still carries the recorded marker, so a reused pid is never touched. Returns the names it stopped. */
export async function reapOrphans(childrenFile: string): Promise<string[]> {
  let records: ChildRecord[] = [];
  try {
    records = JSON.parse(fs.readFileSync(childrenFile, 'utf8')) as ChildRecord[];
  } catch {
    return [];
  }
  const reaped: string[] = [];
  for (const rec of Array.isArray(records) ? records : []) {
    if (!rec || typeof rec.pid !== 'number' || typeof rec.marker !== 'string' || !rec.marker) continue;
    const cmd = commandLine(rec.pid);
    if (cmd && cmd.includes(rec.marker)) {
      await killProcessTree(rec.pid, 2000);
      reaped.push(rec.name);
    }
  }
  fs.rmSync(childrenFile, { force: true });
  return reaped;
}

/** okStatus "any": ready as soon as the server answers at all. Only for services that do not receive the API token. */
export async function waitForHttp(url: string, okStatus: number | 'any' = 200, timeoutMs = 90_000, alive?: () => boolean): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (alive && !alive()) throw new Error(`service exited before ${url} became ready`);
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (okStatus === 'any' || r.status === okStatus) return;
    } catch { /* not up yet */ }
    await sleep(300);
  }
  throw new Error(`timed out waiting for ${url}`);
}
