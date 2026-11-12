"""ADR-038 E2: registered framework context providers for explicit views.

A framework mixin that renders context declares a ``ProviderContract`` (see
``_exposure.py``) in its class body and writes each key through
:func:`provide_context`. Under the explicit policy the render context is an
:class:`ExplicitRenderContext`, which knows which provider owns each key, so:

* a provider key that an application kwarg or earlier write already holds is a
  collision, never a silent replacement;
* an application write, update or removal of a provider key after
  ``super().get_context_data()`` is a collision;
* a provider can write only the keys its contract declares.

Legacy views keep their exact behaviour: :func:`provide_context` is a plain
item assignment for them. Provider values are render-only; no persistence,
client, snapshot or debug projection reads the render context.
"""

from types import FunctionType
from typing import Any, Dict, Iterable, Mapping, Optional

from ._class_snapshot import namespace
from ._exposure import (
    ExposureConfigurationError,
    ExposureError,
    ProviderContract,
    provider_owners,
    uses_legacy_exposure,
)

COLLISION = "Explicit context provider collision"


class ExplicitRenderContext(dict[str, Any]):
    """Render-only context whose provider keys only their provider may write."""

    __slots__ = ("_djust_manifest", "_djust_owners")

    def __init__(self, manifest: Mapping[str, str]) -> None:
        super().__init__()
        self._djust_manifest: Dict[str, str] = dict(manifest)
        self._djust_owners: Dict[str, str] = {}

    def provider_keys(self) -> frozenset[str]:
        return frozenset(self._djust_owners)

    def _provide(self, provider: str, key: str, value: Any) -> None:
        owner = self._djust_manifest.get(key)
        if owner is None:
            raise ExposureError("Undeclared explicit context provider key")
        if owner != provider:
            raise ExposureError(COLLISION)
        if key in self and self._djust_owners.get(key) != provider:
            raise ExposureError(COLLISION)
        dict.__setitem__(self, key, value)
        self._djust_owners[key] = provider

    def _guard(self, keys: Iterable[Any]) -> None:
        owners = self._djust_owners
        if any(key in owners for key in keys):
            raise ExposureError(COLLISION)

    def __setitem__(self, key: str, value: Any) -> None:
        self._guard((key,))
        dict.__setitem__(self, key, value)

    def __delitem__(self, key: str) -> None:
        self._guard((key,))
        dict.__delitem__(self, key)

    def setdefault(self, key: str, default: Any = None) -> Any:
        self._guard((key,))
        return dict.setdefault(self, key, default)

    def pop(self, key: str, *default: Any) -> Any:
        self._guard((key,))
        return dict.pop(self, key, *default)

    def popitem(self) -> Any:
        raise ExposureError("Explicit render context does not support popitem")

    def clear(self) -> None:
        self._guard(tuple(self._djust_owners))
        dict.clear(self)

    def update(self, *args: Any, **kwargs: Any) -> None:
        incoming: Dict[str, Any] = dict(*args, **kwargs)
        self._guard(incoming)
        dict.update(self, incoming)

    def __ior__(self, other: Any) -> "ExplicitRenderContext":
        self.update(other)
        return self

    def __reduce__(self) -> Any:
        # Copies and pickles are plain dicts: ownership is per render.
        return (dict, (dict(self),))


def new_render_context(view: Any) -> ExplicitRenderContext:
    return ExplicitRenderContext(provider_owners(type(view)))


def _record(view: Any, keys: Iterable[str]) -> None:
    current = getattr(view, "_explicit_context_provider_keys", frozenset())
    view._explicit_context_provider_keys = frozenset(current) | frozenset(keys)


def provide_context(
    view: Any, context: Dict[str, Any], provider: str, key: str, value: Any
) -> None:
    """Write one key as the named provider; a plain assignment for legacy views.

    ``provider`` is the ``ProviderContract.name`` the view class registered;
    the key must be one that contract declares.
    """
    if uses_legacy_exposure(view):
        context[key] = value
        return
    if type(context) is not ExplicitRenderContext:
        raise ExposureError("Explicit context providers require the framework render context")
    context._provide(provider, key, value)
    _record(view, (key,))


def provide_context_items(
    view: Any, context: Dict[str, Any], provider: str, items: Mapping[str, Any]
) -> None:
    """Write several keys as the named provider; ``context.update`` for legacy views."""
    if uses_legacy_exposure(view):
        context.update(items)
        return
    for key, value in items.items():
        provide_context(view, context, provider, key, value)


def require_no_provider_keys(
    view: Any, context: Mapping[str, Any], contract: ProviderContract
) -> None:
    """Reject an application-supplied value for a late provider's keys."""
    if not uses_legacy_exposure(view) and contract.rendered.intersection(context):
        raise ExposureError(COLLISION)


def require_mixin_before_view(cls: type, mixin: type) -> None:
    """Refuse an explicit view that lists a context-providing mixin after its view base.

    Such a mixin provides its names from ``get_context_data()``. The view
    base's ``get_context_data()`` does not call ``super()``, so a mixin listed
    after it is never asked and its names would render empty without an
    error. A legacy view is not checked: its attribute walk renders the names
    in either order, and it keeps working as written.
    """
    from django.views import View

    mro = cls.__mro__
    if (
        View in mro
        and mixin in mro
        and mro.index(View) < mro.index(mixin)
        and getattr(cls, "exposure_policy", None) == "explicit"
    ):
        host = next(c for c in mro if issubclass(c, View) and c is not cls)
        raise TypeError(
            '%s lists %s after %s. With exposure_policy = "explicit" the mixin provides '
            "its template names from get_context_data(), which is never reached in that "
            "order, so they would render empty. Put the mixin first: class %s(%s, %s)."
            % (
                cls.__name__,
                mixin.__name__,
                host.__name__,
                cls.__name__,
                mixin.__name__,
                host.__name__,
            )
        )


def renderable_provider_names(
    view_class: type, names: Iterable[Any], provider: Any
) -> frozenset[str]:
    """The ``names`` a provider whose keys come from application configuration can own.

    A name is left out when it is not a valid provider key (an underscore
    name, a reserved or unsafe name) or when another provider the class
    registers already renders it (``streams``, a declared component). The
    caller says so in a diagnostic; registering the name would make the whole
    view unmountable. ``provider`` is the caller's own manifest entry, which is
    skipped while the other providers' keys are collected.
    """
    taken: set[str] = set()
    for owner in view_class.__mro__:
        declared = vars(owner).get("_djust_context_providers")
        if type(declared) is not tuple:
            continue
        for entry in declared:
            if entry is provider:
                continue
            contract = entry if type(entry) is ProviderContract else None
            if contract is None and callable(entry):
                try:
                    contract = entry(view_class)
                except ExposureError:
                    # Reported where the manifest is collected, not here.
                    continue
            if type(contract) is ProviderContract:
                taken.update(contract.rendered)
    usable = set()
    for name in names:
        if type(name) is not str or name in taken:
            continue
        try:
            ProviderContract("djust.probe", rendered=frozenset({name}))
        except ExposureError:
            continue
        usable.add(name)
    return frozenset(usable)


# --------------------------------------------------------------------------
# Framework providers that already existed before the manifest (E2-0).
# --------------------------------------------------------------------------

STREAMS_PROVIDER = ProviderContract("djust.streams", rendered=frozenset({"streams"}))

#: ``UploadMixin``'s render-only projection of ``get_upload_state()`` (E2-6).
UPLOADS_PROVIDER = ProviderContract("djust.uploads", rendered=frozenset({"uploads"}))

#: Keys ``_sync_state_to_rust`` adds for the Rust renderer when absent.
RUST_RENDER_PROVIDER = ProviderContract(
    "djust.rust_render", rendered=frozenset({"csrf_token", "DATE_FORMAT", "TIME_FORMAT"})
)


def registered_components(view_class: type) -> Dict[str, Any]:
    """The class-level component declarations a view class registers.

    ADR-031 descriptors (``_component_descriptors``, including State-less
    components) and ADR-034 interactive declarations
    (``_component_declarations``). Where a subclass replaced one kind with the
    other under the same name, the effective class attribute wins; a stale
    entry is left for the caller's declaration check to reject. Reads class
    dictionaries only: no descriptor or property is evaluated.
    """
    from inspect import getattr_static

    from ._component_subscriptions import DECLARATIONS_ATTR
    from .components.base import LiveComponent

    descriptors = getattr(view_class, "_component_descriptors", None) or {}
    declarations = getattr(view_class, DECLARATIONS_ATTR, None) or {}
    if type(descriptors) is not dict or type(declarations) is not dict:
        raise ExposureError("Invalid component descriptor registry")
    merged: Dict[str, Any] = dict(descriptors)
    for name, declaration in declarations.items():
        # Only a declaration that is also a component, or an ADR-034 keyed
        # collection of them, renders.
        if not (
            isinstance(declaration, LiveComponent)
            or getattr(type(declaration), "_djust_component_collection", False)
        ):
            continue
        if name not in merged or getattr_static(view_class, name, None) is declaration:
            merged[name] = declaration
    return merged


def components_provider(view_class: type) -> ProviderContract:
    """Registered component declarations, keyed by their attribute names."""
    return ProviderContract(
        "djust.components", rendered=frozenset(registered_components(view_class))
    )


#: Instance attribute naming the keys ``RequestMixin._processor_context``
#: injected for a nonlegacy view (present only while that context is open).
PROCESSOR_KEYS_ATTR = "_djust_processor_injected_keys"


def instance_assigned_component_error(view: Any) -> Optional[ExposureConfigurationError]:
    """ADR-038 D-h: a component assigned on the instance is never discovered.

    Returns the configuration error for the first public instance attribute
    holding a component, or None. Checks types only (``type()``, so a lazy
    object is not evaluated) and names only the attribute, never a value.
    """
    from .components.base import BoundComponent, Component, LiveComponent

    kinds = (Component, LiveComponent, BoundComponent)
    attrs = vars(view)
    # The HTTP POST path injects context-processor output as attributes for
    # the duration of its render; those are the framework's, not the view's.
    injected = attrs.get(PROCESSOR_KEYS_ATTR) or frozenset()
    for name, value in attrs.items():
        if type(name) is not str or name.startswith("_") or name in injected:
            continue
        if issubclass(type(value), kinds):
            label = repr(name) if name.isidentifier() else "a public attribute"
            return ExposureConfigurationError(
                f"Component {label} is assigned on the view instance. Explicit views "
                "render only components declared at class level: declare it in the "
                "class body instead of assigning it in mount() or a handler."
            )
    return None


def _action_name(value: Any) -> Optional[str]:
    if type(value) is not FunctionType:
        return None
    metadata = value.__dict__.get("_djust_decorators")
    if type(metadata) is not dict or "action" not in metadata:
        return None
    action = metadata["action"]
    name = action.get("name") if type(action) is dict else None
    return name if type(name) is str else value.__name__


def actions_provider(view_class: type) -> ProviderContract:
    """``@action`` methods, whose ``_action_state`` entries render by name."""
    names: set[str] = set()
    seen: set[str] = set()
    for owner in view_class.__mro__:
        for name, value in namespace(owner).items():  # #3151
            if name in seen:
                continue
            seen.add(name)
            action = _action_name(value)
            if action is not None:
                names.add(action)
    return ProviderContract("djust.actions", rendered=frozenset(names))
