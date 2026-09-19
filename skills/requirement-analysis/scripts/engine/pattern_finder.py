"""
Pattern Finder
For "add" requirements, locates an existing implementation of the same
kind (route handler, model class, service function, UI component) to use
as a structural template — so the new code follows the project's existing
conventions instead of inventing a new style. Selection is deterministic:
first match by sorted path.
"""
from pathlib import Path
from typing import Dict, List, Optional

from .codebase_indexer import FileIndex
from .requirement_parser import RequirementIntent

# Which symbol "kind" best represents a template for each target type.
_TEMPLATE_KIND = {
    "api_endpoint": "route",
    "database": "class",
    "service": "function",
    "ui_component": "function",
}


class PatternFinder:
    def find(self, intent: RequirementIntent, file_indexes: List[FileIndex]) -> Optional[Dict]:
        if intent.action != "add" or not intent.target_types:
            return None

        target_type = intent.target_types[0]
        desired_kind = _TEMPLATE_KIND.get(target_type)
        if not desired_kind:
            return None

        candidates: List[Dict] = []
        for fi in sorted(file_indexes, key=lambda f: f.path):
            if fi.module_type != target_type:
                continue
            for sym in fi.symbols:
                if sym.kind == desired_kind:
                    candidates.append({
                        "reference_file": fi.path,
                        "reference_symbol": {
                            "name": sym.name,
                            "kind": sym.kind,
                            "line": sym.line,
                            "detail": sym.detail,
                        },
                    })

        if not candidates:
            return None

        chosen = candidates[0]
        chosen["note"] = (
            f"Follow this existing {desired_kind} as a structural template "
            f"for the new {target_type}: "
            f"{chosen['reference_symbol']['name']} "
            f"in {chosen['reference_file']}:{chosen['reference_symbol']['line']}."
        )
        return chosen
