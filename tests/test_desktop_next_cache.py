"""The desktop Next server runs from resources/frontend inside Program Files, which standard users cannot write.

Next writes to <distDir>/cache at runtime only for the image optimizer's disk cache and the ISR flush to disk. The
desktop build turns both off (frontend/next.config.ts, DESKTOP_BUILD=1 branch). This test pins that config, and checks
that the frontend uses none of the features that would write a cache file (next/image, ISR revalidation, the data or
use-cache APIs, fetch revalidation options). If one of those is added, the config must change with it.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend"
CONFIG = FRONTEND / "next.config.ts"
CACHE_WRITERS = re.compile(
    r"from\s+['\"]next/image['\"]|revalidate\s*[:=]|unstable_cache|['\"]use cache['\"]|cacheLife|cacheTag|"
    r"revalidatePath|revalidateTag|next:\s*\{"
)


def _desktop_branch(text: str) -> str:
    start = text.index("process.env.DESKTOP_BUILD === '1'")
    return text[start : text.index("} : {})", start)]


def test_desktop_build_turns_off_next_disk_cache_writes():
    branch = _desktop_branch(CONFIG.read_text(encoding="utf-8"))
    assert "output: 'standalone'" in branch
    assert "images: { maximumDiskCacheSize: 0 }" in branch
    assert "experimental: { isrFlushToDisk: false }" in branch


def test_frontend_uses_no_next_feature_that_writes_a_cache_file_at_runtime():
    offenders = []
    for path in sorted(FRONTEND.rglob("*")):
        if path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
            continue
        if {"node_modules", ".next", "tests"} & set(path.relative_to(FRONTEND).parts):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CACHE_WRITERS.search(line):
                offenders.append(f"{path.relative_to(REPO).as_posix()}:{number}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)
