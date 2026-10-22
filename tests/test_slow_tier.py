"""The duration-derived `slow` tier (tests/slow_tier.py) and its pre-push use."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.slow_tier import SLOW_SECONDS, slow_ids

ROOT = Path(__file__).resolve().parents[1]

INI = """
[pytest]
markers =
    slow: slow
    nightly: nightly
"""


def _project(pytester: pytest.Pytester, durations: dict[str, float]) -> None:
    pytester.makeini(INI)
    pytester.makepyfile(
        test_a="""
        import pytest

        def test_fast():
            pass

        def test_heavy():
            pass

        @pytest.mark.nightly
        def test_sweep():
            pass
        """
    )
    (pytester.path / ".test_durations").write_text(json.dumps(durations))


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    # Same isolation as tests/test_lost_items_guard.py: no Django in the inner run.
    return pytester.runpytest(
        "-p", "tests.slow_tier", "-p", "no:cacheprovider", "-p", "no:django", *args
    )


def test_threshold_is_strictly_greater_than():
    assert slow_ids({"a": SLOW_SECONDS, "b": SLOW_SECONDS + 0.01, "c": 0.0}) == {"b"}


def test_recorded_slow_tests_are_deselected_by_not_slow(pytester):
    _project(pytester, {"test_a.py::test_heavy": 5.0, "test_a.py::test_fast": 0.01})
    result = _run(pytester, "-m", "not slow")
    result.assert_outcomes(passed=2, deselected=1)


def test_combined_expression_drops_both_tiers(pytester):
    _project(pytester, {"test_a.py::test_heavy": 5.0})
    result = _run(pytester, "-m", "not nightly and not slow")
    result.assert_outcomes(passed=1, deselected=2)


def test_unrecorded_tests_stay_in_the_fast_tier(pytester):
    _project(pytester, {})
    result = _run(pytester, "-m", "not slow")
    result.assert_outcomes(passed=3)


def test_env_switch_disables_the_derived_markers(pytester, monkeypatch):
    monkeypatch.setenv("DJUST_SLOW_TIER", "0")
    _project(pytester, {"test_a.py::test_heavy": 5.0})
    result = _run(pytester, "-m", "not slow")
    result.assert_outcomes(passed=3)


def test_pre_push_passes_one_combined_marker_expression():
    # pytest honours only the LAST -m: a separate `-m "not slow"` next to
    # `-m "not nightly"` would silently drop one of the two filters.
    script = (ROOT / "scripts/pre-push-pytest.sh").read_text()
    (invocation,) = [
        line
        for line in script.splitlines()
        if line.startswith("bash scripts/run-with-venv-python.sh -m pytest")
    ]
    assert len(re.findall(r'(?<!\w)-m\s+"', invocation)) == 0, invocation
    assert "MARK_FILTER" in invocation
    assert 'MARK_EXPR="${MARK_EXPR:+$MARK_EXPR and }not slow"' in script
