"""#3686 -- a view declares its own query budget; LiveViewSmokeTest reads it.

Every case runs the real ``test_smoke_queries`` against real views that issue a
known number of real queries (counted by Django's own query log), so a budget
that is not enforced or a count that is not taken fails here rather than being
asserted over a mock.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from djust import LiveView
from djust.testing import LiveViewSmokeTest


def _queries(n):
    for _ in range(n):
        User.objects.count()


class FewMountQueries(LiveView):
    """Mount runs 3 queries, render runs none."""

    template = "<div dj-root>few</div>"

    def mount(self, request, **kwargs):
        _queries(3)


class ManyMountQueries(LiveView):
    """Mount runs 35 queries (the issue's SalesReportView)."""

    template = "<div dj-root>many</div>"

    def mount(self, request, **kwargs):
        _queries(35)


class RenderQueries(LiveView):
    """Mount runs 1 query; rendering runs 4 more."""

    template = "<div dj-root>{{ n }}</div>"

    def mount(self, request, **kwargs):
        _queries(1)

    def get_context_data(self, **kwargs):
        _queries(4)
        return {"n": 1}


def _run(views, **attrs):
    cls = type("Smoke", (LiveViewSmokeTest,), attrs)
    with patch("djust.testing._discover_views", return_value=list(views)):
        cls().test_smoke_queries()


def _fails(views, **attrs):
    with pytest.raises(AssertionError) as exc:
        _run(views, **attrs)
    return str(exc.value)


def _with_budget(base, budget):
    return type(base.__name__, (base,), {"query_budget": budget, "__module__": base.__module__})


@pytest.mark.django_db
class TestPerViewBudget:
    def test_no_budget_falls_back_to_class_max_queries(self):
        _run([FewMountQueries], max_queries=3)
        assert "FewMountQueries: 3 queries (max 2)" in _fails([FewMountQueries], max_queries=2)

    def test_the_default_message_format_is_unchanged(self):
        msg = _fails([ManyMountQueries], max_queries=10)
        assert "ManyMountQueries: 35 queries (max 10)" in msg
        assert "Query threshold exceeded for 1/1 views" in msg

    def test_issue_scenario_each_view_has_its_own_number(self):
        """List at 3, report at 35: one class, one app-wide max_queries of 50."""
        tight = _with_budget(FewMountQueries, 3)
        loose = _with_budget(ManyMountQueries, 40)
        _run([tight, loose], max_queries=50)

    def test_a_tight_view_budget_fails_even_under_a_generous_app_wide_max(self):
        tight = _with_budget(FewMountQueries, 2)
        msg = _fails([tight], max_queries=50)
        assert "3 queries (max 2, from query_budget)" in msg

    def test_only_the_overspending_view_is_reported(self):
        tight = _with_budget(FewMountQueries, 2)
        loose = _with_budget(ManyMountQueries, 40)
        msg = _fails([tight, loose], max_queries=50)
        assert "1/2 views" in msg
        assert "ManyMountQueries" not in msg

    def test_a_view_budget_can_be_looser_than_the_app_wide_max(self):
        loose = _with_budget(ManyMountQueries, 35)
        _run([loose], max_queries=3)

    def test_budget_is_inclusive(self):
        _run([_with_budget(FewMountQueries, 3)], max_queries=0)
        _fails([_with_budget(FewMountQueries, 2)], max_queries=100)

    def test_zero_budget_means_no_queries(self):
        no_queries = _with_budget(type("Quiet", (LiveView,), {"template": "<div>q</div>"}), 0)
        _run([no_queries], max_queries=100)
        _fails([_with_budget(FewMountQueries, 0)], max_queries=100)

    def test_view_config_max_queries_beats_the_view_declaration(self):
        declared = _with_budget(FewMountQueries, 2)
        _run([declared], max_queries=1, view_config={declared: {"max_queries": 3}})
        msg = _fails([declared], max_queries=50, view_config={declared: {"max_queries": 1}})
        assert "3 queries (max 1, from view_config)" in msg

    def test_view_config_max_queries_works_without_a_view_budget(self):
        _run([FewMountQueries], max_queries=1, view_config={FewMountQueries: {"max_queries": 3}})

    def test_view_config_without_max_queries_keeps_the_view_budget(self):
        declared = _with_budget(FewMountQueries, 2)
        assert "from query_budget" in _fails([declared], view_config={declared: {"user": None}})

    def test_budget_is_inherited_by_subclasses_and_overridable(self):
        parent = _with_budget(FewMountQueries, 3)
        child = type("Child", (parent,), {"__module__": __name__})
        _run([child], max_queries=0)
        override = type("Override", (parent,), {"query_budget": 1, "__module__": __name__})
        _fails([override], max_queries=100)


@pytest.mark.django_db
class TestPhaseBudget:
    def test_int_budget_covers_mount_plus_render(self):
        """RenderQueries spends 1 (mount) + 4 (render) = 5."""
        _run([_with_budget(RenderQueries, 5)])
        assert "5 queries (max 4, from query_budget)" in _fails([_with_budget(RenderQueries, 4)])

    def test_dict_budget_measures_mount_and_render_separately(self):
        _run([_with_budget(RenderQueries, {"mount": 1, "render": 4})], max_queries=0)

    def test_dict_mount_phase_over_budget(self):
        msg = _fails([_with_budget(FewMountQueries, {"mount": 2, "render": 0})])
        assert "mount ran 3 queries (max 2, from query_budget['mount'])" in msg

    def test_dict_render_phase_over_budget(self):
        msg = _fails([_with_budget(RenderQueries, {"mount": 1, "render": 3})])
        assert "render ran 4 queries (max 3, from query_budget['render'])" in msg

    def test_both_phases_over_budget_are_both_reported(self):
        msg = _fails([_with_budget(RenderQueries, {"mount": 0, "render": 0})])
        assert "mount ran 1 queries (max 0" in msg
        assert "render ran 4 queries (max 0" in msg

    def test_a_phase_left_out_of_the_dict_falls_back_to_max_queries(self):
        view = _with_budget(RenderQueries, {"render": 4})
        _run([view], max_queries=1)
        assert "mount ran 1 queries (max 0, from max_queries)" in _fails([view], max_queries=0)

    def test_render_is_not_charged_against_the_mount_budget(self):
        """A split budget of 1+4 passes where a combined budget of 4 fails."""
        _run([_with_budget(RenderQueries, {"mount": 1, "render": 4})])
        _fails([_with_budget(RenderQueries, 4)])


@pytest.mark.django_db
class TestBudgetValidation:
    """A budget the test cannot enforce is rejected, not silently ignored."""

    @pytest.mark.parametrize("key", ["mout", "total", "queries"])
    def test_unsupported_dict_key_is_rejected(self, key):
        msg = _fails([_with_budget(FewMountQueries, {"mount": 3, key: 1})])
        assert "ValueError" in msg
        assert "query_budget" in msg and repr(key) in msg
        # The message lists what IS supported; "event" / "events" joined in #3701.
        for supported in ("'mount'", "'render'", "'event'", "'events'"):
            assert supported in msg

    @pytest.mark.parametrize("bad", [True, False, -1, 2.5, "3", None.__class__, [3]])
    def test_non_int_or_negative_budget_is_rejected(self, bad):
        msg = _fails([_with_budget(FewMountQueries, bad)])
        assert "ValueError" in msg and "query_budget" in msg

    @pytest.mark.parametrize("bad", [True, -1, 1.5, "3"])
    def test_bad_phase_value_is_rejected(self, bad):
        msg = _fails([_with_budget(FewMountQueries, {"mount": bad})])
        assert "ValueError" in msg and "'mount'" in msg

    def test_bad_view_config_max_queries_is_rejected(self):
        msg = _fails([FewMountQueries], view_config={FewMountQueries: {"max_queries": -5}})
        assert "ValueError" in msg and "max_queries" in msg

    def test_empty_dict_budget_is_the_class_default(self):
        _run([_with_budget(FewMountQueries, {})], max_queries=3)

    def test_a_misconfigured_view_does_not_hide_a_second_view_over_budget(self):
        bad = _with_budget(FewMountQueries, {"mout": 1})
        over = _with_budget(ManyMountQueries, 2)
        msg = _fails([bad, over])
        assert "2/2 views" in msg
        assert "FewMountQueries: ValueError" in msg
        assert "35 queries (max 2" in msg


class TestAttribute:
    def test_liveview_declares_query_budget_defaulting_to_none(self):
        assert LiveView.query_budget is None
        assert FewMountQueries.query_budget is None

    def test_a_query_budget_assigned_in_mount_is_not_reactive_state(self):
        """``self.query_budget = n`` in mount() is config, like ``self.tick_interval``."""

        class Budgeted(LiveView):
            template = "<div dj-root>{{ count }}</div>"

            def mount(self, request, **kwargs):
                self.count = 1
                self.query_budget = 4

        view = Budgeted()
        view.mount(None)
        assert view.get_state() == {"count": 1}
