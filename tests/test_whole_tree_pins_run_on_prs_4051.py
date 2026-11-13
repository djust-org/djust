"""A slow whole-tree pin must carry ``pr_tier`` or say why not (#4051).

``tests/slow_tier.py`` marks every test recorded over 1 s in ``.test_durations``
as ``slow``, and PR and merge-queue CI run ``-m "not slow"``. A whole-tree pin
(a ratchet, inventory or doc-table test that reads every source or test file)
is slow *because* it reads the tree, and it is exactly the kind of test an
unrelated PR breaks. Four of them went red on ``main`` only after merge, from
#4013 on, because no run before the merge selected them.

This guard reads the same ``.test_durations`` and fails when a slow-tier test
whose file or name looks like a pin is neither marked ``pr_tier`` nor listed in
``ALLOWED`` with the reason it may stay in the full tier. The name patterns are
a net, not a classification: a match that is not a whole-tree pin goes in
``ALLOWED``.

Limits: it sees only tests ``.test_durations`` records over 1 s, so a new pin
is checked after the next durations refresh, not on the PR that adds it. A
whole-tree pin whose file and test names match none of the patterns is not
seen. "Marked" is read from the source: ``@pytest.mark.pr_tier`` on the test or
an enclosing class, or ``pytestmark`` assigned it (alone or in a list) at module
level; a marker applied any other way (an alias, a conftest hook,
``pytest.param(..., marks=...)``) is not recognised and must be written in one
of those forms.
"""

from __future__ import annotations

import ast
import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DURATIONS = ROOT / ".test_durations"
SLOW_SECONDS = 1.0  # tests/slow_tier.py SLOW_SECONDS

#: A test file whose name says it pins the tree.
PIN_FILE = re.compile(r"ratchet|_audit_\d|structural|_pin(?:_|\.py$)|inventory_ratchet")
#: A test whose name says it pins a count or a closed set.
PIN_TEST = re.compile(r"ratchet|only_falls|exactly_the_known|known_ones|structural_pin")

#: Slow-tier tests that match the patterns and may stay out of the PR tier.
#: Keyed by node-id prefix (a file, a class or a test); the value is the reason.
#: A ``@pytest.mark.sweep(...)`` test counts as in the PR tier: a seeded sample of it runs on
#: every PR and every unit nightly (``tests/sweep_sample.py``, #4074), so ``TestPayloadSweep``
#: needs no entry here any more.
ALLOWED: dict[str, str] = {
    "python/tests/test_safe_markup_audit_3774.py::TestEverySinkIsReached": (
        "nightly (#4074): asks whether EVERY audited sink ran under a tracer (about 6 min "
        "recorded per test), so it needs the whole payload sweep, which a PR only samples; "
        "the site audit beside it is pr_tier"
    ),
}


#: The committed durations, read ONCE at import (collection), never from inside a test. CI runs
#: ``--store-durations --clean-durations`` under xdist, and pytest-split rewrites
#: ``.test_durations`` in every worker at its sessionfinish with only that worker's tests, while
#: other workers are still running (see tests/test_ci_python_test_shards.py). A read inside a test
#: can then see a subset: PR #4084's shard 3 reported the ALLOWED entry stale that way.
_COMMITTED = (DURATIONS, DURATIONS.read_text())


def _durations() -> dict[str, float]:
    if DURATIONS == _COMMITTED[0]:
        return json.loads(_COMMITTED[1])
    return json.loads(DURATIONS.read_text())  # a test pointed DURATIONS at its own file


def _slow_pins() -> list[str]:
    out = []
    for nodeid, seconds in _durations().items():
        if seconds <= SLOW_SECONDS:
            continue
        path, _, rest = nodeid.partition("::")
        if PIN_FILE.search(Path(path).name) or PIN_TEST.search(rest):
            out.append(nodeid)
    return sorted(out)


def _is_pr_tier_marker(node: ast.expr) -> bool:
    """``pytest.mark.pr_tier`` itself (an attribute chain, not a call or a string)."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "pr_tier"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _is_sweep_marker(node: ast.expr) -> bool:
    """``pytest.mark.sweep("<population>")``: sampled on PRs, full nightly (#4074)."""
    func = node.func if isinstance(node, ast.Call) else None
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "sweep"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "mark"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "pytest"
    )


def _is_pr_tier(decorators: list[ast.expr]) -> bool:
    return any(_is_pr_tier_marker(d) or _is_sweep_marker(d) for d in decorators)


@lru_cache(maxsize=None)
def _module(path: str) -> ast.Module | None:
    file = ROOT / path
    if not file.is_file():
        return None
    return ast.parse(file.read_text(encoding="utf-8"))


def _marked(nodeid: str) -> bool | None:
    """Whether the test, its class or its module carries ``pr_tier``; None if gone."""
    path, _, rest = nodeid.partition("::")
    tree = _module(path)
    if tree is None:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            value = node.value
            items = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
            if any(_is_pr_tier_marker(item) for item in items):
                return True
    parts = rest.split("[", 1)[0].split("::")
    scope: list[ast.stmt] = tree.body
    for name in parts:
        found = next(
            (
                n
                for n in scope
                if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == name
            ),
            None,
        )
        if found is None:
            return None
        if _is_pr_tier(found.decorator_list):
            return True
        scope = found.body if isinstance(found, ast.ClassDef) else []
    return False


def _allowed(nodeid: str) -> bool:
    return any(nodeid == key or nodeid.startswith(key + "::") for key in ALLOWED)


def test_every_slow_pin_runs_on_prs_or_says_why_not():
    unmarked = [
        nodeid for nodeid in _slow_pins() if not _allowed(nodeid) and _marked(nodeid) is False
    ]
    assert not unmarked, (
        "These slow-tier tests look like whole-tree pins, so PR and merge-queue CI "
        "(-m 'not slow') never run them and a break shows up only on main (#4051). "
        "Mark each with @pytest.mark.pr_tier, or add it to ALLOWED in this file with "
        "the reason it may run only in the full tier:\n  " + "\n  ".join(unmarked)
    )


def test_the_four_tests_that_broke_main_are_in_the_pr_tier():
    """The #4051 incident, by node id: each is marked, not allow-listed."""
    for nodeid in (
        "python/djust/tests/test_communicator_timeouts_ratchet_3709.py::"
        "test_the_count_of_short_communicator_timeouts_only_falls",
        "python/djust/tests/test_communicator_timeouts_ratchet_3709.py::"
        "test_what_the_ratchet_does_not_see_only_falls_too",
        "python/tests/test_pwa_tag_template_injection_3824.py::"
        "TestOnlyKnownTemplateSourceBuildersExist::"
        "test_the_construction_sites_are_exactly_the_known_ones",
        "python/tests/test_safe_markup_audit_3774.py::TestTheDocMatchesTheAudit::"
        "test_the_table_lists_exactly_the_audited_functions",
    ):
        assert _marked(nodeid) is True, nodeid
        assert not _allowed(nodeid), nodeid


def test_every_allowed_entry_still_names_a_slow_pin():
    """An allow-list entry that matches nothing is a stale exemption."""
    pins = _slow_pins()
    stale = [key for key in ALLOWED if not any(n == key or n.startswith(key + "::") for n in pins)]
    assert not stale, stale


def test_the_guard_catches_an_unmarked_pin(tmp_path, monkeypatch):
    """Non-vacuous: a slow ratchet with no marker is reported, a marked one is not."""
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "test_x_ratchet.py").write_text(
        "import pytest\n\n@pytest.mark.slow  # says pr_tier in a comment only\ndef test_a():\n"
        "    pass\n\n@pytest.mark.pr_tier\ndef test_b():\n    pass\n"
    )
    (tmp_path / ".test_durations").write_text(
        json.dumps({"t/test_x_ratchet.py::test_a": 5.0, "t/test_x_ratchet.py::test_b": 5.0})
    )
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    monkeypatch.setitem(globals(), "DURATIONS", tmp_path / ".test_durations")
    _module.cache_clear()
    try:
        assert _slow_pins() == ["t/test_x_ratchet.py::test_a", "t/test_x_ratchet.py::test_b"]
        assert _marked("t/test_x_ratchet.py::test_a") is False
        assert _marked("t/test_x_ratchet.py::test_b") is True
    finally:
        _module.cache_clear()
