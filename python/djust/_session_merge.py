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

1. takes a snapshot of the recorded changes;
2. reads the session as the store holds it NOW (one read);
3. applies the snapshot's writes and removals to that copy and writes the
   result through a store object of its own, so the live session dict is not
   touched while the write is in flight;
4. brings the live dict up to the written copy KEY BY KEY, and clears from the
   record only what the snapshot held.

Step 4 is why a write made while a save is in flight (the next event's
handler, after a save that outran its deadline) is not lost: it is newer than
the snapshot, so it stays recorded and is saved by the next save. It is also
why an object the application holds a reference to stays attached: a key whose
stored value equals the in-memory one keeps its in-memory object; only a key
another writer changed is replaced.

What counts as a write is what the code did to THIS object: djust's own state
keys (``liveview_*``, ``_djust_explicit_*``), and equally a handler's or
``check_permissions``' ``request.session[...] = ...``, which therefore still
reach the store. A mutable value read through ``session[key]`` /
``session.get(key)`` or created by ``setdefault`` / assignment (a ``dict``,
``list`` or ``set`` that is not one of djust's own keys) is compared with a
copy taken at that point, and written when it changed, so a reference held
across events and changed in place keeps being saved. A change made only to a
value reached through ``items()``/``values()``, or after
``session.modified = True`` with no read, is not seen: assign the key.

On ``db`` and ``cached_db`` the read takes the row lock (``SELECT ... FOR
UPDATE`` inside one ``transaction.atomic``), so two merging saves of one
session serialise instead of interleaving. That removes the lost update between
djust saves. It cannot reach a writer that does not merge and read earlier
(Django's own ``SessionMiddleware`` saves its whole dict at the end of its
request). ``cache``, ``file`` and SQLite have no row lock, so there the window
shrinks to the read-to-write interval and the read and the write are two
statements outside any transaction of ours. ``cached_db``'s cache write runs
after the transaction ends, so a slow cache never holds the row lock.

Left as they were, with a plain whole-session ``save()``:

- ``signed_cookies``: the client holds the whole session; there is no stored
  copy to merge into.
- a session the store no longer holds (the check of :mod:`djust._late_save`
  drops a pool save; elsewhere the backend answers as before);
- a session whose store cannot be read: the failure is logged and the save goes
  ahead as before;
- a session djust did not track (a save with no :func:`track_session` before
  it), one whose data dict was replaced since (``flush()``/``clear()`` in a
  handler), and one whose key changed since the save began.

After such a whole-session save the store holds what the object holds, so the
session is tracked again from there. A ``cycle_key()`` (a login) in a handler
copies the in-memory session to the new key, as Django does, before any save:
what another request stored earlier is not carried over to the new key.

The cost is one extra session read per save (``db``: one ``SELECT ... FOR
UPDATE`` in the same transaction as the ``UPDATE``).
"""

from __future__ import annotations

import contextlib
import copy
import logging
import threading
from typing import Any, Callable, Dict, Iterator, NamedTuple, Optional, Set, Tuple

from asgiref.sync import sync_to_async

from ._late_save import _stored_session, check_session

logger = logging.getLogger(__name__)

#: djust's own session keys: written explicitly by a save, never compared.
_OWN_PREFIXES = ("liveview_", "_djust_")
#: Instance attributes of a session that are not carried to the writer.
_NOT_COPIED = frozenset({"_session_cache", "_SessionBase__session_key"})
#: A value ``copy.deepcopy`` could not copy: assumed changed when it is read.
#: The same goes for a value whose ``==`` is identity (an object without
#: ``__eq__``, only possible with a pickle-based ``SESSION_SERIALIZER``): its
#: copy never compares equal, so a container holding one counts as changed at
#: every save (written each time, as the whole-session save did) and, when the
#: store holds that key, the in-memory object is replaced by the stored copy
#: rather than kept (``reconcile``).
_UNCOPYABLE = object()


def _watches(key: Any, value: Any) -> bool:
    """Whether an in-place change to ``value`` under ``key`` is looked for."""
    return (
        isinstance(key, str)
        and isinstance(value, (dict, list, set))
        and not key.startswith(_OWN_PREFIXES)
    )


def _baseline(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:  # noqa: BLE001 — cannot compare it: treat it as changed
        return _UNCOPYABLE


def _differs(before: Any, now: Any) -> bool:
    if before is _UNCOPYABLE:
        return True
    try:
        return bool(now != before)
    except Exception:  # noqa: BLE001 — an unorderable value: treat it as changed
        return True


class Snapshot(NamedTuple):
    """The changes one save writes, as they stood when it started."""

    gen: int  #: writes stamped after this are newer than the snapshot
    changed: Dict[Any, Any]  #: key -> the value to write
    removed: Set[Any]
    baselines: Dict[Any, Any]  #: key -> copy of a watched value, for the next save


class TrackedSessionData(dict):
    """A session's data dict that remembers which keys it was given or lost.

    ``pickle`` and ``copy`` see a plain ``dict``: ``cache`` and ``cached_db``
    store ``session._session`` as it is, and a tracker that reached a cache
    entry would be read back as a tracker by another session.
    """

    __slots__ = ("written", "removed", "_seen", "_stamp", "_gen", "_lock")

    def __init__(self, data: Dict[Any, Any]) -> None:
        super().__init__(data)
        self.written: Set[Any] = set()
        self.removed: Set[Any] = set()
        # Watched values as they were when last read or saved.
        self._seen: Dict[Any, Any] = {}
        # When each key was last written or removed, on a counter.
        self._stamp: Dict[Any, int] = {}
        self._gen = 0
        # A save runs on a pool thread while the next handler may write.
        self._lock = threading.RLock()

    def __reduce_ex__(self, protocol: Any) -> Tuple[Any, ...]:
        return (dict, (dict(self),))

    def __eq__(self, other: object) -> bool:
        # Equal by content: what was written is bookkeeping, not value.
        return dict.__eq__(self, other)

    __hash__ = None  # type: ignore[assignment]  # a mutable mapping, as dict

    # -- writes ------------------------------------------------------------

    def _touch(self, key: Any) -> None:
        self._gen += 1
        self._stamp[key] = self._gen
        self._seen.pop(key, None)

    def _wrote(self, key: Any) -> None:
        self._touch(key)
        self.written.add(key)
        self.removed.discard(key)

    def _lost(self, key: Any) -> None:
        self._touch(key)
        self.removed.add(key)
        self.written.discard(key)

    def __setitem__(self, key: Any, value: Any) -> None:
        with self._lock:
            super().__setitem__(key, value)
            self._wrote(key)

    def __delitem__(self, key: Any) -> None:
        with self._lock:
            super().__delitem__(key)
            self._lost(key)

    def pop(self, key: Any, *default: Any) -> Any:
        with self._lock:
            present = key in self
            value = super().pop(key, *default)
            if present:
                self._lost(key)
            return value

    def popitem(self) -> Tuple[Any, Any]:
        with self._lock:
            key, value = super().popitem()
            self._lost(key)
            return key, value

    def clear(self) -> None:
        with self._lock:
            for key in list(self):
                self._lost(key)
            super().clear()

    def update(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def __ior__(self, other: Any) -> "TrackedSessionData":  # type: ignore[override,misc]
        self.update(other)
        return self

    def setdefault(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            if key in self:
                return self[key]
            self[key] = default
            return default

    # -- reads of a mutable value ------------------------------------------

    def _read(self, key: Any, value: Any) -> Any:
        if _watches(key, value):
            with self._lock:
                if key not in self._seen and key not in self.written:
                    self._seen[key] = _baseline(value)
        return value

    def __getitem__(self, key: Any) -> Any:
        return self._read(key, super().__getitem__(key))

    def get(self, key: Any, default: Any = None) -> Any:
        if key not in self:
            return default
        return self._read(key, super().__getitem__(key))

    # -- the merge ---------------------------------------------------------

    def snapshot(self) -> Snapshot:
        """The changes since the last save, and the baselines they leave behind."""
        with self._lock:
            changed = {k: dict.__getitem__(self, k) for k in list(self.written) if k in self}
            for key, before in list(self._seen.items()):
                if key not in changed and key in self:
                    now = dict.__getitem__(self, key)
                    if _differs(before, now):
                        changed[key] = now
            baselines = {k: _baseline(v) for k, v in changed.items() if _watches(k, v)}
            return Snapshot(self._gen, changed, set(self.removed), baselines)

    def reconcile(self, merged: Dict[Any, Any], snap: Snapshot) -> None:
        """Bring this dict up to ``merged`` (what was written), key by key.

        A key the snapshot wrote, or that was written or removed since, is left
        alone. A key whose in-memory value equals the written one keeps its
        in-memory object, so a reference held elsewhere stays attached.
        """
        with self._lock:
            for key in list(dict.keys(self)):
                if key in snap.changed or self._stamp.get(key, 0) > snap.gen:
                    continue
                now = dict.__getitem__(self, key)
                if key in self._seen and _differs(self._seen[key], now):
                    continue  # changed in place since the snapshot: the next save writes it
                if key not in merged:
                    dict.__delitem__(self, key)  # another writer removed it
                    self._seen.pop(key, None)
                elif now is not merged[key] and _differs(merged[key], now):
                    dict.__setitem__(self, key, merged[key])
                    self._rebaseline(key, merged[key])
            for key, value in merged.items():
                if (
                    not dict.__contains__(self, key)
                    and key not in snap.removed
                    and self._stamp.get(key, 0) <= snap.gen
                ):
                    dict.__setitem__(self, key, value)  # another writer added it
                    self._rebaseline(key, value)

    def _rebaseline(self, key: Any, value: Any) -> None:
        """The in-memory value is now the stored one: that is what later changes
        are measured against, not the value it replaced."""
        if _watches(key, value):
            self._seen[key] = _baseline(value)
        else:
            self._seen.pop(key, None)

    def settle(self, snap: Snapshot) -> None:
        """The store has what ``snap`` held: forget it, and only it."""
        with self._lock:
            for key in snap.changed:
                if self._stamp.get(key, 0) <= snap.gen:
                    self.written.discard(key)
            for key in snap.removed:
                if self._stamp.get(key, 0) <= snap.gen:
                    self.removed.discard(key)
            for key, baseline in snap.baselines.items():
                if self._stamp.get(key, 0) <= snap.gen and key in self:
                    self._seen[key] = baseline


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
    """The save as it was before merging: the late-save check, then the whole dict.

    Afterwards the store holds what this object holds, so tracking starts again
    from here (a ``clear()``/``flush()`` in a handler had replaced the dict)."""
    data = getattr(session, "_session_cache", None)
    snap = data.snapshot() if type(data) is TrackedSessionData else None
    check_session(session, expected_key)
    session.save()
    if snap is not None:
        data.settle(snap)
    else:
        track_session(session)


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
            after_commit = _merge_and_save(session, data, key, expected_key)
    except _Unreadable:
        _save(session, expected_key)
        return
    if after_commit is not None:
        after_commit()


def _merge_and_save(
    session: Any, data: TrackedSessionData, key: str, expected_key: Optional[str]
) -> Optional[Callable[[], None]]:
    """Merge and write; returns what has to run once the transaction is over."""
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
        snap = data.snapshot()
        session.save()  # the row is gone and this is no pool save: the backend answers
        data.settle(snap)
        return None
    snap = data.snapshot()
    merged = dict(stored)
    merged.update(snap.changed)
    for name in snap.removed:
        merged.pop(name, None)
    # A store object of its own writes the merged copy: the live dict is not
    # touched while the write is in flight, and a handler's write meanwhile
    # stays recorded for the next save.
    writer = type(session)(session_key=key)
    # State a subclass keeps on the instance (a middleware's ip, user agent...)
    # that its ``save()`` may read.
    for name, value in session.__dict__.copy().items():  # a copy: another thread may set one
        if name not in _NOT_COPIED:
            setattr(writer, name, value)
    writer._session_cache = merged
    after_commit = _write(writer)
    data.reconcile(merged, snap)
    data.settle(snap)
    return after_commit


def _write(writer: Any) -> Optional[Callable[[], None]]:
    """``writer.save()``; for ``cached_db`` the cache write is returned to run
    after the transaction, so a slow cache never holds the row lock."""
    from django.contrib.sessions.backends import cached_db
    from django.contrib.sessions.backends import db as db_backend

    backend = type(writer)
    # Only Django's own ``save`` is split; a subclass or a patch keeps its own.
    if (
        issubclass(backend, cached_db.SessionStore)
        and getattr(backend.save, "__module__", None) == cached_db.__name__
    ):
        db_backend.SessionStore.save(writer)
        return lambda: _cache_write(writer)
    writer.save()
    return None


def _cache_write(writer: Any) -> None:
    try:
        writer._cache.set(writer.cache_key, writer._session, writer.get_expiry_age())
    except Exception as exc:  # noqa: BLE001 — as ``cached_db.save`` itself: the database has it
        from ._exposure_diagnostics import log_failure

        # A cache error can carry session values.
        log_failure(
            logger,
            exc,
            "State save: the session cache could not be written; the database has it",
            level="error",
            traceback=True,
        )


async def asave_merged(session: Any) -> None:
    """:func:`save_merged` from async code (the read and write share one thread)."""
    await sync_to_async(save_merged)(session)


__all__ = ["TrackedSessionData", "asave_merged", "is_tracked", "save_merged", "track_session"]
