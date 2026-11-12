#!/usr/bin/env python3
"""Turn hand edits in the generated docs back into source files (#4080).

The system-check docs and the removed-name tables are generated from one file
per entry (``docs/checks/<ID>.md``, ``docs/removed/2.0/``; see
``scripts/system-check-docs.py`` and ``scripts/removed-names-docs.py``). A branch
cut before that change, or a hand edit inside a ``BEGIN GENERATED`` region,
puts entries straight into the docs, and ``check`` then fails. This script
reads such a doc and writes what it finds into the source files::

    python scripts/import-generated-docs.py      # import, then:
    make generated-docs                          # regenerate, and review the diff

It accepts a doc in either form:

- **marked** (it has ``BEGIN GENERATED`` regions): each ``###`` section, table
  row and removed-name row inside a region is compared with the sources. A new
  one creates a source file; a changed one overwrites that entry's part or
  cells.
- **hand-written** (no markers, as before #4080, e.g. after taking an old
  branch's copy of the doc in a merge): the per-family sections, the
  quick-reference table, the check count, the three removed-name tables and the
  "Removed in 2.0.0" sections are found by their headings, imported, and
  replaced by markers.

It never deletes a source file: an entry that disappeared from a doc is
reported, and removing (or retiring) it is a decision for a person. A new
quick-reference row's severity and summary come from the doc; a new check in a
table family with no row gets its summary from its section heading and its
severity from the levels the code emits it at, and is reported so you can
check them.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import generated_docs as gd  # noqa: E402


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


checks = _load("system_check_docs", "system-check-docs.py")
removed = _load("removed_names_docs", "removed-names-docs.py")

HOW_CHECKS = "do not edit by hand; edit docs/checks/<ID>.md and run make generated-docs"
HOW_REMOVED = "do not edit by hand; edit docs/removed/2.0/%s/ and run make generated-docs"

#: A per-family ``## `` heading in either check doc.
FAMILY_HEADING_RE = re.compile(
    r"^## .*\((?:(?P<l>[CVSTQYBADUI])(?:0xx)?|djust\.(?P<ns>audio)\.\*|(?P<ns2>djust_theming)\.\*)\)\s*$"
)
#: A per-check ``###`` heading; a range (``T019–T022``) belongs to its first ID.
SECTION_HEADING_RE = re.compile(
    r"^### (?:djust\.)?(?P<id>(?:audio\.)?[A-Z]\d{3}|djust_theming\.[A-Z]\d{3})(?:[–-][A-Z]?\d{3})?(?: — |: )"
)
TABLE_HEADER = "| ID | Category | Severity | Summary |"
COUNT_PREFIX = "Quick reference for all "
REMOVED_TABLE_HEADINGS = {
    "### Python names": "python",
    "### Settings": "settings",
    "### Templates and client code": "templates",
}
REMOVED_SECTIONS_HEADING = "## Removed in 2.0.0"
LEVELS = {"Critical": 0, "Error": 1, "Warning": 2, "Info": 3, "Debug": 4}
LEVEL_CLASSES = {"DjustError": "Error", "DjustWarning": "Warning", "DjustInfo": "Info"}


def _fenced(lines: list[str]) -> set[int]:
    inside: set[int] = set()
    open_ = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            open_ = not open_
            inside.add(i)
        elif open_:
            inside.add(i)
    return inside


def _cells(row: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)[1:-1]]


def _split_sections(lines: list[str], fence: set[int], first: int, end: int, where: str) -> list[tuple[str, str]]:
    """``(id, text)`` for each ``###`` section in ``lines[first:end + 1]``."""
    starts = [j for j in range(first, end + 1) if lines[j].startswith("### ") and j not in fence]
    out = []
    for n, s in enumerate(starts):
        m = SECTION_HEADING_RE.match(lines[s])
        if not m:
            raise SystemExit("%s:%d: a section in a check region is not headed by an ID: %s" % (where, s + 1, lines[s]))
        e = starts[n + 1] - 1 if n + 1 < len(starts) else end
        out.append((m.group("id"), "\n".join(lines[s : e + 1]).strip("\n")))
    return out


def _marker(name: str, how: str) -> list[str]:
    return ["<!-- BEGIN GENERATED %s: %s -->" % (name, how), "", "<!-- END GENERATED %s -->" % name]


def _region_bounds(lines: list[str]) -> dict[str, tuple[int, int]]:
    text = "\n".join(lines)
    return {name: (b, e) for name, b, e in gd._split(text)[1]}


def _code_levels(root: Path) -> dict[str, list[str]]:
    found: dict[str, set[str]] = {}
    code = root / "python" / "djust"
    for path in code.rglob("*.py"):
        if "tests" in path.relative_to(code).parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            fn = LEVEL_CLASSES.get(fn, fn)
            if fn not in LEVELS:
                continue
            for kw in node.keywords:
                if kw.arg == "id" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    v = kw.value.value
                    found.setdefault(v[6:] if v.startswith("djust.") else v, set()).add(fn)
    return {k: sorted(v, key=LEVELS.__getitem__) for k, v in found.items()}


# --- system checks ------------------------------------------------------------------


def import_checks(root: Path, report: list[str]) -> None:
    checks_dir = root / "docs" / "checks"
    checks_dir.mkdir(parents=True, exist_ok=True)
    entries = {e.stem: e for e in gd.read_entries(checks_dir)}
    found_parts: dict[tuple[str, str], str] = {}
    rows: dict[str, tuple[str, str]] = {}

    for doc, part in checks.DOCS.items():
        path = root / doc
        lines = path.read_text(encoding="utf-8").split("\n")
        fence = _fenced(lines)
        regions = _region_bounds(lines)
        if regions:
            for name, (b, e) in regions.items():
                if name.startswith("check-sections:"):
                    for sid, text in _split_sections(lines, fence, b + 1, e - 1, doc):
                        found_parts[(sid, part)] = text
                elif name == "check-table":
                    for row in lines[b + 1 : e]:
                        if row.startswith("| ") and not row.startswith(("| ID ", "|----")):
                            c = _cells(row)
                            rows[c[0]] = (c[2], c[3])
            continue
        # Hand-written form: find the regions by their headings and mark them.
        heads = [i for i, ln in enumerate(lines) if ln.startswith(("# ", "## ")) and i not in fence]
        replace: list[tuple[int, int, list[str]]] = []
        for k in heads:
            m = FAMILY_HEADING_RE.match(lines[k])
            if not m:
                continue
            fam = m.group("l") or m.group("ns") or m.group("ns2")
            nxt = min([h for h in heads if h > k] + [len(lines)])
            first = next(
                (j for j in range(k + 1, nxt) if j not in fence and SECTION_HEADING_RE.match(lines[j])), None
            )
            if first is None:
                continue
            end = nxt - 1
            while lines[end].strip() in ("", "---"):
                end -= 1
            for sid, text in _split_sections(lines, fence, first, end, doc):
                if (sid, part) in found_parts:
                    raise SystemExit("%s: two sections for %s" % (doc, sid))
                found_parts[(sid, part)] = text
            replace.append((first, end, _marker("check-sections:%s" % fam, HOW_CHECKS)))
        if TABLE_HEADER in lines:
            t0 = lines.index(TABLE_HEADER)
            t1 = t0 + 2
            while t1 < len(lines) and lines[t1].startswith("|"):
                c = _cells(lines[t1])
                rows[c[0]] = (c[2], c[3])
                t1 += 1
            replace.append((t0, t1 - 1, _marker("check-table", HOW_CHECKS)))
        for i, ln in enumerate(lines):
            if ln.startswith(COUNT_PREFIX):
                replace.append((i, i, _marker("check-count", HOW_CHECKS)))
                break
        for first, end, new in sorted(replace, reverse=True):
            lines[first : end + 1] = new
        path.write_text("\n".join(lines), encoding="utf-8")
        report.append("%s: marked %d generated region(s)" % (doc, len(replace)))

    levels: dict[str, list[str]] | None = None
    ids = sorted({sid for sid, _ in found_parts} | set(rows), key=checks.sort_key)
    for sid in ids:
        old = entries.get(sid)
        meta = dict(old.meta) if old else {"id": sid}
        parts = dict(old.parts) if old else {}
        changed = old is None
        for part in ("reference", "guide"):
            text = found_parts.get((sid, part))
            if text is not None and parts.get(part) != text:
                parts[part] = text
                changed = True
        if sid in rows:
            severity, summary = rows[sid]
            if (meta.get("severity"), meta.get("summary")) != (severity, summary):
                meta.update(severity=severity, summary=summary)
                changed = True
        elif checks.family(sid) in checks.TABLE_FAMILIES and not meta.get("summary") and meta.get("status") != "reserved":
            if levels is None:
                levels = _code_levels(root)
            heading = (parts.get("guide") or parts.get("reference") or "").split("\n", 1)[0]
            meta["summary"] = re.split(r" — |: ", heading, maxsplit=1)[-1]
            meta["severity"] = "/".join(levels.get(sid, [])) or "Warning"
            report.append("%s: no table row; summary %r and severity %r derived, check them" % (sid, meta["summary"], meta["severity"]))
            changed = True
        if meta.get("severity") == "Retired" and meta.get("status") != "retired":
            meta["status"] = "retired"
            changed = True
        if changed:
            ordered = {k: meta[k] for k in ("id", "status", "severity", "summary") if k in meta}
            ordered.update({k: v for k, v in meta.items() if k not in ordered})
            gd.write_entry(checks_dir / ("%s.md" % sid), ordered, {k: parts[k] for k in ("reference", "guide") if k in parts})
            report.append("docs/checks/%s.md: %s" % (sid, "created" if old is None else "updated"))
    gone = sorted(set(entries) - set(ids), key=checks.sort_key)
    for sid in gone:
        # Retired and reserved IDs with no doc text of their own are registry-only.
        if entries[sid].parts or entries[sid].meta.get("status", "live") == "live":
            report.append("docs/checks/%s.md: not found in either doc; left in place (delete or retire it by hand)" % sid)


# --- removed names ------------------------------------------------------------------


def _slug(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9_]+", re.sub(r"`|\(.*?\)", " ", text))
    return "-".join(w.lower().strip("_") for w in words[:4] if w.strip("_")) or "entry"


def _number_between(before: int | None, after: int | None) -> int:
    if before is None:
        return max((after or 11) - 10, 1)
    if after is None:
        return before + 10
    return (before + after) // 2 if after - before > 1 else before


def import_removed(root: Path, report: list[str]) -> None:
    mdir = root / removed.MIGRATION_DIR
    sdir = root / removed.STABILITY_DIR
    mdir.mkdir(parents=True, exist_ok=True)
    sdir.mkdir(parents=True, exist_ok=True)
    rows = gd.read_entries(mdir)
    sections = gd.read_entries(sdir)

    # The rows and sections in each doc, in doc order.
    doc_rows: list[tuple[str, str, str]] = []
    guide = root / removed.GUIDE
    lines = guide.read_text(encoding="utf-8").split("\n")
    regions = _region_bounds(lines)
    if regions:
        for table in removed.TABLES:
            b, e = regions["removed-table:%s" % table]
            for row in lines[b + 1 : e]:
                if row.startswith("| ") and not row.startswith(("| Removed ", "| --- ")):
                    c = _cells(row)
                    doc_rows.append((table, c[0], c[1]))
    else:
        for heading, table in REMOVED_TABLE_HEADINGS.items():
            h = lines.index(heading)
            t0 = next(i for i in range(h, len(lines)) if lines[i].startswith("| Removed | Use |"))
            t1 = t0 + 2
            while t1 < len(lines) and lines[t1].startswith("|"):
                c = _cells(lines[t1])
                doc_rows.append((table, c[0], c[1]))
                t1 += 1
            lines[t0:t1] = _marker("removed-table:%s" % table, HOW_REMOVED % "migration")
        guide.write_text("\n".join(lines), encoding="utf-8")
        report.append("%s: marked the three removed-name tables" % removed.GUIDE)

    stability = root / removed.STABILITY
    lines = stability.read_text(encoding="utf-8").split("\n")
    fence = _fenced(lines)
    regions = _region_bounds(lines)
    if regions:
        b, e = regions["removed-sections:2.0"]
        first, end = b + 1, e - 1
    else:
        h = lines.index(REMOVED_SECTIONS_HEADING)
        nxt = next(i for i in range(h + 1, len(lines)) if lines[i].startswith("## ") and i not in fence)
        first = next(i for i in range(h + 1, nxt) if lines[i].startswith("### ") and i not in fence)
        end = nxt - 1
        while lines[end].strip() in ("", "---"):
            end -= 1
    starts = [i for i in range(first, end + 1) if lines[i].startswith("### ") and i not in fence]
    doc_sections = [
        "\n".join(lines[s : (starts[n + 1] - 1 if n + 1 < len(starts) else end) + 1]).strip("\n")
        for n, s in enumerate(starts)
    ]
    if not regions:
        lines[first : end + 1] = _marker("removed-sections:2.0", HOW_REMOVED % "stability")
        stability.write_text("\n".join(lines), encoding="utf-8")
        report.append("%s: marked the Removed in 2.0.0 sections" % removed.STABILITY)

    # Rows: match by the "removed" cell, keep doc order for new ones.
    by_removed = {e.meta.get("removed"): e for e in rows}
    runtime = removed.runtime_keys(root / removed.RUNTIME)
    covered = {c for e in rows for c in removed._covers(e)}
    numbers = [int(e.stem.split("-", 1)[0]) for e in rows]
    prev: int | None = None
    seen: set[str] = set()
    for i, (table, rem, use) in enumerate(doc_rows):
        seen.add(rem)
        old = by_removed.get(rem)
        if old is not None:
            prev = int(old.stem.split("-", 1)[0])
            if (old.meta.get("table"), old.meta.get("use")) != (table, use):
                old.meta.update(table=table, use=use)
                gd.write_entry(old.path, old.meta, {})
                report.append("%s: updated" % old.path.relative_to(root))
            continue
        nxt = next(
            (int(by_removed[r].stem.split("-", 1)[0]) for _, r, _ in doc_rows[i + 1 :] if r in by_removed), None
        )
        num = _number_between(prev, nxt) if (prev is not None or nxt is not None) else (max(numbers, default=0) + 10)
        meta = {"table": table}
        cov = [
            k
            for k in runtime
            if k not in covered
            and re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(k.rsplit(".", 1)[-1]), rem)
        ]
        if cov:
            meta["covers"] = ", ".join(cov)
            covered.update(cov)
        meta.update(removed=rem, use=use)
        path = mdir / ("%03d-%s.md" % (num, _slug(rem)))
        gd.write_entry(path, meta, {})
        numbers.append(num)
        prev = num
        report.append("%s: created%s" % (path.relative_to(root), " (covers %s)" % meta["covers"] if cov else ""))
    for e in rows:
        if e.meta.get("removed") not in seen:
            report.append("%s: its row is not in the guide; left in place (delete it by hand)" % e.path.relative_to(root))

    # Sections: match by the heading line.
    by_heading = {e.parts.get("", "").split("\n", 1)[0]: e for e in sections}
    snumbers = [int(e.stem.split("-", 1)[0]) for e in sections]
    prev = None
    heads_seen: set[str] = set()
    for i, text in enumerate(doc_sections):
        heading = text.split("\n", 1)[0]
        heads_seen.add(heading)
        old = by_heading.get(heading)
        if old is not None:
            prev = int(old.stem.split("-", 1)[0])
            if old.parts.get("") != text:
                gd.write_entry(old.path, {}, {"": text})
                report.append("%s: updated" % old.path.relative_to(root))
            continue
        nxt = next(
            (int(by_heading[t.split("\n", 1)[0]].stem.split("-", 1)[0]) for t in doc_sections[i + 1 :] if t.split("\n", 1)[0] in by_heading),
            None,
        )
        num = _number_between(prev, nxt) if (prev is not None or nxt is not None) else (max(snumbers, default=0) + 10)
        path = sdir / ("%03d-%s.md" % (num, _slug(heading[4:])))
        gd.write_entry(path, {}, {"": text})
        snumbers.append(num)
        prev = num
        report.append("%s: created" % path.relative_to(root))
    for e in sections:
        if e.parts.get("", "").split("\n", 1)[0] not in heads_seen:
            report.append("%s: its section is not in %s; left in place" % (e.path.relative_to(root), removed.STABILITY))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    report: list[str] = []
    import_checks(args.root, report)
    import_removed(args.root, report)
    print("\n".join(report) or "nothing to import")
    print("now run: make generated-docs, then review the diff")
    return 0


if __name__ == "__main__":
    sys.exit(main())
