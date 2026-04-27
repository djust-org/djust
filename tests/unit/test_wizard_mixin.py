"""
Unit tests for WizardMixin.

Covers:
- Step navigation (next_step, prev_step, go_to_step)
- Per-step validation with Django forms
- Field value storage (update_step_field, validate_field)
- Template context variables
- submit_wizard full-validation and security guard
- on_wizard_complete hook invocation
- Serialisation safety: _steps property, no cleaned_data in state
"""

import pytest
from django import forms
from djust.live_view import LiveView
from djust.wizard import WizardMixin


# ---------------------------------------------------------------------------
# Test forms
# ---------------------------------------------------------------------------


class StepOneForm(forms.Form):
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)


class StepTwoForm(forms.Form):
    email = forms.EmailField()
    age = forms.IntegerField(min_value=0, required=False)


class StatusForm(forms.Form):
    STATUS_CHOICES = [("open", "Open"), ("closed", "Closed")]
    status = forms.ChoiceField(choices=STATUS_CHOICES)


# ---------------------------------------------------------------------------
# Test view
# ---------------------------------------------------------------------------


class ThreeStepView(WizardMixin, LiveView):
    wizard_steps = [
        {"name": "personal", "title": "Personal Info", "form_class": StepOneForm},
        {"name": "contact", "title": "Contact", "form_class": StepTwoForm},
        {"name": "review", "title": "Review"},  # no form — informational
    ]
    template = "<div dj-root>{{ current_step.name }}</div>"

    completed_data = None  # set by on_wizard_complete for test assertions

    def on_wizard_complete(self, step_data):
        ThreeStepView.completed_data = step_data


class SingleStepView(WizardMixin, LiveView):
    wizard_steps = [
        {"name": "only", "title": "Only Step", "form_class": StepOneForm},
    ]
    template = "<div dj-root></div>"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mounted_view(view_class, **mount_kwargs):
    """Return a view instance that has gone through mount()."""
    view = view_class()
    view.get(pytest.importorskip("django").test.RequestFactory().get("/"))
    return view


# ---------------------------------------------------------------------------
# Mount / initial state
# ---------------------------------------------------------------------------


class TestWizardMixinMount:
    @pytest.mark.django_db
    def test_initial_step_is_zero(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        assert view.wizard_step_index == 0

    @pytest.mark.django_db
    def test_initial_step_data_is_empty(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        assert view.wizard_step_data == {}

    @pytest.mark.django_db
    def test_initial_completed_steps_is_empty(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        assert view.wizard_completed_steps == []

    @pytest.mark.django_db
    def test_steps_property_reads_class_definition(self, get_request):
        """_steps always returns the class attribute, surviving JSON round-trips."""
        view = ThreeStepView()
        view.get(get_request)
        # Simulate djust serialisation wiping instance attr
        view.__dict__.pop("wizard_steps", None)
        steps = view._steps
        assert len(steps) == 3
        assert steps[0]["form_class"] is StepOneForm


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


class TestWizardNavigation:
    @pytest.mark.django_db
    def test_next_step_advances_when_valid(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_data["personal"] = {"first_name": "Rosa", "last_name": "Mendez"}
        view.next_step()
        assert view.wizard_step_index == 1

    @pytest.mark.django_db
    def test_next_step_stays_when_invalid(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        # Empty data — StepOneForm requires first_name and last_name
        view.next_step()
        assert view.wizard_step_index == 0

    @pytest.mark.django_db
    def test_next_step_marks_step_completed(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_data["personal"] = {"first_name": "Rosa", "last_name": "Mendez"}
        view.next_step()
        assert 0 in view.wizard_completed_steps

    @pytest.mark.django_db
    def test_next_step_does_nothing_on_last_step(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_index = 2  # last step (no form)
        view.next_step()
        assert view.wizard_step_index == 2

    @pytest.mark.django_db
    def test_prev_step_goes_back(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_index = 1
        view.prev_step()
        assert view.wizard_step_index == 0

    @pytest.mark.django_db
    def test_prev_step_does_nothing_on_first_step(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.prev_step()
        assert view.wizard_step_index == 0

    @pytest.mark.django_db
    def test_go_to_step_jumps_to_completed(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_index = 2
        view.wizard_completed_steps = [0, 1]
        view.go_to_step(step_index=0)
        assert view.wizard_step_index == 0

    @pytest.mark.django_db
    def test_go_to_step_rejects_skipping_uncompleted(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.go_to_step(step_index=2)  # step 2 not completed
        assert view.wizard_step_index == 0

    @pytest.mark.django_db
    def test_go_to_step_rejects_out_of_range(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.go_to_step(step_index=99)
        assert view.wizard_step_index == 0


# ---------------------------------------------------------------------------
# Field updates
# ---------------------------------------------------------------------------


class TestWizardFieldUpdates:
    @pytest.mark.django_db
    def test_update_step_field_stores_value(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.update_step_field(field="first_name", value="Rosa")
        assert view.wizard_step_data["personal"]["first_name"] == "Rosa"

    @pytest.mark.django_db
    def test_validate_field_stores_via_field_param(self, get_request):
        """validate_field bridge: field= param (from data-field attribute)."""
        view = ThreeStepView()
        view.get(get_request)
        view.validate_field(field="last_name", value="Mendez")
        assert view.wizard_step_data["personal"]["last_name"] == "Mendez"

    @pytest.mark.django_db
    def test_validate_field_stores_via_field_name_param(self, get_request):
        """validate_field bridge: legacy field_name= param."""
        view = ThreeStepView()
        view.get(get_request)
        view.validate_field(field_name="first_name", value="Rosa")
        assert view.wizard_step_data["personal"]["first_name"] == "Rosa"

    @pytest.mark.django_db
    def test_update_step_field_ignores_empty_field_name(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.update_step_field(field="", value="ignored")
        assert view.wizard_step_data == {}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestWizardValidation:
    @pytest.mark.django_db
    def test_valid_step_clears_errors(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        # Seed an error, then provide valid data
        view.wizard_step_errors["personal"] = {"first_name": ["Required."]}
        view.wizard_step_data["personal"] = {"first_name": "Rosa", "last_name": "Mendez"}
        result = view._validate_current_step()
        assert result is True
        assert "personal" not in view.wizard_step_errors

    @pytest.mark.django_db
    def test_invalid_step_stores_errors(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        result = view._validate_current_step()  # empty data
        assert result is False
        assert "first_name" in view.wizard_step_errors.get("personal", {})

    @pytest.mark.django_db
    def test_informational_step_always_valid(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_index = 2  # "review" step — no form_class
        result = view._validate_current_step()
        assert result is True

    @pytest.mark.django_db
    def test_next_step_stores_errors_on_failure(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.next_step()  # invalid — no data
        assert "personal" in view.wizard_step_errors


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


class TestWizardSubmit:
    @pytest.mark.django_db
    def test_submit_calls_on_wizard_complete(self, get_request):
        ThreeStepView.completed_data = None
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_index = 2
        view.wizard_completed_steps = [0, 1]
        view.wizard_step_data = {
            "personal": {"first_name": "Rosa", "last_name": "Mendez"},
            "contact": {"email": "rosa@test.com", "age": "30"},
        }
        view.submit_wizard()
        assert ThreeStepView.completed_data is not None
        assert ThreeStepView.completed_data["personal"]["first_name"] == "Rosa"

    @pytest.mark.django_db
    def test_submit_rejected_from_non_last_step(self, get_request):
        ThreeStepView.completed_data = None
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_index = 0
        view.submit_wizard()
        assert ThreeStepView.completed_data is None

    @pytest.mark.django_db
    def test_submit_rejects_tampered_earlier_step(self, get_request):
        """Re-validation of previous steps catches tampered WebSocket data."""
        ThreeStepView.completed_data = None
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_index = 2
        view.wizard_completed_steps = [0, 1]
        view.wizard_step_data = {
            "personal": {"first_name": "", "last_name": ""},  # invalid
            "contact": {"email": "rosa@test.com"},
        }
        view.submit_wizard()
        assert ThreeStepView.completed_data is None
        assert view.wizard_step_index == 0  # rewound to failing step

    @pytest.mark.django_db
    def test_submit_rejects_invalid_last_step(self, get_request):
        ThreeStepView.completed_data = None
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_index = 2  # review (no form) — always valid
        view.wizard_completed_steps = [0, 1]
        # Deliberately break step 1
        view.wizard_step_data = {
            "personal": {"first_name": "", "last_name": ""},
            "contact": {"email": "not-an-email"},
        }
        view.submit_wizard()
        assert ThreeStepView.completed_data is None

    @pytest.mark.django_db
    def test_submit_does_not_store_cleaned_data(self, get_request):
        """step_data must contain strings, not cleaned_data Python objects."""
        ThreeStepView.completed_data = None
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_index = 2
        view.wizard_completed_steps = [0, 1]
        view.wizard_step_data = {
            "personal": {"first_name": "Rosa", "last_name": "Mendez"},
            "contact": {"email": "rosa@test.com"},
        }
        view.submit_wizard()
        # Values must remain as strings, not Python objects
        assert isinstance(ThreeStepView.completed_data["personal"]["first_name"], str)
        assert isinstance(ThreeStepView.completed_data["contact"]["email"], str)


# ---------------------------------------------------------------------------
# Context data
# ---------------------------------------------------------------------------


class TestWizardContext:
    @pytest.mark.django_db
    def test_context_contains_current_step(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        ctx = view.get_context_data()
        assert ctx["current_step"]["name"] == "personal"
        assert ctx["current_step"]["index"] == 0

    @pytest.mark.django_db
    def test_context_total_steps(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        assert view.get_context_data()["total_steps"] == 3

    @pytest.mark.django_db
    def test_context_progress_percent_zero_on_first(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        assert view.get_context_data()["progress_percent"] == 0

    @pytest.mark.django_db
    def test_context_steps_list(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        steps = view.get_context_data()["steps"]
        assert len(steps) == 3
        assert steps[0]["is_current"] is True
        assert steps[1]["is_current"] is False

    @pytest.mark.django_db
    def test_context_can_go_back_false_on_first(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        assert view.get_context_data()["can_go_back"] is False

    @pytest.mark.django_db
    def test_context_can_go_back_true_after_advance(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_index = 1
        assert view.get_context_data()["can_go_back"] is True

    @pytest.mark.django_db
    def test_context_form_data_populated_from_step_data(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_data["personal"] = {"first_name": "Rosa", "last_name": "Mendez"}
        ctx = view.get_context_data()
        assert ctx["form_data"]["first_name"] == "Rosa"

    @pytest.mark.django_db
    def test_context_step_errors_for_current_step(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        view.wizard_step_errors["personal"] = {"first_name": ["Required."]}
        ctx = view.get_context_data()
        assert ctx["step_errors"]["first_name"] == ["Required."]

    @pytest.mark.django_db
    def test_context_field_html_contains_input_tags(self, get_request):
        view = ThreeStepView()
        view.get(get_request)
        ctx = view.get_context_data()
        assert "<input" in ctx["field_html"]["first_name"]
        assert "<input" in ctx["field_html"]["last_name"]

    @pytest.mark.django_db
    def test_context_safe_before_mount_guard(self, get_request):
        """get_context_data() must not crash if called before mount()."""
        view = ThreeStepView()
        # Do NOT call mount — simulate Rust bridge pre-mount call
        ctx = view.get_context_data()
        assert ctx["total_steps"] == 3
        assert ctx["current_step"]["index"] == 0

    @pytest.mark.django_db
    def test_context_flat_choices_exposed(self, get_request):
        """ChoiceField choices exposed as <field>_choices top-level var."""

        class ChoiceStepView(WizardMixin, LiveView):
            wizard_steps = [{"name": "s", "title": "S", "form_class": StatusForm}]
            template = "<div dj-root></div>"

        view = ChoiceStepView()
        view.get(get_request)
        ctx = view.get_context_data()
        assert "status_choices" in ctx
        labels = [opt["label"] for opt in ctx["status_choices"]]
        assert "Open" in labels


# ---------------------------------------------------------------------------
# as_live_field — widget-aware dom_event default (#1156)
# ---------------------------------------------------------------------------


class MixedWidgetForm(forms.Form):
    """A form covering every widget category that `as_live_field` dispatches on."""

    # text-stream widgets — should track wizard_input_event
    first_name = forms.CharField(max_length=100)
    bio = forms.CharField(widget=forms.Textarea, required=False)
    age = forms.IntegerField(required=False)
    email = forms.EmailField()

    # click-fired widgets — should always be dj-change
    has_attorney = forms.ChoiceField(
        choices=[("yes", "Yes"), ("no", "No")],
        widget=forms.RadioSelect,
    )
    terms_accepted = forms.BooleanField(required=False)
    state = forms.ChoiceField(
        choices=[("ny", "NY"), ("ca", "CA")],
        # default widget is Select
    )
    tags = forms.MultipleChoiceField(
        choices=[("a", "A"), ("b", "B")],
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )


class MixedWidgetWizard(WizardMixin, LiveView):
    wizard_steps = [
        {"name": "mixed", "title": "Mixed", "form_class": MixedWidgetForm},
    ]
    template = "<div dj-root></div>"


class MixedWidgetWizardStreaming(MixedWidgetWizard):
    """Same wizard but opts into dj-input for text streams (per #1095)."""

    wizard_input_event = "dj-input"


class TestAsLiveFieldWidgetAwareDomEvent:
    """#1156 — as_live_field picks dj-change for click-fired widgets regardless of
    wizard_input_event. Text-stream widgets track wizard_input_event."""

    # -- text-stream widgets ------------------------------------------------

    @pytest.mark.django_db
    def test_text_input_uses_wizard_input_event_default(self, get_request):
        view = MixedWidgetWizard()
        view.get(get_request)
        html = view.as_live_field("first_name")
        # default wizard_input_event = "dj-change"
        assert 'dj-change="validate_field"' in html
        assert "dj-input" not in html

    @pytest.mark.django_db
    def test_text_input_uses_dj_input_when_streaming(self, get_request):
        view = MixedWidgetWizardStreaming()
        view.get(get_request)
        html = view.as_live_field("first_name")
        assert 'dj-input="validate_field"' in html

    @pytest.mark.django_db
    def test_textarea_uses_dj_input_when_streaming(self, get_request):
        view = MixedWidgetWizardStreaming()
        view.get(get_request)
        html = view.as_live_field("bio")
        assert 'dj-input="validate_field"' in html

    @pytest.mark.django_db
    def test_integer_input_uses_dj_input_when_streaming(self, get_request):
        view = MixedWidgetWizardStreaming()
        view.get(get_request)
        html = view.as_live_field("age")
        assert 'dj-input="validate_field"' in html

    @pytest.mark.django_db
    def test_email_input_uses_dj_input_when_streaming(self, get_request):
        view = MixedWidgetWizardStreaming()
        view.get(get_request)
        html = view.as_live_field("email")
        assert 'dj-input="validate_field"' in html

    # -- click-fired widgets -----------------------------------------------

    @pytest.mark.django_db
    def test_radio_uses_dj_change_even_when_streaming(self, get_request):
        """Radios commit one value per click — no event stream to debounce.
        Must emit dj-change regardless of wizard_input_event = dj-input."""
        view = MixedWidgetWizardStreaming()
        view.get(get_request)
        html = view.as_live_field("has_attorney")
        assert 'dj-change="validate_field"' in html
        assert "dj-input" not in html

    @pytest.mark.django_db
    def test_select_uses_dj_change_even_when_streaming(self, get_request):
        view = MixedWidgetWizardStreaming()
        view.get(get_request)
        html = view.as_live_field("state")
        assert 'dj-change="validate_field"' in html
        assert "dj-input" not in html

    @pytest.mark.django_db
    def test_checkbox_uses_dj_change_even_when_streaming(self, get_request):
        view = MixedWidgetWizardStreaming()
        view.get(get_request)
        html = view.as_live_field("terms_accepted")
        assert 'dj-change="validate_field"' in html
        assert "dj-input" not in html

    @pytest.mark.django_db
    def test_checkbox_select_multiple_uses_dj_change_even_when_streaming(self, get_request):
        view = MixedWidgetWizardStreaming()
        view.get(get_request)
        html = view.as_live_field("tags")
        assert 'dj-change="validate_field"' in html
        assert "dj-input" not in html

    # -- explicit override wins --------------------------------------------

    @pytest.mark.django_db
    def test_caller_passed_dom_event_wins_on_click_widget(self, get_request):
        """Caller can force dj-input on a radio if they really want to —
        the widget-aware default is only a default."""
        view = MixedWidgetWizard()
        view.get(get_request)
        html = view.as_live_field("has_attorney", dom_event="dj-input")
        assert 'dj-input="validate_field"' in html

    @pytest.mark.django_db
    def test_caller_passed_dom_event_wins_on_text_widget(self, get_request):
        view = MixedWidgetWizardStreaming()
        view.get(get_request)
        html = view.as_live_field("first_name", dom_event="dj-change")
        assert 'dj-change="validate_field"' in html

    # -- subclass extension --------------------------------------------

    @pytest.mark.django_db
    def test_subclass_can_extend_click_fired_set(self, get_request):
        """Apps with custom widgets can add them to _CLICK_FIRED_WIDGET_CLASSES
        without touching as_live_field itself."""

        class _MyCommitWidget(forms.TextInput):
            pass

        class _FormWithCustom(forms.Form):
            committed = forms.CharField(widget=_MyCommitWidget())

        class _WizardWithCustom(WizardMixin, LiveView):
            wizard_input_event = "dj-input"
            # Extend rather than replace so built-in click widgets still win
            _CLICK_FIRED_WIDGET_CLASSES = frozenset({
                *WizardMixin._CLICK_FIRED_WIDGET_CLASSES,
                "_MyCommitWidget",
            })
            wizard_steps = [{"name": "x", "title": "X", "form_class": _FormWithCustom}]
            template = "<div dj-root></div>"

        view = _WizardWithCustom()
        view.get(get_request)
        html = view.as_live_field("committed")
        assert 'dj-change="validate_field"' in html
        assert "dj-input" not in html


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class TestWizardPublicAPI:
    def test_wizard_mixin_importable_from_djust(self):
        from djust import WizardMixin as WM

        assert WM is WizardMixin

    def test_wizard_mixin_importable_from_djust_wizard(self):
        from djust.wizard import WizardMixin as WM

        assert WM is WizardMixin
