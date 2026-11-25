"""Pin the 2.0 upgrade guide (#4208): every snippet is run, every table names its check.

``tests/test_doc_snippets_execute.py`` runs each guide block that defines a view or
component. This file covers the rest: a block that defines neither (a settings
block, a test function) must run as written, the capability tables must say
which check reports each row and whether ``--fix`` rewrites it, and every link
into the guide must land on a heading.
"""

from __future__ import annotations

import pathlib
import re
import textwrap

import pytest

from tests.test_doc_snippets_execute import (
    KNOWN_FAILURES,
    Block,
    _blocks_in,
    _defines_live_class,
    _load_extractor,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
GUIDE = REPO_ROOT / "docs/website/guides/migrating-to-2.0.md"
TEXT = GUIDE.read_text(encoding="utf-8")

CHECK_IDS = {
    "V034",
    "V035",
    "V037",
    "T029",
    "T027",
    "V016",
    "T020",
    "V031",
}
FIX_STATUS = ("Yes", "Partly", "No")


def _guide_blocks():
    return _load_extractor()(GUIDE)


def test_every_python_block_is_executed_or_marked_before():
    """Gate-off: put a ``skip`` marker before any block and this fails."""
    assert "<!-- doc-snippet-check: skip" not in TEXT
    executed = {block.key for block in _blocks_in([GUIDE])}
    seen = 0
    for lineno, code, marker in _guide_blocks():
        if marker in ("before", "anti-pattern"):
            continue
        seen += 1
        if _defines_live_class(code):
            key = Block(GUIDE, lineno, code).key
            assert key in executed, f"L{lineno}: not collected by the execution test"
            assert key not in KNOWN_FAILURES, f"L{lineno}: listed as a known failure"
            continue
        namespace: dict = {"__name__": f"guide_L{lineno}"}
        exec(compile(textwrap.dedent(code), f"guide:L{lineno}", "exec"), namespace)  # noqa: S102
        config = namespace.get("LIVEVIEW_CONFIG")
        if config is not None:
            from djust.config import LiveViewConfig

            unknown = set(config) - set(LiveViewConfig._defaults)
            assert not unknown, f"L{lineno}: LIVEVIEW_CONFIG keys djust does not know: {unknown}"
    assert seen > 10


def _tables():
    """Each markdown table whose header is ``| 1.3 | 2.0 | Reported by | --fix |``."""
    lines = TEXT.splitlines()
    for index, line in enumerate(lines):
        cells = [cell.strip().replace("`", "") for cell in line.strip().strip("|").split("|")]
        if cells == ["1.3", "2.0", "Reported by", "--fix"]:
            rows = []
            for row in lines[index + 2 :]:
                if not row.startswith("|"):
                    break
                rows.append(re.split(r"(?<!\\)\|", row.strip().strip("|")))
            yield rows


def test_capability_tables_name_a_check_and_a_fix_status():
    """Gate-off: change one row's ``--fix`` cell to ``Maybe``."""
    tables = list(_tables())
    assert len(tables) >= 3
    for rows in tables:
        assert rows
        for cells in rows:
            assert len(cells) == 4, cells
            fix = cells[3].strip().replace("`", "")
            assert fix.startswith(FIX_STATUS), f"unknown --fix status: {fix!r}"
            reported_by = cells[2].strip()
            assert reported_by and reported_by != "—", f"no reporter named: {cells}"
            for check_id in re.findall(r"djust\.([A-Z]\d{3})", reported_by):
                doc = REPO_ROOT / "docs/checks" / f"{check_id}.md"
                assert doc.exists(), check_id
                # A retired check is never reported, so it cannot be how a row is found.
                assert "status: retired" not in doc.read_text(encoding="utf-8"), check_id
    mentioned = set(re.findall(r"djust\.([A-Z]\d{3})", TEXT))
    assert CHECK_IDS <= mentioned, CHECK_IDS - mentioned


def _slugs() -> set[str]:
    slugs = set()
    for line in TEXT.splitlines():
        match = re.match(r"#{1,6}\s+(.*)", line)
        if match:
            slug = re.sub(r"[^a-z0-9 _-]", "", match.group(1).strip().lower().replace("`", ""))
            slugs.add(slug.replace(" ", "-"))
    return slugs


def _external_files():
    for pattern in ("docs/**/*.md", "python/djust/**/*.py", "CHANGELOG.md"):
        for path in REPO_ROOT.glob(pattern):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative.startswith(("docs/plans/", "docs/archive/")) or path == GUIDE:
                continue
            yield path


def test_links_into_the_guide_resolve():
    """Gate-off: rename ``### Python names``."""
    slugs = _slugs()
    anchors: list[tuple[str, str]] = [
        (GUIDE.name, a) for a in re.findall(r"\]\(#([a-z0-9_-]+)\)", TEXT)
    ]
    pattern = re.compile(r"migrating-to-2\.0(?:\.md|/)?#([a-z0-9_-]+)")
    for path in _external_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        anchors += [(str(path.relative_to(REPO_ROOT)), a) for a in pattern.findall(text)]
    assert len(anchors) > 20
    missing = sorted({(where, anchor) for where, anchor in anchors if anchor not in slugs})
    assert not missing, missing


@pytest.mark.parametrize(
    "anchor",
    [
        "1-move-each-view-to-explicit-exposure",
        "2-make-event-handlers-strict",
        "3-replace-removed-names-and-settings",
        "5-what-does-not-survive",
        "python-names",
        "defaults-that-changed",
        "state-a-legacy-view-no-longer-keeps",
    ],
)
def test_published_anchors_survive(anchor):
    assert anchor in _slugs()
