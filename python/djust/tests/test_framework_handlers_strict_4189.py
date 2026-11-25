"""Every handler the framework ships or shows compiles under the strict policy.

Strict (the default since #4189) refuses to compile a handler whose named
parameter has no annotation. The framework's own component handlers, its
scaffolds and the examples in its docstrings are what users copy, so each
``@event_handler`` in ``python/djust`` (code or docstring) must annotate every
named parameter (``**kwargs`` and ``*args`` are exempt).
"""

import re
from pathlib import Path

import djust

ROOT = Path(djust.__file__).parent
HANDLER = re.compile(
    r"@event_handler[^\n]*\n(?:\s*@[^\n]*\n)*\s*(?:async\s+)?def\s+(\w+)\(([^)]*)\)", re.M
)


def _unannotated(params: str) -> list:
    bad = []
    for part in re.split(r",\s*(?![^\[(]*[\])])", params):
        part = part.strip()
        if not part or part in ("self", "cls", "*", "/") or part.startswith("*"):
            continue
        if part.startswith("%(") or "%s" in part:  # a scaffold's formatted slot
            continue
        if ":" not in part.split("=")[0]:
            bad.append(part)
    return bad


def test_every_framework_handler_annotates_its_named_parameters():
    offenders = []
    scanned = 0
    for path in sorted(ROOT.rglob("*.py")):
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        for match in HANDLER.finditer(path.read_text(encoding="utf-8")):
            scanned += 1
            bad = _unannotated(match.group(2))
            if bad:
                offenders.append((str(path.relative_to(ROOT)), match.group(1), bad))
    assert scanned > 50, scanned  # the scan itself found the handlers
    assert offenders == [], offenders
