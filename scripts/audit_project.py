"""Build a static file inventory without importing or executing audited files."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
paths = sorted({p for folder in ('src', 'tests', 'skills', 'scripts') for p in (ROOT/folder).rglob('*')
                if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
               | {ROOT/name for name in ('app.py', 'pyproject.toml', 'uv.lock', 'README.md', '.env.example', '.gitignore')})
records = []
for path in paths:
    raw = path.read_bytes()
    entry = {'path': str(path.relative_to(ROOT)), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    if path.suffix == '.py':
        try:
            tree = ast.parse(raw.decode('utf-8'))
            entry['syntax'] = 'valid'
            entry['symbols'] = [{'name': node.name, 'line': node.lineno, 'kind': type(node).__name__}
                                for node in ast.walk(tree)
                                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            entry['imports'] = sorted({node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
                                      | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                                         for alias in node.names})
        except (SyntaxError, UnicodeError) as exc:
            entry['syntax'] = str(exc)
    records.append(entry)
output = ROOT/'docs/audit/file-inventory.json'
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(records, indent=2))
print(f'Inventoried {len(records)} files: {output}')
