"""LiveView-aware helpers for Django formsets / inline-formsets.

v0.5.1 — companion module to the ``{% inputs_for %}`` template tag in
``djust.templatetags.djust_formsets``. Provides lightweight add-row /
remove-row helpers so LiveView developers don't have to manually rewrite
the formset's management-form fields on each mutation.

Two use shapes:

1. **Direct helpers** — call :func:`add_row` or :func:`remove_row` from
   your event handlers with the current formset and a key identifying the
   row to remove::

       @event_handler
       def add_row(self, formset=None, **kwargs):
           self.addresses = add_row(AddressFormSet, data=self._formset_data)

       @event_handler
       def remove_row(self, formset=None, prefix=None, **kwargs):
           self.addresses = remove_row(AddressFormSet, prefix, data=self._formset_data)

2. **Mixin** — :class:`FormSetHelpersMixin` provides ``add_row`` /
   ``remove_row`` event handlers that read the formset name from the
   ``dj-value-formset`` attribute (and prefix from ``dj-value-prefix``
   for remove). Opt-in by listing each formset in
   ``formset_classes = {"addresses": AddressFormSet}``.

Both shapes respect the formset's ``max_num`` / ``absolute_max`` bounds
and cap adds accordingly. Removes mark the row with ``DELETE=True`` (the
standard Django formset deletion protocol) rather than dropping it
unconditionally, so the server-side form-valid step sees the deletion.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Type

from django.forms import BaseFormSet

from .decorators import event_handler


def _management_data(
    formset_cls: Type[BaseFormSet], total: int, initial: int = 0
) -> Dict[str, Any]:
    """Build the minimum management-form dict needed to re-instantiate a formset.

    Uses the formset's own prefix (derived from the class or manager default).
    """
    prefix = (
        formset_cls.get_default_prefix() if hasattr(formset_cls, "get_default_prefix") else "form"
    )
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


def add_row(formset_cls: Type[BaseFormSet], data: Optional[Dict[str, Any]] = None) -> BaseFormSet:
    """Return a new formset instance with one additional empty row.

    Caps at ``formset_cls.absolute_max`` (Django's hard ceiling, default
    2000). Data for existing rows is preserved; the new row's fields are
    left empty.
    """
    data = dict(data) if data else {}
    prefix = (
        formset_cls.get_default_prefix() if hasattr(formset_cls, "get_default_prefix") else "form"
    )
    total_key = f"{prefix}-TOTAL_FORMS"
    current = int(data.get(total_key, 0))
    absolute_max = getattr(formset_cls, "absolute_max", 2000)
    new_total = min(current + 1, absolute_max)
    if new_total == current:
        # Already at the cap — return a formset built from current data,
        # no-op from the user's perspective.
        return formset_cls(data or None)
    data[total_key] = str(new_total)
    data.setdefault(f"{prefix}-INITIAL_FORMS", "0")
    data.setdefault(f"{prefix}-MIN_NUM_FORMS", "0")
    data.setdefault(f"{prefix}-MAX_NUM_FORMS", str(absolute_max))
    return formset_cls(data)


def remove_row(
    formset_cls: Type[BaseFormSet], prefix: str, data: Optional[Dict[str, Any]] = None
) -> BaseFormSet:
    """Mark the row with the given prefix for deletion.

    Follows Django's standard formset delete protocol: sets the row's
    ``DELETE`` field to ``"on"`` so ``formset.deleted_forms`` picks it up
    during validation. Does NOT renumber or prune rows — the row stays in
    the collection with DELETE=True until the server processes the
    formset via ``formset.save()``.
    """
    data = dict(data) if data else {}
    data[f"{prefix}-DELETE"] = "on"
    return formset_cls(data)


class FormSetHelpersMixin:
    """LiveView mixin with pre-baked ``add_row`` / ``remove_row`` event handlers.

    Opt-in by declaring which formsets you manage::

        class AddressListView(FormSetHelpersMixin, LiveView):
            formset_classes = {"addresses": AddressFormSet}

            def mount(self, request, **kwargs):
                self.addresses = AddressFormSet()
                self._formset_data = {}

    The template sends events with ``dj-value-formset="addresses"`` and
    (for remove) ``dj-value-prefix="addresses-2"``. The mixin resolves the
    class via ``formset_classes`` and calls :func:`add_row` / :func:`remove_row`
    against ``self._formset_data`` (the dict that backs the current
    formset state).
    """

    #: Map of ``dj-value-formset`` name → Django ``BaseFormSet`` subclass.
    formset_classes: Dict[str, Type[BaseFormSet]] = {}

    def _resolve_formset_class(self, name: str) -> Type[BaseFormSet]:
        cls = self.formset_classes.get(name)
        if cls is None:
            raise ValueError(
                f"{type(self).__name__}.formset_classes has no entry for {name!r}. "
                f"Declare it: formset_classes = {{{name!r}: YourFormSet}}"
            )
        return cls

    @event_handler
    def add_row(self, formset: str = "", **kwargs) -> None:
        """Append an empty row to the named formset and refresh the bound attr."""
        if not formset:
            return
        cls = self._resolve_formset_class(formset)
        data = dict(getattr(self, "_formset_data", {}) or {})
        updated = add_row(cls, data=data)
        self._formset_data = updated.data
        setattr(self, formset, updated)

    @event_handler
    def remove_row(self, formset: str = "", prefix: str = "", **kwargs) -> None:
        """Mark a row for deletion and refresh the bound attr."""
        if not formset or not prefix:
            return
        cls = self._resolve_formset_class(formset)
        data = dict(getattr(self, "_formset_data", {}) or {})
        updated = remove_row(cls, prefix, data=data)
        self._formset_data = updated.data
        setattr(self, formset, updated)
