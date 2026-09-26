"""Covered documentation cannot drift away from its executed fixtures."""

import json
import subprocess
import sys

import pytest

from djust.tests import _doc_examples as H
from djust.tests.doc_scenarios import load_all

SCENARIOS = load_all()


def test_the_covered_documentation_has_no_problems():
    assert H.problems(H.COVERED, SCENARIOS) == []


def test_every_covered_block_is_executed_or_skipped():
    for path, counts in H.report(H.COVERED)["covered"].items():
        assert counts["unmarked"] == 0, path
    # Derived, not restated: the report agrees with the collected examples.
    collected = [e for s in H.COVERED for e in H.examples(s)]
    executed = sum(c["executed"] for c in H.report(H.COVERED)["covered"].values())
    assert executed == len([e for e in collected if e.scenario]) > 0


def test_the_report_script_prints_the_same_totals():
    out = subprocess.run(
        [sys.executable, str(H.ROOT / "scripts/doc-examples-report.py"), "--json"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert json.loads(out) == H.report(H.COVERED)


def _section(tmp_path, body):
    path = tmp_path / "doc.md"
    path.write_text(body)
    return H.Section(str(path))


@pytest.mark.parametrize(
    "body, expected",
    [
        ("```python\nx = 1\n```\n", "without a djust-example marker"),
        ("<!-- djust-example: a scenario=nope -->\n```python\nx = 1\n```\n", "unknown scenario"),
        ("<!-- djust-example: a scenario=project-menu -->\n\ntext\n", "not attached"),
        (
            "<!-- djust-example: a scenario=project-menu -->\n```python\nx = 1\n```\n"
            "<!-- djust-example: a scenario=project-menu -->\n```python\ny = 1\n```\n",
            "duplicate id",
        ),
        ("<!-- djust-example: skip -- -->\n```python\nx = 1\n```\n", "without a reason"),
    ],
)
def test_each_drift_is_reported(tmp_path, body, expected):
    found = H.problems((_section(tmp_path, body),), SCENARIOS)
    assert any(expected in problem for problem in found), found


def test_a_renamed_heading_is_reported(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text("## Renamed\n")
    found = H.problems((H.Section(str(path), "## Original"),), SCENARIOS)
    assert any("not found" in problem for problem in found), found
