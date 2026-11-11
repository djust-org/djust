"""V023 and the reserved names of ``params_form`` (#3706, follow-up to #3689).

``params_form`` is built by the framework with ``form_class(data=...)`` and
nothing else. Two declarations therefore fail on the FIRST REQUEST of the view,
long after ``manage.py check`` passed:

* a ``params_form`` that is not a form class raised ``ImproperlyConfigured``;
* a form whose ``__init__`` needs another argument (a user, a queryset, a
  tenant) raised ``TypeError`` from the framework's own call.

V023 (Error) reports both at check time. The three framework class attributes
this feature added, ``params_form``, ``http_status_of_state`` and
``state_attribute``, are also reserved names for ``exposure_policy = "explicit"``
state fields and are kept out of a legacy view's reactive state when an
instance assigns them.
"""

from __future__ import annotations

import pytest
from django import forms

from djust import LiveView
from djust._params_form import (
    construction_problem,
    form_class_problem,
    run_handle_params_sync,
)
from djust.checks.components import check_params_form_declarations


class Plain(forms.Form):
    page = forms.IntegerField(required=False, initial=1)


class NeedsUser(forms.Form):
    page = forms.IntegerField(required=False)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user


class NeedsKeywordOnly(forms.Form):
    def __init__(self, *args, tenant, **kwargs):
        super().__init__(*args, **kwargs)


class OptionalExtra(forms.Form):
    """An extra argument WITH a default is fine: the framework passes data= only."""

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)


class NoDataParameter(forms.Form):
    """An ``__init__`` that cannot take ``data=`` at all."""

    def __init__(self):
        super().__init__()


class NotAForm:
    """A class, but not a Django form."""


class GoodView(LiveView):
    template = "<div dj-root></div>"
    params_form = Plain


class UndeclaredView(LiveView):
    template = "<div dj-root></div>"


class NotAClassView(LiveView):
    template = "<div dj-root></div>"
    params_form = "myapp.forms.ListParams"  # the string a FormView-style API might take


class InstanceView(LiveView):
    template = "<div dj-root></div>"
    params_form = Plain()  # an instance, not the class


class WrongClassView(LiveView):
    template = "<div dj-root></div>"
    params_form = NotAForm


class NeedsUserView(LiveView):
    template = "<div dj-root></div>"
    params_form = NeedsUser


class NeedsKeywordOnlyView(LiveView):
    template = "<div dj-root></div>"
    params_form = NeedsKeywordOnly


class OptionalExtraView(LiveView):
    template = "<div dj-root></div>"
    params_form = OptionalExtra


class NoDataView(LiveView):
    template = "<div dj-root></div>"
    params_form = NoDataParameter


class InheritsBadView(NeedsUserView):
    """The declaration is inherited: the subclass is reported too."""


def _v023(*classes):
    labels = ["%s.%s" % (c.__module__, c.__qualname__) for c in classes]
    return [
        m
        for m in check_params_form_declarations(None)
        if any(m.msg.startswith(label + ".params_form ") for label in labels)
    ]


class TestV023:
    def test_a_good_declaration_is_silent(self):
        assert _v023(GoodView, UndeclaredView, OptionalExtraView) == []

    @pytest.mark.parametrize("view", [NotAClassView, InstanceView, WrongClassView])
    def test_a_params_form_that_is_not_a_form_class_is_an_error(self, view):
        (message,) = _v023(view)
        assert message.id == "djust.V023"
        assert message.level == 40  # ERROR: the first request raises ImproperlyConfigured
        assert "params_form" in message.msg
        assert "form class" in message.msg
        assert "ImproperlyConfigured" in message.hint

    def test_the_message_names_what_was_declared(self):
        (string,) = _v023(NotAClassView)
        assert "str" in string.msg
        (instance,) = _v023(InstanceView)
        assert "Plain" in instance.msg  # an instance of Plain, not the class

    def test_a_required_init_argument_is_an_error_naming_it(self):
        (message,) = _v023(NeedsUserView)
        assert message.id == "djust.V023"
        assert message.level == 40
        assert "user" in message.msg
        assert "data=" in message.msg

    def test_a_required_keyword_only_argument_is_found_too(self):
        (message,) = _v023(NeedsKeywordOnlyView)
        assert "tenant" in message.msg

    def test_an_init_that_cannot_take_data_is_an_error(self):
        (message,) = _v023(NoDataView)
        assert "data=" in message.msg

    def test_an_optional_extra_argument_is_fine(self):
        assert _v023(OptionalExtraView) == []

    def test_an_inherited_declaration_is_reported_for_the_subclass(self):
        assert len(_v023(InheritsBadView)) == 1

    def test_it_can_be_suppressed(self, settings):
        settings.DJUST_CONFIG = {"suppress_checks": ["V023"]}
        assert _v023(NeedsUserView, NotAClassView) == []


class TestTheCheckAgreesWithTheRuntime:
    """The check must judge a form the way the request path builds it.

    A check that called a form buildable which the runtime then failed to build
    (or the reverse) would be the drift this feature exists to avoid, so each
    verdict is replayed through the real runtime function.
    """

    @pytest.mark.parametrize(
        "form_class, buildable",
        [
            (Plain, True),
            (OptionalExtra, True),
            (NeedsUser, False),
            (NeedsKeywordOnly, False),
            (NoDataParameter, False),
        ],
    )
    def test_construction_verdict_matches_a_real_build(self, form_class, buildable):
        problem = construction_problem(form_class)
        assert (problem is None) is buildable, problem

        class Probe(LiveView):
            template = "<div dj-root></div>"
            params_form = form_class

            def handle_params(self, params, uri):
                pass

        view = Probe()
        if buildable:
            run_handle_params_sync(view, {}, "/")
        else:
            with pytest.raises(TypeError):
                run_handle_params_sync(view, {}, "/")

    @pytest.mark.parametrize("declared", ["x.Form", Plain(), NotAForm, 3])
    def test_a_non_form_is_judged_as_the_runtime_judges_it(self, declared):
        from django.core.exceptions import ImproperlyConfigured

        assert form_class_problem(declared) is not None

        class Probe(LiveView):
            template = "<div dj-root></div>"
            params_form = declared

        with pytest.raises(ImproperlyConfigured):
            run_handle_params_sync(Probe(), {}, "/")

    def test_a_form_class_has_no_problem(self):
        assert form_class_problem(Plain) is None
        assert form_class_problem(forms.Form) is None

    def test_a_signature_that_cannot_be_read_is_not_reported(self):
        class Opaque(forms.Form):
            pass

        # builtins and C types may have no introspectable signature; the check
        # must not turn "could not read it" into "it is broken".
        import inspect
        from unittest.mock import patch

        with patch.object(inspect, "signature", side_effect=ValueError("no signature")):
            assert construction_problem(Opaque) is None


class TestReservedNames:
    """``params_form``, ``http_status_of_state`` and ``state_attribute`` are framework names."""

    NAMES = ["params_form", "http_status_of_state", "state_attribute"]

    @pytest.mark.parametrize("name", NAMES)
    def test_an_explicit_state_field_cannot_use_the_name(self, name):
        from djust._exposure import ExposureContract, ExposureError, FieldExposure

        with pytest.raises(ExposureError, match="reserved"):
            ExposureContract("app.View", {name: FieldExposure()})

    def test_the_neighbouring_params_errors_stays_available(self):
        """``params_errors`` is deliberately NOT a class attribute (see #3689)."""
        from djust._exposure import ExposureContract, FieldExposure

        ExposureContract("app.View", {"params_errors": FieldExposure()})

    @pytest.mark.parametrize("name", NAMES)
    def test_the_names_are_in_the_framework_internal_set(self, name):
        from djust.live_view import _FRAMEWORK_INTERNAL_ATTRS

        assert name in _FRAMEWORK_INTERNAL_ATTRS

    @pytest.mark.parametrize("name", NAMES)
    def test_an_instance_assignment_is_not_reactive_state(self, name):
        """``self.state_attribute = ...`` in ``mount()`` must not become template state."""

        class Assigns(LiveView):
            template = "<div dj-root></div>"

            def mount(self, request, **kwargs):
                setattr(self, name, {"leak": 1} if name != "state_attribute" else "other")
                self.visible = 1

        from django.test import RequestFactory

        view = Assigns()
        view.mount(RequestFactory().get("/"))
        for state in (view.get_state(), view._capture_snapshot_state()):
            assert name not in state
            assert "visible" in state
