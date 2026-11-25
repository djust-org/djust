"""The default exposure policy is ``"explicit"`` (ADR-038; exposure-flip plan, PR 1).

Three places hold the default: the ``LiveView.exposure_policy`` class attribute,
the fallback in ``djust._exposure`` for an owner that has no policy at all, and
the inventory command's reading of a class without one. ``"legacy"`` is a
removed value: the tests of the legacy mechanism itself are gone (#4198).

This file's views leave the policy unset on purpose.
"""

import logging

import pytest
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust._exposure import _read_exposure_policy, states_exposure_policy
from djust._exposure_diagnostics import (
    PROTECTED_FAILURE,
    diagnostics_policy_allows,
    exception_details_allowed_for,
    log_failure_for,
)

# ------------------------------------------------------------------ #
# The three defaults
# ------------------------------------------------------------------ #


def test_the_liveview_default_is_explicit():
    assert LiveView.__dict__["exposure_policy"] == "explicit"

    class Unstated(LiveView):
        template = "<div dj-root>{{ secret }}</div>"

    assert "exposure_policy" not in Unstated.__dict__
    assert Unstated.exposure_policy == "explicit"
    assert _read_exposure_policy(Unstated()) == "explicit"


def test_an_unstated_view_renders_only_what_it_supplies():
    """The behaviour the default buys: an attribute is not template context."""

    class Unstated(LiveView):
        template = "<div dj-root>[{{ undeclared }}][{{ supplied }}]</div>"

        def mount(self, request, **kwargs):
            self.undeclared = "UNDECLARED_SENTINEL"
            self.supplied = "SUPPLIED_SENTINEL"

        def get_context_data(self, **kwargs):
            return {**super().get_context_data(**kwargs), "supplied": self.supplied}

    request = RequestFactory().get("/")
    view = Unstated()
    view.request = request
    view.mount(request)
    assert "UNDECLARED_SENTINEL" not in view.render()
    # The control: what the view does supply is rendered.
    assert "SUPPLIED_SENTINEL" in view.render()


@pytest.mark.parametrize("owner", [None, object(), type("Stub", (), {})()])
def test_an_owner_without_a_policy_reads_as_explicit(owner):
    assert _read_exposure_policy(owner) == "explicit"


def test_the_inventory_reads_a_class_without_a_policy_as_explicit():
    from djust.management._exposure_inventory import inventory_view

    class NotAView:
        pass

    class Unstated(LiveView):
        template = "<div dj-root></div>"

    assert inventory_view(NotAView)["policy"] == "explicit"
    assert inventory_view(Unstated)["policy"] == "explicit"


# ------------------------------------------------------------------ #
# Diagnostics: an owner that states no policy neither grants nor restricts
# ------------------------------------------------------------------ #


class _Explicit(LiveView):
    exposure_policy = "explicit"


class _PolicyThatRaises:
    @property
    def exposure_policy(self):
        raise AttributeError("POLICY_SECRET_SENTINEL")


class _Component:
    """Stands for a component or a formset class: an owner that is not a view."""


def test_states_exposure_policy():
    assert not states_exposure_policy(None)
    assert not states_exposure_policy(_Component())
    assert not states_exposure_policy(_Component)
    assert states_exposure_policy(_Explicit)
    assert states_exposure_policy(_PolicyThatRaises())  # stated, only unreadable
    assert states_exposure_policy(type("NonePolicy", (), {"exposure_policy": None})())


@override_settings(DEBUG=False)
def test_in_production_only_a_stated_nonlegacy_policy_restricts():
    assert diagnostics_policy_allows(None)
    assert diagnostics_policy_allows(_Component())
    assert not diagnostics_policy_allows(_Explicit())
    assert not diagnostics_policy_allows(_Explicit)
    assert not diagnostics_policy_allows(_PolicyThatRaises())
    assert not diagnostics_policy_allows(type("Bad", (), {"exposure_policy": "invalid"})())


@override_settings(DEBUG=False)
@pytest.mark.parametrize("bystander", [None, _Component()], ids=["no-view", "non-view"])
def test_a_policyless_owner_does_not_grant_an_explicit_views_failure(bystander, caplog):
    """Neutral is not a grant: the explicit owner beside it still restricts."""
    log = logging.getLogger("djust.tests.default_policy")
    with caplog.at_level(logging.ERROR, logger=log.name):
        log_failure_for(
            log, (bystander, _Explicit), ValueError("x"), "failed: %s", "DETAIL_SENTINEL"
        )
    assert "DETAIL_SENTINEL" not in caplog.text
    assert PROTECTED_FAILURE in caplog.text
    assert not exception_details_allowed_for((bystander, _Explicit))
    assert not exception_details_allowed_for((_Explicit, bystander))


# ------------------------------------------------------------------ #
# The silent-empty case has a signal: a DEBUG log line and djust.T027
# ------------------------------------------------------------------ #

UNSUPPLIED_LOGGER = "djust.exposure"


class _OneXStyle(LiveView):
    """A 1.x view: sets attributes, supplies nothing. States no policy."""

    template = "<div dj-root>[{{ count }}][{% if flag %}on{% endif %}][{{ typo }}]</div>"
    title = "Inventory"

    def mount(self, request, **kwargs):
        self.count = 7
        self.flag = True
        self._private = "PRIVATE_SENTINEL"
        self.unread = "UNREAD_VALUE_SENTINEL"


class _Supplies(_OneXStyle):
    exposure_policy = "explicit"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "count": self.count, "flag": self.flag}


def _get(view_class):
    from djust.tests.test_exposure_runtime import make_request

    response = view_class().get(make_request())
    assert response.status_code == 200
    return response.content.decode()


def _unsupplied_records(caplog, view_class):
    return [
        record
        for record in caplog.records
        if record.name == UNSUPPLIED_LOGGER and view_class.__qualname__ in record.getMessage()
    ]


@pytest.fixture
def fresh_reports():
    from djust import _exposure_unsupplied

    saved = dict(_exposure_unsupplied._REPORTED)
    _exposure_unsupplied._REPORTED.clear()
    yield
    _exposure_unsupplied._REPORTED.clear()
    _exposure_unsupplied._REPORTED.update(saved)


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_debug_names_what_a_1x_style_view_renders_empty_once(caplog, fresh_reports):
    with caplog.at_level(logging.WARNING, logger=UNSUPPLIED_LOGGER):
        html = _get(_OneXStyle)
        _get(_OneXStyle)  # a second render adds nothing
    assert "[][<!--dj-if-->][]" in html  # the silent-empty render the warning is about
    records = _unsupplied_records(caplog, _OneXStyle)
    messages = [record.getMessage() for record in records]
    # Once per name the template reads AND the view holds: not the typo
    # (nothing holds it), not the attribute the template never reads.
    assert sorted(m.split("{{ ")[1].split(" }}")[0] for m in messages) == ["count", "flag"]
    for message in messages:
        assert "get_context_data()" in message and "state()" in message
        assert "https://docs.djust.org/guides/migrating-to-2.0/" in message
    text = "\\n".join(messages)
    # Names only, never values.
    assert "7" not in text.replace("2.0", "")
    assert "SENTINEL" not in text
    # Parameterized, not pre-formatted (CodeQL js/py tainted-format; CLAUDE.md rule 4).
    assert all(record.args for record in records)


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_debug_is_quiet_for_a_view_that_supplies(caplog, fresh_reports):
    with caplog.at_level(logging.WARNING, logger=UNSUPPLIED_LOGGER):
        assert "[7][on][]" in _get(_Supplies)
    assert _unsupplied_records(caplog, _Supplies) == []


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_with_debug_off_nothing_is_logged_and_the_template_is_not_inspected(
    caplog, fresh_reports, monkeypatch
):
    def inspected(view, context):
        raise AssertionError("the template was inspected with DEBUG off")

    monkeypatch.setattr("djust._exposure_unsupplied.unsupplied_template_names", inspected)
    with caplog.at_level(logging.DEBUG, logger=UNSUPPLIED_LOGGER):
        assert "[][<!--dj-if-->][]" in _get(_OneXStyle)
    assert [r for r in caplog.records if r.name == UNSUPPLIED_LOGGER] == []


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_a_failing_diagnostic_does_not_break_the_render(caplog, fresh_reports, monkeypatch):
    def broken(view, context):
        raise RuntimeError("DIAGNOSTIC_SECRET_SENTINEL")

    monkeypatch.setattr("djust._exposure_unsupplied.unsupplied_template_names", broken)
    with caplog.at_level(logging.DEBUG, logger=UNSUPPLIED_LOGGER):
        assert "[][<!--dj-if-->][]" in _get(_OneXStyle)
    assert "SENTINEL" not in caplog.text


def _t027(view_class):
    from djust.checks.templates import check_unsupplied_template_names

    label = "%s.%s" % (view_class.__module__, view_class.__qualname__)
    return [
        message
        for message in check_unsupplied_template_names(None)
        if message.msg.startswith(label + " ")
    ]


def test_t027_reports_the_names_a_1x_style_view_holds_but_does_not_supply():
    messages = _t027(_OneXStyle)
    assert {m.id for m in messages} == {"djust.T027"}
    named = sorted(m.msg.split("reads '")[1].split("'")[0] for m in messages)
    # ``title`` is a class value the template does not read; ``typo`` is T018's.
    assert named == ["count", "flag"]
    for message in messages:
        assert "get_context_data()" in message.hint and "state()" in message.hint
        assert message.fix_hint and "get_context_data()" in message.fix_hint
        assert "SENTINEL" not in message.msg + message.hint


def test_t027_is_quiet_for_a_supplying_view_and_when_suppressed(settings):
    assert _t027(_Supplies) == []
    settings.DJUST_CONFIG = {"suppress_checks": ["T027"]}
    assert _t027(_OneXStyle) == []


def test_t027_follows_a_returned_context_skips_an_unfollowable_one_and_honours_noqa():
    class Unfollowable(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root>{{ count }}</div>"

        def mount(self, request, **kwargs):
            self.count = 0

        def _fill(self, context):
            context["count"] = self.count

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            self._fill(context)
            return context

    class Dynamic(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root>{{ count }}</div>"

        def mount(self, request, **kwargs):
            self.count = 0

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["count"] = self.count
            return context

    class Silenced(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root>{# djust_typecheck: noqa count #}{{ count }}</div>"

        def mount(self, request, **kwargs):
            self.count = 0

    assert _t027(Unfollowable) == []  # skipped: the key is written by a call it cannot follow
    assert _t027(Dynamic) == []  # followed: ``count`` is supplied
    assert _t027(Silenced) == []
