"""Public form hooks must cover every form-construction entry point."""

import json

import pytest
from django import forms
from django.test import override_settings

from djust import LiveView
from djust.forms import FormMixin


class ContactForm(forms.Form):
    name = forms.CharField(initial="field default")


class ContactView(FormMixin, LiveView):
    form_class = ContactForm


class HookTransportView(ContactView):
    prefix = "contact"
    template = (
        '<div><input name="contact-name" value="{{ form_data.name }}">'
        "<p>{{ success_message }}</p></div>"
    )

    def get_initial(self):
        return {"name": "Hook initial"}

    def form_valid(self, form):
        self.success_message = "Saved " + form.cleaned_data["name"]


def test_empty_mapping_is_bound_not_initial():
    view = ContactView()
    assert not view._create_form().is_bound
    form = view._create_form({})
    assert form.is_bound
    assert not form.is_valid()
    assert "name" in form.errors


def test_initial_is_copied_and_used_by_form_and_reactive_state():
    class View(ContactView):
        initial = {"name": "configured"}

    first, second = View(), View()
    initial = first.get_initial()
    initial["name"] = "changed"
    assert second.get_initial() == {"name": "configured"}
    first.mount(None)
    assert first.form_data["name"] == "configured"
    assert first.form_instance.initial["name"] == "configured"
    first.form_data["name"] = "edited"
    first.reset_form()
    assert first.form_data["name"] == "configured"


def test_dynamic_form_class_does_not_require_class_attribute():
    class View(FormMixin, LiveView):
        def get_form_class(self):
            return ContactForm

    view = View()
    view.mount(None)
    assert isinstance(view.form_instance, ContactForm)
    assert view.form_data["name"] == "field default"


def test_kwargs_hook_sees_bound_data_and_prefix():
    seen = []

    class View(ContactView):
        prefix = "contact"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            seen.append(kwargs.copy())
            return kwargs

    view = View()
    form = view._create_form({"contact-name": "Ada"})
    assert form.is_valid()
    assert form.cleaned_data == {"name": "Ada"}
    assert seen[-1]["data"] == {"contact-name": "Ada"}
    assert seen[-1]["prefix"] == "contact"
    assert not view.get_form().is_bound
    assert "_form_binding_data" not in view.__dict__


def test_failed_form_construction_does_not_retain_binding_data():
    class View(ContactView):
        def get_form(self, form_class=None):
            raise ValueError("construction failed")

    import pytest

    view = View()
    with pytest.raises(ValueError, match="construction failed"):
        view._create_form({"name": "private input"})
    assert "_form_binding_data" not in view.__dict__


def test_legacy_create_form_override_is_still_used():
    seen = []

    class View(ContactView):
        def _create_form(self, data=None):
            seen.append(data)
            return super()._create_form(data)

    view = View()
    view.mount(None)
    view.validate_field(field="name", value="Ada")
    view.submit_form()
    assert any(data == {"name": "Ada"} for data in seen)


def test_callable_field_initial_uses_django_binding_rules():
    class InitialForm(forms.Form):
        name = forms.CharField(initial=lambda: "computed initial")

    view = ContactView()
    view.form_class = InitialForm
    view.mount(None)
    assert view.form_data["name"] == "computed initial"


def test_prefixed_events_validate_and_submit_logical_state():
    class View(ContactView):
        prefix = "contact"

    view = View()
    view.mount(None)
    view.validate_field(field="contact-name", value="")
    assert "name" in view.field_errors
    view.validate_field(field="contact-name", value="Ada")
    assert not view.field_errors
    view.submit_form()
    assert view.is_valid
    assert view.form_data == {"name": "Ada"}
    view.submit_form(**{"contact-name": "Grace"})
    assert view.is_valid
    assert view.form_data == {"name": "Grace"}


def test_initial_override_wins_over_model_value():
    from django.contrib.auth import get_user_model

    user_class = get_user_model()

    class UserForm(forms.ModelForm):
        class Meta:
            model = user_class
            fields = ["first_name"]

    class View(ContactView):
        form_class = UserForm

        def get_initial(self):
            return {"first_name": "Initial override"}

    view = View()
    view._model_instance = user_class(first_name="Model value")
    view.mount(None)
    assert view.form_data["first_name"] == "Initial override"
    assert view.form_instance.instance is view._model_instance


def test_callable_initial_is_shared_with_cached_form_not_evaluated_twice():
    values = iter(["first", "second"])

    class InitialForm(forms.Form):
        name = forms.CharField(initial=lambda: next(values))

    view = ContactView()
    view.form_class = InitialForm
    view.mount(None)
    assert view.form_data["name"] == view.form_instance["name"].value() == "first"
    view.reset_form()
    assert view.form_data["name"] == view.form_instance["name"].value() == "second"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_public_hooks_and_prefix_through_websocket():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        connected, _ = await comm.connect()
        assert connected
        try:
            await comm.receive_json_from(timeout=5)
            await comm.send_json_to(
                {"type": "mount", "view": f"{__name__}.HookTransportView", "url": "/x/"}
            )
            mounted = await comm.receive_json_from(timeout=5)
            assert mounted["type"] == "mount", mounted
            assert "Hook initial" in json.dumps(mounted)
            await comm.send_json_to(
                {
                    "type": "event",
                    "event": "submit_form",
                    "params": {"contact-name": "Ada"},
                    "ref": 35,
                }
            )
            frames = []
            for _ in range(6):
                frame = await comm.receive_json_from(timeout=5)
                frames.append(frame)
                if frame.get("type") in {"patch", "html_update", "error", "noop"}:
                    break
            assert not any(frame.get("type") == "error" for frame in frames), frames
            assert "Saved Ada" in json.dumps(frames), frames
        finally:
            await comm.disconnect()
