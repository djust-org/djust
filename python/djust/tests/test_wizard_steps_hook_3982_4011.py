"""WizardMixin: declared data for ``on_wizard_complete`` (#3982), per-instance steps (#4011).

#3982: ``validate_field`` / ``update_step_field`` store any name the page sends,
and ``on_wizard_complete()`` was handed all of it, while ``on_step_saved()`` was
handed the step's declared fields only.

#4011: ``_steps`` read ``type(self).wizard_steps`` only, so a wizard whose steps
depend on the record it edits overrode a private property, and the resume hooks
(``get_wizard_initial()`` / ``get_wizard_step()``) never ran for it: ``mount()``
resumed before the view had loaded the record, when there were no steps yet.
"""

import pytest
from django import forms

from djust.live_view import LiveView
from djust.testing import LiveViewTestClient
from djust.wizard import WizardMixin


class NameForm(forms.Form):
    name = forms.CharField()


class AgeForm(forms.Form):
    age = forms.IntegerField()


class CityForm(forms.Form):
    city = forms.CharField()


COMPLETED: list = []


@pytest.fixture(autouse=True)
def _clear():
    COMPLETED.clear()
    yield
    COMPLETED.clear()


# --------------------------------------------------------------------------
# #3982
# --------------------------------------------------------------------------


class TwoSteps(WizardMixin, LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>{{ current_step.name }}</div>"
    wizard_steps = [
        {"name": "who", "title": "Who", "form_class": NameForm},
        {"name": "age", "title": "Age", "form_class": AgeForm},
        {"name": "end", "title": "End"},
    ]

    def on_wizard_complete(self, step_data):
        COMPLETED.append(step_data)


def test_on_wizard_complete_is_handed_only_declared_fields():
    client = LiveViewTestClient(TwoSteps).mount()
    client.send_event("validate_field", field="name", value="Ann")
    client.send_event("validate_field", field="is_admin", value="1")
    client.send_event("update_step_field", field="age", value="9")  # another step's field
    client.send_event("next_step")
    client.send_event("validate_field", field="age", value="30")
    client.send_event("update_step_field", field="is_admin", value="1")
    client.send_event("next_step")
    client.send_event("submit_wizard")
    assert COMPLETED == [{"who": {"name": "Ann"}, "age": {"age": "30"}}]
    # The stored data still holds what was sent; only the hook's view of it is limited.
    assert client.view_instance.wizard_step_data["who"]["is_admin"] == "1"


def test_on_wizard_complete_agrees_with_on_step_saved():
    saved = []

    class Both(TwoSteps):
        def on_step_saved(self, step_name, data):
            saved.append((step_name, dict(data)))

    client = LiveViewTestClient(Both).mount()
    client.send_event("validate_field", field="name", value="Ann")
    client.send_event("validate_field", field="extra", value="x")
    client.send_event("next_step")
    client.send_event("next_step", age="4")
    client.send_event("submit_wizard")
    assert dict(saved) == COMPLETED[0]


# --------------------------------------------------------------------------
# #4011
# --------------------------------------------------------------------------

SECTIONS = {"name": NameForm, "age": AgeForm, "city": CityForm}
RECORDS = {
    1: {"asked": ["name", "city"], "answers": {"name": {"name": "Bo"}, "city": {"city": "Oslo"}}},
    2: {"asked": ["age"], "answers": {}},
}


class FollowUp(WizardMixin, LiveView):
    """Steps chosen per record, loaded in the view's own mount() after super().mount()."""

    exposure_policy = "legacy"
    template = "<div dj-root>{{ current_step.name }}</div>"
    wizard_steps = []

    def mount(self, request, record=1, **kwargs):
        super().mount(request, **kwargs)
        self._record = RECORDS[record]
        self.asked = list(self._record["asked"])
        self.resume_wizard()

    def get_wizard_steps(self):
        return [
            {"name": key, "title": key, "form_class": SECTIONS[key]}
            for key in getattr(self, "asked", [])
        ]

    def get_wizard_initial(self):
        return self._record["answers"]

    def get_wizard_step(self):
        return "city" if "city" in self._record["answers"] else 0

    def on_wizard_complete(self, step_data):
        COMPLETED.append(step_data)


def test_steps_chosen_per_instance_and_resumed_after_the_views_own_load():
    client = LiveViewTestClient(FollowUp).mount(record=1)
    view = client.view_instance
    assert [step["name"] for step in view._steps] == ["name", "city"]
    assert view.wizard_step_data == {"name": {"name": "Bo"}, "city": {"city": "Oslo"}}
    assert view.wizard_step_index == 1
    assert view.wizard_completed_steps == [0]
    assert view.get_context_data()["total_steps"] == 2
    client.send_event("submit_wizard")
    assert COMPLETED == [{"name": {"name": "Bo"}, "city": {"city": "Oslo"}}]


def test_another_instance_gets_its_own_steps():
    client = LiveViewTestClient(FollowUp).mount(record=2)
    view = client.view_instance
    assert [step["name"] for step in view._steps] == ["age"]
    assert view.wizard_step_index == 0 and view.wizard_step_data == {}
    client.send_event("submit_wizard", age="7")
    assert COMPLETED == [{"age": {"age": "7"}}]
    # The class's own steps are untouched, and no form class reaches the public state.
    assert FollowUp.wizard_steps == []
    assert "asked" in view.__dict__ and not any(
        isinstance(value, type) for value in view.__dict__.values()
    )


def test_without_the_views_own_resume_the_hooks_did_not_apply():
    """The pre-#4011 shape: mount() resumed before the record was loaded."""

    class NoResume(FollowUp):
        def mount(self, request, record=1, **kwargs):
            WizardMixin.mount(self, request, **kwargs)
            self._record = RECORDS[record]
            self.asked = list(self._record["asked"])

    view = LiveViewTestClient(NoResume).mount(record=1).view_instance
    assert view.wizard_step_data == {} and view.wizard_step_index == 0


def test_the_default_get_wizard_steps_is_the_class_attribute():
    view = TwoSteps()
    assert view.get_wizard_steps() is TwoSteps.wizard_steps
    assert view._steps is TwoSteps.wizard_steps
