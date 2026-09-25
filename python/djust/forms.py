"""
Django Forms integration for djust

This module provides seamless integration between Django's forms system and LiveView,
enabling real-time validation, error display, and reactive form handling.
"""

import logging
import math
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Generic,
    List,
    Mapping,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
)
from django import forms
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import models
from django.http import Http404
from django.utils.safestring import SafeString

from ._deprecation import warn_deprecated
from ._exposure import ExposureConfigurationError, ExposureError, ProviderContract
from ._exposure_providers import provide_context_items
from ._state import StateProperty
from .decorators import event_handler

logger = logging.getLogger(__name__)

#: ADR-038 E2-3: the form keys an explicit view renders. Render-only: the
#: provider persists and discloses nothing (``persisted``/``client`` empty).
#: ``model_pk``/``model_label`` are deliberately absent; see
#: ``FormMixin._ensure_model_instance``.
FORM_PROVIDER = ProviderContract(
    "djust.forms",
    rendered=frozenset(
        {
            "form_data",
            "form_choices",
            "form_errors",
            "field_errors",
            "is_valid",
            "success_message",
            "error_message",
        }
    ),
    tracked=frozenset({"form_data", "form_errors", "field_errors", "is_valid"}),
)

#: Replacement for an error message that would echo a sensitive field's value.
_REDACTED_FORM_ERROR = "Invalid value."


def is_sensitive_form_field(name: str, field: forms.Field) -> bool:
    """Whether a form field's value may never be persisted, rendered or debugged.

    ADR-038 D-e: a ``PasswordInput`` widget (or subclass), or a field name in the
    serialization floor (``password``, ``is_superuser``, ``is_staff``) unioned
    with ``settings.DJUST_SENSITIVE_FIELDS``.
    """
    from .serialization import _resolve_sensitive_fields

    return isinstance(field.widget, forms.PasswordInput) or name in _resolve_sensitive_fields()


def _is_persistable_input(value: Any) -> bool:
    """JSON primitives only (D-i); anything else resets on reconnect."""
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(type(item) is str for item in value)
    return False


class PersistedFormInput(StateProperty[Dict[str, Any]]):
    """Server-persisted input for named, non-sensitive form fields (ADR-038 D-e).

    Declare with :func:`persisted_form_input`. The field is an ordinary
    ``state(persist="server")`` declaration in the view's exposure contract,
    but its value is a projection of ``form_data`` rather than stored state:
    reading it selects the declared fields from ``form_data`` (JSON primitives
    only), and restoring it merges those fields back into ``form_data`` after
    ``mount()``. Nothing else of the form is persisted.
    """

    def __init__(self, field_names: FrozenSet[str]) -> None:
        super().__init__(default_factory=dict, persist="server")
        self.field_names = field_names

    def __get__(self, obj: Any, objtype: Optional[type] = None) -> Any:
        if obj is None:
            return self
        data = obj.__dict__.get("form_data")
        if type(data) is not dict:
            return {}
        form = obj.__dict__.get("_form_instance")
        fields = getattr(form, "fields", None) or {}
        selected: Dict[str, Any] = {}
        for name in sorted(self.field_names):
            if name not in data or not _is_persistable_input(data[name]):
                continue
            field = fields.get(name)
            # Defence in depth: configuration already refuses sensitive fields.
            if field is not None and is_sensitive_form_field(name, field):
                continue
            selected[name] = data[name]
        return selected

    def __set__(self, obj: Any, value: Dict[str, Any]) -> None:
        if (
            type(value) is not dict
            or not set(value) <= self.field_names
            or not all(_is_persistable_input(item) for item in value.values())
        ):
            raise ExposureError("Persisted form input does not match its declaration")
        data = obj.__dict__.get("form_data")
        if type(data) is not dict:
            data = {}
            obj.form_data = data
        data.update(value)


def persisted_form_input(*field_names: str) -> Any:
    """Opt named form fields into server persistence under the explicit policy.

    ADR-038 D-e: form input and errors are not persisted by default. Declare
    ``form_input = persisted_form_input("name", "email")`` on a ``FormMixin``
    view to restore those fields' input across reconnect and HTTP POST;
    every other field, all errors and ``is_valid`` reset to their mount
    values. A field with a ``PasswordInput`` widget or a sensitive name can
    never be listed: that is an ``ExposureConfigurationError`` when the class
    is defined (static ``form_class``) or mounted (``get_form_class()``).
    Like any ``state()`` grant, it requires ``exposure_policy = "explicit"``.
    """
    names = frozenset(field_names)
    if not names or any(type(name) is not str or not name for name in field_names):
        raise ExposureConfigurationError("persisted_form_input() requires form field names")
    return PersistedFormInput(names)


def _persisted_form_inputs(view_class: type) -> List[PersistedFormInput]:
    """Declarations visible on the class, read from class dictionaries only."""
    found: List[PersistedFormInput] = []
    seen: set = set()
    for owner in view_class.__mro__:
        for name, declaration in vars(owner).items():
            if name in seen:
                continue
            seen.add(name)
            if isinstance(declaration, PersistedFormInput):
                found.append(declaration)
    return found


def _check_persisted_form_input(view_class: type, form_class: Optional[Type[forms.Form]]) -> None:
    """Refuse an opt-in that names a sensitive, unknown or reserved field."""
    declarations = _persisted_form_inputs(view_class)
    if not declarations:
        return
    for declaration in declarations:
        if declaration.public_name in FormMixin._djust_injects_context:
            raise ExposureConfigurationError(
                "persisted_form_input() cannot be assigned to a FormMixin context name"
            )
    if form_class is None:
        return
    fields = form_class.base_fields
    for declaration in declarations:
        for name in declaration.field_names:
            field = fields.get(name)
            if field is None:
                raise ExposureConfigurationError(
                    "persisted_form_input() names a field the form does not define"
                )
            if is_sensitive_form_field(name, field):
                raise ExposureConfigurationError(
                    "persisted_form_input() cannot persist a password or sensitive form field"
                )


def initial_field_value(form: Any, name: str, field: Any) -> Any:
    """A field's initial value as Django's bound field sees it, or ``""``.

    ``get_initial_for_field`` calls a callable ``initial``
    (``UUIDField(initial=uuid.uuid4)``, ``initial=timezone.now``) instead of
    returning the function, and lets the form's own ``initial`` (and a
    ModelForm's ``instance``) win. ``prepare_value`` then gives the value an
    unbound ``form[name].value()`` renders: a related object becomes its pk
    (ADR-035). Only ``None`` becomes ``""``: ``0`` and ``False`` are real
    initial values.
    """
    # ``BoundField.initial`` is ``get_initial_for_field`` cached on the form's
    # bound field, so a callable initial is called once per form and the form
    # instance renders the same value this returns.
    initial = field.prepare_value(form[name].initial)
    return "" if initial is None else initial


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
    initial: Dict[str, Any] = {}
    prefix: Optional[str] = None
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

    # Template-visible state this mixin injects at runtime (#2827). The
    # `djust_typecheck` / T018 static context extraction deliberately skips
    # framework modules (djust.*) when AST-walking the MRO, so these runtime
    # `self.x = ...` assignments would otherwise be invisible and every
    # `{{ form_data }}`-style reference on a FormMixin-based view would
    # false-positive. Framework mixins declare their injected context keys
    # here instead; `_extract_context_keys_from_ast` reads the manifest
    # without needing to AST-walk framework source. Keep in sync with the
    # assignments in mount()/_init_form_state()/form handlers — pinned by
    # test_form_mixin_manifest_covers_all_runtime_assignments.
    _djust_injects_context = frozenset(
        {
            "form_data",
            "form_choices",
            "form_errors",
            "field_errors",
            "is_valid",
            "success_message",
            "error_message",
            "model_pk",
            "model_label",
        }
    )

    # ADR-038 E2-3: under the explicit policy the form keys are a registered,
    # render-only provider (see FORM_PROVIDER and get_context_data).
    _djust_context_providers: Tuple[
        Union[ProviderContract, Callable[[type], ProviderContract]], ...
    ] = (FORM_PROVIDER,)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Configuration-time refusal of a sensitive persistence opt-in (D-e).
        # A dynamic get_form_class() is checked again at mount.
        _check_persisted_form_input(cls, cls.__dict__.get("form_class") or cls.form_class)

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        """Provide the form's keys to explicit templates; legacy is unchanged."""
        context: Dict[str, Any] = super().get_context_data(**kwargs)  # type: ignore[misc]  # mixin: LiveView provides get_context_data()
        from ._exposure import uses_legacy_exposure

        if not uses_legacy_exposure(self):
            provide_context_items(self, context, FORM_PROVIDER.name, self._explicit_form_context())
        return context

    def _sensitive_form_field_names(self) -> FrozenSet[str]:
        form = self._form_instance
        if form is None and self.get_form_class():
            form = self.form_instance
        if form is None:
            return frozenset()
        return frozenset(
            name for name, field in form.fields.items() if is_sensitive_form_field(name, field)
        )

    def _explicit_form_context(self) -> Dict[str, Any]:
        """Render values with sensitive fields' input blanked (ADR-038 D-e).

        A sensitive field renders as empty, like Django's ``PasswordInput``
        (``render_value=False``); an error message that contains a sensitive
        field's value is replaced by a generic one. Errors themselves render.
        """
        form_data: Dict[str, Any] = dict(getattr(self, "form_data", None) or {})
        sensitive = self._sensitive_form_field_names()
        secrets: List[str] = []
        for name in sensitive:
            value = form_data.get(name)
            items = value if isinstance(value, (list, tuple)) else [value]
            secrets.extend(str(item) for item in items if item not in (None, ""))
            if name in form_data:
                form_data[name] = ""

        def scrub(messages: Any) -> List[str]:
            texts = [str(message) for message in messages]
            return [
                _REDACTED_FORM_ERROR if any(secret in text for secret in secrets) else text
                for text in texts
            ]

        field_errors = {
            name: scrub(errors)
            for name, errors in (getattr(self, "field_errors", None) or {}).items()
        }
        form_errors = getattr(self, "form_errors", None) or {}
        if isinstance(form_errors, dict):
            form_errors = {name: scrub(errors) for name, errors in form_errors.items()}
        else:
            form_errors = scrub(form_errors)
        return {
            "form_data": form_data,
            "form_choices": dict(getattr(self, "form_choices", None) or {}),
            "form_errors": form_errors,
            "field_errors": field_errors,
            "is_valid": bool(getattr(self, "is_valid", False)),
            "success_message": getattr(self, "success_message", ""),
            "error_message": getattr(self, "error_message", ""),
        }

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
        if self._form_instance is None and self.get_form_class():
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
        if self.get_form_class():
            form = self._create_form()
            self._form_instance = form
            self.form_data = self._initial_form_data(form)
            for field_name, field in form.fields.items():
                # Expose serializable choices for template iteration
                if hasattr(field, "choices"):
                    self.form_choices[field_name] = [(str(k), str(v)) for k, v in field.choices]
            # ADR-038 D-e: refuse a sensitive opt-in for a dynamic form class.
            _check_persisted_form_input(type(self), type(form))
        elif _persisted_form_inputs(type(self)):
            raise ExposureConfigurationError("persisted_form_input() requires a form class")

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
        if self._form_instance is None and self.get_form_class():
            self._ensure_model_instance()
            self._form_instance = self._create_form()
        return self._form_instance

    @form_instance.setter
    def form_instance(self, value: Optional[forms.Form]) -> None:
        self._form_instance = value

    def _ensure_model_instance(self) -> None:
        """Re-hydrate _model_instance from stored PK if lost after WS serialization.

        Legacy only. Under ADR-038's explicit policy ``model_pk`` is neither
        persisted nor rendered, and a raw primary key is never re-resolved with
        an unscoped ``objects.get``: every restore (reconnect, HTTP POST) runs
        ``mount()`` first, which must establish ``_model_instance`` through the
        view's own authorized lookup. ADR-035's managed-object identity is the
        eventual owner of a persisted model reference.
        """
        if self._model_instance is not None:
            return
        if not getattr(self, "model_pk", None):
            return
        from ._exposure import uses_legacy_exposure

        if not uses_legacy_exposure(self):
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

    def get_form_class(self) -> Optional[Type[forms.Form]]:
        """Return the configured form class; override for dynamic forms."""
        return self.form_class

    def get_initial(self) -> Dict[str, Any]:
        """Return a fresh initial-value mapping, following Django's form hook."""
        return self.initial.copy()

    def get_prefix(self) -> Optional[str]:
        """Return the HTML field-name prefix, if configured."""
        return self.prefix

    def get_form_kwargs(self) -> Dict[str, Any]:
        """Build form arguments for initial rendering or the current event.

        Event data is supplied by the compatibility bridge, not request.POST.
        Overrides may add constructor arguments (including authorized files).
        """
        kwargs: Dict[str, Any] = {"initial": self.get_initial(), "prefix": self.get_prefix()}
        data = getattr(self, "_form_binding_data", None)
        if data is not None:
            kwargs["data"] = data
        form_class = self.get_form_class()
        if (
            self._model_instance is not None
            and form_class
            and issubclass(form_class, forms.ModelForm)
        ):
            kwargs["instance"] = self._model_instance
        return kwargs

    def get_form(self, form_class: Optional[Type[forms.Form]] = None) -> forms.Form:
        """Construct a form through the public class, initial, prefix and kwargs hooks."""
        form_class = form_class or self.get_form_class()
        if form_class is None:
            raise ValueError("form_class must be set to use FormMixin")
        return form_class(**self.get_form_kwargs())

    def _create_form(self, data: Optional[Dict[str, Any]] = None) -> forms.Form:
        """Compatibility entry point: existing overrides delegate to public hooks.

        Preserve nested construction and remove transient binding data even when
        a custom hook raises. An empty mapping deliberately creates a bound form.
        """
        had_binding = "_form_binding_data" in self.__dict__
        previous = self.__dict__.get("_form_binding_data")
        self._form_binding_data = data
        try:
            return self.get_form()
        finally:
            if had_binding:
                self._form_binding_data = previous
            else:
                del self._form_binding_data

    def _form_event_field_name(self, name: str) -> str:
        """Map an HTML-prefixed field name back to reactive form state."""
        prefix = self.get_prefix()
        marker = f"{prefix}-" if prefix else ""
        return name[len(marker) :] if marker and name.startswith(marker) else name

    def _form_event_data(self) -> Dict[str, Any]:
        """Bind logical state keys using Django's configured HTML prefix."""
        prefix = self.get_prefix()
        if not prefix:
            return self.form_data
        return {f"{prefix}-{name}": value for name, value in self.form_data.items()}

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
        field_name = self._form_event_field_name(name)

        # Ensure form state is initialized (defensive check — see #2667)
        self._ensure_form_state()

        # Update form data
        self.form_data[field_name] = value

        # Re-hydrate model instance if needed
        self._ensure_model_instance()

        # Create form with current data
        form = self._create_form(self._form_event_data())

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
        self.form_data.update(
            {self._form_event_field_name(name): value for name, value in kwargs.items()}
        )

        # Re-hydrate model instance if lost after WS serialization
        self._ensure_model_instance()

        # Create form with all data
        form = self._create_form(self._form_event_data())

        # Validate entire form
        if form.is_valid():
            self.is_valid = True
            self.field_errors = {}
            self.form_errors = {}
            self._form_instance = form

            # A ``reset_form()`` inside ``form_valid`` raises
            # ``_should_reset_form``; clear it first so a reset made by THIS
            # hook can be told apart from one still pending from earlier.
            reset_pending = getattr(self, "_should_reset_form", False)
            self._should_reset_form = False

            # Call form_valid hook
            if hasattr(self, "form_valid"):
                self.form_valid(form)

            # Sync form_data from the saved instance so the VDOM reflects the
            # new values — unless form_valid reset the form: syncing the
            # submitted values back would undo the reset (#2974).
            if not self._should_reset_form:
                self._sync_form_data(form)
            self._should_reset_form = self._should_reset_form or reset_pending
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

    @staticmethod
    def _initial_form_data(form: Any) -> Dict[str, Any]:
        """Each field's initial value, or ``""`` when it has none."""
        return {name: initial_field_value(form, name, field) for name, field in form.fields.items()}

    @event_handler
    def reset_form(self, **kwargs: Any) -> None:
        """Reset the form to its initial state.

        An event handler, so a template can call it directly
        (``dj-click="reset_form"``), and safe to call from ``form_valid``:
        ``submit_form`` does not sync the submitted values back over a reset
        (#2974).
        """
        # reset_form() writes every attribute below EXCEPT form_choices, so an
        # unmounted view would still be missing that one (#2667).
        self._ensure_form_state()

        # Reset form_data with all field keys initialized (matching mount() behavior)
        # This ensures consistent VDOM state and prevents alternating patches/html_update
        self.form_data = {}
        if self.get_form_class():
            form = self._create_form()
            self._form_instance = form
            self.form_data = self._initial_form_data(form)

        self.form_errors = {}
        self.field_errors = {}
        self.is_valid = False
        self.success_message = ""
        self.error_message = ""

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
            missing: str = SafeString(
                "<!-- ERROR: form_instance not initialized. Did you call super().mount()? -->"
            )
            return missing

        framework = kwargs.pop("framework", None)
        adapter = get_adapter(framework)

        html = ""
        for field_name in fi.fields.keys():
            html += self.as_live_field(field_name, adapter=adapter, **kwargs)

        # #3043: markup built by the adapters, every value in it escaped.
        form_html: str = SafeString(html)
        return form_html

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

        # Render using adapter. #3043: the result is markup whose values the
        # adapter escaped (the ``FrameworkAdapter`` contract), returned as a
        # ``SafeString`` so ``{% live_field %}`` / ``{{ form_html }}`` render it
        # instead of escaping it into visible text.
        field_html: str = SafeString(
            adapter.render_field(field, field_name, value, errors, **kwargs)
        )
        return field_html


_ModelT = TypeVar("_ModelT", bound=models.Model)

#: ADR-035 D4: the edit adapter's configuration and framework slots. They are
#: never inferred template context and never persisted or snapshotted state.
_MODEL_FORM_CONFIGURATION = frozenset(
    {
        "model",
        "queryset",
        "pk_url_kwarg",
        "slug_url_kwarg",
        "slug_field",
        "query_pk_and_slug",
        "context_object_name",
        "kwargs",
        "object",
    }
)

_MANAGED_OBJECT_PROVIDER = "djust.forms.object"

_RETARGET_MESSAGE = (
    "self.object can only be replaced by the same record, as in "
    "`self.object = form.save()`. To edit a different record, navigate to its URL."
)


def _managed_object_provider(view_class: type) -> ProviderContract:
    """Render-only ``object`` (and the opt-in ``context_object_name``)."""
    keys = {"object"}
    alias = getattr(view_class, "context_object_name", None)
    if alias:
        keys.add(alias)
    return ProviderContract(_MANAGED_OBJECT_PROVIDER, rendered=frozenset(keys))


class ModelFormMixin(FormMixin, Generic[_ModelT]):
    """Edit one authorized model instance with a ``ModelForm`` (ADR-035).

    Usage::

        class EditProjectView(ModelFormMixin[Project], LiveView):
            template_name = "projects/edit.html"
            model = Project
            form_class = ProjectForm  # a ModelForm naming its editable fields
            login_required = True

            def get_queryset(self):
                return super().get_queryset().filter(owner=self.request.user)

            def has_object_permission(self, request, obj):
                return obj.owner_id == request.user.pk

            def form_valid(self, form):
                self.object = form.save()
                self.success_message = "Saved!"

    The route supplies the lookup (``pk_url_kwarg`` / ``slug_url_kwarg``);
    ``self.kwargs`` is the route's resolved URL kwargs on every transport, never
    client mount parameters. Before the form exists, ``mount()`` resolves the
    object through ``get_queryset()``/``get_object()`` and authorizes it with
    ``has_object_permission()`` (ADR-017). Every later event resolves and
    authorizes it again before the form is built. A missing, filtered-out or
    forbidden object is the same ``PermissionDenied``; nothing is created in
    its place.

    ``self.object`` is the object authorized for the current dispatch. It
    renders as ``object`` (plus ``context_object_name``, if set) and is never
    persisted: only the route identifies the record. It can be replaced by the
    same record (``self.object = form.save()``); anything else is a
    ``ValueError``. ``form_valid`` decides whether and how to save.
    """

    model: Optional[Type[_ModelT]] = None
    queryset: Optional["models.QuerySet[_ModelT]"] = None
    pk_url_kwarg: str = "pk"
    slug_url_kwarg: str = "slug"
    slug_field: str = "slug"
    query_pk_and_slug: bool = False
    context_object_name: Optional[str] = None

    kwargs: Dict[str, Any]

    # The ADR-017 object lifecycle treats "no object" as a denial for this view.
    _djust_object_required = True
    _djust_configuration_names = _MODEL_FORM_CONFIGURATION
    _djust_injects_context = FormMixin._djust_injects_context | frozenset({"object"})
    _djust_context_providers = (_managed_object_provider,)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Allocated before LiveView records its framework slots, so the legacy
        # session never mistakes the verdict for user private state.
        self._djust_authorized_object: Optional[tuple] = None
        super().__init__(*args, **kwargs)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for owner in cls.__mro__:
            if owner is ModelFormMixin:
                break
            if "_model_instance" in vars(owner):
                raise ImproperlyConfigured(
                    "%s uses ModelFormMixin and _model_instance together. "
                    "ModelFormMixin resolves self.object from the route; "
                    "remove _model_instance." % cls.__name__
                )
        alias = cls.context_object_name
        if alias is not None and (
            type(alias) is not str
            or not alias.isidentifier()
            or alias.startswith("_")
            or alias == "object"
            or alias in FormMixin._djust_injects_context
        ):
            raise ImproperlyConfigured(
                "%s.context_object_name must be a template name other than "
                "'object' and the form's own context names." % cls.__name__
            )

    # -- the managed object --------------------------------------------------

    @property
    def object(self) -> Optional[_ModelT]:
        """The object authorized for the current dispatch, or ``None``."""
        obj: Optional[_ModelT] = self.__dict__.get("_object")
        return obj

    @object.setter
    def object(self, value: Optional[_ModelT]) -> None:
        current = self.__dict__.get("_object")
        if value is current:
            return
        if (
            current is None
            or not isinstance(value, models.Model)
            or value._meta.concrete_model is not current._meta.concrete_model
            or value.pk is None
            or value.pk != current.pk
        ):
            raise ValueError(_RETARGET_MESSAGE)
        self._object = value

    def _djust_bind_route_kwargs(self, route_kwargs: Optional[Mapping[str, Any]]) -> None:
        """Framework hook: the route's resolved kwargs, bound before mount/restore.

        ``None`` means the transport could not resolve this view's own route;
        the lookup then fails closed.
        """
        self.kwargs = dict(route_kwargs) if route_kwargs is not None else {}

    def _djust_render_only_context(self) -> Dict[str, Any]:
        """The managed object's template names (render-only, never persisted)."""
        obj = self.object
        items: Dict[str, Any] = {"object": obj}
        if self.context_object_name:
            items[self.context_object_name] = obj
        return items

    def _djust_render_only_context_keys(self) -> FrozenSet[str]:
        return frozenset(self._djust_render_only_context())

    # -- Django's single-object vocabulary -----------------------------------

    def get_queryset(self) -> "models.QuerySet[_ModelT]":
        """A fresh queryset from ``queryset`` or ``model``."""
        if self.queryset is not None:
            return self.queryset.all()
        if self.model is not None:
            return self.model._default_manager.all()
        raise ImproperlyConfigured(
            "%(cls)s is missing a QuerySet. Define %(cls)s.model, %(cls)s.queryset, "
            "or override %(cls)s.get_queryset()." % {"cls": type(self).__name__}
        )

    def get_slug_field(self) -> str:
        """The model field a route slug is matched against."""
        return self.slug_field

    def get_object(self, queryset: Optional["models.QuerySet[_ModelT]"] = None) -> _ModelT:
        """Look up the route's record in ``queryset`` (default ``get_queryset()``).

        The result is not authorized by this call; the framework runs
        ``has_object_permission()`` on it before it becomes ``self.object``.
        """
        if queryset is None:
            queryset = self.get_queryset()
        route = getattr(self, "kwargs", None) or {}
        pk = route.get(self.pk_url_kwarg)
        slug = route.get(self.slug_url_kwarg)
        if pk is not None:
            queryset = queryset.filter(pk=pk)
        if slug is not None and (pk is None or self.query_pk_and_slug):
            queryset = queryset.filter(**{self.get_slug_field(): slug})
        if pk is None and slug is None:
            raise ImproperlyConfigured(
                "%s must be routed with a %r or %r URL kwarg; client mount "
                "parameters never select the object."
                % (type(self).__name__, self.pk_url_kwarg, self.slug_url_kwarg)
            )
        try:
            obj: _ModelT = queryset.get()
        except queryset.model.DoesNotExist:
            raise Http404("No object matches the route")
        return obj

    # -- lifecycle -----------------------------------------------------------

    def mount(self, request: Any, **kwargs: Any) -> None:
        """Resolve and authorize the object, then build the form around it."""
        if self._model_instance is not None:
            raise ImproperlyConfigured(
                "%s sets _model_instance; ModelFormMixin resolves self.object from "
                "the route instead." % type(self).__name__
            )
        if "kwargs" not in self.__dict__:
            # Nothing bound the route: never fall back to the mount parameters.
            self.kwargs = {}
        if not self._authorize_object(request):
            # Denied: no object and no form. The object-permission check that
            # follows mount on every transport reports the denial.
            return
        super().mount(request, **kwargs)

    def _authorize_object(self, request: Any) -> bool:
        from .auth.core import enforce_object_permission

        self._djust_authorized_object = None
        try:
            enforce_object_permission(self, request)
        except PermissionDenied:
            self._object = None
            self._djust_authorized_object = (request, None)
            return False
        self._djust_authorized_object = (request, self._object)
        return True

    def get_form(self, form_class: Optional[Type[forms.Form]] = None) -> forms.Form:
        form_class = form_class or self.get_form_class()
        if form_class is None or not issubclass(form_class, forms.ModelForm):
            raise ImproperlyConfigured(
                "%s.form_class must be a ModelForm that declares its editable fields."
                % type(self).__name__
            )
        return super().get_form(form_class)

    def get_form_kwargs(self) -> Dict[str, Any]:
        """Bind the authorized object; never construct a form for a new record."""
        obj = self.object
        if obj is None:
            raise PermissionDenied("No authorized object to edit.")
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = obj
        return kwargs

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        from ._exposure import uses_legacy_exposure

        # Legacy views receive these through the attribute walk, before its
        # model serialization, and drop them from every session save.
        if not uses_legacy_exposure(self):
            provide_context_items(
                self, context, _MANAGED_OBJECT_PROVIDER, self._djust_render_only_context()
            )
        return context


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
