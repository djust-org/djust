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


logger = __import__("logging").getLogger(__name__)


def _resolve_prefix(formset_cls: Type[BaseFormSet], explicit: Optional[str] = None) -> str:
    """Prefer the caller-supplied prefix; fall back to the class default."""
    if explicit:
        return explicit
    if hasattr(formset_cls, "get_default_prefix"):
        return formset_cls.get_default_prefix()
    return "form"


def _effective_cap(formset_cls: Type[BaseFormSet]) -> int:
    """Return the hard row limit Django enforces on this formset.

    Django's ``absolute_max`` defaults to ``max_num + DEFAULT_MAX_NUM`` (1000)
    so a formset with ``max_num=5`` has ``absolute_max=1005``. The correct
    add-row cap is ``max_num`` when it's set — the "extra head-room" above
    ``max_num`` is for validation tolerance on submit, not for adding rows.
    """
    max_num = getattr(formset_cls, "max_num", None)
    if max_num is not None:
        return int(max_num)
    # No explicit max_num — fall back to absolute_max (Django's last-resort
    # ceiling) so we never grow unboundedly.
    return int(getattr(formset_cls, "absolute_max", 2000))


def add_row(
    formset_cls: Type[BaseFormSet],
    data: Optional[Dict[str, Any]] = None,
    prefix: Optional[str] = None,
) -> BaseFormSet:
    """Return a new formset instance with one additional empty row.

    Args:
        formset_cls: The Django ``BaseFormSet`` subclass.
        data: Current bound data (management fields + existing row values).
        prefix: Optional custom formset prefix. If not provided, the class's
            ``get_default_prefix()`` is used. Pass this when the user
            instantiates the formset with a custom prefix (e.g.
            ``FS(prefix="addresses")``) — without it, the management fields
            under ``addresses-*`` will never be updated.

    Caps at ``formset_cls.max_num`` when set, otherwise at ``absolute_max``.
    Data for existing rows is preserved; the new row's fields are empty.
    When already at the cap, the call is a logged no-op.
    """
    data = dict(data) if data else {}
    prefix = _resolve_prefix(formset_cls, prefix)
    total_key = f"{prefix}-TOTAL_FORMS"
    current = int(data.get(total_key, 0))
    cap = _effective_cap(formset_cls)
    if current >= cap:
        logger.debug("add_row: %s at cap %d — no-op", prefix, cap)
        return formset_cls(data or None, prefix=prefix)
    data[total_key] = str(current + 1)
    data.setdefault(f"{prefix}-INITIAL_FORMS", "0")
    data.setdefault(f"{prefix}-MIN_NUM_FORMS", "0")
    data.setdefault(f"{prefix}-MAX_NUM_FORMS", str(cap))
    return formset_cls(data, prefix=prefix)


def remove_row(
    formset_cls: Type[BaseFormSet],
    row_prefix: str,
    data: Optional[Dict[str, Any]] = None,
    prefix: Optional[str] = None,
) -> BaseFormSet:
    """Mark the row with ``row_prefix`` for deletion.

    Args:
        formset_cls: The Django ``BaseFormSet`` subclass.
        row_prefix: The full prefix of the row to remove — e.g.
            ``"addresses-0"`` when the formset was instantiated with
            ``prefix="addresses"``.
        data: Current bound data.
        prefix: Optional custom formset prefix for re-instantiation.

    Follows Django's standard formset delete protocol: sets ``DELETE="on"``
    on the row so ``formset.deleted_forms`` picks it up during validation.
    Does NOT decrement ``TOTAL_FORMS`` — Django expects deleted forms to
    remain counted until submit so the index gap doesn't confuse the
    management form. The server's ``formset.save()`` handles the actual
    removal.
    """
    data = dict(data) if data else {}
    data[f"{row_prefix}-DELETE"] = "on"
    prefix = _resolve_prefix(formset_cls, prefix)
    return formset_cls(data, prefix=prefix)


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

    def _require_formset_data(self) -> Dict[str, Any]:
        """Return ``self._formset_data``, raising a clear error if the user forgot to init it."""
        data = getattr(self, "_formset_data", None)
        if data is None:
            raise RuntimeError(
                f"{type(self).__name__} did not initialize self._formset_data in mount(). "
                f"Set self._formset_data = {{}} before the first add_row/remove_row call."
            )
        return dict(data)

    @event_handler
    def add_row(self, formset: str = "", **kwargs) -> None:
        """Append an empty row to the named formset and refresh the bound attr.

        The formset name doubles as the prefix — every entry in
        ``formset_classes`` is instantiated with ``prefix=<name>`` so
        multiple formsets on the same view don't collide on management-form
        keys.
        """
        if not formset:
            return
        cls = self._resolve_formset_class(formset)
        data = self._require_formset_data()
        updated = add_row(cls, data=data, prefix=formset)
        self._formset_data = updated.data
        setattr(self, formset, updated)

    @event_handler
    def remove_row(self, formset: str = "", prefix: str = "", **kwargs) -> None:
        """Mark a row for deletion and refresh the bound attr.

        Args:
            formset: Formset name (matches the ``formset_classes`` key and the
                instance's prefix).
            prefix: Full row prefix — e.g. ``"addresses-0"``.
        """
        if not formset or not prefix:
            return
        cls = self._resolve_formset_class(formset)
        data = self._require_formset_data()
        updated = remove_row(cls, row_prefix=prefix, data=data, prefix=formset)
        self._formset_data = updated.data
        setattr(self, formset, updated)
