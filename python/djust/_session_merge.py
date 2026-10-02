"""A state save writes what its turn changed, not the whole session (#3347).

``SessionBase.save()`` persists the in-memory dict in full. djust loads a
session once, at mount, and reuses that object for every later event, so a
save wrote back every key as it was at mount: a value another tab, request,
middleware or handler stored since was rolled back, and a key deleted since
came back.

Here the session's data dict is replaced by :class:`TrackedSessionData`, which
records the keys the code holding the session wrote or removed
(:func:`track_session`, called where djust takes a session for a turn). The
save (:func:`save_merged`) then

1. reads the session as the store holds it NOW (one read);
2. applies only the recorded writes and removals to that copy;
3. writes the result through the session's own ``save()``, and refreshes the
   in-memory dict to it, so the next read in the same session is current.

What counts as a write is what the code did to THIS object: djust's own state
keys (``liveview_*``, ``_djust_explicit_*``), and equally a handler's or
``check_permissions``' ``request.session[...] = ...``, which therefore still
reach the store. A mutable value read through ``session[key]`` /
``session.get(key)`` (a ``dict``, ``list`` or ``set`` that is not one of
djust's own keys) is compared with a copy taken at that read, and written when
it changed: Django's ``modified = True`` pattern for an in-place change keeps
working. A change made only to a value reached through ``items()``/``values()``,
or after ``session.modified = True`` with no read, is not seen: assign the key.

On ``db`` and ``cached_db`` the read takes the row lock (``SELECT ... FOR
UPDATE`` inside one ``transaction.atomic``), so two merging saves of one
session serialise instead of interleaving. That removes the lost update between
djust saves. It cannot reach a writer that does not merge and read earlier
(Django's own ``SessionMiddleware`` saves its whole dict at the end of its
request). ``cache``, ``file`` and SQLite have no row lock, so there the window
shrinks to the read-to-write interval and the read and the write are two
statements outside any transaction of ours.

Left as they were, with a plain whole-session ``save()``:

- ``signed_cookies``: the client holds the whole session; there is no stored
  copy to merge into.
- a session the store no longer holds (the check of :mod:`djust._late_save`
  drops a pool save; elsewhere the backend answers as before);
- a session whose store cannot be read: the failure is logged and the save goes
  ahead as before;
- a session djust did not track (a save with no :func:`track_session` before
  it), one whose data dict was replaced since (``flush()``/``clear()`` after a
  logout in a handler), and one whose key changed since the save began.

The cost is one extra session read per save (``db``: one ``SELECT ... FOR
UPDATE`` in the same transaction as the ``UPDATE``).
"""

from __future__ import annotations

import contextlib
import copy
import logging
from typing import Any, Dict, Iterator, Optional, Set, Tuple

from asgiref.sync import sync_to_async

from ._late_save import _stored_session, check_session

logger = logging.getLogger(__name__)

#: djust's own session keys: written explicitly by a save, never compared.
_OWN_PREFIXES = ("liveview_", "_djust_")
#: A value ``copy.deepcopy`` could not copy: assumed changed when it is read.
_UNCOPYABLE = object()


class TrackedSessionData(dict):
    """A session's data dict that remembers which keys it was given or lost.

    ``pickle`` and ``copy`` see a plain ``dict``: ``cache`` and ``cached_db``
    store ``session._session`` as it is, and a tracker that reached a cache
    entry would be read back as a tracker by another session.
    """

    __slots__ = ("written", "removed", "_seen")

    def __init__(self, data: Dict[str, Any]) -> None:
        super().__init__(data)
        self.written: Set[str] = set()
        self.removed: Set[str] = set()
        # Mutable values handed out by a read, as they were then.
        self._seen: Dict[str, Any] = {}

    def __reduce_ex__(self, protocol: int) -> Tuple[Any, ...]:
        return (dict, (dict(self),))

    def __eq__(self, other: object) -> bool:
        # Equal by content: what was written is bookkeeping, not value.
        return dict.__eq__(self, other)

    __hash__ = None  # type: ignore[assignment]  # a mutable mapping, as dict

    # -- writes ------------------------------------------------------------

    def _wrote(self, key: str) -> None:
        self.written.add(key)
        self.removed.discard(key)
        self._seen.pop(key, None)

    def _lost(self, key: str) -> None:
        self.removed.add(key)
        self.written.discard(key)
        self._seen.pop(key, None)

    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(key, value)
        self._wrote(key)

    def __delitem__(self, key: str) -> None:
        super().__delitem__(key)
        self._lost(key)

    def pop(self, key: str, *default: Any) -> Any:
        present = key in self
        value = super().pop(key, *default)
        if present:
            self._lost(key)
        return value

    def popitem(self) -> Tuple[str, Any]:
        key, value = super().popitem()
        self._lost(key)
        return key, value

    def clear(self) -> None:
        for key in list(self):
            self._lost(key)
        super().clear()

    def update(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def __ior__(self, other: Any) -> "TrackedSessionData":  # type: ignore[override,misc]
        self.update(other)
        return self

    def setdefault(self, key: str, default: Any = None) -> Any:
        if key in self:
            return self[key]
        self[key] = default
        return default

    # -- reads of a mutable value ------------------------------------------

    def _read(self, key: str, value: Any) -> Any:
        if (
            isinstance(value, (dict, list, set))
            and key not in self._seen
            and key not in self.written
            and not key.startswith(_OWN_PREFIXES)
        ):
            try:
                self._seen[key] = copy.deepcopy(value)
            except Exception:  # noqa: BLE001 — cannot compare it: treat it as changed
                self._seen[key] = _UNCOPYABLE
        return value

    def __getitem__(self, key: str) -> Any:
        return self._read(key, super().__getitem__(key))

    def get(self, key: str, default: Any = None) -> Any:
        if key not in self:
            return default
        return self._read(key, super().__getitem__(key))

    # -- the merge ---------------------------------------------------------

    def pending(self) -> Tuple[Dict[str, Any], Set[str]]:
        """``(key -> value to write, keys to remove)`` since the last save."""
        changed = {k: dict.__getitem__(self, k) for k in self.written if k in self}
        for key, before in self._seen.items():
            if key in changed or key not in self:
                continue
            now = dict.__getitem__(self, key)
            if before is _UNCOPYABLE or now != before:
                changed[key] = now
        return changed, set(self.removed)

    def adopt(self, merged: Dict[str, Any], changed: Dict[str, Any], removed: Set[str]) -> None:
        """Become ``merged`` in place. The changes stay recorded until :meth:`settle`,
        so a save that fails is retried with them."""
        self.written.update(changed)
        self.removed.update(removed)
        self._seen.clear()
        dict.clear(self)
        dict.update(self, merged)

    def settle(self) -> None:
        """The store has what this dict has: nothing is pending."""
        self.written.clear()
        self.removed.clear()
        self._seen.clear()


def track_session(session: Any) -> bool:
    """Start recording the writes made through ``session``; True when it is tracked.

    Loads the session. Idempotent. A session this cannot merge into, because
    the client holds it (``signed_cookies``) or because it is not a Django
    session, is left alone and its saves stay whole-session saves.
    """
    from django.contrib.sessions.backends import signed_cookies
    from django.contrib.sessions.backends.base import SessionBase

    if not isinstance(session, SessionBase) or isinstance(session, signed_cookies.SessionStore):
        return False
    try:
        data = session._session  # loads
    except Exception:  # noqa: BLE001 — the turn reports a failing store itself
        return False
    if type(data) is TrackedSessionData:
        return True
    if type(data) is not dict:
        return False
    session._session_cache = TrackedSessionData(data)
    return True


def is_tracked(session: Any) -> bool:
    return type(getattr(session, "_session_cache", None)) is TrackedSessionData


class _Unreadable(Exception):
    """The stored session could not be read; the save goes ahead whole."""


def _is_db_backed(backend: type) -> bool:
    from django.contrib.sessions.backends import db as db_backend

    return issubclass(backend, db_backend.SessionStore)  # cached_db is a subclass


def _supports_row_lock(backend: type) -> bool:
    """Whether the session's database can lock a row (``db``, ``cached_db``; not SQLite)."""
    if not _is_db_backed(backend):
        return False
    from django.db import connections, router

    using = router.db_for_write(backend.get_model_class())
    return bool(connections[using].features.has_select_for_update)


@contextlib.contextmanager
def _row_lock(backend: type) -> Iterator[None]:
    """One transaction around the read and the write, where the store can lock a row."""
    if not _supports_row_lock(backend):
        yield
        return
    from django.db import router, transaction

    using = router.db_for_write(backend.get_model_class())
    with transaction.atomic(using=using):
        yield


def _read_stored(backend: type, key: str) -> Optional[Any]:
    """The stored data for ``key``; ``None`` when it is gone. Locks the row on ``db``."""
    if not _is_db_backed(backend):
        return _stored_session(backend, key)
    from django.db import router
    from django.utils import timezone

    # ``select_for_update`` locks only inside the transaction ``_row_lock``
    # opens; where the database cannot lock (SQLite) it is a plain read.

    fresh = backend(session_key=key)
    model = fresh.model
    row = (
        model.objects.using(router.db_for_write(model))
        .select_for_update()
        .filter(session_key=key, expire_date__gt=timezone.now())
        .first()
    )
    return None if row is None else fresh.decode(row.session_data)


def _save(session: Any, expected_key: Optional[str]) -> None:
    """The save as it was before merging: the late-save check, then the whole dict."""
    check_session(session, expected_key)
    session.save()


def save_merged(session: Any, expected_key: Optional[str] = None) -> None:
    """Store ``session`` by merging this object's changes into the stored copy.

    Replaces ``check_session(session, key); session.save()``: the late-save
    check (:mod:`djust._late_save`) runs first on the same read. ``expected_key``
    is the key the save began with. A session that was not tracked is saved whole.
    """
    data = getattr(session, "_session_cache", None)
    key = getattr(session, "session_key", None)
    if (
        type(data) is not TrackedSessionData
        or not key
        or (expected_key is not None and key != expected_key)
    ):
        _save(session, expected_key)
        return
    try:
        with _row_lock(type(session)):
            _merge_and_save(session, data, key, expected_key)
    except _Unreadable:
        _save(session, expected_key)


def _merge_and_save(
    session: Any, data: TrackedSessionData, key: str, expected_key: Optional[str]
) -> None:
    try:
        stored = _read_stored(type(session), key)
    except Exception as exc:  # noqa: BLE001 — an unreadable store is not a logout
        from ._exposure_diagnostics import log_failure

        # A storage error can carry session values.
        log_failure(
            logger,
            exc,
            "State save: the stored session could not be read; saving the whole session",
            level="warning",
            traceback=True,
        )
        raise _Unreadable from None
    check_session(session, expected_key, stored=stored)
    if stored is None:
        session.save()  # the row is gone and this is no pool save: the backend answers
        return
    changed, removed = data.pending()
    merged = dict(stored)
    merged.update(changed)
    for name in removed:
        merged.pop(name, None)
    data.adopt(merged, changed, removed)
    session.save()
    data.settle()


async def asave_merged(session: Any) -> None:
    """:func:`save_merged` from async code (the read and write share one thread)."""
    await sync_to_async(save_merged)(session)


__all__ = ["TrackedSessionData", "asave_merged", "is_tracked", "save_merged", "track_session"]
