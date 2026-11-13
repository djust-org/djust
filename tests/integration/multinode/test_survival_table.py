"""The published state-survival table agrees with its tests (#4000).

Cheap and static, so it runs on every PR even though the tests it checks are in
the slow tier. The other half of the agreement, that each test SEES what its
cells say, is enforced when those tests run (``conftest.SurvivalRecorder``).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.integration.multinode import survival_table as st

TESTS = Path(__file__).with_name("test_state_survival.py")


def _test_functions() -> set:
    tree = ast.parse(TESTS.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }


def _observed_literals() -> set:
    """``(table, row, column)`` of every ``survival.observe`` with literal
    arguments in the test module (rows built in a loop are counted by the
    recorder at run time, not here)."""
    found = set()
    for node in ast.walk(ast.parse(TESTS.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "observe"
            and len(node.args) >= 3
            and all(isinstance(arg, ast.Constant) for arg in node.args[:3])
        ):
            found.add(tuple(arg.value for arg in node.args[:3]))
    return found


def test_every_table_is_complete():
    for table in st.TABLES:
        wanted = {(row.key, key) for row in table.rows for key, _ in table.columns}
        assert set(table.cells) == wanted, table.name


def test_every_outcome_cell_names_a_test_that_exists():
    tests = _test_functions()
    for key, entry in st.ALL_CELLS.items():
        if isinstance(entry, st.Cell):
            assert entry.test in tests, "%r cites %s, which does not exist" % (key, entry.test)
            assert entry.outcome.strip(), key
        else:
            assert entry.reason.strip(), key


def test_every_test_proves_at_least_one_cell():
    cited = {entry.test for entry in st.ALL_CELLS.values() if isinstance(entry, st.Cell)}
    assert _test_functions() <= cited, sorted(_test_functions() - cited)


def test_literal_observations_name_real_cells():
    for key in _observed_literals():
        assert isinstance(st.ALL_CELLS.get(key), st.Cell), key


def test_the_docs_page_carries_the_rendered_tables():
    text = st.DOC.read_text(encoding="utf-8")
    assert st.render_doc(text) == text, (
        "docs/website/guides/state-survival.md differs from the table: run "
        "python -m tests.integration.multinode.survival_table --write"
    )


def test_a_contradicting_observation_fails():
    """The recorder rejects an outcome the table does not state (gate check)."""
    import pytest

    from tests.integration.multinode.conftest import SurvivalRecorder

    recorder = SurvivalRecorder("test_ws_events_on_one_process")
    with pytest.raises(AssertionError, match="the table says"):
        recorder.observe("state", "persist_server", "same", "lost")
    with pytest.raises(AssertionError, match="is proved by"):
        recorder.observe("state", "persist_server", "ws", "restored")
    recorder.observe("state", "persist_server", "same", "kept")
    assert ("state", "plain_attr", "same") in recorder.missing()
