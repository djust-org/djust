"""#3248: every per-connection framework attribute is in the framework-internal list.

The snapshot, assigns and exposure paths all skip ``_FRAMEWORK_INTERNAL_ATTRS``
(``live_view.py``). On the WebSocket the transport writes per-connection identity
(``_websocket_session_id``, ``_django_session_key``, ...) after ``__init__`` and
before ``mount()``; a name missing from the list is saved and restored across
connections by any path that walks the instance.

The legacy private-state capture and restore this issue first fixed
(``_get_private_state`` / ``_restore_private_state``, the ``__private`` session
key) went with the legacy exposure policy (#4194, #4200): an explicit view
persists only declared ``state()`` fields, so the tests that drove them were
deleted with it.
"""

from __future__ import annotations

from djust.live_view import _FRAMEWORK_INTERNAL_ATTRS

#: Per-connection identity the transports set before ``mount()``. The event
#: save carried them, and a reconnect restored the previous connection's.
CONNECTION_ATTRS = (
    "_websocket_session_id",
    "_websocket_path",
    "_websocket_query_string",
    "_websocket_host",
    "_websocket_secure",
    "_django_session_key",
    "_djust_mount_view_path",
    "_sse_session_id",
)


def test_every_per_connection_name_is_framework_internal():
    """The full list of names a transport sets per connection. A name added
    here without joining ``_FRAMEWORK_INTERNAL_ATTRS`` would be saved and
    restored across connections; the real-path tests below derive the saved
    set instead, so a name missing from this list still fails there."""
    missing = [name for name in CONNECTION_ATTRS if name not in _FRAMEWORK_INTERNAL_ATTRS]
    assert not missing, missing
