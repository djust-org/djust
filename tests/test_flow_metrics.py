"""``scripts/flow-metrics.py``: the maths and the budget check (the GitHub fetch is not tested)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "flow_metrics", Path(__file__).resolve().parent.parent / "scripts" / "flow-metrics.py"
)
fm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fm)


def pr(n, hours, commits=3):
    return {"number": n, "created_at": "2026-11-05T00:00:00Z", "merged_at": f"2026-11-05T{hours:02d}:00:00Z", "commits": commits}


def issue(n, hours):
    day, hour = divmod(hours, 24)
    return {"number": n, "created_at": "2026-11-01T00:00:00Z", "closed_at": f"2026-11-{1 + day:02d}T{hour:02d}:00:00Z"}


class TestTheMaths:
    def test_hours_between(self):
        assert fm.hours_between("2026-11-05T00:00:00Z", "2026-11-05T03:30:00Z") == 3.5

    @pytest.mark.parametrize("values, p, want", [([], 0.9, 0.0), ([5], 0.9, 5), ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9, 10), ([1, 2, 3, 4], 0.5, 3)])
    def test_percentile_is_nearest_rank(self, values, p, want):
        assert fm.percentile(values, p) == want

    def test_summary_names_the_worst_offenders(self):
        s = fm.summarize([pr(1, 1, 2), pr(2, 5, 9)], [issue(10, 3), issue(11, 30)])
        assert s["pr_commits_max"] == 9 and s["pr_commits_worst"] == 2
        assert s["issue_slowest"] == 11 and s["issue_lead_max_hours"] == 30

    def test_an_empty_window_is_all_zero_not_an_error(self):
        s = fm.summarize([], [])
        assert s["prs"] == 0 and s["pr_lead_p90_hours"] == 0.0 and s["pr_commits_worst"] is None
        assert fm.breaches(s) == []


class TestTheBudgets:
    def test_within_budget_is_silent(self):
        assert fm.breaches(fm.summarize([pr(1, 1)], [issue(2, 5)]), {}) == []

    @pytest.mark.parametrize(
        "prs, issues, name",
        [
            ([pr(1, 6)], [], "pr_lead_p90_hours"),
            ([pr(1, 1, 12)], [], "pr_commits_max"),
            ([], [issue(1, 20)], "issue_lead_p75_hours"),
            ([], [issue(1, 72)], "issue_lead_max_hours"),
        ],
    )
    def test_each_budget_trips_on_its_own_measure(self, prs, issues, name):
        names = [b.split(":")[0] for b in fm.breaches(fm.summarize(prs, issues), {})]
        assert name in names

    def test_a_budget_can_be_overridden_and_a_bad_override_falls_back(self):
        s = fm.summarize([pr(1, 6)], [])
        assert fm.breaches(s, {"FLOW_PR_LEAD_P90_H": "12"}) == []
        assert fm.breaches(s, {"FLOW_PR_LEAD_P90_H": "nonsense"}) != []

    def test_the_boundary_is_not_a_breach(self):
        assert fm.breaches(fm.summarize([pr(1, 3)], []), {}) == []
