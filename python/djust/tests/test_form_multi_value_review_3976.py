"""Review findings on PR #3976 that live on the server side or in a renderer.

* ``TestHttpEnvelope``: why the client never sends a button named ``event`` as a field. The HTTP
  fallback posts the flat params; ``post()`` reads a flat body that contains ``event`` as its
  ``{"event", "params"}`` envelope, so that one key re-routes the call. The client side of the
  exclusion is ``tests/js/form_events_review_3976.test.js`` (the exact bytes, both transports).
* ``TestDjChangeValue``: what a handler receives from a ``dj-change`` on a same-named checkbox
  group, now a LIST (owner decision on #3959): pinned so the change is visible.
* ``TestRenderKeepsTheList``: the renderers that draw a ``MultipleChoiceField`` must tick the boxes
  of the list the multi-value fix makes ``form_data`` hold, through the real ``{% dj_form %}`` tag in
  BOTH engines, or the user's choice un-ticks on the first re-render.
"""

import re

import pytest
from django import forms

from djust import LiveView
from djust.decorators import event_handler
from djust.validation import validate_handler_params, validated_call_arguments

RAN: list = []


class EnvelopeView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><span>{{ n }}</span></div>"

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler(parameter_policy="strict")
    def save(self, **form_data):
        RAN.append(("save", dict(form_data)))

    @event_handler(parameter_policy="strict")
    def dangerous(self, **form_data):
        RAN.append(("dangerous", dict(form_data)))


@pytest.mark.django_db
class TestHttpEnvelope:
    def _post(self, body):
        from djust.tests.test_http_parameter_contracts import _get, _post

        RAN.clear()
        request, _html = _get(EnvelopeView)
        return _post(EnvelopeView, request, "save", body)

    def test_a_flat_body_with_event_reroutes_the_call_which_is_why_the_client_never_sends_it(self):
        status, _ = self._post({"title": "T", "event": "dangerous"})
        assert status == 200
        assert [name for name, _ in RAN] == ["dangerous"]

    def test_control_an_ordinary_flat_field_runs_the_header_event(self):
        status, _ = self._post({"title": "T", "intent": "draft"})
        assert status == 200
        assert RAN == [("save", {"title": "T", "intent": "draft"})]


class TestDjChangeValue:
    class View(LiveView):
        exposure_policy = "legacy"

        @event_handler(parameter_policy="legacy")
        def toggle_untyped(self, value=False, **kw):
            return value

        @event_handler(parameter_policy="legacy")
        def toggle_bool(self, value: bool = False, **kw):
            return value

        @event_handler(parameter_policy="strict")
        def toggle_strict(self, value: bool, field: str):
            return value

    GROUP = {"value": ["a", "b"], "field": "done"}

    def test_an_untyped_legacy_handler_receives_the_list_which_is_truthy(self):
        view = self.View()
        result = validate_handler_params(view.toggle_untyped, dict(self.GROUP), "toggle_untyped")
        assert result["valid"]
        args, kwargs = validated_call_arguments(result)
        got = view.toggle_untyped(*args, **kwargs)
        assert got == ["a", "b"]
        assert bool(got)

    def test_an_empty_group_is_a_falsy_list(self):
        view = self.View()
        result = validate_handler_params(
            view.toggle_untyped, {"value": [], "field": "done"}, "toggle_untyped"
        )
        args, kwargs = validated_call_arguments(result)
        assert view.toggle_untyped(*args, **kwargs) == []

    def test_a_legacy_bool_handler_is_rejected_loudly(self):
        result = validate_handler_params(self.View().toggle_bool, dict(self.GROUP), "toggle_bool")
        assert not result["valid"]
        assert "expected bool, got list" in result["error"]

    def test_a_strict_bool_handler_is_rejected(self):
        result = validate_handler_params(
            self.View().toggle_strict, dict(self.GROUP), "toggle_strict"
        )
        assert not result["valid"]


class TagsForm(forms.Form):
    tags = forms.MultipleChoiceField(
        choices=[("x", "X"), ("y", "Y"), ("z", "Z")], widget=forms.CheckboxSelectMultiple
    )
    colors = forms.MultipleChoiceField(
        choices=[("r", "R"), ("g", "G"), ("b", "B")], widget=forms.SelectMultiple
    )


def render(engine, form):
    from djust.tests.test_components_csp_aria import _django, _rust

    src = "{% load djust_components %}{% dj_form form=f %}"
    return (_django if engine == "django" else _rust)(src, {"f": form})


def drawn(html, attr):
    """The ``value`` of every ``<input>`` / ``<option>`` that carries the boolean ``attr``."""
    return re.findall(r'value="(\w+)"[^>]*?\b%s\b' % attr, html)


@pytest.mark.parametrize("engine", ["django", "native"])
class TestRenderKeepsTheList:
    def test_checkbox_group_ticks_every_member_of_the_list(self, engine):
        html = render(engine, TagsForm(data={"tags": ["x", "y"], "colors": ["r"]}))
        assert drawn(html, "checked") == ["x", "y"]

    def test_select_multiple_selects_every_member_of_the_list(self, engine):
        html = render(engine, TagsForm(data={"tags": ["x"], "colors": ["r", "b"]}))
        assert drawn(html, "selected") == ["r", "b"]

    def test_an_empty_list_ticks_nothing(self, engine):
        html = render(engine, TagsForm(data={"tags": [], "colors": []}))
        assert drawn(html, "checked") == []
        assert drawn(html, "selected") == []

    def test_one_member_is_a_list_of_one(self, engine):
        html = render(engine, TagsForm(data={"tags": ["z"], "colors": ["g"]}))
        assert drawn(html, "checked") == ["z"]
        assert drawn(html, "selected") == ["g"]

    def test_a_comma_string_still_ticks_each_part_of_a_checkbox_group(self, engine):
        html = render(engine, TagsForm(data={"tags": "x,y", "colors": "r"}))
        assert drawn(html, "checked") == ["x", "y"]
        assert drawn(html, "selected") == ["r"]

    def test_the_round_trip_the_client_now_makes_survives_a_re_render(self, engine):
        """dj-change -> ``FormMixin.validate_field`` stores the list -> the form is drawn from it."""
        from djust.forms import FormMixin

        class View(FormMixin, LiveView):
            exposure_policy = "legacy"
            form_class = TagsForm

        view = View()
        view.mount(None)
        result = validate_handler_params(
            view.validate_field, {"field": "tags", "value": ["y", "z"]}, "validate_field"
        )
        args, kwargs = validated_call_arguments(result)
        view.validate_field(*args, **kwargs)
        html = render(engine, TagsForm(data=view.form_data))
        assert drawn(html, "checked") == ["y", "z"]
