#!/usr/bin/env python3
"""System-check docs and the check-ID registry, generated from ``docs/checks/<ID>.md`` (#4080).

Every system-check ID djust has ever assigned has one file in ``docs/checks/``,
named by the ID (``V027.md``, ``audio.E001.md``, ``djust_theming.W001.md``):

    ---
    id: V027
    severity: Warning
    summary: The quick-reference table cell (required for C, V, S, T, Q, Y, B)
    status: live            # or "retired" (never emitted again, never reused)
    ---                     #    or "reserved" (held for a check not yet shipped)

    <!-- doc: reference -->
    ### V027 — the section in docs/system-checks.md

    <!-- doc: guide -->
    ### V027: the section in docs/website/guides/error-codes.md

That directory is the registry: a new check adds one new file and touches
nothing shared, and two branches that both take ``V028`` both add
``docs/checks/V028.md`` and conflict on it (add/add). The registry test
(``python/djust/tests/test_check_id_registry_4080.py``) fails when code emits an
ID with no file, or emits a retired or reserved one.

Subcommands::

    python scripts/system-check-docs.py check     # committed docs match the sources
    python scripts/system-check-docs.py write     # regenerate the docs
    python scripts/system-check-docs.py next-id V # print the next free ID (V028)

``next-id`` takes a family letter (``V``, ``C``: the 0xx range) or a longer
prefix (``C3``, ``audio.W``). Retired and reserved IDs count as taken.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import generated_docs as gd  # noqa: E402

CHECKS_DIR = REPO_ROOT / "docs" / "checks"
CODE_DIR = REPO_ROOT / "python" / "djust"
HOW = "make generated-docs  (or: python scripts/system-check-docs.py write)"

#: Each doc, and which part of an entry fills its per-family section regions.
DOCS: dict[str, str] = {
    "docs/system-checks.md": "reference",
    "docs/website/guides/error-codes.md": "guide",
}

#: Families in the quick-reference table, in table order, with their category cell.
TABLE_FAMILIES: dict[str, str] = {
    "C": "Config",
    "V": "LiveView",
    "S": "Security",
    "T": "Templates",
    "Q": "Quality",
    "Y": "Accessibility",
    "B": "Vendored Assets",
}

STATUSES = ("live", "retired", "reserved")
ALLOWED_KEYS = {"id", "severity", "summary", "status"}

STEM_RE = re.compile(r"^(?:(?P<ns>audio|djust_theming)\.)?(?P<letter>[A-Z])(?P<num>\d{3})$")

#: String literals that are a system-check ID as code emits or suppresses it.
_CODE_ID_RE = re.compile(r"^(?:djust\.(?P<a>(?:audio\.)?[A-Z]\d{3})|(?P<b>djust_theming\.[A-Z]\d{3}))$")


def family(stem: str) -> str:
    m = STEM_RE.match(stem)
    if not m:
        raise gd.GeneratedDocError("%s.md: not a check ID file name" % stem)
    return m.group("ns") or m.group("letter")


def sort_key(stem: str) -> tuple[str, str, int]:
    m = STEM_RE.match(stem)
    assert m is not None
    return (m.group("ns") or "", m.group("letter"), int(m.group("num")))


def code_id(stem: str) -> str:
    """The ID as code passes it to ``checks.Warning(id=...)``."""
    return stem if stem.startswith("djust_theming.") else "djust." + stem


def load(checks_dir: Path = CHECKS_DIR) -> list[gd.Entry]:
    entries = gd.read_entries(checks_dir)
    for e in entries:
        family(e.stem)  # validates the name
    return sorted(entries, key=lambda e: sort_key(e.stem))


def validate(entries: list[gd.Entry]) -> list[str]:
    """Problems with the entry files themselves."""
    problems: list[str] = []
    for e in entries:
        where = str(e.path.relative_to(REPO_ROOT)) if e.path.is_relative_to(REPO_ROOT) else str(e.path)
        if e.meta.get("id") != e.stem:
            problems.append("%s: front matter 'id: %s' does not match the file name" % (where, e.meta.get("id")))
        status = e.meta.get("status", "live")
        if status not in STATUSES:
            problems.append("%s: status %r is not one of %s" % (where, status, ", ".join(STATUSES)))
        extra = sorted(set(e.meta) - ALLOWED_KEYS)
        if extra:
            problems.append("%s: unknown front matter key(s): %s" % (where, ", ".join(extra)))
        if family(e.stem) in TABLE_FAMILIES and status != "reserved":
            for key in ("severity", "summary"):
                if not e.meta.get(key):
                    problems.append(
                        "%s: needs a %r line (it fills the quick-reference table in "
                        "docs/system-checks.md)" % (where, key)
                    )
        unknown_parts = sorted(set(e.parts) - {"reference", "guide"})
        if unknown_parts:
            problems.append(
                "%s: unknown part(s) %s; use <!-- doc: reference --> or <!-- doc: guide -->"
                % (where, ", ".join(repr(p) for p in unknown_parts))
            )
    return problems


def _table(entries: list[gd.Entry]) -> str:
    rows = ["| ID | Category | Severity | Summary |", "|----|----------|----------|---------|"]
    for fam, category in TABLE_FAMILIES.items():
        for e in entries:
            if family(e.stem) == fam and e.meta.get("status", "live") != "reserved":
                rows.append("| %s | %s | %s | %s |" % (e.stem, category, e.meta["severity"], e.meta["summary"]))
    return "\n".join(rows)


def _count(entries: list[gd.Entry]) -> str:
    live = sum(
        1
        for e in entries
        if family(e.stem) in TABLE_FAMILIES and e.meta.get("status", "live") == "live"
    )
    return (
        "Quick reference for all %d djust system checks — IDs, severities, suppression "
        "patterns, and known false positive conditions." % live
    )


def contents_for(doc: str, text: str, entries: list[gd.Entry]) -> dict[str, str]:
    part = DOCS[doc]
    out: dict[str, str] = {}
    for name in gd.region_names(text):
        if name == "check-table":
            out[name] = _table(entries)
        elif name == "check-count":
            out[name] = _count(entries)
        elif name.startswith("check-sections:"):
            fam = name.split(":", 1)[1]
            out[name] = "\n\n".join(
                e.parts[part] for e in entries if family(e.stem) == fam and part in e.parts
            )
    return out


def _run(mode: str, root: Path = REPO_ROOT, checks_dir: Path | None = None) -> int:
    entries = load(checks_dir or root / "docs" / "checks")
    problems = validate(entries)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    changed = False
    for doc in DOCS:
        path = root / doc
        contents = contents_for(doc, path.read_text(encoding="utf-8"), entries)
        if mode == "write":
            if gd.write(path, contents):
                print("regenerated %s" % doc)
                changed = True
        else:
            problems.extend(gd.check(path, contents, HOW))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    if mode == "write" and not changed:
        print("system-check docs are current")
    return 0


def next_id(prefix: str, entries: list[gd.Entry]) -> str:
    if re.fullmatch(r"(?:(?:audio|djust_theming)\.)?[A-Z]", prefix):
        prefix += "0"  # a bare family means its 0xx range
    m = re.fullmatch(r"((?:(?:audio|djust_theming)\.)?[A-Z])(\d{1,2})", prefix)
    if not m:
        raise SystemExit("next-id: %r is not a family (V, C3, audio.W, djust_theming.W)" % prefix)
    head, digits = m.groups()
    taken = [int(e.stem[len(head) :]) for e in entries if e.stem.startswith(prefix)]
    nxt = max(taken) + 1 if taken else max(int(digits.ljust(3, "0")), 1)
    candidate = head + str(nxt).zfill(3)
    if not candidate.startswith(prefix):
        raise SystemExit("next-id: the %s range is full" % prefix)
    return candidate


def emitted_ids(code_dir: Path = CODE_DIR) -> dict[str, list[str]]:
    """Every check ID a string literal in djust's code names, with where (``path:line``).

    Test modules are skipped. Docstrings never match: the literal must be the ID
    and nothing else (``"djust.V027"``).
    """
    found: dict[str, list[str]] = {}
    for path in sorted(code_dir.rglob("*.py")):
        rel = path.relative_to(code_dir)
        if "tests" in rel.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                m = _CODE_ID_RE.match(node.value)
                if m:
                    stem = m.group("a") or m.group("b")
                    found.setdefault(stem, []).append("python/djust/%s:%d" % (rel, node.lineno))
    return found


def registry_problems(entries: list[gd.Entry], emitted: dict[str, list[str]]) -> list[str]:
    """IDs the code and the ``docs/checks/`` registry disagree about."""
    status = {e.stem: e.meta.get("status", "live") for e in entries}
    problems: list[str] = []
    for stem, where in sorted(emitted.items(), key=lambda kv: sort_key(kv[0])):
        if stem not in status:
            problems.append(
                "%s (used at %s) has no registry file. Add docs/checks/%s.md with its "
                "severity, a one-line summary and its doc sections (copy a neighbour; see "
                "docs/checks/README.md). If the ID is new, take it from "
                "`make new-check-id FAMILY=%s` so two branches cannot both take it."
                % (code_id(stem), ", ".join(where), stem, family(stem))
            )
        elif status[stem] != "live":
            problems.append(
                "%s (used at %s) is %s in docs/checks/%s.md: a retired or reserved ID is "
                "never reused. Take a new one from `make new-check-id FAMILY=%s`."
                % (code_id(stem), ", ".join(where), status[stem], stem, family(stem))
            )
    for stem, st in status.items():
        if st == "live" and stem not in emitted:
            problems.append(
                "docs/checks/%s.md says %s is live, but no code emits it. If the check was "
                "removed, set 'status: retired' and say so in its summary." % (stem, code_id(stem))
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("write")
    p = sub.add_parser("next-id")
    p.add_argument("family")
    args = parser.parse_args(argv)
    if args.cmd == "next-id":
        print(next_id(args.family, load(args.root / "docs" / "checks")))
        return 0
    return _run(args.cmd, args.root)


if __name__ == "__main__":
    sys.exit(main())
