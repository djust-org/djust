#!/usr/bin/env python3
"""Refuse a committed `uv.lock` that records a non-public package index.

`uv lock` writes whichever index it resolved against into every entry's
``source = { registry = ... }``. A developer-local mirror or caching proxy set
in a GLOBAL config (``~/.config/uv/uv.toml``) therefore leaks machine-local
URLs into a file everyone else consumes, where they are meaningless and
unreproducible.

Nothing breaks loudly when it happens, which is why it needs a guard: `uv`
resolves from the per-wheel ``url`` fields (always ``files.pythonhosted.org``),
so installs keep working and CI stays green while the lockfile quietly records
a host only one machine can reach.

This has now leaked twice:

* introduced in ``e39a9242`` ("chore: clean working tree for release"),
  removed in ``231e966e`` (PR #2174);
* reintroduced in ``5fe74931`` (the 1.1.0 release commit) — where the lockfile
  *was* verified clean right after ``make version``, then rewritten by a later
  ``uv`` invocation in the same session whose shell no longer carried the
  ``UV_INDEX_URL`` override. Verification before the commit is not
  verification of the commit.

Fix when this fires:

    UV_INDEX_URL=https://pypi.org/simple uv lock

and re-stage. Set the variable on EVERY `uv` call in the session, not once —
`uv run`/`uv sync` can re-lock too.
"""

from __future__ import annotations

import pathlib
import re
import sys
from collections import Counter

# Public indexes a committed lockfile may legitimately name. Add a private
# company index here deliberately, never to silence a local proxy leak.
ALLOWED = {"https://pypi.org/simple"}

REGISTRY = re.compile(r'registry = "([^"]+)"')


def check(path: pathlib.Path) -> int:
    if not path.is_file():
        return 0
    found = Counter(REGISTRY.findall(path.read_text()))
    bad = {url: n for url, n in found.items() if url not in ALLOWED}
    if not bad:
        total = sum(found.values())
        print(f"OK — {path}: {total} registry entries, all public")
        return 0

    print(f"FAIL — {path} records a non-public package index:", file=sys.stderr)
    for url, n in sorted(bad.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}x  {url}", file=sys.stderr)
    print(
        "\nThis is almost always a developer-local mirror leaking from a global\n"
        "~/.config/uv/uv.toml. Those URLs are unreachable for everyone else, and\n"
        "nothing fails loudly because uv installs from the per-wheel `url` fields.\n"
        "\nRe-lock against the public index and re-stage:\n"
        "    UV_INDEX_URL=https://pypi.org/simple uv lock\n"
        "\nSet it on every uv call in the session — `uv run`/`uv sync` re-lock too.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str]) -> int:
    paths = [pathlib.Path(a) for a in argv[1:]] or [pathlib.Path("uv.lock")]
    return max((check(p) for p in paths), default=0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
