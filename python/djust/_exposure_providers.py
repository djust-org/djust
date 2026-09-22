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

from ._exposure import ExposureError, ProviderContract, provider_owners, uses_legacy_exposure

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


# --------------------------------------------------------------------------
# Framework providers that already existed before the manifest (E2-0).
# --------------------------------------------------------------------------

STREAMS_PROVIDER = ProviderContract("djust.streams", rendered=frozenset({"streams"}))

#: Keys ``_sync_state_to_rust`` adds for the Rust renderer when absent.
RUST_RENDER_PROVIDER = ProviderContract(
    "djust.rust_render", rendered=frozenset({"csrf_token", "DATE_FORMAT", "TIME_FORMAT"})
)


def components_provider(view_class: type) -> ProviderContract:
    """Registered component descriptors, keyed by their attribute names."""
    descriptors = getattr(view_class, "_component_descriptors", None) or {}
    if not isinstance(descriptors, dict):
        raise ExposureError("Invalid component descriptor registry")
    return ProviderContract("djust.components", rendered=frozenset(descriptors))


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
        for name, value in vars(owner).items():
            if name in seen:
                continue
            seen.add(name)
            action = _action_name(value)
            if action is not None:
                names.add(action)
    return ProviderContract("djust.actions", rendered=frozenset(names))
