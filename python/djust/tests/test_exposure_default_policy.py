"""The default exposure policy is ``"explicit"`` (ADR-038; exposure-flip plan, PR 1).

Three places hold the default: the ``LiveView.exposure_policy`` class attribute,
the fallback in ``djust._exposure`` for an owner that has no policy at all, and
the inventory command's reading of a class without one. ``"legacy"`` stays a
working value for a class that states it, and stating it is deprecated.

This file's views leave the policy unset on purpose (``UNPINNED_ON_PURPOSE`` in
``tests/exposure_policy_pin_guard.py``).
"""

import logging
import os
import warnings

import pytest
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust._exposure import states_exposure_policy, uses_legacy_exposure
from djust._exposure_diagnostics import (
    PROTECTED_FAILURE,
    diagnostics_policy_allows,
    exception_details_allowed_for,
    log_failure_for,
)
from djust.live_view import EXPLICIT_EXPOSURE_GUIDE_URL

LEGACY_WARNING = r'exposure_policy = "legacy" \(stated on '


# ------------------------------------------------------------------ #
# The three defaults
# ------------------------------------------------------------------ #


def test_the_liveview_default_is_explicit():
    assert LiveView.__dict__["exposure_policy"] == "explicit"

    class Unstated(LiveView):
        template = "<div dj-root>{{ secret }}</div>"

    assert "exposure_policy" not in Unstated.__dict__
    assert Unstated.exposure_policy == "explicit"
    assert not uses_legacy_exposure(Unstated())


def test_an_unstated_view_renders_only_what_it_supplies():
    """The behaviour the default buys: an attribute is not template context."""

    class Unstated(LiveView):
        template = "<div dj-root>[{{ undeclared }}][{{ supplied }}]</div>"

        def mount(self, request, **kwargs):
            self.undeclared = "UNDECLARED_SENTINEL"
            self.supplied = "SUPPLIED_SENTINEL"

        def get_context_data(self, **kwargs):
            return {**super().get_context_data(**kwargs), "supplied": self.supplied}

    class Stated(Unstated):
        exposure_policy = "legacy"

    request = RequestFactory().get("/")
    explicit, legacy = Unstated(), Stated()
    for view in (explicit, legacy):
        view.request = request
        view.mount(request)
    assert "UNDECLARED_SENTINEL" not in explicit.render()
    assert "SUPPLIED_SENTINEL" in explicit.render()
    # The control: the same view under the stated legacy policy exports it.
    assert "UNDECLARED_SENTINEL" in legacy.render()


@pytest.mark.parametrize("owner", [None, object(), type("Stub", (), {})()])
def test_an_owner_without_a_policy_is_not_legacy(owner):
    assert not uses_legacy_exposure(owner)


def test_the_inventory_reads_a_class_without_a_policy_as_explicit():
    from djust.management._exposure_inventory import inventory_view

    class NotAView:
        pass

    class Unstated(LiveView):
        template = "<div dj-root></div>"

    assert inventory_view(NotAView)["policy"] == "explicit"
    assert inventory_view(Unstated)["policy"] == "explicit"


# ------------------------------------------------------------------ #
# Stating "legacy" still works, and warns once per stating class
# ------------------------------------------------------------------ #


def _legacy_warnings(record):
    return [w for w in record if "exposure_policy" in str(w.message)]


def test_stating_legacy_warns_at_the_class_statement():
    with pytest.warns(DeprecationWarning, match=LEGACY_WARNING) as record:

        class Stated(LiveView):
            exposure_policy = "legacy"

    (warning,) = _legacy_warnings(record)
    message = str(warning.message)
    assert "%s.%s" % (Stated.__module__, Stated.__qualname__) in message
    assert "deprecated since djust 2.0" in message
    assert "removed no earlier than 2.0.0" in message
    assert 'Use exposure_policy = "explicit"' in message
    assert EXPLICIT_EXPOSURE_GUIDE_URL in message
    # It points at the class statement, the line to change.
    assert os.path.basename(warning.filename) == os.path.basename(__file__)
    assert uses_legacy_exposure(Stated())


def test_the_warning_is_once_per_stating_class_not_per_instance_or_subclass():
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")

        class Stated(LiveView):
            exposure_policy = "legacy"

        class Inherits(Stated):
            pass

        for _ in range(3):
            Stated()
            Inherits()
    assert len(_legacy_warnings(record)) == 1

    with pytest.warns(DeprecationWarning, match=LEGACY_WARNING) as record:

        class Restates(Inherits):
            exposure_policy = "legacy"

    assert len(_legacy_warnings(record)) == 1


def test_a_policy_assigned_after_the_class_statement_warns_on_first_construction():
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        late = type("Late", (LiveView,), {"exposure_policy": "explicit"})
        late.exposure_policy = "legacy"
        assert _legacy_warnings(record) == []
        late()
        late()
    (warning,) = _legacy_warnings(record)
    assert "Late" in str(warning.message)


def test_explicit_and_unstated_views_do_not_warn():
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")

        class Unstated(LiveView):
            pass

        class Stated(LiveView):
            exposure_policy = "explicit"

        Unstated()
        Stated()
    assert _legacy_warnings(record) == []


def test_a_legacy_statement_shipped_in_djust_does_not_warn_through_a_subclass():
    """``TtydTerminalView`` states legacy itself; an application cannot change that line."""
    from djust.components.ttyd.terminal import TtydTerminalView

    assert TtydTerminalView.__dict__["exposure_policy"] == "legacy"
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")

        class Subclass(TtydTerminalView):
            pass

        Subclass()
    assert _legacy_warnings(record) == []
    assert uses_legacy_exposure(Subclass())


def test_the_suite_filter_hides_exactly_this_warning():
    """pyproject's filterwarnings entry: the 1,850 pinned test views stay quiet,
    and nothing else is hidden by it."""
    import re
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    filters = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"][
        "filterwarnings"
    ]
    (entry,) = [f for f in filters if "exposure_policy" in f]
    action, pattern, category = entry.split(":")
    assert (action, category) == ("ignore", "DeprecationWarning")

    with pytest.warns(DeprecationWarning) as record:

        class Stated(LiveView):
            exposure_policy = "legacy"

    (warning,) = _legacy_warnings(record)
    assert re.match(pattern, str(warning.message))
    assert not re.match(pattern, "@event is deprecated since djust 1.0")


# ------------------------------------------------------------------ #
# Diagnostics: an owner that states no policy neither grants nor restricts
# ------------------------------------------------------------------ #


class _Explicit(LiveView):
    exposure_policy = "explicit"


with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)

    class _Legacy(LiveView):
        exposure_policy = "legacy"


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
    assert states_exposure_policy(_Legacy())
    assert states_exposure_policy(_PolicyThatRaises())  # stated, only unreadable
    assert states_exposure_policy(type("NonePolicy", (), {"exposure_policy": None})())


@override_settings(DEBUG=False)
def test_in_production_only_a_stated_nonlegacy_policy_restricts():
    assert diagnostics_policy_allows(None)
    assert diagnostics_policy_allows(_Component())
    assert diagnostics_policy_allows(_Legacy())
    assert not diagnostics_policy_allows(_Explicit())
    assert not diagnostics_policy_allows(_Explicit)
    assert not diagnostics_policy_allows(_PolicyThatRaises())
    assert not diagnostics_policy_allows(type("Bad", (), {"exposure_policy": "invalid"})())


@override_settings(DEBUG=False)
@pytest.mark.parametrize("bystander", [None, _Component()], ids=["no-view", "non-view"])
def test_a_policyless_owner_does_not_hide_a_legacy_views_failure(bystander, caplog):
    """A legacy view's failure handled while the view slot is empty (a failed
    constructor, a failed mount) or beside a component keeps its detail."""
    log = logging.getLogger("djust.tests.default_policy")
    with caplog.at_level(logging.ERROR, logger=log.name):
        log_failure_for(log, (_Legacy, bystander), ValueError("x"), "failed: %s", "DETAIL_SENTINEL")
    assert "DETAIL_SENTINEL" in caplog.text
    assert exception_details_allowed_for((_Legacy, bystander))


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


def test_warnings_as_errors_fails_every_construction_not_only_the_first():
    """Review of #4049: the class was recorded before the warning was raised,
    so under ``-W error`` the first construction raised and later ones passed."""

    class StatesItOnAMixin:  # a plain mixin: no class-statement warning of ours
        exposure_policy = "legacy"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        view_class = type("LateLegacy", (StatesItOnAMixin, LiveView), {})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for _ in range(2):
            with pytest.raises(DeprecationWarning, match=LEGACY_WARNING):
                view_class()
    # Once it has been raised as a warning, it is not raised again.
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        view_class()
        view_class()
    (warning,) = _legacy_warnings(record)
    # It points at the code that constructed the view, not at djust.
    assert os.path.basename(warning.filename) == os.path.basename(__file__)


def test_warnings_as_errors_at_the_class_statement_fails_each_definition():
    for _ in range(2):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with pytest.raises(DeprecationWarning, match=LEGACY_WARNING):
                type("Stated", (LiveView,), {"exposure_policy": "legacy"})


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


with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)

    class _OneXStyleLegacy(_OneXStyle):
        exposure_policy = "legacy"


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
def test_debug_is_quiet_for_a_view_that_supplies_and_for_a_legacy_view(caplog, fresh_reports):
    with caplog.at_level(logging.WARNING, logger=UNSUPPLIED_LOGGER):
        assert "[7][on][]" in _get(_Supplies)
        assert "[7][on][]" in _get(_OneXStyleLegacy)
    assert _unsupplied_records(caplog, _Supplies) == []
    assert _unsupplied_records(caplog, _OneXStyleLegacy) == []


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


def test_t027_is_quiet_for_a_supplying_view_a_legacy_view_and_when_suppressed(settings):
    assert _t027(_Supplies) == []
    assert _t027(_OneXStyleLegacy) == []
    settings.DJUST_CONFIG = {"suppress_checks": ["T027"]}
    assert _t027(_OneXStyle) == []


def test_t027_skips_a_get_context_data_it_cannot_follow_and_honours_noqa():
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

    assert _t027(Dynamic) == []
    assert _t027(Silenced) == []
