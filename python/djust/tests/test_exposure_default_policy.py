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
from djust.live_view import EXPLICIT_EXPOSURE_GUIDE_URL, PersistentLiveView

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
    """``PersistentLiveView`` states legacy itself; an application cannot change that line."""
    assert PersistentLiveView.__dict__["exposure_policy"] == "legacy"
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")

        class Subclass(PersistentLiveView):
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
