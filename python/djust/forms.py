"""
Django Forms integration for djust

This module provides seamless integration between Django's forms system and LiveView,
enabling real-time validation, error display, and reactive form handling.
"""

import logging
from typing import Dict, Any, Optional, Type, List, cast
from django import forms
from django.core.exceptions import ValidationError

from ._deprecation import warn_deprecated
from .decorators import event_handler

logger = logging.getLogger(__name__)


class FormMixin:
    """
    Mixin for LiveView classes to add Django Forms support with real-time validation.

    Usage:
        class MyFormView(FormMixin, LiveView):
            form_class = MyDjangoForm

            def form_valid(self, form):
                # Handle valid form submission
                form.save()
                self.success_message = "Form saved successfully!"

            def form_invalid(self, form):
                # Handle invalid form submission
                self.error_message = "Please correct the errors below"

    ModelForm usage:
        class MyModelFormView(FormMixin, LiveView):
            form_class = MyModelForm
            _model_instance = None  # Set in mount() for editing

            def mount(self, request, **kwargs):
                self._model_instance = MyModel.objects.get(pk=kwargs['pk'])
                super().mount(request, **kwargs)
    """

    form_class: Optional[Type[forms.Form]] = None
    # Private: non-serializable form object, re-created as needed
    _form_instance: Optional[forms.Form] = None
    _model_instance: Any = None

    # Reactive form state (initialized in mount()). Annotated at class level so
    # accessors (get_field_value / get_field_errors) carry precise return types.
    form_data: Dict[str, Any]
    form_choices: Dict[str, Any]
    form_errors: Any
    field_errors: Dict[str, List[str]]
    is_valid: bool
    success_message: str
    error_message: str

    def mount(self, request: Any, **kwargs: Any) -> None:
        """Initialize form on view mount"""
        super().mount(request, **kwargs)  # type: ignore[misc]  # mixin: LiveView provides mount()

        # Store model PK for re-hydration after WS serialization.
        # Public attrs so they survive get_context_data() → session save → WS restore.
        if self._model_instance and hasattr(self._model_instance, "pk"):
            self.model_pk = self._model_instance.pk
            self.model_label = self._model_instance._meta.label
        elif not hasattr(self, "model_pk"):
            self.model_pk = None
            self.model_label = ""

        self._init_form_state()

        # Create initial form instance (private, not serialized)
        if self.form_class:
            self._form_instance = self._create_form()

    def _init_form_state(self) -> None:
        """Populate every reactive form attribute from ``form_class``.

        The single chokepoint for form-state initialization. ``mount()`` calls
        it on the happy path; ``_ensure_form_state()`` calls it to repair a view
        whose ``FormMixin.mount()`` never ran (#2667).
        """
        # Initialize form state with all form fields set to empty strings
        # This ensures that when template renders {{ form_data.field_name }},
        # it doesn't render missing keys as empty, which would clear user input
        self.form_data = {}
        self.form_choices = {}
        if self.form_class:
            form = self.form_class()
            # Initialize all fields with their initial values or empty string
            for field_name, field in form.fields.items():
                initial = field.initial
                if initial is None:
                    initial = ""
                self.form_data[field_name] = initial

                # Expose serializable choices for template iteration
                if hasattr(field, "choices"):
                    self.form_choices[field_name] = [(str(k), str(v)) for k, v in field.choices]

            # If _model_instance is set and this is a ModelForm, populate from instance
            if self._model_instance and issubclass(self.form_class, forms.ModelForm):
                for field_name in form.fields:
                    if hasattr(self._model_instance, field_name):
                        val = getattr(self._model_instance, field_name)
                        if val is not None:
                            # For FK fields, store the PK not the related object
                            if hasattr(val, "pk"):
                                val = val.pk
                            self.form_data[field_name] = val

        self.form_errors = {}
        self.field_errors = {}
        self.is_valid = False
        self.success_message = ""
        self.error_message = ""

    def _ensure_form_state(self) -> None:
        """Repair form state when ``FormMixin.mount()`` never ran (#2667).

        ``form_data`` and friends are only class-level *annotations* — nothing
        exists on the instance until ``mount()`` assigns them. Two ordinary
        authoring mistakes skip that assignment entirely, and neither raises at
        mount time; both blow up later on the first event that reads the state:

        * a ``mount()`` override that forgets ``super().mount(request, **kwargs)``
        * declaring the bases as ``(LiveView, FormMixin)``, so ``LiveView.mount``
          wins the MRO and ``FormMixin.mount`` is never reached

        ``validate_field`` already carried an inline version of this guard; the
        other entry points had drifted without one (#1646). Rather than repeat
        the ``hasattr`` check per attribute, every entry point that *reads* form
        state now routes through here.
        """
        if hasattr(self, "form_data"):
            return
        logger.warning(
            "%s reached a FormMixin handler without form state — FormMixin.mount() "
            "never ran. %s Initializing form state now so the request can proceed.",
            type(self).__name__,
            self._diagnose_unrun_mount(),
        )
        self._init_form_state()

    def _diagnose_unrun_mount(self) -> str:
        """Name the specific mistake that kept ``FormMixin.mount()`` from running.

        The two shapes need OPPOSITE advice, so one generic sentence is wrong for
        whichever view is reading it: telling a class that has no ``mount()`` at
        all to "call super().mount() from your mount()" sends the author looking
        for a method they never wrote.

        The discriminator is whether the class itself defines ``mount`` — not
        which class wins the MRO. A first pass used the MRO winner's module
        prefix and got BOTH shapes wrong: a test view living under a ``djust.``
        package looked like a framework class, and the reversed-bases view
        resolved to ``ComponentMixin`` rather than the ``LiveView`` the author
        actually typed, so the advice named a class absent from their source.
        """
        cls = type(self)
        bases = cls.__bases__

        if "mount" in cls.__dict__:
            return (
                f"{cls.__name__} overrides mount() without calling "
                f"super().mount(request, **kwargs) — add that call as the first line."
            )

        if FormMixin in bases and bases.index(FormMixin) > 0:
            ordered = ", ".join(b.__name__ for b in sorted(bases, key=lambda b: b is not FormMixin))
            typed = ", ".join(b.__name__ for b in bases)
            return (
                f"{cls.__name__} does not define mount(), and FormMixin is not first "
                f"in its bases, so another class's mount() wins the MRO. Change "
                f"({typed}) to ({ordered}) — FormMixin must come FIRST."
            )

        mro = cls.__mro__
        owner = next((k for k in mro if "mount" in k.__dict__), None)
        if owner is None or owner is FormMixin:
            return (
                "FormMixin.mount() is the resolved mount() for this class, so the "
                "state was cleared after mount, or mount() was never called at all."
            )
        return (
            f"{owner.__name__}.mount() resolves ahead of FormMixin.mount(); make sure "
            f"FormMixin comes first in the bases and that every mount() override "
            f"calls super().mount(request, **kwargs)."
        )

    @property
    def form_instance(self) -> Optional[forms.Form]:
        """Access the form instance (re-creates if lost after serialization)."""
        if self._form_instance is None and self.form_class:
            self._ensure_model_instance()
            self._form_instance = self._create_form()
        return self._form_instance

    @form_instance.setter
    def form_instance(self, value: Optional[forms.Form]) -> None:
        self._form_instance = value

    def _ensure_model_instance(self) -> None:
        """Re-hydrate _model_instance from stored PK if lost after WS serialization."""
        if self._model_instance is not None:
            return
        if not getattr(self, "model_pk", None):
            return
        try:
            from django.apps import apps

            model = apps.get_model(self.model_label)
            self._model_instance = model.objects.get(pk=self.model_pk)
        except Exception:
            logger.warning(
                "Failed to re-hydrate model instance (label=%s, pk=%s)",
                getattr(self, "model_label", ""),
                getattr(self, "model_pk", None),
                exc_info=True,
            )
            self._model_instance = None

    def _create_form(self, data: Optional[Dict[str, Any]] = None) -> forms.Form:
        """
        Create a form instance with optional data.

        Args:
            data: Form data dictionary

        Returns:
            Django Form instance
        """
        if not self.form_class:
            raise ValueError("form_class must be set to use FormMixin")

        kwargs: Dict[str, Any] = {}
        if self._model_instance and issubclass(self.form_class, forms.ModelForm):
            kwargs["instance"] = self._model_instance

        if data:
            return self.form_class(data, **kwargs)
        else:
            return self.form_class(**kwargs)

    @event_handler
    def validate_field(
        self, field: str = "", field_name: str = "", value: Any = None, **kwargs: Any
    ) -> None:
        """
        Validate a single field in real-time.

        This is called when a field changes (dj-change / dj-input event).

        The client sends the field name under the key ``field``, because
        ``buildFormEventParams`` (``09-event-binding.js:525``) HARDCODES that
        key, sourcing the value from ``getFieldName`` — ``data-field``, then
        the element's ``name``, then its ``id``. It merges only ``dj-value-*``;
        it does NOT collect ``data-*``, which is what ``extractTypedParams``
        does on the click/poll/mount paths. (An earlier version of this
        docstring said "djust maps ``data-*`` attributes to handler
        parameters" — true for those other paths, false for this one, and a
        maintainer acting on it would expect ``data-foo`` on an input to reach
        the handler. It does not.) ``field_name`` is
        accepted for backwards compatibility and is what this signature used
        to take EXCLUSIVELY — which meant the client's payload matched nothing,
        ``**kwargs`` swallowed it instead of raising ``TypeError``, and the
        handler ran on every keystroke doing nothing at all (#2137). No error,
        no warning; the form looked live and was inert.

        ``WizardMixin.validate_field`` already had this coalesce; this is the
        same shape, applied to the two implementations that did not (#1646).

        POSITIONAL CALLERS: ``field`` is the first parameter, so
        ``validate_field("email", "text")`` binds ``field_name="text"`` and
        leaves ``value`` as ``None`` — a filled field then reports "required".
        Call with keywords. The order is deliberate: djust admin's own adapters
        emit ``validate_field('<name>', value)`` (``admin_ext/adapters.py``, 8
        sites), whose second positional token is the literal string
        ``"value"``; with ``field`` first that junk lands in ``field_name`` and
        the real value survives as a keyword. Under the previous order it
        overwrote the real value, so the admin validated the string "value" on
        every change.

        Args:
            field: Name of the field to validate — what the client sends
            field_name: Legacy alias for ``field``
            value: Current field value
        """
        name = field or field_name
        if not name:
            return
        field_name = name

        # Ensure form state is initialized (defensive check — see #2667)
        self._ensure_form_state()

        # Update form data
        self.form_data[field_name] = value

        # Re-hydrate model instance if needed
        self._ensure_model_instance()

        # Create form with current data
        form = self._create_form(self.form_data)

        # Clear previous error for this field
        if field_name in self.field_errors:
            del self.field_errors[field_name]

        # Validate the specific field
        try:
            # Get the field
            # `form_field`, not `field`: `field` is now a PARAMETER of this
            # method (the key the client sends), and rebinding it here shadowed
            # it — mypy caught it as `"str" has no attribute "clean"`. Nothing
            # downstream read the parameter, so it was latent rather than
            # broken, but a later edit that did would have got a form field
            # where it expected a name.
            form_field = form.fields.get(field_name)
            if form_field:
                # Clean the value
                cleaned_value = form_field.clean(value)

                # Run field-specific validators
                form_field.run_validators(cleaned_value)

                # Set up cleaned_data for custom clean methods
                if not hasattr(form, "cleaned_data"):
                    form.cleaned_data = {}
                form.cleaned_data[field_name] = cleaned_value

                # Run form's clean method for this field if it exists
                clean_method = getattr(form, f"clean_{field_name}", None)
                if clean_method:
                    clean_method()

        except ValidationError as e:
            # Store field error
            self.field_errors[field_name] = e.messages

        # Update form instance
        self._form_instance = form

    @event_handler
    def submit_form(self, **kwargs: Any) -> None:
        """
        Handle form submission.

        This is called when the form is submitted (dj-submit event).
        Validates all fields and calls form_valid() or form_invalid().
        """
        self._ensure_form_state()

        # Merge kwargs into form_data (for fields submitted with the form)
        self.form_data.update(kwargs)

        # Re-hydrate model instance if lost after WS serialization
        self._ensure_model_instance()

        # Create form with all data
        form = self._create_form(self.form_data)

        # Validate entire form
        if form.is_valid():
            self.is_valid = True
            self.field_errors = {}
            self.form_errors = {}
            self._form_instance = form

            # Call form_valid hook
            if hasattr(self, "form_valid"):
                self.form_valid(form)

            # Sync form_data from saved instance so VDOM reflects new values
            self._sync_form_data(form)
        else:
            self.is_valid = False

            # Store all errors
            self.field_errors = {field: errors for field, errors in form.errors.items()}

            # Store non-field errors
            if form.non_field_errors():
                self.form_errors = form.non_field_errors()

            self._form_instance = form

            # Call form_invalid hook
            if hasattr(self, "form_invalid"):
                self.form_invalid(form)

    def _sync_form_data(self, form: forms.Form) -> None:
        """Sync form_data from the form's cleaned/saved values.

        After form_valid(), update form_data so the VDOM diff sends patches
        reflecting the saved state. For ModelForms, reads from the saved
        instance; for plain forms, reads from cleaned_data.
        """
        source = None
        if isinstance(form, forms.ModelForm) and hasattr(form, "instance"):
            source = form.instance
        for field_name in form.fields:
            if source is not None and hasattr(source, field_name):
                val = getattr(source, field_name)
                # FK fields: store PK, not the related object
                if hasattr(val, "pk"):
                    val = val.pk
            elif hasattr(form, "cleaned_data"):
                val = form.cleaned_data.get(field_name)
            else:
                continue
            self.form_data[field_name] = val if val is not None else ""

    def reset_form(self, **kwargs: Any) -> None:
        """Reset form to initial state"""
        # reset_form() writes every attribute below EXCEPT form_choices, so an
        # unmounted view would still be missing that one (#2667).
        self._ensure_form_state()

        # Reset form_data with all field keys initialized (matching mount() behavior)
        # This ensures consistent VDOM state and prevents alternating patches/html_update
        self.form_data = {}
        if self.form_class:
            form = self.form_class()
            # Initialize all fields with their initial values or empty string
            for field_name, field in form.fields.items():
                initial = field.initial
                if initial is None:
                    initial = ""
                self.form_data[field_name] = initial

        self.form_errors = {}
        self.field_errors = {}
        self.is_valid = False
        self.success_message = ""
        self.error_message = ""

        if self.form_class:
            self._form_instance = self._create_form()

        # Signal to WebSocket handler that we need to reset the form on client-side
        # This bypasses VDOM form value preservation
        self._should_reset_form = True

    def get_field_value(self, field_name: str, default: Any = "") -> Any:
        """Get current value for a field"""
        self._ensure_form_state()
        return self.form_data.get(field_name, default)

    def get_field_errors(self, field_name: str) -> List[str]:
        """Get errors for a specific field"""
        self._ensure_form_state()
        return self.field_errors.get(field_name, [])

    def has_field_errors(self, field_name: str) -> bool:
        """Check if a field has errors"""
        self._ensure_form_state()
        return field_name in self.field_errors

    def as_live(self, **kwargs: Any) -> str:
        """
        Render the entire form automatically using the configured CSS framework.

        This eliminates the need for manual field-by-field rendering. The form
        will use the framework adapter (Bootstrap 5, Tailwind, etc.) to render
        all fields with proper styling, labels, errors, and event handlers.

        Args:
            **kwargs: Rendering options
                - framework: Override the configured CSS framework
                - render_labels: Whether to render field labels (default: True)
                - render_help_text: Whether to render help text (default: True)
                - render_errors: Whether to render errors (default: True)
                - auto_validate: Whether to add validation on change (default: True)
                - wrapper_class: Custom wrapper class for each field

        Returns:
            HTML string for the entire form

        Example:
            # In template:
            <form dj-submit="submit_form">
                {{ form.as_live }}
                <button type="submit">Submit</button>
            </form>
        """
        from .frameworks import get_adapter

        fi = self.form_instance
        if not fi:
            return "<!-- ERROR: form_instance not initialized. Did you call super().mount()? -->"

        framework = kwargs.pop("framework", None)
        adapter = get_adapter(framework)

        html = ""
        for field_name in fi.fields.keys():
            html += self.as_live_field(field_name, adapter=adapter, **kwargs)

        return html

    def as_live_field(self, field_name: str, adapter: Any = None, **kwargs: Any) -> str:
        """
        Render a single form field automatically using the configured CSS framework.

        This method uses the framework adapter to render a field with proper styling,
        labels, errors, help text, and LiveView event handlers automatically.

        Args:
            field_name: Name of the field to render
            adapter: Framework adapter to use (if None, uses configured framework)
            event_name: Override the dj-change event handler name (default: "validate_field").
                Use this when integrating with a multi-step wizard or custom handler:
                ``{{ form.as_live_field('first_name', event_name='update_step_field')|safe }}``
            **kwargs: Rendering options

        Returns:
            HTML string for the field
        """
        from .frameworks import get_adapter

        fi = self.form_instance
        if not fi:
            return ""

        field = fi.fields.get(field_name)
        if not field:
            return ""

        # Get adapter
        if adapter is None:
            framework = kwargs.pop("framework", None)
            adapter = get_adapter(framework)

        # Get current value and errors
        value = self.get_field_value(field_name, default="")
        errors = self.get_field_errors(field_name)

        # Render using adapter
        return cast(str, adapter.render_field(field, field_name, value, errors, **kwargs))


class LiveViewForm(forms.Form):
    """
    Base form class for LiveView usage.

    .. deprecated:: 0.3
        ``LiveViewForm`` adds no functionality over ``forms.Form`` and will
        be removed no earlier than djust 1.1.0. Use ``django.forms.Form``
        directly instead.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # stacklevel=5: __init_subclass__ runs inside Django's
        # DeclarativeFieldsMetaclass __new__ chain, which adds two metaclass
        # frames. Chain: warnings.warn -> warn_deprecated -> __init_subclass__
        # -> widgets.py metaclass __new__ -> forms.py metaclass __new__ ->
        # user `class` statement. Empirically verified (scratch sweep): 5
        # points the warning at the user's file, not forms.py / widgets.py.
        warn_deprecated(
            "LiveViewForm",
            since="0.3",
            removed_in="1.1.0",
            instead="django.forms.Form",
            stacklevel=5,
        )

    def get_field_errors_json(self) -> str:
        """Get field errors as JSON string"""
        import json

        return json.dumps({field: errors for field, errors in self.errors.items()})

    def get_field_value(self, field_name: str, default: Any = "") -> Any:
        """Get cleaned value for a field"""
        if hasattr(self, "cleaned_data"):
            return self.cleaned_data.get(field_name, default)
        return self.data.get(field_name, default)
