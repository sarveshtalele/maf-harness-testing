"""Entry point of the bundled desktop backend (``maf-backend``).

Serves the loopback API exactly like ``python -m maf_test_harness.ui.api`` (``--port``, ``HARNESS_API_TOKEN``),
plus ``--self-check`` for CI: verify bundled resources and lint the skills, no network, then exit.
"""

from __future__ import annotations

import multiprocessing
import sys

from maf_test_harness import paths


def self_check() -> int:
    missing = [
        str(p)
        for p in (
            paths.skills_dir() / "SKILLS.lock.json",
            paths.prompts_dir() / "personas.json",
            paths.prompts_dir() / "repository-review.md",
        )
        if not p.is_file()
    ]
    from maf_test_harness.qa import step_reports

    if not (step_reports.TEMPLATES / "00-summary.md").is_file():
        missing.append(str(step_reports.TEMPLATES / "00-summary.md"))
    if missing:
        print("self-check FAILED, missing bundled resources:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 1
    from maf_test_harness import cli

    code = cli.main(["lint-skills"])
    print(f"self-check {'OK' if not code else 'FAILED'} (resources: {paths.resource_root()})", flush=True)
    return int(bool(code))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--self-check" in argv:
        return self_check()
    from maf_test_harness.ui import api

    sys.argv = [sys.argv[0], *argv]
    api.main()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
