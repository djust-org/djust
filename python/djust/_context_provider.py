"""Context-provider mixin — React Context API equivalent.

Extracted from live_view.py to break the live_view -> components.base cycle
(both LiveView and LiveComponent mix this in; each needs to import from a
shared location that doesn't depend on the other).
"""

from typing import Any


class ContextProviderMixin:
    """Component/view context sharing — React Context API equivalent (v0.5.1).

    Mixed into both :class:`LiveView` and :class:`LiveComponent` so provider
    and consumer roles work at any depth of the render tree. Providers are
    stored per-instance in ``_djust_context_providers`` and the consumer walks
    the ``_djust_context_parent`` chain (set by
    ``LiveComponent.set_parent``).
    """

    def provide_context(self, key: str, value: Any) -> None:
        """Expose a value to descendant components under ``key``.

        Descendants read via :meth:`consume_context`. Scoped to the current
        render tree; reset at the start of each render via
        :meth:`clear_context_providers`.
        """
        providers = self.__dict__.setdefault("_djust_context_providers", {})
        providers[key] = value

    def consume_context(self, key: str, default: Any = None) -> Any:
        """Return the value provided under ``key`` by this node or any ancestor.

        Walks ``_djust_context_parent`` upward; returns ``default`` if no
        provider is found.
        """
        node = self
        while node is not None:
            providers = getattr(node, "_djust_context_providers", None)
            if providers and key in providers:
                return providers[key]
            node = getattr(node, "_djust_context_parent", None)
        return default

    def clear_context_providers(self) -> None:
        """Reset all context providers — called at the start of each render."""
        providers = self.__dict__.get("_djust_context_providers")
        if providers:
            providers.clear()
