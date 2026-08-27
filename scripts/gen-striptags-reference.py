#!/usr/bin/env python3
"""Regenerate `python/tests/fixtures/striptags_reference_2273.json`.

Why this fixture exists
-----------------------
`striptags` is a port of `django.utils.html.strip_tags`, which is fifteen lines
of Django over CPython's `html.parser`. The Rust port is fixed; **the reference
is not**. `html/parser.py` was rewritten for HTML5-spec alignment in CPython
3.12.10, and 3.14 changed the end-of-input character-reference handling again,
so the three interpreters djust's CI matrix runs disagree with each other:

    3.12.9   vs 3.12.13 : 1108 / 4000 corpus values differ
    3.12.13  vs 3.13.7  :    0
    3.12.13  vs 3.14.6  :  224

A differential that computes its reference at run time therefore asserts a
DIFFERENT contract on every runner — it can pass locally and fail in CI for
reasons that have nothing to do with djust. That is exactly what happened to
the first version of `test_striptags_parity_2273.py`.

So the reference is captured here, once, across every interpreter in the
support matrix, and split:

* **stable**   — every captured interpreter produces the same answer. djust
                 must match it, on any runner. This is the real contract.
* **unstable** — the interpreters disagree, so there is no single answer for
                 djust to match. Recorded with EVERY version's answer, so the
                 disagreement is visible in the repo rather than discovered in
                 CI. djust must still behave like *one of* them.

Usage
-----
    python scripts/gen-striptags-reference.py \\
        /path/to/python3.12 /path/to/python3.13 /path/to/python3.14

Each interpreter needs only the standard library. The Django half of
`strip_tags` (the `MAX_STRIP_TAGS_DEPTH` guards and the re-strip loop) is
inlined below because most interpreters on a dev box will not have Django 5.2
installed; `test_pinned_reference_matches_this_interpreter` re-checks the
inlined copy against the REAL Django on the runner, so a Django upgrade that
changed `strip_tags` fails loudly instead of silently invalidating the pin.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "python" / "tests" / "fixtures" / "striptags_reference_2273.json"

# The corpus generator is imported from the test module so the two can never
# drift; see `test_striptags_parity_2273.build_corpus`.
sys.path.insert(0, str(ROOT / "python" / "tests"))

# --- the reference, run inside each interpreter ------------------------------
WORKER = r'''
import json, re, sys
from html.parser import HTMLParser

MAX_STRIP_TAGS_DEPTH = 50
long_open_tag_without_closing_re = re.compile(r"<[a-zA-Z][^>]{1000,}")

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
    for m in long_open_tag_without_closing_re.finditer(value):
        if m.group().count("<") >= MAX_STRIP_TAGS_DEPTH:
            raise Suspicious
    depth = 0
    while "<" in value and ">" in value:
        if depth >= MAX_STRIP_TAGS_DEPTH:
            raise Suspicious
        new_value = _strip_once(value)
        if value.count("<") == new_value.count("<"):
            break
        value = new_value
        depth += 1
    return value

def answer(v):
    try:
        return "OK:" + strip_tags(v)
    except Suspicious:
        return "RAISE:SuspiciousOperation"
    except Exception as e:
        return "RAISE:" + type(e).__name__

corpus = json.load(sys.stdin)
json.dump(
    {"version": ".".join(map(str, sys.version_info[:3])),
     "answers": [answer(v) for v in corpus]},
    sys.stdout,
)
'''


def main() -> int:
    interpreters = sys.argv[1:]
    if not interpreters:
        print(__doc__)
        return 2

    from test_striptags_parity_2273 import build_corpus  # noqa: PLC0415

    corpus = build_corpus()
    payload = json.dumps(corpus)

    captured: dict[str, list[str]] = {}
    for exe in interpreters:
        proc = subprocess.run(
            [exe, "-c", WORKER],
            input=payload,
            capture_output=True,
            text=True,
            check=True,
        )
        got = json.loads(proc.stdout)
        captured[got["version"]] = got["answers"]
        print(f"  captured {got['version']} ({exe})")

    if len(captured) < 2:
        print("ERROR: need at least two DISTINCT interpreter versions", file=sys.stderr)
        return 1

    versions = sorted(captured, key=lambda t: tuple(map(int, t.split("."))))
    stable: dict[str, str] = {}
    unstable: dict[str, dict[str, str]] = {}
    for idx, value in enumerate(corpus):
        answers = {v: captured[v][idx] for v in versions}
        if len(set(answers.values())) == 1:
            stable[value] = answers[versions[0]]
        else:
            unstable[value] = answers

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(
        json.dumps(
            {
                "_comment": (
                    "Generated by scripts/gen-striptags-reference.py. "
                    "'stable' is asserted on every runner; 'unstable' records "
                    "each CPython's answer where they disagree, so the moving "
                    "reference is visible in the repo. See #2273."
                ),
                "versions": versions,
                "stable": stable,
                "unstable": unstable,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )
    print(f"\n{FIXTURE.relative_to(ROOT)}")
    print(f"  versions : {', '.join(versions)}")
    print(f"  stable   : {len(stable)}")
    print(f"  unstable : {len(unstable)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
