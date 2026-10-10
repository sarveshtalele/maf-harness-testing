import { ChildProcess, spawn, spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

export interface ServiceSpec {
  name: string;
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Starts services in their own process group (POSIX) / tree (Windows) and kills the whole tree. */
export class ProcessManager {
  private extraRedact = new Set<string>();
  private procs = new Map<string, { child: ChildProcess; log: fs.WriteStream }>();

  constructor(private logDir: string) {
    fs.mkdirSync(logDir, { recursive: true, mode: 0o700 });
  }

  start(spec: ServiceSpec, redact: string[] = []): ChildProcess {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const log = fs.createWriteStream(path.join(this.logDir, `desktop-${spec.name}-${stamp}.log`), { mode: 0o600 });
    const child = spawn(spec.command, spec.args, {
      cwd: spec.cwd,
      env: spec.env,
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: process.platform !== 'win32', // own process group so killpg reaches grandchildren
      windowsHide: true,
    });
    const pump = (buf: Buffer) => {
      let text = buf.toString('utf8');
      for (const s of [...redact, ...this.extraRedact]) if (s) text = text.split(s).join('[REDACTED]');
      log.write(text);
    };
    child.stdout?.on('data', pump);
    child.stderr?.on('data', pump);
    child.on('error', (e) => log.write(`spawn error: ${e.message}\n`));
    child.on('exit', (code, sig) => log.write(`\n[exit code=${code} signal=${sig}]\n`));
    this.procs.set(spec.name, { child, log });
    return child;
  }

  /** Values to hide from every service log from now on (e.g. a secret entered while services run). */
  addRedactions(values: string[]): void {
    for (const v of values) if (v) this.extraRedact.add(v);
  }

  isRunning(name: string): boolean {
    const p = this.procs.get(name)?.child;
    return !!p && p.exitCode === null && p.signalCode === null;
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
    await killTree(entry.child);
    entry.log.end();
  }

  async stopAll(): Promise<void> {
    await Promise.all([...this.procs.keys()].map((n) => this.stop(n)));
  }
}

export async function killTree(child: ChildProcess): Promise<void> {
  const pid = child.pid;
  if (!pid || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true });
    return;
  }
  const signal = (s: NodeJS.Signals) => {
    try { process.kill(-pid, s); } catch { /* group already gone */ }
  };
  signal('SIGTERM');
  for (let i = 0; i < 50; i++) { // 5 s grace
    if (child.exitCode !== null || child.signalCode !== null) return;
    await sleep(100);
  }
  signal('SIGKILL');
}

export async function waitForHttp(url: string, okStatus = 200, timeoutMs = 90_000, alive?: () => boolean): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (alive && !alive()) throw new Error(`service exited before ${url} became ready`);
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (r.status === okStatus) return;
    } catch { /* not up yet */ }
    await sleep(300);
  }
  throw new Error(`timed out waiting for ${url}`);
}
