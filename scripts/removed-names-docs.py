#!/usr/bin/env python3
"""The "removed in 2.0" tables and sections, generated from one file per removal (#4080).

Every removal used to append a row to a table in
``docs/website/guides/migrating-to-2.0.md`` and a section to "Removed in 2.0.0"
in ``docs/API_STABILITY.md``, so two unrelated removals conflicted. Now each is
one new file:

``docs/removed/2.0/migration/<NNN>-<slug>.md`` — one row of a migration-guide table::

    ---
    table: python            # python | settings | templates
    covers: djust.PersistentLiveView, djust.live_view.PersistentLiveView
    removed: `PersistentLiveView` (`djust.PersistentLiveView`, ...)
    use: `LiveView`, with the fields to keep declared as above. ...
    ---

``docs/removed/2.0/stability/<NNN>-<slug>.md`` — one ``###`` section of
"Removed in 2.0.0" in ``docs/API_STABILITY.md`` (the whole file is the section).

Rows and sections render in file-name order; ``NNN`` only orders them, so two
branches may pick the same number (the slug breaks the tie).

``covers`` lists the keys of the ``REMOVED_*`` tables in
``python/djust/_removed_settings.py`` (the runtime refusals) the row documents;
names in ``REMOVED_NAMES`` are written ``module.name``. A key no row covers
fails ``check``, so a refusal added in code cannot ship without its doc row.

Subcommands::

    python scripts/removed-names-docs.py check
    python scripts/removed-names-docs.py write
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import generated_docs as gd  # noqa: E402

HOW = "make generated-docs  (or: python scripts/removed-names-docs.py write)"
GUIDE = "docs/website/guides/migrating-to-2.0.md"
STABILITY = "docs/API_STABILITY.md"
MIGRATION_DIR = "docs/removed/2.0/migration"
STABILITY_DIR = "docs/removed/2.0/stability"
RUNTIME = "python/djust/_removed_settings.py"
TABLES = ("python", "settings", "templates")
ROW_KEYS = {"table", "covers", "removed", "use"}


def runtime_keys(path: Path) -> dict[str, str]:
    """Each key of a ``REMOVED_*`` dict in ``_removed_settings.py`` -> the dict it is in.

    Read with ``ast`` so the check runs without importing djust.
    """
    keys: dict[str, str] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id.startswith("REMOVED_")
            and isinstance(node.value, ast.Dict)
        ):
            continue
        for key in node.value.keys:
            if isinstance(key, ast.Tuple):
                name = ".".join(str(ast.literal_eval(e)) for e in key.elts)
            elif key is not None:
                name = str(ast.literal_eval(key))
            else:
                continue
            keys[name] = node.target.id
    return keys


def _covers(entry: gd.Entry) -> list[str]:
    return [c.strip() for c in entry.meta.get("covers", "").split(",") if c.strip()]


def validate(rows: list[gd.Entry], sections: list[gd.Entry], runtime: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for e in rows:
        if e.meta.get("table") not in TABLES:
            problems.append("%s: 'table:' must be one of %s" % (e.path, ", ".join(TABLES)))
        for key in ("removed", "use"):
            if not e.meta.get(key):
                problems.append("%s: needs a %r line (its table cell)" % (e.path, key))
        extra = sorted(set(e.meta) - ROW_KEYS)
        if extra:
            problems.append("%s: unknown front matter key(s): %s" % (e.path, ", ".join(extra)))
        for c in _covers(e):
            if c not in runtime:
                problems.append(
                    "%s: covers %r, which is not a key of any REMOVED_* table in %s"
                    % (e.path, c, RUNTIME)
                )
        if e.parts:
            problems.append("%s: a migration row has only front matter, no body" % e.path)
    for e in sections:
        body = e.parts.get("", "")
        if e.meta or set(e.parts) != {""} or not body.startswith("### "):
            problems.append("%s: a stability section is one '### ' section and nothing else" % e.path)
    covered = {c for e in rows for c in _covers(e)}
    for key, table in sorted(runtime.items()):
        if key not in covered:
            problems.append(
                "%s[%r] (%s) has no row in the migration guide. Add a file under %s/ "
                "with 'covers: %s' (see the docstring of scripts/removed-names-docs.py), "
                "then run: %s" % (table, key, RUNTIME, MIGRATION_DIR, key, HOW)
            )
    return problems


def contents(rows: list[gd.Entry], sections: list[gd.Entry]) -> dict[str, dict[str, str]]:
    tables: dict[str, str] = {}
    for table in TABLES:
        lines = ["| Removed | Use |", "| --- | --- |"]
        lines += ["| %s | %s |" % (e.meta["removed"], e.meta["use"]) for e in rows if e.meta.get("table") == table]
        tables["removed-table:%s" % table] = "\n".join(lines)
    return {
        GUIDE: tables,
        STABILITY: {"removed-sections:2.0": "\n\n".join(e.parts[""] for e in sections)},
    }


def run(mode: str, root: Path = REPO_ROOT) -> int:
    rows = gd.read_entries(root / MIGRATION_DIR)
    sections = gd.read_entries(root / STABILITY_DIR)
    problems = validate(rows, sections, runtime_keys(root / RUNTIME))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    changed = False
    for doc, regions in contents(rows, sections).items():
        path = root / doc
        if mode == "write":
            if gd.write(path, regions):
                print("regenerated %s" % doc)
                changed = True
        else:
            problems.extend(gd.check(path, regions, HOW))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    if mode == "write" and not changed:
        print("removed-name docs are current")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cmd", choices=("check", "write"))
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return run(args.cmd, args.root)


if __name__ == "__main__":
    sys.exit(main())
