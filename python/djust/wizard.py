"""
Multi-step form wizard mixin for djust LiveViews.

WizardMixin manages step navigation, per-step validation, and data collection
for guided multi-step form flows. Compose with LiveView to build wizards:

    class ClaimIntakeView(WizardMixin, LiveView):
        wizard_steps = [
            {"name": "personal", "title": "Personal Info", "form_class": PersonalInfoForm},
            {"name": "details",  "title": "Details",       "form_class": DetailsForm},
            {"name": "review",   "title": "Review"},
        ]

        def on_wizard_complete(self, step_data):
            # step_data["personal"] → dict of raw string values from PersonalInfoForm
            # step_data["details"]  → dict of raw string values from DetailsForm
            MyModel.objects.create(**step_data["personal"])

Template context provided on every render:

    current_step.name       — step identifier
    current_step.title      — human-readable title
    current_step.index      — 0-based index
    total_steps             — total number of steps
    progress_percent        — floor(index / total * 100)
    steps                   — list of step dicts with is_current / is_completed flags
    can_go_back             — True if not on first step
    can_go_forward          — True if current step is already completed
    is_first_step / is_last_step
    form_data               — {field_name: current_value} for current step's form
    form_required           — {field_name: bool} required flag per field
    form_choices            — {field_name: [{value, label}, ...]} for choice fields
    field_html              — {field_name: SafeString} pre-rendered widget HTML with
                              dj-change="validate_field" and data-field bindings
    step_errors             — {field_name: [error_msg, ...]} for current step
    step_data               — all collected data across steps

Event handlers available to templates via dj-click / dj-submit:

    next_step               — validate current step, advance if valid
    prev_step               — go back one step (no validation)
    go_to_step              — jump to a completed step (data-step_index=N)
    update_step_field       — update a single field value (data-field=name)
    validate_field          — bridge for as_live_field() dj-change events
    submit_wizard           — validate all steps, call on_wizard_complete()

Implementation notes
--------------------
Several design decisions here work around known djust serialization constraints:

- Public attrs only for step state (wizard_step_index, wizard_step_data, etc.).
  Private (_) attrs are wiped between WebSocket events — they cannot hold state
  that must survive across user interactions.

- _steps property reads from type(self).wizard_steps, not self.wizard_steps.
  djust serializes ALL public instance attributes to JSON, including class-level
  ones. Django form classes are not JSON-serializable, so they come back as None
  after the first event.  Reading from the class definition always returns the
  original value.

- Never store form.cleaned_data in wizard_step_data. Django's cleaned_data
  contains Python objects (datetime.date, Decimal, etc.) that are not JSON-
  serializable. Store the original string inputs; parse in on_wizard_complete().
"""

import logging
import math
from typing import Any

from .decorators import event_handler

logger = logging.getLogger(__name__)


class WizardMixin:
    """Multi-step form wizard mixin for djust LiveViews.

    Place **before** LiveView in the MRO so wizard methods are found first::

        class MyWizard(WizardMixin, LiveView):
            wizard_steps = [...]

    See module docstring for full usage and implementation notes.
    """

    wizard_steps: list = []  # Subclasses override with a list of step dicts

    #: DOM event that triggers `validate_field` on text/textarea/select inputs.
    #: Default ``"dj-change"`` fires only on blur. Set to ``"dj-input"`` to
    #: capture every keystroke (300ms debounced) — needed when fields can be
    #: pre-filled and submitted without an intermediate blur. Closes #1095.
    wizard_input_event: str = "dj-change"

    @property
    def _steps(self) -> list:
        """Read wizard_steps from the CLASS definition, not the instance.

        djust serializes all public instance attributes to JSON between
        WebSocket events. Django form classes in wizard_steps are not
        JSON-serializable and come back as None after the first event.
        Reading from type(self) always returns the original class value.
        """
        return type(self).wizard_steps

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def mount(self, request, **kwargs):
        """Initialise wizard state on first load."""
        super().mount(request, **kwargs)
        self.wizard_step_index: int = 0
        self.wizard_step_data: dict[str, dict[str, Any]] = {}
        self.wizard_step_errors: dict[str, dict[str, list]] = {}
        self.wizard_completed_steps: list[int] = []

    # ------------------------------------------------------------------
    # Field rendering
    # ------------------------------------------------------------------

    def as_live_field(self, field_name: str, event_name: str = "validate_field", **kwargs) -> str:
        """Render a form field as HTML with djust event bindings.

        Returns an HTML string containing the widget markup with
        ``<dom_event>="<event_name>"`` and ``data-field="<field_name>"``
        attributes so the field participates in real-time validation.

        ``dom_event`` defaults to the wizard's ``wizard_input_event`` class
        attribute (``"dj-change"`` by default — fires on blur). Pass
        ``dom_event="dj-input"`` per-call, or set ``wizard_input_event =
        "dj-input"`` on the view, to fire on every keystroke (debounced
        client-side) — required when fields can be pre-filled and submitted
        without an intermediate blur (#1095).

        Pre-render all fields in get_context_data() and pass as field_html
        dict — the Rust renderer cannot call Python methods with arguments
        from templates, so the rendering must happen in Python:

            context["field_html"] = {
                name: self.as_live_field(name)
                for name in form_instance.fields
            }

        Template usage::

            {{ field_html.first_name|safe }}

        Args:
            field_name:  Django form field name.
            event_name:  dj-change handler name (default: ``validate_field``).
            **kwargs:    Extra kwargs forwarded to the framework adapter.
        """
        from .frameworks import get_adapter

        current_index = getattr(self, "wizard_step_index", 0)
        if not self._steps or current_index >= len(self._steps):
            return ""
        step = self._steps[current_index]
        form_class = step.get("form_class")
        if not form_class:
            return ""
        step_name = step.get("name", "")
        step_data = getattr(self, "wizard_step_data", {}).get(step_name, {})
        step_errors = getattr(self, "wizard_step_errors", {}).get(step_name, {})

        form_instance = form_class(data=step_data) if step_data else form_class()
        field = form_instance.fields.get(field_name)
        if not field:
            return ""

        value = step_data.get(field_name, field.initial or "")
        errors = step_errors.get(field_name, [])

        adapter = get_adapter(kwargs.pop("framework", None))
        # setdefault would not overwrite a caller-passed None; coalesce explicitly
        # so attrs[<dom_event>] never receives None and produces broken HTML.
        if kwargs.get("dom_event") is None:
            kwargs["dom_event"] = self.wizard_input_event
        return adapter.render_field(
            field, field_name, value, errors, event_name=event_name, **kwargs
        )

    # ------------------------------------------------------------------
    # Template context
    # ------------------------------------------------------------------

    def get_context_data(self, **kwargs):
        """Inject wizard state into the template context."""
        context = super().get_context_data(**kwargs)

        # Guard: the Rust bridge may call get_context_data() before mount()
        # initialises instance attributes.  Use getattr() with safe defaults.
        total = len(self._steps)
        current_index = getattr(self, "wizard_step_index", 0)
        completed_steps = getattr(self, "wizard_completed_steps", [])
        step_data = getattr(self, "wizard_step_data", {})
        step_errors = getattr(self, "wizard_step_errors", {})

        # Build step list with status flags for the step-indicator UI
        steps = [
            {
                "name": step["name"],
                "title": step.get("title", ""),
                "index": i,
                "is_current": i == current_index,
                "is_completed": i in completed_steps,
            }
            for i, step in enumerate(self._steps)
        ]

        current_step = self._steps[current_index] if total > 0 else {}
        current_name = current_step.get("name", "")
        progress_percent = math.floor((current_index / total) * 100) if total > 0 else 0

        # Build form_data, form_choices, form_required, and pre-rendered field_html
        # for the current step.  Passing rendered HTML strings (field_html) rather
        # than BoundField / Form objects avoids Rust renderer limitations.
        form_data: dict = {}
        form_choices: dict = {}
        form_required: dict = {}
        field_html: dict = {}
        form_class = current_step.get("form_class")
        if form_class:
            current_step_data = step_data.get(current_name, {})
            form_instance = (
                form_class(data=current_step_data) if current_step_data else form_class()
            )
            for fname, field in form_instance.fields.items():
                val = current_step_data.get(fname, field.initial or "")
                form_data[fname] = val if val is not None else ""
                form_required[fname] = bool(field.required)
                if hasattr(field, "choices"):
                    form_choices[fname] = [
                        {"value": str(k), "label": str(v)} for k, v in field.choices if str(k)
                    ]
                field_html[fname] = self.as_live_field(fname, event_name="validate_field")

        # Expose choices as flat top-level vars too (e.g. borough_choices)
        # so templates can use either form_choices.borough or borough_choices.
        flat_choices = {f"{k}_choices": v for k, v in form_choices.items()}

        context.update(
            {
                "current_step": {
                    "name": current_name,
                    "title": current_step.get("title", ""),
                    "index": current_index,
                },
                "total_steps": total,
                "progress_percent": progress_percent,
                "steps": steps,
                "can_go_back": current_index > 0,
                "can_go_forward": current_index in completed_steps,
                "is_first_step": current_index == 0,
                "is_last_step": current_index == total - 1 if total > 0 else True,
                "form_data": form_data,
                "form_choices": form_choices,
                "form_required": form_required,
                "field_html": field_html,
                "step_data": dict(step_data),
                "step_errors": step_errors.get(current_name, {}),
                **flat_choices,
            }
        )
        return context

    # ------------------------------------------------------------------
    # Validation (internal)
    # ------------------------------------------------------------------

    def _validate_current_step(self) -> bool:
        """Validate the current step using its form_class.

        Returns True if valid or if the step has no form_class (informational
        steps are always valid).  Validation errors are stored in
        wizard_step_errors so templates can render them immediately.

        Important: validated data is NOT stored back as cleaned_data.
        Django's cleaned_data contains Python objects (datetime.date,
        Decimal, …) that are not JSON-serializable.  djust serialises public
        state to JSON between events, so storing non-serialisable objects
        causes them to become None on the next event.  Keep the original
        string input in wizard_step_data and parse in on_wizard_complete().
        """
        if not self._steps:
            return True

        step = self._steps[self.wizard_step_index]
        step_name = step["name"]
        form_class = step.get("form_class")

        if form_class is None:
            return True  # Informational step — no form, always valid

        form = form_class(data=self.wizard_step_data.get(step_name, {}))
        if form.is_valid():
            self.wizard_step_errors.pop(step_name, None)
            return True

        self.wizard_step_errors[step_name] = {
            field: list(errors) for field, errors in form.errors.items()
        }
        return False

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    @event_handler()
    def next_step(self, **kwargs):
        """Validate the current step and advance to the next one."""
        if not self._steps or self.wizard_step_index >= len(self._steps) - 1:
            return
        if self._validate_current_step():
            if self.wizard_step_index not in self.wizard_completed_steps:
                self.wizard_completed_steps.append(self.wizard_step_index)
            self.wizard_step_index += 1

    @event_handler()
    def prev_step(self, **kwargs):
        """Go back one step without validation."""
        if self.wizard_step_index > 0:
            self.wizard_step_index -= 1

    @event_handler()
    def go_to_step(self, step_index: int = 0, **kwargs):
        """Jump to a specific already-completed step for editing.

        Only allows jumping to completed steps or the current step to prevent
        skipping required validation.
        """
        if not self._steps or step_index < 0 or step_index >= len(self._steps):
            return
        if step_index in self.wizard_completed_steps or step_index == self.wizard_step_index:
            self.wizard_step_index = step_index

    @event_handler()
    def update_step_field(self, field: str = "", value: Any = "", **kwargs):
        """Store a single field value for the current step.

        Called by dj-change events on form inputs.  The ``field`` parameter
        comes from the ``data-field`` HTML attribute set by ``as_live_field()``.
        """
        if not field or not self._steps:
            return
        step_name = self._steps[self.wizard_step_index]["name"]
        if step_name not in self.wizard_step_data:
            self.wizard_step_data[step_name] = {}
        self.wizard_step_data[step_name][field] = value

    @event_handler()
    def validate_field(self, field: str = "", field_name: str = "", value: Any = "", **kwargs):
        """Store a field value triggered by as_live_field() dj-change events.

        ``as_live_field()`` generates ``dj-change="validate_field"`` and
        ``data-field="<name>"``.  djust maps data-* attributes to handler
        parameters, so the field name arrives as ``field``.  The ``field_name``
        parameter is accepted for backwards compatibility.
        """
        name = field or field_name
        if not name or not self._steps:
            return
        step_name = self._steps[self.wizard_step_index]["name"]
        if step_name not in self.wizard_step_data:
            self.wizard_step_data[step_name] = {}
        self.wizard_step_data[step_name][name] = value

    @event_handler()
    def submit_wizard(self, **kwargs):
        """Validate all steps and call on_wizard_complete() if everything passes.

        Security note: dj-click events can be replayed over the WebSocket so
        template-level restrictions on the submit button are insufficient.
        This handler guards against submission from any step other than the
        last one, and re-validates all previous steps before calling the hook.
        """
        if not self._steps:
            return

        last_index = len(self._steps) - 1
        if self.wizard_step_index != last_index:
            logger.warning(
                "submit_wizard called from step %d (not last step %d) — rejected",
                self.wizard_step_index,
                last_index,
            )
            return

        if not self._validate_current_step():
            return

        # Re-validate all previous steps to guard against tampered WebSocket data
        for step_idx in range(last_index):
            step = self._steps[step_idx]
            form_class = step.get("form_class")
            if form_class:
                step_name = step["name"]
                form = form_class(data=self.wizard_step_data.get(step_name, {}))
                if not form.is_valid():
                    self.wizard_step_errors[step_name] = {
                        f: list(e) for f, e in form.errors.items()
                    }
                    self.wizard_step_index = step_idx
                    logger.warning("submit_wizard: re-validation failed for step %s", step_name)
                    return

        if self.wizard_step_index not in self.wizard_completed_steps:
            self.wizard_completed_steps.append(self.wizard_step_index)
        self.on_wizard_complete(self.wizard_step_data)

    # ------------------------------------------------------------------
    # Hook for subclasses
    # ------------------------------------------------------------------

    def on_wizard_complete(self, step_data: dict[str, Any]):
        """Called when the wizard is fully completed and all steps are valid.

        Override this in your subclass to persist the collected data.

        Args:
            step_data:  ``{step_name: {field_name: raw_string_value}}`` for
                        every step that has a form_class.  Values are the raw
                        strings entered by the user, not cleaned_data Python
                        objects (see serialization note in module docstring).

        Example::

            def on_wizard_complete(self, step_data):
                from django.db import transaction
                with transaction.atomic():
                    personal = step_data["personal"]
                    MyModel.objects.create(
                        first_name=personal["first_name"],
                        birth_date=date.fromisoformat(personal["date_of_birth"]),
                    )
        """
