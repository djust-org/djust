"""Every `C0xx` id the configuration checks can emit has a row in the docs table.

A set pin, not a floor (#1125): the emitted set must be a subset of the
documented set, so a new check without a row fails here, and the C013–C015
rows this test forced into `docs/system-checks.md` (#2562) cannot be dropped
without a check disappearing too.
"""

from __future__ import annotations

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs" / "system-checks.md"
SOURCE = ROOT / "python" / "djust" / "checks" / "configuration.py"

ROW = re.compile(r"^\| (C0\d\d) \|", re.MULTILINE)


def _emitted_ids() -> set[str]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            m = re.fullmatch(r"djust\.(C0\d\d)", node.value)
            if m:
                ids.add(m.group(1))
    return ids


def test_every_emitted_c0xx_id_has_a_docs_row() -> None:
    emitted = _emitted_ids()
    documented = set(ROW.findall(DOCS.read_text(encoding="utf-8")))
    assert "C016" in emitted, emitted
    missing = sorted(emitted - documented)
    assert not missing, f"checks without a row in docs/system-checks.md: {missing}"
