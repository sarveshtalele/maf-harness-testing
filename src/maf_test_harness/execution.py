"""Explicit local export/execution tools. A working copy is not a security sandbox.

Design rules: every run writes to a NEW result directory (immutable evidence); commands are explicit argv
lists (no shell); credentials are not inherited; timeouts kill the whole process group; a run is only
"passed" when fresh test-report evidence shows at least one executed and passing test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .repo_analyzer import SENSITIVE_PATH
from .skill_contracts import validate_generated_files

EXPORT_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".harness-venv", ".harness-reports",
                    ".gradle", ".idea", ".vscode"}
# Host variables forwarded to setup/test commands. Model/cloud credentials are deliberately absent.
FORWARDED_ENV = ("PATH", "SYSTEMROOT", "LANG", "LC_ALL", "TMPDIR", "JAVA_HOME", "MAVEN_HOME", "GRADLE_HOME",
                 "GOROOT", "DOTNET_ROOT", "PLAYWRIGHT_BROWSERS_PATH", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                 "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
BASIC_ENV = ("PATH", "SYSTEMROOT", "LANG", "TMPDIR")


def _exportable(relative: Path, source: Path) -> bool:
    return (not source.is_symlink() and not any(part in EXPORT_SKIP_DIRS for part in relative.parts)
            and not SENSITIVE_PATH.search(relative.as_posix()))


def export_tests(repo: Path, payload: dict, case_ids: set[str], destination: Path) -> Path:
    """Copy the repository into a NEW workspace and add the approved generated files (never overwrite)."""
    validation = validate_generated_files(payload, case_ids)
    if validation['uncovered_case_ids']:
        raise ValueError(f"uncovered cases: {validation['uncovered_case_ids']}")
    repo, destination = repo.resolve(), destination.resolve()
    if destination.exists() or destination.is_relative_to(repo) or repo.is_relative_to(destination):
        raise ValueError('Export needs a new directory outside the source repository.')
    for item in payload['files']:
        if (repo / item['path']).exists():
            raise ValueError(f"Export would overwrite repository file: {item['path']}")
    destination.mkdir(parents=True)
    for folder, dirs, names in os.walk(repo):
        folder_path = Path(folder)
        dirs[:] = [d for d in dirs if d not in EXPORT_SKIP_DIRS]  # prune, do not descend
        for name in names:
            source = folder_path / name
            relative = source.relative_to(repo)
            if source.is_file() and _exportable(relative, source):
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    for item in payload['files']:
        target = destination / item['path']
        if not target.resolve().is_relative_to(destination) or target.exists():
            raise ValueError(f"Export would overwrite or escape workspace: {item['path']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item['content'], encoding='utf-8')
    return destination


def read_junit_cases(paths: list[Path]) -> list[dict]:
    """Per-testcase outcomes (never nested aggregate attributes, which double-count)."""
    cases = []
    for path in paths:
        for case in ET.parse(path).getroot().iter('testcase'):
            failure = case.find('failure') if case.find('failure') is not None else case.find('error')
            status = ('error' if case.find('error') is not None else 'failed' if case.find('failure') is not None
                      else 'skipped' if case.find('skipped') is not None else 'passed')
            message = ''
            if failure is not None:
                message = ((failure.get('message') or '') + '\n' + (failure.text or '')).strip()[:1500]
            cases.append({'name': case.get('name', ''), 'classname': case.get('classname', ''),
                          'file': case.get('file', ''), 'status': status, 'message': message})
    return cases


def read_junit(paths: list[Path]) -> dict:
    counts = dict(tests=0, passed=0, failed=0, errors=0, skipped=0)
    for case in read_junit_cases(paths):
        counts['tests'] += 1
        counts[{'error': 'errors'}.get(case['status'], case['status'])] += 1
    return counts


def _child_env(output_dir: Path, inherit_env: bool, extra: dict[str, str] | None) -> dict[str, str]:
    names = FORWARDED_ENV if inherit_env else BASIC_ENV
    env = {key: os.environ[key] for key in names if key in os.environ}
    env.update({'HOME': str(output_dir / 'home'), 'PYTHONUNBUFFERED': '1', 'CI': '1'})
    (output_dir / 'home').mkdir()
    env.update(extra or {})
    return env


def run_command(argv: list[str], cwd: Path, output_dir: Path, *, timeout: float = 300,
                junit_glob: str | None = None, env: dict[str, str] | None = None,
                inherit_env: bool = False) -> dict:
    if not argv or not all(isinstance(arg, str) and '\0' not in arg for arg in argv) or timeout <= 0:
        raise ValueError('Provide explicit argv and a positive timeout.')
    cwd, output_dir = cwd.resolve(), output_dir.resolve()
    if not cwd.is_dir():
        raise ValueError('Execution working directory does not exist.')
    if output_dir.exists():
        raise ValueError('Use a new result directory for each execution; old evidence is immutable.')
    output_dir.mkdir(parents=True)
    child_env = _child_env(output_dir, inherit_env, env)
    started = time.time()
    result = {'argv': argv, 'cwd': str(cwd), 'started_at': started, 'status': 'running', 'exit_code': None,
              'isolation': 'local-process-not-security-sandbox'}
    with (output_dir / 'stdout.log').open('w') as stdout, (output_dir / 'stderr.log').open('w') as stderr:
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=child_env, stdout=stdout, stderr=stderr,
                                       start_new_session=True)
            try:
                result['exit_code'] = process.wait(timeout=timeout)
                result['status'] = 'command_succeeded' if process.returncode == 0 else 'failed'
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                result['status'] = 'timeout'
        except OSError as exc:
            result.update(status='blocked', error=str(exc))
    if junit_glob and result['status'] not in {'timeout', 'blocked'}:
        try:
            reports = sorted(p for p in cwd.glob(junit_glob) if p.is_file() and p.stat().st_mtime >= started)
            summary = read_junit(reports)
            result.update(test_results=summary, junit_files=[str(p) for p in reports])
            if result['exit_code'] == 0:
                result['status'] = ('failed' if summary['failed'] or summary['errors']
                                    else 'passed_with_skips' if summary['passed'] and summary['skipped']
                                    else 'passed' if summary['passed'] else 'no_test_evidence')
        except (ET.ParseError, OSError, ValueError) as exc:
            result.update(status='invalid_test_report', error=str(exc))
    result['duration_seconds'] = round(time.time() - started, 3)
    result['log_sha256'] = {name: hashlib.sha256((output_dir / name).read_bytes()).hexdigest()
                            for name in ('stdout.log', 'stderr.log')}
    (output_dir / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    export = sub.add_parser('export', help='Export an approved run into a new working copy')
    export.add_argument('run_dir', type=Path)
    export.add_argument('destination', type=Path)
    execute = sub.add_parser('run', help='Explicitly execute a reviewed command locally')
    execute.add_argument('--cwd', type=Path, required=True)
    execute.add_argument('--output', type=Path, required=True)
    execute.add_argument('--timeout', type=float, default=300)
    execute.add_argument('--junit-glob')
    execute.add_argument('argv', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == 'export':
        from .workflow import approved_case_ids, digest
        state = json.loads((args.run_dir / 'run.json').read_text())['state']
        generated, design = state['artifacts']['test_generation'], state['artifacts']['test_design']
        for name, artifact in (('test_generation', generated), ('test_design', design)):
            if state.get('approved', {}).get(name) != digest(artifact):
                parser.error(f'{name} has not been approved or changed after approval.')
        print(export_tests(args.run_dir / 'repo', generated, approved_case_ids(design), args.destination))
    else:
        argv = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
        result = run_command(argv, args.cwd, args.output, timeout=args.timeout, junit_glob=args.junit_glob)
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result['status'] in {'passed', 'command_succeeded'} else 1)


if __name__ == '__main__':
    main()
