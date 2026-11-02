"""#3701 -- query budgets beyond the smoke test.

Follow-up to #3686. ``LiveView.query_budget`` was read by one test only:

* event budgets (``"event"`` / ``"events": {handler: n}``) are enforced by the
  smoke tests, and a key nothing enforces is still rejected;
* a view that declares a budget is measured at run time, per phase, in DEBUG or
  under pytest, and a phase over its number is a warning (``"error"`` mode: an
  exception);
* ``djust_audit`` lists each view's budget, or that it has none.

Every case counts REAL queries (``User.objects.count()``) in REAL views, through
the real code paths: the test client, the HTTP GET and a ``WebsocketCommunicator``
socket, because Django connections are per thread and a counter installed in
the wrong place would see nothing on exactly the transports that matter.
"""

from __future__ import annotations

import json
import logging
import sys
from io import StringIO
from unittest.mock import patch

import pytest
from django import forms
from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.contrib.sessions.backends.db import SessionStore
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.urls import path

from djust import LiveView
from djust._query_budget import Budget, QueryBudgetExceeded, parse_budget
from djust.config import config as djust_config
from djust.decorators import event_handler
from djust.testing import LiveViewSmokeTest, LiveViewTestClient

from djust.tests._ws_frames import drain_extra, receive_until

LOGGER = "djust._query_budget"


def _queries(n):
    for _ in range(n):
        User.objects.count()


class Base(LiveView):
    """Each phase spends the number of queries its ``spend_*`` attribute says."""

    login_required = False
    template = "<div dj-root>{{ n }}</div>"
    spend_mount = 0
    spend_params = 0
    spend_render = 0

    def mount(self, request, **kwargs):
        _queries(self.spend_mount)
        self.n = 0

    def handle_params(self, params, uri):
        _queries(self.spend_params)

    def get_context_data(self, **kwargs):
        _queries(self.spend_render)
        return {"n": 1}

    @event_handler()
    def cheap(self, **kwargs):
        _queries(1)

    @event_handler()
    def costly(self, **kwargs):
        _queries(5)


class NeedsIdBase(Base):
    @event_handler()
    def needs_id(self, item_id: int = 0, **kwargs):
        """Fails for every generated payload: only a real id reaches the query."""
        if item_id != 42:
            raise ValueError("no such item")
        _queries(4)


def view(budget=None, base=Base, **attrs):
    """A ``base`` subclass with ``query_budget = budget`` and the given spends."""
    return type("V", (base,), {"__module__": __name__, "query_budget": budget, **attrs})


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


@pytest.fixture
def warned(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    return lambda: _warnings(caplog)


@pytest.fixture(autouse=True)
def _reset_invalid_warned():
    from djust import _query_budget

    _query_budget._reset_for_tests()
    yield
    _query_budget._reset_for_tests()


# --------------------------------------------------------------------- parse --


class TestParse:
    def test_none_is_an_empty_budget(self):
        assert parse_budget(None) == Budget()

    def test_an_int_is_the_mount_plus_first_render_total(self):
        assert parse_budget(5) == Budget(total=5)

    def test_every_dict_key_is_read(self):
        parsed = parse_budget({"mount": 1, "render": 2, "event": 3, "events": {"a": 4}})
        assert (parsed.mount, parsed.render, parsed.event, dict(parsed.events)) == (
            1,
            2,
            3,
            {"a": 4},
        )
        assert parsed.total is None

    @pytest.mark.parametrize("bad", [True, -1, 2.5, "3", [3]])
    def test_a_bad_number_is_rejected(self, bad):
        with pytest.raises(ValueError, match="non-negative int"):
            parse_budget(bad)

    @pytest.mark.parametrize("key", ["mout", "total", "queries", 3])
    def test_an_unknown_key_is_rejected(self, key):
        with pytest.raises(ValueError, match="not supported"):
            parse_budget({"mount": 1, key: 1})

    @pytest.mark.parametrize("bad", [["a"], 3, "a"])
    def test_events_must_be_a_dict(self, bad):
        with pytest.raises(ValueError, match="events"):
            parse_budget({"events": bad})

    def test_events_values_and_names_are_checked(self):
        with pytest.raises(ValueError, match=r"events'\]\['a'\]"):
            parse_budget({"events": {"a": -1}})
        with pytest.raises(ValueError, match="handler names"):
            parse_budget({"events": {3: 1}})


# ------------------------------------------------------------ smoke: events --


def _smoke(views, test="test_smoke_event_queries", **attrs):
    cls = type("Smoke", (LiveViewSmokeTest,), attrs)
    with patch("djust.testing._discover_views", return_value=list(views)):
        getattr(cls(), test)()


def _smoke_fails(views, test="test_smoke_event_queries", **attrs):
    with pytest.raises(AssertionError) as exc:
        _smoke(views, test, **attrs)
    return str(exc.value)


@pytest.mark.django_db
class TestSmokeEventBudget:
    def test_a_handler_within_the_event_budget_passes(self):
        _smoke([view({"event": 5})], view_config={})

    def test_a_handler_over_the_event_budget_fails_naming_it(self):
        msg = _smoke_fails([view({"event": 4})])
        assert "costly: ran 5 queries (max 4, from query_budget['event'])" in msg
        assert ".cheap" not in msg  # 1 <= 4

    def test_the_event_budget_is_inclusive(self):
        _smoke([view({"event": 5})])
        assert "ran 5 queries (max 4" in _smoke_fails([view({"event": 4})])

    def test_events_gives_one_handler_its_own_number_and_it_wins_over_event(self):
        # "event" 1 would fail costly (5); the per-handler 5 wins, cheap stays at 1.
        _smoke([view({"event": 1, "events": {"costly": 5}})])
        msg = _smoke_fails([view({"event": 5, "events": {"cheap": 0}})])
        assert "cheap: ran 1 queries (max 0, from query_budget['events']['cheap'])" in msg
        assert "costly" not in msg

    def test_events_alone_leaves_the_other_handlers_unbudgeted(self):
        _smoke([view({"events": {"costly": 5}})])

    def test_a_dict_with_event_keys_keeps_mount_and_render_on_max_queries(self):
        """Mount/render fall back to the class-wide max_queries, as for any dict."""
        v = view({"event": 9}, spend_mount=3)
        _smoke([v], "test_smoke_queries", max_queries=3)
        assert "mount ran 3 queries (max 2, from max_queries)" in _smoke_fails(
            [v], "test_smoke_queries", max_queries=2
        )

    def test_the_event_budget_is_enforced_with_fuzz_off(self):
        """``fuzz = False`` is a supported setting: a skipped budget would pass for protection."""
        assert "costly" in _smoke_fails([view({"event": 1})], fuzz=False)

    def test_a_view_without_an_event_budget_is_not_measured(self):
        _smoke([view(None), view({"mount": 1}), view(7)])

    def test_view_config_max_queries_does_not_switch_the_event_budget_off(self):
        v = view({"event": 1})
        msg = _smoke_fails([v], view_config={v: {"max_queries": 99}})
        assert "costly" in msg

    def test_a_handler_named_in_events_that_the_view_lacks_is_rejected(self):
        msg = _smoke_fails([view({"events": {"costy": 5}})])
        assert "ValueError" in msg and "'costy'" in msg and "not an @event_handler" in msg
        assert "costly" in msg  # the real handlers are listed

    def test_the_other_smoke_test_catches_the_same_typo(self):
        """Whichever smoke test runs, an event budget nothing can enforce is an error."""
        msg = _smoke_fails([view({"events": {"costy": 5}})], "test_smoke_queries")
        assert "ValueError" in msg and "'costy'" in msg

    def test_a_named_handler_that_never_succeeds_is_an_error_not_a_pass(self):
        """``needs_id`` raises for every generated payload, so its budget was not measured."""
        msg = _smoke_fails([view({"events": {"needs_id": 100}}, base=NeedsIdBase)])
        assert "needs_id: no call succeeded, so its event budget was not measured" in msg
        assert "event_params" in msg

    def test_a_handler_covered_only_by_the_broad_event_number_is_a_warning(self):
        """The broad key is best effort where a handler cannot be called, but never silent."""
        with pytest.warns(
            UserWarning, match="query_budget\\['event'\\] was not measured for needs_id"
        ):
            _smoke([view({"event": 9}, base=NeedsIdBase)])

    def test_the_broad_event_number_still_fails_a_handler_it_can_measure(self):
        with pytest.warns(UserWarning):
            msg = _smoke_fails([view({"event": 4}, base=NeedsIdBase)])
        assert "costly: ran 5 queries" in msg

    def test_supplied_event_params_reach_the_handlers_query(self):
        v = view({"events": {"needs_id": 4}}, base=NeedsIdBase)
        _smoke([v], view_config={v: {"event_params": {"needs_id": {"item_id": 42}}}})
        tight = view({"events": {"needs_id": 3}}, base=NeedsIdBase)
        msg = _smoke_fails(
            [tight], view_config={tight: {"event_params": {"needs_id": {"item_id": 42}}}}
        )
        assert "needs_id: ran 4 queries (max 3, from query_budget['events']['needs_id'])" in msg

    def test_event_params_may_be_a_list_and_the_worst_call_is_charged(self):
        v = view({"events": {"cheap": 0}})
        # Two real calls to a handler that spends 1: both exceed 0; one message.
        msg = _smoke_fails([v], view_config={v: {"event_params": {"cheap": [{}, {"x": 1}]}}})
        assert msg.count("cheap: ran 1 queries") == 1

    def test_the_worst_call_is_charged_not_the_first(self):
        class Varies(Base):
            @event_handler()
            def vary(self, n: int = 0, **kwargs):
                # The fuzz payloads include huge ints: only a small n spends anything.
                _queries(n if 0 <= n <= 4 else 0)

        v = view({"events": {"vary": 3}}, base=Varies)
        view_config = {v: {"event_params": {"vary": [{"n": 1}, {"n": 4}, {"n": 2}]}}}
        msg = _smoke_fails([v], view_config=view_config)
        assert "vary: ran 4 queries (max 3" in msg and "[event_params[1]]" in msg
        ok = view({"events": {"vary": 4}}, base=Varies)
        _smoke([ok], view_config={ok: view_config[v]})

    def test_bad_event_params_are_an_error(self):
        v = view({"events": {"cheap": 9}})
        msg = _smoke_fails([v], view_config={v: {"event_params": {"cheap": 3}}})
        assert "event_params" in msg and "ValueError" in msg

    def test_only_the_overspending_view_is_reported(self):
        msg = _smoke_fails([view({"event": 4}), view({"event": 9})])
        assert "1/2 views" in msg

    def test_event_budget_is_inherited_by_subclasses(self):
        parent = view({"event": 4})
        child = type("Child", (parent,), {"__module__": __name__})
        assert "costly" in _smoke_fails([child])

    def test_the_count_is_the_handlers_own_not_mounts(self):
        """A view whose mount is expensive does not charge it to the event."""
        _smoke([view({"event": 5}, spend_mount=30)])


# ----------------------------------------------------------- run-time meter --


@pytest.mark.django_db
class TestRunTimeMeasurement:
    def test_a_mount_over_budget_is_a_warning_naming_view_phase_and_numbers(self, warned):
        LiveViewTestClient(view({"mount": 2}, spend_mount=3)).mount()
        assert warned() == ["V: mount ran at least 3 queries, over its query_budget of 2"]

    def test_within_budget_is_silent(self, warned):
        client = LiveViewTestClient(
            view({"mount": 3, "render": 1, "event": 5}, spend_mount=3, spend_render=1)
        )
        client.mount()
        client.render()
        client.send_event("costly")
        assert warned() == []

    def test_the_budget_is_inclusive(self, warned):
        LiveViewTestClient(view({"mount": 3}, spend_mount=3)).mount()
        assert warned() == []
        LiveViewTestClient(view({"mount": 2}, spend_mount=3)).mount()
        assert len(warned()) == 1

    def test_handle_params_queries_belong_to_the_mount_phase(self, warned):
        """mount 1 + handle_params 2 = 3 against a mount budget of 2."""
        LiveViewTestClient(view({"mount": 2}, spend_mount=1, spend_params=2)).mount()
        assert warned() == ["V: mount ran at least 3 queries, over its query_budget of 2"]

    def test_the_mount_phase_is_reported_once_not_once_per_hop(self, warned):
        LiveViewTestClient(view({"mount": 0}, spend_mount=2, spend_params=2)).mount()
        assert len(warned()) == 1

    def test_render_is_its_own_phase(self, warned):
        client = LiveViewTestClient(view({"mount": 1, "render": 2}, spend_mount=1, spend_render=3))
        client.mount()
        assert warned() == []
        client.render()
        assert warned() == ["V: render ran 3 queries, over its query_budget of 2"]

    def test_every_render_pass_is_reported(self, warned):
        client = LiveViewTestClient(view({"render": 1}, spend_render=2)).mount()
        client.render()
        client.render()
        assert len(warned()) == 2

    def test_an_int_budget_is_mount_plus_the_first_render(self, warned):
        client = LiveViewTestClient(view(5, spend_mount=3, spend_render=3)).mount()
        client.render()
        assert warned() == [
            "V: mount + first render ran at least 6 queries, over its query_budget of 5"
        ]
        warned_before = len(warned())
        client.render()  # a later render is not part of the load
        assert len(warned()) == warned_before

    def test_an_event_between_mount_and_render_closes_the_first_render_window(self, warned):
        """A render after an event is not "the first render of the load"."""
        client = LiveViewTestClient(view(5, spend_mount=3, spend_render=3)).mount()
        client.send_event("cheap")
        client.render()  # 3 + 3 = 6 would be over 5 if it still counted as the load
        assert warned() == []

    def test_an_int_budget_within_the_total_is_silent(self, warned):
        LiveViewTestClient(view(6, spend_mount=3, spend_render=3)).mount().render()
        assert warned() == []

    def test_an_event_is_judged_by_its_own_queries(self, warned):
        client = LiveViewTestClient(view({"event": 3}, spend_mount=40)).mount()
        client.send_event("cheap")
        assert warned() == []
        client.send_event("costly")
        assert warned() == ["V: event costly ran 5 queries, over its query_budget of 3"]

    def test_every_event_over_budget_is_reported_not_just_the_first(self, warned):
        client = LiveViewTestClient(view({"event": 3})).mount()
        client.send_event("costly")
        client.send_event("costly")
        assert len(warned()) == 2

    def test_events_gives_a_handler_its_own_number(self, warned):
        client = LiveViewTestClient(view({"event": 1, "events": {"costly": 5}})).mount()
        client.send_event("costly")
        assert warned() == []
        client.send_event("cheap")
        client.send_event("cheap")
        assert len(warned()) == 0  # 1 <= 1
        client2 = LiveViewTestClient(view({"event": 9, "events": {"cheap": 0}})).mount()
        client2.send_event("cheap")
        assert warned() == ["V: event cheap ran 1 queries, over its query_budget of 0"]

    def test_a_handler_with_no_number_is_not_measured(self, warned):
        LiveViewTestClient(view({"events": {"cheap": 1}})).mount().send_event("costly")
        assert warned() == []

    def test_url_change_opens_a_new_mount_phase(self, warned):
        client = LiveViewTestClient(view({"mount": 1}, spend_mount=1, spend_params=2)).mount()
        assert len(warned()) == 1  # 1 + 2 = 3 > 1
        client.render()  # every transport renders between a mount and the next URL
        client.url_change({"a": "1"})  # handle_params alone: 2 > 1, its own phase
        assert warned() == [
            "V: mount ran at least 3 queries, over its query_budget of 1",
            "V: mount ran at least 2 queries, over its query_budget of 1",
        ]

    def test_an_event_between_does_not_leak_into_the_next_phase(self, warned):
        client = LiveViewTestClient(view({"mount": 1}, spend_mount=1)).mount()
        client.send_event("costly")
        assert warned() == []  # no event budget; its 5 are not mount's

    def test_nothing_is_metered_for_a_view_that_declares_no_budget(self, warned):
        client = LiveViewTestClient(view(None, spend_mount=50, spend_render=50)).mount()
        client.render()
        client.send_event("costly")
        assert warned() == []

    def test_a_failed_hop_is_not_reported_over_its_own_error(self, warned):
        class Boom(Base):
            query_budget = {"mount": 0}

            def mount(self, request, **kwargs):
                _queries(2)
                raise RuntimeError("mount failed")

        with pytest.raises(RuntimeError, match="mount failed"):
            LiveViewTestClient(Boom).mount()
        assert warned() == []

    def test_the_budget_is_read_from_the_class(self, warned):
        """``self.query_budget = n`` in mount() is config like ``tick_interval`` (#3686)."""

        class InstanceOnly(Base):
            spend_mount = 3

            def mount(self, request, **kwargs):
                self.query_budget = 0
                super().mount(request, **kwargs)

        LiveViewTestClient(InstanceOnly).mount()
        assert warned() == []


@pytest.mark.django_db
class TestAnInvalidBudgetAtRunTime:
    """Run time never raises over a typo (the smoke test does); it says so once and measures nothing."""

    def test_it_is_logged_once_per_class_and_not_measured(self, warned):
        broken = view({"mout": 0}, spend_mount=9)
        LiveViewTestClient(broken).mount()
        LiveViewTestClient(broken).mount()
        messages = warned()
        assert len(messages) == 1
        assert "V declares an invalid query_budget and it is not measured" in messages[0]
        assert "'mout'" in messages[0]

    def test_error_mode_does_not_raise_for_it(self):
        with override_settings(LIVEVIEW_CONFIG={"query_budget": "error"}):
            LiveViewTestClient(view({"mount": -1}, spend_mount=3)).mount()


@pytest.mark.django_db
class TestModes:
    def test_unset_under_pytest_warns(self, warned):
        assert djust_config.get("query_budget") is None
        LiveViewTestClient(view({"mount": 0}, spend_mount=1)).mount()
        assert len(warned()) == 1

    def test_unset_outside_debug_and_pytest_measures_nothing(self, warned, settings):
        settings.DEBUG = False
        with patch("djust.apps._running_under_pytest", return_value=False):
            LiveViewTestClient(view({"mount": 0}, spend_mount=1)).mount()
        assert warned() == []

    def test_unset_with_debug_on_warns(self, warned, settings):
        settings.DEBUG = True
        with patch("djust.apps._running_under_pytest", return_value=False):
            LiveViewTestClient(view({"mount": 0}, spend_mount=1)).mount()
        assert len(warned()) == 1

    def test_off_never_measures(self, warned):
        with override_settings(LIVEVIEW_CONFIG={"query_budget": "off"}):
            LiveViewTestClient(view({"mount": 0}, spend_mount=1)).mount()
        assert warned() == []

    def test_explicit_warn_measures_even_with_debug_off(self, warned, settings):
        settings.DEBUG = False
        with patch("djust.apps._running_under_pytest", return_value=False):
            with override_settings(LIVEVIEW_CONFIG={"query_budget": "warn"}):
                LiveViewTestClient(view({"mount": 0}, spend_mount=1)).mount()
        assert len(warned()) == 1

    def test_error_mode_raises_from_the_mount(self):
        with override_settings(LIVEVIEW_CONFIG={"query_budget": "error"}):
            with pytest.raises(
                QueryBudgetExceeded,
                match="V: mount ran at least 2 queries, over its query_budget of 1",
            ):
                LiveViewTestClient(view({"mount": 1}, spend_mount=2)).mount()

    def test_error_mode_fails_the_event_it_came_from(self):
        with override_settings(LIVEVIEW_CONFIG={"query_budget": "error"}):
            client = LiveViewTestClient(view({"event": 1})).mount()
            result = client.send_event("costly")
        assert result["success"] is False
        assert "event costly ran 5 queries, over its query_budget of 1" in result["error"]

    def test_error_mode_within_budget_does_not_raise(self):
        with override_settings(LIVEVIEW_CONFIG={"query_budget": "error"}):
            LiveViewTestClient(view({"mount": 2, "event": 5}, spend_mount=2)).mount().send_event(
                "costly"
            )

    def test_an_unknown_mode_never_fails_a_request(self, warned):
        """It is C029 at startup; the request path reads it as ``warn`` and says so once."""
        with override_settings(LIVEVIEW_CONFIG={"query_budget": "loud"}):
            LiveViewTestClient(view({"mount": 0}, spend_mount=1)).mount()
        messages = warned()
        assert any("LIVEVIEW_CONFIG['query_budget'] must be one of" in m for m in messages)
        assert any("mount ran at least 1 queries" in m for m in messages)  # it measured, as warn

    def test_an_unknown_mode_is_not_read_for_a_view_without_a_budget(self):
        """Zero cost when unused includes not reading the knob."""
        with override_settings(LIVEVIEW_CONFIG={"query_budget": "loud"}):
            LiveViewTestClient(view(None)).mount()


@pytest.mark.django_db
class TestZeroCostWhenUnused:
    def test_an_unbudgeted_view_never_builds_a_meter_or_installs_a_wrapper(self):
        from djust import _query_budget

        built = []

        class Spy(_query_budget._Hop):
            def __init__(self, *args, **kwargs):
                built.append(args)
                super().__init__(*args, **kwargs)

        client = LiveViewTestClient(view(None, spend_mount=1)).mount()
        with patch.object(_query_budget, "_Hop", Spy):
            client.render()
            client.send_event("cheap")
            client.url_change({"a": "1"})
            LiveViewTestClient(view(None)).mount()
            assert built == []
            # not vacuous: the same spy sees a budgeted view
            LiveViewTestClient(view({"mount": 9})).mount()
            assert built, "the spy never saw a meter built"

    def test_no_execute_wrapper_is_left_on_the_connection(self):
        client = LiveViewTestClient(view({"mount": 9, "event": 9}, spend_mount=1)).mount()
        client.send_event("cheap")
        assert connection.execute_wrappers == []

    def test_every_scope_entered_is_exited_explicitly(self):
        """Not left to the garbage collector: CPython closing the generator hides a leak."""
        from django.db.backends.base.base import BaseDatabaseWrapper

        real = BaseDatabaseWrapper.execute_wrapper
        entered, exited = [], []

        class Recording:
            def __init__(self, scope):
                self.scope = scope

            def __enter__(self):
                entered.append(1)
                return self.scope.__enter__()

            def __exit__(self, *exc):
                exited.append(1)
                return self.scope.__exit__(*exc)

        with patch.object(
            BaseDatabaseWrapper, "execute_wrapper", lambda self, w: Recording(real(self, w))
        ):
            client = LiveViewTestClient(view({"mount": 9, "event": 9}, spend_mount=1)).mount()
            client.send_event("cheap")
        assert entered, "no scope was entered"
        assert len(entered) == len(exited)

    def test_a_scope_is_exited_explicitly_when_the_step_raises_too(self):
        """The exception path is the one refcounting would otherwise be hiding."""
        from django.db.backends.base.base import BaseDatabaseWrapper

        real = BaseDatabaseWrapper.execute_wrapper
        entered, exited = [], []

        class Recording:
            def __init__(self, scope):
                self.scope = scope

            def __enter__(self):
                entered.append(1)
                return self.scope.__enter__()

            def __exit__(self, *exc):
                exited.append(exc[0])
                return self.scope.__exit__(*exc)

        class Boom(Base):
            query_budget = {"mount": 9}

            def mount(self, request, **kwargs):
                _queries(1)
                raise RuntimeError("x")

        with patch.object(
            BaseDatabaseWrapper, "execute_wrapper", lambda self, w: Recording(real(self, w))
        ):
            with pytest.raises(RuntimeError):
                LiveViewTestClient(Boom).mount()
        assert entered == [1] and exited == [RuntimeError]

    def test_the_wrapper_is_removed_when_the_hop_fails(self):
        class Boom(Base):
            query_budget = {"mount": 9}

            def mount(self, request, **kwargs):
                _queries(1)
                raise RuntimeError("x")

        with pytest.raises(RuntimeError):
            LiveViewTestClient(Boom).mount()
        assert connection.execute_wrappers == []

    def test_sync_step_returns_the_function_itself_for_an_unbudgeted_view(self):
        from djust.sync_turn import guarded_step, sync_step

        def fn():
            return 1

        plain = view(None)()
        assert sync_step(plain, "event", fn) is fn
        assert guarded_step(plain, "handle_params", fn) is fn
        assert sync_step(view({"event": 1})(), "event", fn) is not fn

    def test_an_async_hop_is_never_metered(self):
        from djust.sync_turn import sync_step

        async def handler():
            return 1

        assert sync_step(view({"event": 1})(), "event", handler) is handler


class WhoParams(forms.Form):
    """A ``ModelChoiceField`` queries while it cleans: the form's own database cost."""

    who = forms.ModelChoiceField(queryset=User.objects.all(), required=False)


@pytest.mark.django_db
class TestParamsFormValidationIsPartOfTheMountPhase:
    """The form is validated in the guarded ``handle_params`` step (#3689), which is metered too."""

    def _mount(self):
        broken = view({"mount": 0}, params_form=WhoParams)
        LiveViewTestClient(broken).mount(query_params={"who": "999"})  # one query: no such user

    def test_without_a_sync_turn_wrapper(self, warned):
        self._mount()
        assert warned() == ["V: mount ran at least 1 queries, over its query_budget of 0"]

    def test_with_a_sync_turn_wrapper(self, warned):
        from contextlib import nullcontext

        previous = djust_config.get("sync_turn_wrapper")
        djust_config.set("sync_turn_wrapper", lambda view_, step, turn: nullcontext())
        try:
            self._mount()
        finally:
            djust_config.set("sync_turn_wrapper", previous)
        assert warned() == ["V: mount ran at least 1 queries, over its query_budget of 0"]

    def test_a_bare_url_queries_nothing_and_is_silent(self, warned):
        LiveViewTestClient(view({"mount": 0}, params_form=WhoParams)).mount()
        assert warned() == []


@pytest.mark.django_db
class TestWithASyncTurnWrapper:
    def test_the_wrappers_own_queries_are_not_charged_to_the_step(self, warned):
        from contextlib import contextmanager

        @contextmanager
        def wrapper(view_, step, turn):
            _queries(3)  # e.g. SET LOCAL: not the step's spend
            yield

        previous = djust_config.get("sync_turn_wrapper")
        djust_config.set("sync_turn_wrapper", wrapper)
        try:
            LiveViewTestClient(view({"mount": 1}, spend_mount=1)).mount()
        finally:
            djust_config.set("sync_turn_wrapper", previous)
        assert warned() == []

    def test_both_run_when_both_are_configured(self, warned):
        entered = []

        from contextlib import contextmanager

        @contextmanager
        def wrapper(view_, step, turn):
            entered.append(step)
            yield

        previous = djust_config.get("sync_turn_wrapper")
        djust_config.set("sync_turn_wrapper", wrapper)
        try:
            LiveViewTestClient(view({"mount": 0}, spend_mount=1)).mount()
        finally:
            djust_config.set("sync_turn_wrapper", previous)
        assert "mount" in entered
        assert len(warned()) == 1


@pytest.mark.django_db
class TestNestedSteps:
    def test_a_handler_that_renders_is_charged_once(self, warned):
        class Renders(Base):
            query_budget = {"event": 6, "render": 0}
            spend_render = 2

            @event_handler()
            def both(self, **kwargs):
                _queries(1)
                self.get_context_data()  # a nested render step: part of this event

        client = LiveViewTestClient(Renders).mount()
        client.send_event("both")
        # 1 + 2 = 3 <= 6, and the nested render is NOT also judged as a render hop
        assert warned() == []


# -------------------------------------------------------- real transports ----


class SpendView(LiveView):
    """The transports' view: 3 queries in mount, 1 in handle_params, 2 in render, 5 in an event."""

    login_required = False
    template = "<div dj-root>{{ n }}</div>"
    query_budget = {"mount": 1, "render": 1, "event": 1}

    def mount(self, request, **kwargs):
        _queries(3)
        self.n = 0

    def handle_params(self, params, uri):
        _queries(1)

    def get_context_data(self, **kwargs):
        _queries(2)
        return {"n": self.n}

    @event_handler()
    def spend(self, **kwargs):
        _queries(5)
        self.n += 1


class FrugalView(SpendView):
    query_budget = {"mount": 9, "render": 9, "event": 9}


setattr(sys.modules[__name__], "SpendView", SpendView)
setattr(sys.modules[__name__], "FrugalView", FrugalView)

urlpatterns = [path("spend/", SpendView.as_view()), path("frugal/", FrugalView.as_view())]


@pytest.fixture
def transports(settings):
    settings.ROOT_URLCONF = __name__
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = [__name__]


@pytest.mark.django_db
@pytest.mark.usefixtures("transports")
class TestHttpGet:
    def _get(self, url):
        from django.test import Client

        return Client().get(url)

    def test_the_get_is_measured_per_phase(self, warned):
        response = self._get("/spend/")
        assert response.status_code == 200
        messages = warned()
        assert (
            "SpendView: mount ran at least 3 queries, over its query_budget of 1" in messages
        )  # mount + handle_params
        assert "SpendView: render ran 2 queries, over its query_budget of 1" in messages

    def test_a_view_within_its_budget_is_silent_on_the_get(self, warned):
        assert self._get("/frugal/").status_code == 200
        assert warned() == []

    def test_error_mode_turns_the_get_into_an_error(self):
        from django.test import Client

        with override_settings(LIVEVIEW_CONFIG={"query_budget": "error"}):
            with pytest.raises(QueryBudgetExceeded):
                Client(raise_request_exception=True).get("/spend/")


class FormMountView(LiveView):
    """Mount and handle_params are free; only the form's ``ModelChoiceField`` queries."""

    login_required = False
    template = "<div dj-root>{{ n }}</div>"
    query_budget = {"mount": 0}
    params_form = WhoParams

    def mount(self, request, **kwargs):
        self.n = 0


setattr(sys.modules[__name__], "FormMountView", FormMountView)
urlpatterns.append(path("formmount/", FormMountView.as_view()))


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _connect(view_name, url):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    def _create_session():
        store = SessionStore()
        store.create()
        return store.session_key

    key = await sync_to_async(_create_session)()
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)
    await communicator.send_json_to(
        {"type": "mount", "view": f"{__name__}.{view_name}", "url": url, "params": {}}
    )
    frames = await receive_until(
        communicator, lambda fs: any(f.get("type") in ("mount", "error") for f in fs)
    )
    return communicator, frames[-1]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.usefixtures("transports")
class TestWebSocket:
    """The hops run on ``sync_to_async`` worker threads, with connections of their own."""

    async def test_the_socket_mount_and_render_are_measured_on_the_worker_thread(self, warned):
        communicator, mount = await _connect("SpendView", "/spend/")
        assert mount["type"] == "mount", mount
        messages = warned()
        assert "SpendView: mount ran at least 3 queries, over its query_budget of 1" in messages
        assert any("SpendView: render ran 2 queries" in m for m in messages)
        await communicator.disconnect()

    async def test_the_forms_validation_queries_are_part_of_the_socket_mount(self, warned):
        """On the socket the form binds in a guarded ``handle_params`` step on a worker thread."""
        pytest.importorskip("channels")
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer

        def _create_session():
            store = SessionStore()
            store.create()
            return store.session_key

        key = await sync_to_async(_create_session)()
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        communicator.scope["session"] = _ScopeSession(key)
        connected, _ = await communicator.connect()
        assert connected
        await communicator.receive_json_from(timeout=2)
        await communicator.send_json_to(
            {
                "type": "mount",
                "view": f"{__name__}.FormMountView",
                "url": "/formmount/",
                "params": {"who": "999"},
            }
        )
        frames = await receive_until(
            communicator, lambda fs: any(f.get("type") in ("mount", "error") for f in fs)
        )
        assert frames[-1]["type"] == "mount", frames
        assert warned() == [
            "FormMountView: mount ran at least 1 queries, over its query_budget of 0"
        ]
        await communicator.disconnect()

    async def test_a_socket_event_is_measured(self, warned):
        communicator, _ = await _connect("SpendView", "/spend/")
        await drain_extra(communicator)
        before = len(warned())
        await communicator.send_json_to({"type": "event", "event": "spend", "params": {}, "ref": 1})
        await receive_until(
            communicator,
            lambda fs: any(f.get("type") in ("patch", "html_update", "error", "noop") for f in fs),
        )
        new = warned()[before:]
        assert "SpendView: event spend ran 5 queries, over its query_budget of 1" in new
        await communicator.disconnect()

    async def test_a_frugal_view_is_silent_on_the_socket(self, warned):
        communicator, mount = await _connect("FrugalView", "/frugal/")
        assert mount["type"] == "mount", mount
        await communicator.send_json_to({"type": "event", "event": "spend", "params": {}, "ref": 1})
        await receive_until(
            communicator,
            lambda fs: any(f.get("type") in ("patch", "html_update", "error", "noop") for f in fs),
        )
        assert warned() == []
        await communicator.disconnect()

    async def test_error_mode_answers_an_over_budget_event_with_an_error_frame(self):
        """FrugalView mounts within budget; its event is over a budget of 1 in error mode."""

        class TightEvent(FrugalView):
            query_budget = {"mount": 99, "render": 99, "event": 1}

        setattr(sys.modules[__name__], "TightEvent", TightEvent)
        try:
            with override_settings(LIVEVIEW_CONFIG={"query_budget": "error"}):
                communicator, mount = await _connect("TightEvent", "/frugal/")
                assert mount["type"] == "mount", mount
                await communicator.send_json_to(
                    {"type": "event", "event": "spend", "params": {}, "ref": 1}
                )
                frames = await receive_until(
                    communicator,
                    lambda fs: any(
                        f.get("type") in ("patch", "html_update", "error", "noop") for f in fs
                    ),
                )
            assert frames[-1]["type"] == "error", frames
            await communicator.disconnect()
        finally:
            delattr(sys.modules[__name__], "TightEvent")

    async def test_error_mode_fails_the_socket_mount(self):
        with override_settings(LIVEVIEW_CONFIG={"query_budget": "error"}):
            communicator, frame = await _connect("SpendView", "/spend/")
        assert frame["type"] == "error", frame
        await communicator.disconnect()


# ----------------------------------------------------------------- the audit --


class AuditedPlain(LiveView):
    login_required = False
    template = "<div dj-root></div>"


class AuditedInt(LiveView):
    login_required = False
    template = "<div dj-root></div>"
    query_budget = 5


class AuditedDict(LiveView):
    login_required = False
    template = "<div dj-root></div>"
    query_budget = {"mount": 2, "render": 1, "events": {"go": 3}}

    @event_handler()
    def go(self, **kwargs):
        pass


def _audit_json(*classes):
    out = StringIO()
    names = {c.__name__ for c in classes}
    with patch("djust.management.commands.djust_audit.Command._collect_audits") as collect:
        from djust.management.commands.djust_audit import _audit_class

        collect.return_value = [_audit_class(c, "LiveView") for c in classes]
        call_command("djust_audit", "--json", stdout=out)
    data = json.loads(out.getvalue())
    return {
        a["class"].rsplit(".", 1)[1]: a
        for a in data["audits"]
        if a["class"].rsplit(".", 1)[1] in names
    }


class TestAuditListing:
    def test_json_lists_each_views_budget_or_none(self):
        audits = _audit_json(AuditedPlain, AuditedInt, AuditedDict)
        assert audits["AuditedPlain"]["query_budget"] is None
        assert audits["AuditedInt"]["query_budget"] == 5
        assert audits["AuditedDict"]["query_budget"] == {
            "mount": 2,
            "render": 1,
            "events": {"go": 3},
        }

    def test_the_pretty_report_says_so_for_every_view(self):
        out = StringIO()
        with patch("djust.management.commands.djust_audit.Command._collect_audits") as collect:
            from djust.management.commands.djust_audit import _audit_class

            collect.return_value = [
                _audit_class(c, "LiveView") for c in (AuditedPlain, AuditedInt, AuditedDict)
            ]
            call_command("djust_audit", stdout=out)
        text = out.getvalue()
        assert "Query budget: (none)" in text
        assert "Query budget: 5 (mount + first render)" in text
        assert "Query budget: mount 2, render 1, events go 3" in text

    def test_a_component_has_no_budget_line(self):
        from djust.components.base import LiveComponent
        from djust.management.commands.djust_audit import _audit_class

        class Widget(LiveComponent):
            template = "<div></div>"

        audit = _audit_class(Widget, "LiveComponent")
        assert "query_budget" not in audit

    def test_an_invalid_budget_is_listed_as_invalid_with_its_reason(self):
        class Typo(LiveView):
            login_required = False
            template = "<div dj-root></div>"
            query_budget = {"mout": 1}

        audits = _audit_json(Typo)
        assert audits["Typo"]["query_budget"] is None
        assert "'mout'" in audits["Typo"]["query_budget_error"]


def test_the_config_key_is_declared_with_an_unset_default():
    assert "query_budget" in djust_config._defaults
    assert djust_config._defaults["query_budget"] is None
