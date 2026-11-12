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

import weakref
from typing import Any, Dict, FrozenSet, Iterable, Optional, Type

from django.forms import BaseFormSet
from django.forms.models import BaseModelFormSet

from . import _form_data_bounds
from ._exposure import ProviderContract, uses_legacy_exposure
from ._exposure_diagnostics import log_failure_for
from ._exposure_providers import (
    provide_context,
    renderable_provider_names,
    require_mixin_before_view,
)
from .decorators import event_handler


logger = __import__("logging").getLogger(__name__)


def _resolve_prefix(formset_cls: Type[BaseFormSet], explicit: Optional[str] = None) -> str:
    """Prefer the caller-supplied prefix; fall back to the class default."""
    if explicit:
        return explicit
    if hasattr(formset_cls, "get_default_prefix"):
        return str(formset_cls.get_default_prefix())
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


_MANAGEMENT_KEYS = ("TOTAL_FORMS", "INITIAL_FORMS", "MIN_NUM_FORMS", "MAX_NUM_FORMS")


#: The suffixes a formset's forms always carry besides their own fields (Django's deletion, ordering and
#: primary-key fields).
_ROW_EXTRAS = ("DELETE", "ORDER", "id")
#: Rows are indexed by a handful of digits at most; a longer index is never a row the formset has (and a
#: longer string must not reach ``int()``, which refuses more than 4,300 digits).
_MAX_INDEX_DIGITS = 9


#: ``_row_suffixes`` per formset class. Keyed weakly, so a class a project builds per request (a
#: ``formset_factory`` call in a handler) is not kept alive by the cache.
_SUFFIX_CACHE: "weakref.WeakKeyDictionary[type, FrozenSet[str]]" = weakref.WeakKeyDictionary()


def _declared_row_names(formset_cls: Type[BaseFormSet]) -> FrozenSet[str]:
    """The class-declared part of a row's names: the fields the formset's form class declares, the
    foreign key an inline formset adds, and Django's own ``DELETE`` / ``ORDER`` / ``id``. The fallback
    when the formset cannot be built without arguments (:func:`_row_suffixes`)."""
    names = set(_ROW_EXTRAS)
    names.update(getattr(getattr(formset_cls, "form", None), "base_fields", None) or ())
    fk_name = getattr(getattr(formset_cls, "fk", None), "name", None)
    if isinstance(fk_name, str):
        names.add(fk_name)
    return frozenset(name for name in names if isinstance(name, str))


def _unbound_formset(formset_cls: Type[BaseFormSet]) -> BaseFormSet:
    """*formset_cls* built with no data and no arguments, for its ``empty_form``. A model formset's
    default queryset is the model's whole table, evaluated on first use, so it is given an empty one: the
    form's fields do not depend on the rows, and deriving them must not read any (#3881 review)."""
    model = getattr(formset_cls, "model", None)
    manager = getattr(model, "_default_manager", None)
    if manager is not None and issubclass(formset_cls, BaseModelFormSet):
        return formset_cls(queryset=manager.none())
    return formset_cls()


def _row_suffixes(formset_cls: Type[BaseFormSet]) -> FrozenSet[str]:
    """The names a row's key may end in: the fields a row of this formset really has. Derived from the
    CLASS, never from the client's data (#3858), and computed once per class (#3861).

    A form may add fields in its ``__init__``, so ``form.base_fields`` is not all of them. The names
    come from ``formset_cls().empty_form.fields``: the formset built with no data and no arguments, as
    Django builds a row for the page, which includes what ``__init__`` adds, the inline formset's
    foreign key, and ``DELETE`` / ``ORDER`` / ``id``. Nothing in that construction is the client's: the
    form is unbound, so a field a form adds only when it holds data (``self.data``) is not one of them.
    When the formset cannot be built without arguments (a form whose ``__init__`` requires a
    ``form_kwargs`` entry), the class-declared names are used instead (:func:`_declared_row_names`):
    narrower, so a field added by such a form counts toward ``max_form_fields``, which fails closed.

    Computed once per class per process: a form whose fields depend on state that changes later keeps the
    first answer."""
    cached = _SUFFIX_CACHE.get(formset_cls)
    if cached is not None:
        return cached
    try:
        names = {*_ROW_EXTRAS, *_unbound_formset(formset_cls).empty_form.fields}
    except Exception as exc:  # noqa: BLE001 - an app's form code runs here; any failure is the fallback
        log_failure_for(
            logger,
            (formset_cls,),
            exc,
            "formset %s cannot be built without arguments; only the fields its form class declares "
            "are exempt from max_form_fields",
            getattr(formset_cls, "__name__", "?"),
            level="warning",
        )
        names = set(_declared_row_names(formset_cls))
    result = frozenset(name for name in names if isinstance(name, str))
    _SUFFIX_CACHE[formset_cls] = result
    return result


def _row_index(text: str) -> Optional[int]:
    """*text* as a row index, or ``None`` unless it is the canonical spelling of one: ASCII digits only
    (``int()`` also reads Arabic-Indic and full-width digits, and ``str.isdigit()`` is true of ``"\u00b2"``,
    which ``int()`` refuses), no leading zeros, at most :data:`_MAX_INDEX_DIGITS` of them. A key that
    spells a row any other way is not the formset's row (``fs-00-x`` is not ``fs-0-x``)."""
    if not (text.isascii() and text.isdigit()) or len(text) > _MAX_INDEX_DIGITS:
        return None
    if len(text) > 1 and text[0] == "0":
        return None
    return int(text)


def _schema_keys(
    formset_cls: Type[BaseFormSet], prefix: str, keys: Iterable[str], data: Dict[str, Any]
) -> FrozenSet[str]:
    """The *keys* that belong to the formset itself: its management form, and ``<prefix>-<n>-<field>``
    for a row ``n`` below ``TOTAL_FORMS`` and a ``<field>`` the form really has (:func:`_row_suffixes`).
    They are not the client's to add, so they do not count toward ``max_form_fields`` (#3858). Anything
    else under the formset's prefix is: the suffix is as much the client's as the prefix is, so
    ``fs-0-anything`` under a real row is counted, and so is ``fs-00-street`` or ``fs-5000-street``."""
    head = prefix + "-"
    rows = _row_index(str(data.get(f"{prefix}-TOTAL_FORMS", ""))) or 0
    rows = min(
        rows, max(_effective_cap(formset_cls), int(getattr(formset_cls, "absolute_max", 0) or 0))
    )
    suffixes = _row_suffixes(formset_cls)
    own = set()
    for key in keys:
        if not isinstance(key, str) or not key.startswith(head):
            continue
        rest = key[len(head) :]
        if rest in _MANAGEMENT_KEYS:
            own.add(key)
            continue
        index, _, field = rest.partition("-")
        row = _row_index(index)
        if row is not None and row < rows and field in suffixes:
            own.add(key)
    return frozenset(own)


def _seeded(
    formset_cls: Type[BaseFormSet],
    prefix: str,
    data: Dict[str, Any],
    formset_kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    """*data*, or the formset's own rows as bound data when there is nothing bound yet (#3966).

    A formset built with ``instance`` (inline), ``queryset`` (model) or ``initial`` already shows rows
    the caller has no bound data for: a view that starts with ``_formset_data = {}`` has bound nothing.
    Rebuilding it from that empty dict with one more row would show only the new row, and for an inline
    formset the existing rows would be missing from the submission. So when *data* has no management
    form and the caller passed formset kwargs, the starting point is the formset those kwargs build
    (unbound), written out the way a browser would post it: its management form and one key per field
    of every row, existing rows first. With no formset kwargs the unbound formset holds nothing the
    caller could lose, and *data* is used as it is. *data* itself is never changed.
    """
    if not formset_kwargs or f"{prefix}-TOTAL_FORMS" in data:
        return data
    from .wizard import _submittable

    unbound = formset_cls(prefix=prefix, **formset_kwargs)
    seeded: Dict[str, Any] = {
        f"{prefix}-TOTAL_FORMS": str(unbound.total_form_count()),
        f"{prefix}-INITIAL_FORMS": str(unbound.initial_form_count()),
    }
    for form in unbound.forms:
        for name, field in form.fields.items():
            value = form[name].value()
            if value is not None:
                seeded[form.add_prefix(name)] = _submittable(field, value)
    seeded.update(data)
    return seeded


def add_row(
    formset_cls: Type[BaseFormSet],
    data: Optional[Dict[str, Any]] = None,
    prefix: Optional[str] = None,
    **formset_kwargs: Any,
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
        **formset_kwargs: Every other argument the formset is built with, passed
            through to ``formset_cls(data, prefix=prefix, **formset_kwargs)``:
            ``instance`` (an inline formset's parent), ``queryset`` (a model
            formset's rows), ``form_kwargs``, ``initial``, ``auto_id``. Without
            them the rebuilt formset has lost its parent and its rows (#3966).
            When *data* holds no management form yet, the formset these kwargs
            build is the starting point, so its existing rows are kept.

    Caps at ``formset_cls.max_num`` when set, otherwise at ``absolute_max``.
    Data for existing rows is preserved; the new row's fields are empty.
    When already at the cap, the call is a logged no-op.
    """
    prefix = _resolve_prefix(formset_cls, prefix)
    data = dict(_seeded(formset_cls, prefix, dict(data) if data else {}, formset_kwargs))
    total_key = f"{prefix}-TOTAL_FORMS"
    current = int(data.get(total_key, 0))
    cap = _effective_cap(formset_cls)
    if current >= cap:
        logger.debug("add_row: %s at cap %d — no-op", prefix, cap)
        return formset_cls(data or None, prefix=prefix, **formset_kwargs)
    data[total_key] = str(current + 1)
    data.setdefault(f"{prefix}-INITIAL_FORMS", "0")
    data.setdefault(f"{prefix}-MIN_NUM_FORMS", "0")
    data.setdefault(f"{prefix}-MAX_NUM_FORMS", str(cap))
    return formset_cls(data, prefix=prefix, **formset_kwargs)


def remove_row(
    formset_cls: Type[BaseFormSet],
    row_prefix: str,
    data: Optional[Dict[str, Any]] = None,
    prefix: Optional[str] = None,
    **formset_kwargs: Any,
) -> BaseFormSet:
    """Mark the row with ``row_prefix`` for deletion.

    Args:
        formset_cls: The Django ``BaseFormSet`` subclass.
        row_prefix: The full prefix of the row to remove — e.g.
            ``"addresses-0"`` when the formset was instantiated with
            ``prefix="addresses"``.
        data: Current bound data.
        prefix: Optional custom formset prefix for re-instantiation.
        **formset_kwargs: Every other argument the formset is built with (``instance``,
            ``queryset``, ``form_kwargs``, ``initial``, ``auto_id``), as for :func:`add_row` (#3966).

    Follows Django's standard formset delete protocol: sets ``DELETE="on"``
    on the row so ``formset.deleted_forms`` picks it up during validation.
    Does NOT decrement ``TOTAL_FORMS`` — Django expects deleted forms to
    remain counted until submit so the index gap doesn't confuse the
    management form. The server's ``formset.save()`` handles the actual
    removal.

    *row_prefix* is usually the client's, so the key it names is bounded like any other key a client
    names in a form's data: when storing it would take *data* past ``max_form_fields`` (counting only
    keys that are not the formset's own), ``max_form_data_size`` or ``max_form_members``, nothing is
    stored and the call is a logged no-op (#3858).
    """
    prefix = _resolve_prefix(formset_cls, prefix)
    data = dict(_seeded(formset_cls, prefix, dict(data) if data else {}, formset_kwargs))
    key = f"{row_prefix}-DELETE"
    # ``row_prefix`` is the client's (``dj-value-prefix``), so the key it names is judged like any other
    # key a client names in a form's data (#3858, #3834): past ``max_form_fields`` / ``max_form_data_size``
    # / ``max_form_members`` the call is a logged no-op, like an add past the cap.
    message = _form_data_bounds.refusal(
        data, {key: "on"}, _schema_keys(formset_cls, prefix, (*data, key), data)
    )
    if message is not None:
        logger.warning("remove_row refused form data: %s", message)
        return formset_cls(data or None, prefix=prefix, **formset_kwargs)
    data[key] = "on"
    return formset_cls(data, prefix=prefix, **formset_kwargs)


_FORMSETS_PROVIDER = "djust.formsets"

#: The contract per view class, with the ``formset_classes`` keys it was built
#: from: it is asked for on every explicit render.
_PROVIDER_CACHE: "weakref.WeakKeyDictionary[type, tuple[tuple, ProviderContract]]" = (
    weakref.WeakKeyDictionary()
)


def _formsets_provider(view_class: type) -> ProviderContract:
    """The formset names a ``FormSetHelpersMixin`` view declares in ``formset_classes``."""
    declared = getattr(view_class, "formset_classes", None)
    names = tuple(declared) if isinstance(declared, dict) else ()
    cached = _PROVIDER_CACHE.get(view_class)
    if cached is not None and cached[0] == names:
        return cached[1]
    # A key that cannot be a provider key (``_rows``) or that another provider
    # owns (``streams``) is left out, and that formset is not rendered; the
    # view still mounts. ``get_context_data`` reports it.
    contract = ProviderContract(
        _FORMSETS_PROVIDER,
        rendered=renderable_provider_names(view_class, names, _formsets_provider),
    )
    _PROVIDER_CACHE[view_class] = (names, contract)
    return contract


_UNSET: Any = object()

#: Classes already told about a formset name that cannot be rendered.
_UNRENDERABLE_REPORTED: "weakref.WeakSet[type]" = weakref.WeakSet()


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

    _djust_context_providers = (_formsets_provider,)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        require_mixin_before_view(cls, FormSetHelpersMixin)

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        """Render the declared formsets for a view with explicit exposure.

        ``add_row`` / ``remove_row`` assign the rebuilt formset to the attribute
        named in ``formset_classes``. A legacy view is untouched: its attribute
        walk already renders that attribute. An explicit view renders nothing it
        is not given, so the mixin provides each declared name the view has
        assigned. Render-only: the formset and ``_formset_data`` are neither
        persisted nor sent to the browser as data.
        """
        # FormSetHelpersMixin is mixed into a LiveView at runtime; the MRO
        # supplies get_context_data(). mypy only sees object here.
        context: Dict[str, Any] = (
            super().get_context_data(**kwargs)  # type: ignore[misc]
            if hasattr(super(), "get_context_data")
            else {}
        )
        if uses_legacy_exposure(self):
            return context
        view_class = type(self)
        rendered = _formsets_provider(view_class).rendered
        declared = self.formset_classes if isinstance(self.formset_classes, dict) else {}
        skipped = sorted(str(name) for name in declared if name not in rendered)
        if skipped and view_class not in _UNRENDERABLE_REPORTED:
            _UNRENDERABLE_REPORTED.add(view_class)
            logger.warning(
                "%s.formset_classes names %s cannot be template names under explicit "
                "exposure (a name must not start with an underscore, be reserved, or "
                "belong to another framework provider such as 'streams'). Those formsets "
                "are not rendered; rename the key.",
                view_class.__name__,
                ", ".join(repr(name) for name in skipped),
            )
        for name in sorted(rendered):
            # ``getattr``, not ``__dict__``: the view may declare the name
            # with ``state()``. A method of that name is not a formset.
            value = getattr(self, name, _UNSET)
            if value is not _UNSET and not callable(value):
                provide_context(self, context, _FORMSETS_PROVIDER, name, value)
        return context

    def _resolve_formset_class(self, name: str) -> Type[BaseFormSet]:
        cls = self.formset_classes.get(name)
        if cls is None:
            raise ValueError(
                f"{type(self).__name__}.formset_classes has no entry for {name!r}. "
                f"Declare it: formset_classes = {{{name!r}: YourFormSet}}"
            )
        return cls

    #: Arguments ``get_formset_kwargs()`` may not return: the helpers pass them themselves.
    _RESERVED_FORMSET_KWARGS = frozenset({"data", "prefix"})

    def get_formset_kwargs(self, name: str) -> Dict[str, Any]:
        """The arguments formset *name* is built with, besides its data and prefix (#3966).

        ``add_row`` and ``remove_row`` rebuild the formset from its bound data on
        every call; without this hook everything it was first built with (an inline
        formset's ``instance``, a model formset's ``queryset``, ``form_kwargs``,
        ``initial``, ``auto_id``) is lost. Override it and build the formset in
        ``mount()`` with the same method, so there is one place that says how::

            def get_formset_kwargs(self, name):
                return {"instance": self.order}

            def mount(self, request, pk=None, **kwargs):
                self.order = Order.objects.get(pk=pk)
                self.items = ItemFormSet(prefix="items", **self.get_formset_kwargs("items"))
                self._formset_data = {}

        The default returns ``{}``. ``data`` and ``prefix`` are not accepted: the
        mixin passes the formset's name as its prefix.
        """
        return {}

    def _formset_kwargs(self, name: str) -> Dict[str, Any]:
        """``get_formset_kwargs(name)``, refusing the arguments the helpers set themselves."""
        kwargs = dict(self.get_formset_kwargs(name))
        clash = sorted(self._RESERVED_FORMSET_KWARGS.intersection(kwargs))
        if clash:
            raise ValueError(
                f"{type(self).__name__}.get_formset_kwargs({name!r}) returned {clash}; "
                f"the formset's data and prefix are set by add_row/remove_row, not by this hook."
            )
        return kwargs

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
    def add_row(self, formset: str = "", **kwargs: Any) -> None:
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
        updated = add_row(cls, data=data, prefix=formset, **self._formset_kwargs(formset))
        self._formset_data = updated.data
        setattr(self, formset, updated)

    @event_handler
    def remove_row(self, formset: str = "", prefix: str = "", **kwargs: Any) -> None:
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
        updated = remove_row(
            cls, row_prefix=prefix, data=data, prefix=formset, **self._formset_kwargs(formset)
        )
        self._formset_data = updated.data
        setattr(self, formset, updated)
