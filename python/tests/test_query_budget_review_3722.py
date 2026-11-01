"""Review findings on PR #3722 (#3701): the three readers of ``query_budget`` must agree.

The smoke test, the run-time meter and ``djust_audit`` each read ``query_budget``
their own way, so the same declaration got three verdicts. Every case here is a
finding of the fresh-context review, written first and seen red on the branch:

1. a budget on an ``async def`` / ``@background`` handler was accepted and
   measured nothing, and the smoke test passed it;
2. ``djust_audit`` printed invalid budgets as valid and ``--json`` crashed;
3. smoke counted an event's object-permission queries and run time did not, and a
   misspelt handler name raised in smoke but was silent at run time;
5. an invalid ``LIVEVIEW_CONFIG["query_budget"]`` failed at request time.

Plus the structural pin: one reader (``declared_budget``) for the attribute.
"""

from __future__ import annotations

import ast
import itertools
import json
import logging
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import path

from djust import LiveView
from djust.decorators import background, event_handler
from djust.testing import LiveViewSmokeTest, LiveViewTestClient

LOGGER = "djust._query_budget"


def _queries(n):
    for _ in range(n):
        User.objects.count()


class Base(LiveView):
    login_required = False
    template = "<div dj-root>{{ n }}</div>"

    def mount(self, request, **kwargs):
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"n": 1}

    @event_handler()
    def plain(self, **kwargs):
        _queries(9)

    @event_handler()
    async def ahandler(self, **kwargs):
        await sync_to_async(_queries)(9)

    @event_handler()
    @background
    def bg(self, **kwargs):
        _queries(9)

    @event_handler()
    def noop(self, **kwargs):
        pass


_names = itertools.count()


def view(budget, base=Base, **attrs):
    """A subclass with a name of its own (the checks find views by walking subclasses)."""
    return type(
        f"V{next(_names)}", (base,), {"__module__": __name__, "query_budget": budget, **attrs}
    )


def _smoke(views, test="test_smoke_event_queries", **attrs):
    cls = type("Smoke", (LiveViewSmokeTest,), attrs)
    with patch("djust.testing._discover_views", return_value=list(views)):
        getattr(cls(), test)()


def _smoke_fails(views, test="test_smoke_event_queries", **attrs):
    with pytest.raises(AssertionError) as exc:
        _smoke(views, test, **attrs)
    return str(exc.value)


@pytest.fixture
def warned(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    return lambda: [r.getMessage() for r in caplog.records if r.name == LOGGER]


@pytest.fixture(autouse=True)
def _reset():
    from djust import _query_budget

    _query_budget._reset_for_tests()
    yield
    _query_budget._reset_for_tests()


# ------------------------------------------------------------ finding 1 --------


@pytest.mark.django_db
class TestAnUnmeasurableHandlerIsNeverAccepted:
    def test_an_async_handler_named_in_events_is_an_error(self):
        msg = _smoke_fails([view({"events": {"ahandler": 1}})])
        assert "ahandler" in msg and "async def" in msg

    def test_a_background_handler_named_in_events_is_an_error(self):
        msg = _smoke_fails([view({"events": {"bg": 1}})])
        assert "bg" in msg and "@background" in msg

    def test_the_other_smoke_test_reports_it_too(self):
        msg = _smoke_fails([view({"events": {"ahandler": 1}})], "test_smoke_queries")
        assert "ahandler" in msg and "ValueError" in msg

    def test_under_the_broad_event_key_they_are_a_warning_and_the_rest_is_measured(self):
        with pytest.warns(
            UserWarning, match=r"not measured for .*ahandler.*bg|not measured for .*bg.*ahandler"
        ):
            msg = _smoke_fails([view({"event": 1})])
        assert "plain: ran 9 queries" in msg  # the sync handler is still judged

    def test_a_synchronous_handler_is_still_accepted(self):
        _smoke([view({"events": {"plain": 9, "noop": 0}})])

    def test_the_check_reports_it_at_startup(self):
        from djust.checks.components import check_query_budget_declarations

        V = view({"events": {"ahandler": 1}})
        messages = [m for m in check_query_budget_declarations(None) if V.__qualname__ in m.msg]
        assert len(messages) == 1 and messages[0].id == "djust.V024"
        assert "async def" in messages[0].msg

    def test_run_time_says_so_once_and_measures_nothing(self, warned):
        V = view({"events": {"ahandler": 1}, "mount": 0}, spend=1)
        LiveViewTestClient(V).mount()
        LiveViewTestClient(V).mount()
        assert len(warned()) == 1 and "ahandler" in warned()[0]


# ------------------------------------------------------------ finding 2 --------


def _audit(*classes):
    from djust.management.commands.djust_audit import _audit_class

    audits = [_audit_class(c, "LiveView") for c in classes]
    out = StringIO()
    with patch(
        "djust.management.commands.djust_audit.Command._collect_audits", return_value=audits
    ):
        call_command("djust_audit", "--json", stdout=out)
    data = json.loads(out.getvalue())
    pretty = StringIO()
    with patch(
        "djust.management.commands.djust_audit.Command._collect_audits", return_value=audits
    ):
        call_command("djust_audit", stdout=pretty)
    return {a["class"].rsplit(".", 1)[1]: a for a in data["audits"]}, pretty.getvalue()


class TestTheAuditUsesTheSameParser:
    @pytest.mark.parametrize(
        "bad",
        [
            True,
            -1,
            "3",
            {"event": True},
            {"event": "3"},
            {"events": 5},
            {"events": {"typo": 1}},
            {"events": {"ahandler": 1}},
            {"events": {"bg": 1}},
            {"mout": 1},
        ],
    )
    def test_an_invalid_budget_is_listed_as_invalid_not_as_a_budget(self, bad):
        V = view(bad)
        audits, pretty = _audit(V)
        audit = audits[V.__name__]
        assert audit["query_budget"] is None
        assert audit["query_budget_error"]
        assert "Query budget: invalid" in pretty

    @pytest.mark.parametrize(
        "bad", [{"events": {Base.plain: 1}}, {"mount": {1}}, {"mount": object()}]
    )
    def test_a_non_json_declaration_does_not_crash_the_json_audit(self, bad):
        V = view(bad)
        audits, _ = _audit(V)
        assert audits[V.__name__]["query_budget_error"]

    def test_one_bad_view_does_not_hide_the_good_ones(self):
        audits, _ = _audit(
            view({"events": {Base.plain: 1}}),
            type("G", (Base,), {"__module__": __name__, "query_budget": 4}),
        )
        assert audits["G"]["query_budget"] == 4 and "query_budget_error" not in audits["G"]

    def test_a_valid_budget_is_listed_as_declared(self):
        V = view({"mount": 2, "events": {"plain": 3}})
        audits, pretty = _audit(V)
        assert audits[V.__name__]["query_budget"] == {"mount": 2, "events": {"plain": 3}}
        assert "Query budget: mount 2, events plain 3" in pretty


# ------------------------------------------------------------ finding 3 --------


class WithObject(Base):
    """``get_object`` queries; it is re-checked on every event (the consumer's gate)."""

    def get_object(self):
        _queries(2)
        return None

    def has_object_permission(self, request, obj):
        return True


@pytest.mark.django_db
class TestOneRuleForWhatAnEventCounts:
    def test_the_object_permission_recheck_is_not_the_handlers_spend(self, warned):
        V = view({"events": {"noop": 0}}, base=WithObject)
        client = LiveViewTestClient(V).mount()
        client.send_event("noop")
        assert warned() == []  # run time: the handler's own queries
        _smoke([V])  # smoke agrees

    def test_a_handler_that_does_spend_is_over_in_both(self, warned):
        V = view({"events": {"plain": 5}}, base=WithObject)
        LiveViewTestClient(V).mount().send_event("plain")
        assert any("event plain ran 9 queries" in m for m in warned())
        assert "plain: ran 9 queries" in _smoke_fails([V])

    def test_a_misspelt_handler_name_is_reported_at_run_time_once(self, warned):
        V = view({"events": {"typo_handler": 1}})
        LiveViewTestClient(V).mount()
        LiveViewTestClient(V).mount()
        assert len(warned()) == 1 and "typo_handler" in warned()[0]

    def test_and_by_the_startup_check(self):
        from djust.checks.components import check_query_budget_declarations

        V = view({"events": {"typo_handler": 1}})
        messages = [m for m in check_query_budget_declarations(None) if V.__qualname__ in m.msg]
        assert len(messages) == 1 and "typo_handler" in messages[0].msg

    def test_a_valid_budget_is_silent_at_startup(self):
        from djust.checks.components import check_query_budget_declarations

        V = view({"mount": 1, "events": {"plain": 2}})
        assert [m for m in check_query_budget_declarations(None) if V.__qualname__ in m.msg] == []

    def test_the_check_can_be_suppressed(self, settings):
        from djust.checks.components import check_query_budget_declarations

        settings.DJUST_CONFIG = {"suppress_checks": ["V024"]}
        V = view({"mout": 1})
        assert [m for m in check_query_budget_declarations(None) if V.__qualname__ in m.msg] == []


# ------------------------------------------------------------ finding 5 --------


@pytest.mark.django_db
class TestAnInvalidConfigValue:
    @pytest.mark.parametrize("bad", [True, "WARN", 1, "errors"])
    def test_the_startup_check_reports_it(self, bad):
        from djust.checks.configuration import check_configuration

        with override_settings(LIVEVIEW_CONFIG={"query_budget": bad}):
            ids = [m.id for m in check_configuration(None)]
        assert "djust.C029" in ids

    @pytest.mark.parametrize("good", [None, "off", "warn", "error"])
    def test_a_valid_value_passes(self, good):
        from djust.checks.configuration import check_configuration

        with override_settings(LIVEVIEW_CONFIG={"query_budget": good}):
            assert "djust.C029" not in [m.id for m in check_configuration(None)]

    @pytest.mark.parametrize("bad", [True, "WARN", 1])
    def test_a_request_never_fails_over_it(self, bad, warned):
        """The request path degrades to ``warn`` and says so once."""
        V = view({"mount": 0}, spend=1)
        with override_settings(LIVEVIEW_CONFIG={"query_budget": bad}):
            LiveViewTestClient(V).mount()
            LiveViewTestClient(V).mount()
        messages = warned()
        assert sum("LIVEVIEW_CONFIG['query_budget']" in m for m in messages) == 1

    def test_the_check_can_be_suppressed(self, settings):
        from djust.checks.configuration import check_configuration

        settings.DJUST_CONFIG = {"suppress_checks": ["C029"]}
        settings.LIVEVIEW_CONFIG = {"query_budget": True}
        assert "djust.C029" not in [m.id for m in check_configuration(None)]


# ------------------------------------------------------------ one reader -------


class TestOneReaderOfTheAttribute:
    """A pin on the sink: only ``_query_budget.py`` reads ``query_budget`` off a view."""

    ALLOWED = {"_query_budget.py"}

    @staticmethod
    def _reads(source):
        found = []
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "query_budget"
                and isinstance(node.ctx, ast.Load)
            ):
                found.append((node.lineno, "attribute"))
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("getattr", "hasattr")
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "query_budget"
            ):
                found.append((node.lineno, node.func.id))
        return sorted(found)

    def test_only_the_shared_reader_touches_the_attribute(self):
        import djust

        root = Path(djust.__file__).parent
        offenders = []
        scanned = 0
        for module in sorted(root.rglob("*.py")):
            rel = module.relative_to(root).as_posix()
            if rel.startswith("tests/") or "/tests/" in rel or rel in self.ALLOWED:
                continue
            scanned += 1
            for line, kind in self._reads(module.read_text()):
                offenders.append("%s:%d %s" % (rel, line, kind))
        assert scanned > 100
        assert offenders == [], offenders

    def test_the_scan_flags_each_shape_a_new_reader_could_use(self):
        source = (
            "def f(cls, view):\n"
            "    a = cls.query_budget\n"
            "    b = getattr(view, 'query_budget', None)\n"
            "    c = hasattr(view, 'query_budget')\n"
            "    cls.query_budget = 3\n"
        )
        assert [kind for _, kind in self._reads(source)] == [
            "attribute",
            "getattr",
            "hasattr",
        ]

    def test_the_readers_that_must_agree_all_call_the_shared_one(self):
        import djust

        root = Path(djust.__file__).parent
        for rel in ("testing.py", "management/commands/djust_audit.py", "checks/components.py"):
            assert "declared_budget" in (root / rel).read_text(), rel


# ------------------------------------------- documented limits (findings 4, 6) ----

GET_CALLS: list = []


class CountedGet(LiveView):
    """Overrides ``get_context_data`` WITHOUT calling super: the review's case."""

    login_required = False
    template = "<div dj-root>{{ n }}</div>"

    def mount(self, request, **kwargs):
        self.n = 1

    def get_context_data(self, **kwargs):
        GET_CALLS.append("override")
        return {"n": self.n}


class CountedGetSuper(LiveView):
    """Overrides it and calls ``super()``: the framework's own short-circuit applies inside."""

    login_required = False
    template = "<div dj-root>{{ n }}</div>"

    def mount(self, request, **kwargs):
        self.n = 1

    def get_context_data(self, **kwargs):
        GET_CALLS.append("override")
        return super().get_context_data(**kwargs)


class SpendingGet(CountedGet):
    """Two queries per ``get_context_data`` pass, against a render budget of 2."""

    query_budget = {"render": 2}

    def get_context_data(self, **kwargs):
        GET_CALLS.append("spend")
        _queries(2)
        return {"n": 1}


urlpatterns = [
    path("counted/", CountedGet.as_view()),
    path("countedsuper/", CountedGetSuper.as_view()),
    path("spending/", SpendingGet.as_view()),
]


@pytest.mark.django_db
class TestWhatThePhasesCountOnTheGet:
    """Measured, not assumed: the numbers the budget docs quote (finding 4).

    These pin FRAMEWORK behaviour the docs describe, not this PR's: if the double
    evaluation is fixed, update them together with the docs sentence.
    """

    @pytest.fixture(autouse=True)
    def _urls(self, settings):
        settings.ROOT_URLCONF = __name__
        settings.ALLOWED_HOSTS = ["testserver"]
        GET_CALLS.clear()
        yield
        GET_CALLS.clear()

    def test_an_overriding_get_context_data_runs_twice_on_one_get(self):
        assert Client().get("/counted/").status_code == 200
        assert len(GET_CALLS) == 2

    def test_also_when_the_override_calls_super(self):
        assert Client().get("/countedsuper/").status_code == 200
        assert len(GET_CALLS) == 2  # the override runs twice; only super() short-circuits

    def test_no_preloaded_context_reaches_the_second_pass(self):
        from djust.mixins.rust_bridge import RustBridgeMixin

        seen = []
        real = RustBridgeMixin._sync_state_to_rust

        def spy(self, preloaded_context=None):
            seen.append(preloaded_context)
            return real(self, preloaded_context=preloaded_context)

        with patch.object(RustBridgeMixin, "_sync_state_to_rust", spy):
            Client().get("/counted/")
        assert seen and all(context is None for context in seen)

    def test_the_budget_counts_each_pass_separately(self, warned):
        """So a GET can spend twice a ``render`` number without a warning."""
        assert Client().get("/spending/").status_code == 200
        assert GET_CALLS.count("spend") == 2
        assert warned() == []  # 2 queries per pass, within a render budget of 2


@pytest.mark.django_db
class TestWhatIsNotMetered:
    """The "not measured" list of the docs, each item shown to be true (finding 6)."""

    def test_only_the_four_phase_steps_are_metered(self):
        """``on_mount`` hooks and ``connected()`` / ``disconnected()`` run in ``lifecycle``."""
        from djust._query_budget import hop

        V = view({"mount": 0, "render": 0, "event": 0})
        for step in ("lifecycle", "auth", "hook", "async", "tick"):
            assert hop(V(), step, lambda: None) is None, step
        for step in ("mount", "handle_params", "render", "event"):
            assert hop(V(), step, lambda: None) is not None, step

    def test_a_step_nested_in_another_is_the_outer_steps_spend(self, warned):
        """A ``live_render`` child's ``get_context_data`` runs inside the parent's step."""
        from djust.sync_turn import run_sync_step

        child = view({"render": 0})()
        parent = view({"event": 99})()

        def parent_event():
            run_sync_step(child, "render", lambda: _queries(3))

        run_sync_step(parent, "event", parent_event)
        assert warned() == []  # the child's render budget of 0 is never judged

    def test_the_child_is_judged_on_its_own_when_it_is_the_outer_step(self, warned):
        from djust.sync_turn import run_sync_step

        child = view({"render": 0})()
        run_sync_step(child, "render", lambda: _queries(3))
        assert any("render ran 3 queries" in m for m in warned())


class TestTheSmokeTestMethodsAreCountedFromTheSource:
    """The guide says how many test methods ``LiveViewSmokeTest`` generates (a nit)."""

    def test_the_documented_count_is_the_real_one(self):
        import re

        root = Path(__file__).resolve().parents[2]
        text = (root / "docs/testing/TESTING_SMOKE_FUZZ.md").read_text(encoding="utf-8")
        match = re.search(r"generates (\w+) test methods", text)
        assert match, "the guide no longer states a count"
        words = {"four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
        real = [n for n in vars(LiveViewSmokeTest) if n.startswith("test_")]
        assert words[match.group(1)] == len(real), real
        for name in real:  # and every one has a row in the table
            assert f"`{name}`" in text, name


@pytest.mark.django_db
class TestAViewThatCannotBeTrackedIsSaidOnce:
    def test_unhashable_view_logs_and_is_not_measured(self, warned):
        class Unhashable(Base):
            __hash__ = None  # type: ignore[assignment]
            query_budget = {"mount": 0}

            def mount(self, request, **kwargs):
                _queries(2)
                self.n = 0

        for _ in range(2):
            LiveViewTestClient(Unhashable).mount()
        messages = warned()
        assert len([m for m in messages if "cannot be weakly referenced or hashed" in m]) == 1
        assert not any("over its query_budget" in m for m in messages)


class Hostile:
    """A declaration value whose ``repr`` raises: a message must never crash the audit."""

    def __repr__(self):
        raise RuntimeError("no repr for you")


class TestAMessageNeverCrashesOnTheValueItQuotes:
    def test_the_parser_quotes_a_hostile_value_safely(self):
        from djust._query_budget import parse_budget

        with pytest.raises(ValueError, match="Hostile object"):
            parse_budget({"mount": Hostile()})
        with pytest.raises(ValueError, match="Hostile object"):
            parse_budget(Hostile())
        with pytest.raises(ValueError, match="not supported"):
            parse_budget({Hostile(): 1})

    def test_an_enormous_int_is_quoted_without_crashing(self):
        from djust._query_budget import parse_budget

        # ``repr`` of an int over 4300 digits raises ValueError; the message must not.
        with pytest.raises(ValueError, match="non-negative int"):
            parse_budget({"mount": -(10**5000)})

    def test_the_audit_survives_a_hostile_declaration(self):
        V = view({"mount": Hostile()})
        audits, pretty = _audit(V)
        assert audits[V.__name__]["query_budget"] is None
        assert "Hostile object" in audits[V.__name__]["query_budget_error"]
        assert "Query budget: invalid" in pretty


@pytest.mark.django_db
class TestUrlChangeJoinsAMountPhase:
    """Documented on ``LiveViewTestClient.url_change``: no render between = one phase."""

    def test_two_url_changes_without_a_render_are_one_phase(self, warned):
        class Params(Base):
            def handle_params(self, params, uri):
                _queries(1)

        client = LiveViewTestClient(view({"mount": 1}, base=Params)).mount()
        client.render()
        client.url_change({"a": "1"})
        assert warned() == []  # 1 query in its own phase
        client.url_change({"a": "2"})
        assert len(warned()) == 1 and "at least 2 queries" in warned()[0]
        client.render()
        client.url_change({"a": "3"})  # a render closes it: a fresh phase of 1
        assert len(warned()) == 1


@pytest.mark.django_db
class TestTheEventMeasurementDoesNotLeakBetweenCalls:
    def test_last_event_queries_is_per_call(self):
        client = LiveViewTestClient(view(None)).mount()
        client._measure_event_queries = True
        client.send_event("plain")
        assert client.last_event_queries == 9
        client.send_event("nonexistent", raise_on_missing=False)  # returns before the handler
        assert client.last_event_queries is None

    def test_it_stays_unset_unless_asked_for(self):
        client = LiveViewTestClient(view(None)).mount()
        client.send_event("plain")
        assert client.last_event_queries is None

    def test_the_broad_event_key_never_calls_an_async_handler(self):
        """It is skipped, not called: a coroutine that is never awaited is a bug of its own."""
        import gc
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _smoke_fails([view({"event": 1})])
            gc.collect()
        never_awaited = [str(w.message) for w in caught if "was never awaited" in str(w.message)]
        assert never_awaited == []
