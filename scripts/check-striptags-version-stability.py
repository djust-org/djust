#!/usr/bin/env python3
"""Every `striptags` expectation written as a literal must be version-stable.

Why
---
`striptags` is a port of `django.utils.html.strip_tags`, which runs CPython's
`html.parser`. That module was rewritten for HTML5-spec alignment in **3.12.10**
and changed again in **3.14**, so the interpreters this project supports do not
agree with each other on adversarial input. An expectation written as a literal
is a claim about *every* runner, and a claim that happens to hold on the
developer's interpreter is silent about the rest: it passes locally and fails in
CI, which is exactly how #2273 first shipped red.

This script re-runs every literal expectation — the `strip_tags(...)` /
`strip_once(...)` calls in `crates/djust_templates/src/htmlparser.rs`'s test
module, and the live-compared values in
`python/tests/test_striptags_parity_2273.py` — through each interpreter given on
the command line, and reports any whose answer is not the same everywhere.

Two tiers, because the two suites have different audiences:

* **Rust pins** must hold on every interpreter in the CI matrix.
* **Python live-compares** must additionally hold on whatever the contributor's
  local `.venv` is, since they are compared against the running Django.

Usage
-----
    python scripts/check-striptags-version-stability.py \\
        .venv/bin/python /usr/bin/python3.13 /usr/bin/python3.14

Pass the local `.venv` interpreter FIRST; every later one is treated as part of
the supported matrix.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUST = ROOT / "crates" / "djust_templates" / "src" / "htmlparser.rs"
PYTEST = ROOT / "python" / "tests" / "test_striptags_parity_2273.py"

# Django 5.2's `strip_tags`, run against the host interpreter's `html.parser`.
# Kept in sync with scripts/gen-striptags-reference.py.
WORKER = r'''
import json, re, sys
from html.parser import HTMLParser

MAX_STRIP_TAGS_DEPTH = 50
_long = re.compile(r"<[a-zA-Z][^>]{1000,}")

class MLStripper(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.reset()
        self.fed = []
    def handle_data(self, d): self.fed.append(d)
    def handle_entityref(self, name): self.fed.append("&%s;" % name)
    def handle_charref(self, name): self.fed.append("&#%s;" % name)
    def get_data(self): return "".join(self.fed)

def _strip_once(value):
    s = MLStripper(); s.feed(value); s.close(); return s.get_data()

class Suspicious(Exception): pass

def strip_tags(value):
    value = str(value)
    for m in _long.finditer(value):
        if m.group().count("<") >= MAX_STRIP_TAGS_DEPTH:
            raise Suspicious
    depth = 0
    while "<" in value and ">" in value:
        if depth >= MAX_STRIP_TAGS_DEPTH:
            raise Suspicious
        new = _strip_once(value)
        if value.count("<") == new.count("<"):
            break
        value = new
        depth += 1
    return value

def answer(v):
    try:
        return "OK:" + strip_tags(v)
    except Suspicious:
        return "RAISE:SuspiciousOperation"
    except Exception as e:
        return "RAISE:" + type(e).__name__

vals = json.load(sys.stdin)
json.dump({"v": ".".join(map(str, sys.version_info[:3])),
           "a": [answer(x) for x in vals]}, sys.stdout)
'''


def _unescape(literal: str) -> str:
    return literal.encode().decode("unicode_escape")


def rust_literals() -> list[str]:
    src = RUST.read_text()
    tests = src[src.index("mod tests {") :]
    out: list[str] = []
    for fn in ("strip_tags", "strip_once"):
        for m in re.finditer(rf'{fn}\("((?:[^"\\]|\\.)*)"\)', tests):
            out.append(_unescape(m.group(1)))
    return out


def python_literals() -> list[str]:
    """Import the test module's own `LIVE_COMPARED_VALUES`.

    Reading the constant rather than re-parsing the file means the two can
    never disagree about what is live-compared.
    """
    src = PYTEST.read_text()
    ns: dict[str, object] = {}
    # Execute only the constant block, which is plain literals.
    start = src.index("REPORTED_CELLS = [")
    end = src.index("# ---", start)
    exec(compile(src[start:end], str(PYTEST), "exec"), ns)  # noqa: S102
    return list(ns["LIVE_COMPARED_VALUES"])  # type: ignore[arg-type]


def capture(exe: str, values: list[str]) -> tuple[str, list[str]]:
    proc = subprocess.run(
        [exe, "-c", WORKER],
        input=json.dumps(values),
        capture_output=True,
        text=True,
        check=True,
    )
    got = json.loads(proc.stdout)
    return got["v"], got["a"]


def main() -> int:
    interpreters = sys.argv[1:]
    if len(interpreters) < 2:
        print(__doc__)
        return 2

    rust = rust_literals()
    pyvals = python_literals()
    values = sorted(set(rust) | set(pyvals))
    print(f"{len(values)} distinct literal expectations "
          f"({len(set(rust))} Rust, {len(set(pyvals))} Python)\n")

    answers: dict[str, list[str]] = {}
    order: list[str] = []
    for exe in interpreters:
        ver, ans = capture(exe, values)
        if ver not in answers:
            order.append(ver)
        answers[ver] = ans
        print(f"  captured {ver} ({exe})")
    print()

    local, matrix = order[0], order[1:]
    if not matrix:
        print("ERROR: need at least one interpreter beyond the local one")
        return 2

    idx = {v: i for i, v in enumerate(values)}
    rust_bad, py_bad = [], []
    for value in values:
        i = idx[value]
        across_matrix = {answers[v][i] for v in matrix}
        across_all = across_matrix | {answers[local][i]}
        if len(across_matrix) > 1 and value in rust:
            rust_bad.append(value)
        elif len(across_all) > 1 and value in pyvals:
            py_bad.append(value)

    ok = True
    if rust_bad:
        ok = False
        print(f"FAIL: {len(rust_bad)} Rust pin(s) differ across the matrix "
              f"{matrix} — move them to the fixture:")
        for v in rust_bad:
            print(f"  {v!r}")
            for ver in order:
                print(f"      {ver:9} {answers[ver][idx[v]]!r}")
    if py_bad:
        ok = False
        print(f"FAIL: {len(py_bad)} live-compared value(s) differ across "
              f"{order} — they cannot be compared against the running Django:")
        for v in py_bad:
            print(f"  {v!r}")
            for ver in order:
                print(f"      {ver:9} {answers[ver][idx[v]]!r}")
    if ok:
        print(f"OK: every literal expectation is stable across {order}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
