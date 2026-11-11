"""WizardMixin validates what a step's form submitted, and can resume stored progress.

#3964: ``next_step`` ignored the fields a ``<form dj-submit="next_step">`` posted
and validated ``wizard_step_data`` only, and ``validate_field`` stored a value
without validating it.

#3965: ``mount()`` always started at step 0 with empty data; ``get_wizard_initial()``
and ``get_wizard_step()`` let an application resume, and ``on_step_saved()`` saves as
the person goes.

The events go through ``LiveViewTestClient`` (the consumer's own handler lookup,
parameter validation and security chain) and, for the submit shape, a real
WebSocket session.
"""

import datetime
import json

import pytest
from asgiref.sync import sync_to_async
from django import forms
from django.test import override_settings

from djust.live_view import LiveView
from djust.testing import LiveViewTestClient
from djust.wizard import WizardMixin


class ContactForm(forms.Form):
    email = forms.EmailField()
    nickname = forms.CharField(required=False, max_length=5)


class PrefsForm(forms.Form):
    newsletter = forms.BooleanField(required=False)
    since = forms.DateField(required=False)


class PinForm(forms.Form):
    pin = forms.CharField(min_length=4)

    def clean_pin(self):
        pin = self.cleaned_data["pin"]
        if pin == "0000":
            raise forms.ValidationError("Too easy to guess.")
        return pin


SAVED: list = []


class Signup(WizardMixin, LiveView):
    template = "<div dj-root>{{ current_step.name }}</div>"
    wizard_steps = [
        {"name": "contact", "title": "Contact", "form_class": ContactForm},
        {"name": "prefs", "title": "Prefs", "form_class": PrefsForm},
        {"name": "pin", "title": "Pin", "form_class": PinForm},
        {"name": "done", "title": "Done"},
    ]

    def on_step_saved(self, step_name, data):
        SAVED.append((step_name, dict(data)))


@pytest.fixture(autouse=True)
def _clear_saved():
    SAVED.clear()
    yield
    SAVED.clear()


def _client(view_class=Signup):
    client = LiveViewTestClient(view_class)
    client.mount()
    return client


# --------------------------------------------------------------------------
# #3964: next_step validates what was submitted
# --------------------------------------------------------------------------


def test_next_step_validates_the_submitted_fields_not_only_the_stored_ones():
    client = _client()
    result = client.send_event("next_step", email="a@example.com")
    assert result["success"], result
    view = client.view_instance
    assert view.wizard_step_index == 1, view.wizard_step_errors
    assert view.wizard_step_data["contact"]["email"] == "a@example.com"
    assert view.wizard_completed_steps == [0]


def test_next_step_refuses_an_invalid_submission_and_reports_it():
    client = _client()
    client.send_event("next_step", email="not-an-email")
    view = client.view_instance
    assert view.wizard_step_index == 0
    assert view.wizard_step_errors["contact"]["email"]


def test_the_submission_wins_over_a_stale_stored_value():
    client = _client()
    client.send_event("validate_field", field="email", value="old@example.com")
    client.send_event("next_step", email="new@example.com")
    view = client.view_instance
    assert view.wizard_step_index == 1
    assert view.wizard_step_data["contact"]["email"] == "new@example.com"


def test_a_stale_stored_value_no_longer_passes_over_a_bad_submission():
    client = _client()
    client.send_event("validate_field", field="email", value="old@example.com")
    client.send_event("next_step", email="broken")
    assert client.view_instance.wizard_step_index == 0


def test_only_the_current_steps_fields_are_merged():
    client = _client()
    client.send_event(
        "next_step",
        email="a@example.com",
        pin="1234",  # another step's field
        csrfmiddlewaretoken="x",  # not a field at all
    )
    view = client.view_instance
    assert view.wizard_step_index == 1
    assert view.wizard_step_data["contact"] == {"email": "a@example.com"}
    assert "pin" not in view.wizard_step_data.get("pin", {})
    assert "csrfmiddlewaretoken" not in json.dumps(view.wizard_step_data)


def test_a_click_that_posts_nothing_still_validates_the_stored_data():
    client = _client()
    client.send_event("validate_field", field="email", value="a@example.com")
    client.send_event("next_step")
    assert client.view_instance.wizard_step_index == 1


def test_a_checkbox_submitted_as_false_is_stored():
    client = _client()
    client.send_event("next_step", email="a@example.com")
    client.send_event("next_step", newsletter=False, since="2024-02-29")
    view = client.view_instance
    assert view.wizard_step_index == 2
    assert view.wizard_step_data["prefs"] == {"newsletter": False, "since": "2024-02-29"}


def test_submit_wizard_validates_the_last_steps_posted_fields():
    client = _client()
    client.send_event("next_step", email="a@example.com")
    client.send_event("next_step")
    assert client.view_instance.wizard_step_index == 2
    # Step "pin" is not the last (the wizard has a review step), so go on.
    client.send_event("next_step", pin="1234")
    assert client.view_instance.wizard_step_index == 3

    class OneStep(WizardMixin, LiveView):
        template = "<div dj-root></div>"
        wizard_steps = [{"name": "pin", "form_class": PinForm}]
        completed = None

        def on_wizard_complete(self, step_data):
            OneStep.completed = step_data

    single = _client(OneStep)
    single.send_event("submit_wizard", pin="1234")
    assert OneStep.completed == {"pin": {"pin": "1234"}}


def test_submit_wizard_still_refuses_an_invalid_last_step():
    class OneStep(WizardMixin, LiveView):
        template = "<div dj-root></div>"
        wizard_steps = [{"name": "pin", "form_class": PinForm}]
        completed = None

        def on_wizard_complete(self, step_data):
            OneStep.completed = step_data

    single = _client(OneStep)
    single.send_event("submit_wizard", pin="12")
    assert OneStep.completed is None
    assert single.view_instance.wizard_step_errors["pin"]["pin"]


def test_a_submission_past_the_size_bound_is_refused_whole():
    client = _client()
    with override_settings(LIVEVIEW_CONFIG={"max_form_data_size": 20}):
        client.send_event("next_step", email="a@example.com", nickname="n" * 5000)
    view = client.view_instance
    # Nothing of the post was merged (a half-merged submit is a form nobody posted),
    # the step did not advance, and the person is told why.
    assert view.wizard_step_index == 0
    assert view.wizard_step_data.get("contact", {}) == {}
    assert "__all__" in view.wizard_step_errors["contact"]


def test_undeclared_keys_in_a_submission_never_reach_the_stored_data():
    client = _client()
    with override_settings(LIVEVIEW_CONFIG={"max_form_fields": 1}):
        client.send_event("next_step", email="a@example.com", extra1="1", extra2="2", extra3="3")
    view = client.view_instance
    assert view.wizard_step_index == 1
    assert view.wizard_step_data["contact"] == {"email": "a@example.com"}


# --------------------------------------------------------------------------
# #3964: validate_field validates
# --------------------------------------------------------------------------


def test_validate_field_sets_the_fields_error_while_typing():
    client = _client()
    client.send_event("validate_field", field="email", value="not-an-email")
    errors = client.view_instance.wizard_step_errors
    assert errors["contact"]["email"] == ["Enter a valid email address."]
    assert client.view_instance.wizard_step_index == 0


def test_validate_field_clears_the_error_once_the_value_is_valid():
    client = _client()
    client.send_event("validate_field", field="email", value="nope")
    client.send_event("validate_field", field="email", value="a@example.com")
    assert "contact" not in client.view_instance.wizard_step_errors


def test_validate_field_validates_only_the_one_field():
    client = _client()
    # ``email`` is required and still empty: validating ``nickname`` must not
    # report it.
    client.send_event("validate_field", field="nickname", value="toolongname")
    errors = client.view_instance.wizard_step_errors["contact"]
    assert set(errors) == {"nickname"}


def test_validate_field_keeps_the_other_fields_errors():
    client = _client()
    client.send_event("validate_field", field="email", value="nope")
    client.send_event("validate_field", field="nickname", value="toolongname")
    client.send_event("validate_field", field="nickname", value="ok")
    errors = client.view_instance.wizard_step_errors["contact"]
    assert set(errors) == {"email"}


def test_validate_field_runs_the_forms_clean_method():
    client = _client()
    client.send_event("next_step", email="a@example.com")
    client.send_event("next_step")
    client.send_event("validate_field", field="pin", value="0000")
    assert client.view_instance.wizard_step_errors["pin"]["pin"] == ["Too easy to guess."]


def test_validate_field_on_a_field_the_form_does_not_have_only_stores_it():
    client = _client()
    client.send_event("validate_field", field="mystery", value="x")
    view = client.view_instance
    assert view.wizard_step_data["contact"]["mystery"] == "x"
    assert not view.wizard_step_errors


def test_validate_field_accepts_the_legacy_field_name_parameter():
    client = _client()
    client.send_event("validate_field", field_name="email", value="nope")
    assert client.view_instance.wizard_step_errors["contact"]["email"]


def test_validate_field_leaves_a_bounds_refusal_in_place():
    from djust import _form_data_bounds

    client = _client()
    view = client.view_instance
    view.wizard_step_errors["contact"] = {"__all__": ["refused earlier"]}
    view._form_data_refusal = "refused earlier"
    client.send_event("validate_field", field="email", value="a@example.com")
    # Accepted: the refusal message is cleared, and the field is valid.
    assert "contact" not in view.wizard_step_errors
    assert _form_data_bounds is not None


# --------------------------------------------------------------------------
# #3964 through a real WebSocket session
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_dj_submit_next_step_event_over_a_real_websocket():
    from channels.testing import WebsocketCommunicator

    from djust.tests.test_exposure_runtime import make_request
    from djust.websocket import LiveViewConsumer

    request = await sync_to_async(make_request)()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=None, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from()
        await socket.send_json_to(
            {"type": "mount", "view": __name__ + ".Signup", "url": "/", "params": {}}
        )
        mounted = await socket.receive_json_from()
        try:
            assert mounted["type"] == "mount", mounted
            await socket.send_json_to(
                {
                    "type": "event",
                    "event": "next_step",
                    "params": {"email": "a@example.com", "csrfmiddlewaretoken": "t"},
                    "ref": 1,
                }
            )
            frames = []
            for _ in range(6):
                frame = await socket.receive_json_from()
                frames.append(frame)
                if frame.get("type") in {"patch", "html_update", "error", "noop"}:
                    break
            assert not any(f.get("type") == "error" for f in frames), frames
            assert "prefs" in json.dumps(frames), frames
        finally:
            await socket.disconnect()


# --------------------------------------------------------------------------
# #3965: resuming stored progress
# --------------------------------------------------------------------------


class Resumed(Signup):
    """Stored answers for the first two steps; the person is on the third."""

    def get_wizard_initial(self):
        return {
            "contact": {"email": "stored@example.com", "nickname": "ab"},
            "prefs": {"newsletter": True, "since": datetime.date(2024, 2, 29)},
        }

    def get_wizard_step(self):
        return "pin"


def test_a_wizard_that_overrides_nothing_starts_where_it_always_did():
    view = _client().view_instance
    assert view.wizard_step_index == 0
    assert view.wizard_step_data == {}
    assert view.wizard_completed_steps == []


def test_get_wizard_initial_and_step_resume_stored_progress():
    view = _client(Resumed).view_instance
    assert view.wizard_step_index == 2
    assert view.wizard_completed_steps == [0, 1]
    assert view.wizard_step_data["contact"] == {"email": "stored@example.com", "nickname": "ab"}


def test_resumed_values_are_stored_as_json_plain_widget_text():
    view = _client(Resumed).view_instance
    assert view.wizard_step_data["prefs"] == {"newsletter": True, "since": "2024-02-29"}
    json.dumps(view.wizard_step_data)


def test_a_resumed_wizard_continues_normally():
    client = _client(Resumed)
    client.send_event("validate_field", field="pin", value="1234")
    client.send_event("next_step")
    assert client.view_instance.wizard_step_index == 3
    # Earlier steps are completed, so the person can go back to one.
    client.send_event("go_to_step", step_index=0)
    assert client.view_instance.wizard_step_index == 0


def test_a_resumed_step_is_validated_before_the_wizard_completes():
    class Stale(Resumed):
        def get_wizard_initial(self):
            return {"contact": {"email": "no-longer-valid"}}

        def get_wizard_step(self):
            return 3

    client = _client(Stale)
    # The wizard was resumed at its last step with a bad answer stored for the
    # first one: the step after "pin" is a review step, so submit from there.
    client.send_event("submit_wizard")
    view = client.view_instance
    assert view.wizard_step_errors["contact"]["email"]
    assert view.wizard_step_index == 0


@pytest.mark.parametrize("wanted", [99, -1, "nope", True, None, 1.5])
def test_a_step_that_does_not_exist_starts_at_the_first_step(wanted, caplog):
    class Odd(Signup):
        def get_wizard_step(self):
            return wanted

    with caplog.at_level("WARNING", logger="djust.wizard"):
        view = _client(Odd).view_instance
    assert view.wizard_step_index == 0
    assert view.wizard_completed_steps == []
    assert "not a step of the wizard" in caplog.text


def test_initial_answers_for_undeclared_steps_and_fields_are_ignored(caplog):
    class Messy(Signup):
        def get_wizard_initial(self):
            return {
                "contact": {"email": "a@example.com", "bogus": "x"},
                "done": {"anything": 1},  # a step without a form
                "ghost": {"x": 1},  # not a step
            }

    with caplog.at_level("WARNING", logger="djust.wizard"):
        view = _client(Messy).view_instance
    assert view.wizard_step_data == {"contact": {"email": "a@example.com"}}
    assert "bogus" in caplog.text and "ghost" in caplog.text and "done" in caplog.text


def test_on_step_saved_runs_after_a_validated_step_with_its_data():
    client = _client()
    client.send_event("next_step", email="bad")
    assert SAVED == []
    client.send_event("next_step", email="a@example.com", nickname="nick")
    assert SAVED == [("contact", {"email": "a@example.com", "nickname": "nick"})]


def test_a_failing_on_step_saved_leaves_the_person_on_the_step():
    class Failing(Signup):
        def on_step_saved(self, step_name, data):
            raise RuntimeError("storage is down")

    client = _client(Failing)
    result = client.send_event("next_step", email="a@example.com")
    assert not result["success"]
    assert client.view_instance.wizard_step_index == 0
    assert client.view_instance.wizard_completed_steps == []
