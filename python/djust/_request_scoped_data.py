"""Plain data a context processor supplies each request that is not view state (#3631).

A context processor's value that is JSON-serialisable is copied into the view's
stored Rust state by ``_sync_state_to_rust``: that is how a ``csrf_token`` or a
settings string reaches the renderer. For a LARGE value that no template needs
stored, the copy is the cost. ``theme_context``'s ``theme_presets`` is about
11.7 KB of dicts, and it landed in every view's snapshot in any app that installs
the processor, on every page, whether or not a template read it.

:class:`RequestScopedList` is the opt-in. It IS a ``list`` (``isinstance``,
``json.dumps``, ``{{ x|json_script }}``, ``len``, indexing and ``in`` behave as
they always did, in both template engines), so nothing that reads the value
changes. What changes is the bridge's treatment of it: for a request-scoped key
it is left out of ``update_state`` and carried to the renderer in the raw-value
sidecar instead, like the lazy HTML chunks (#3028). The processor runs again on
every sync, so the sidecar always holds the current value.

Why a ``list`` subclass and not a lazy object: ``DjangoJSONEncoder`` (what
``json_script`` uses) handles a ``Promise`` by ``str()`` and has no hook for any
other non-list object, so a lazy list cannot be a ``json_script`` argument.

The marker is read from the TYPE (never evaluating the value), and only matters
for a key the framework recorded as request-scoped: a view that assigns one of
these itself stores it like any other list.
"""

from __future__ import annotations

from typing import Any


class RequestScopedList(list):  # type: ignore[type-arg]
    """A ``list`` a context processor supplies per request, kept out of stored state.

    Equal to, and indistinguishable from, the plain list it was built from for
    every reader; ``type(value) is list`` is the one check that tells them apart.
    """

    __slots__ = ()
    _djust_request_scoped_data = True


def is_request_scoped_data(value: Any) -> bool:
    """Whether ``value`` is plain data that must ride the sidecar, not the state."""
    return getattr(type(value), "_djust_request_scoped_data", False) is True
