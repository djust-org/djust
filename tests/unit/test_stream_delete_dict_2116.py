"""``Stream.delete`` must work for dict items (#2116).

Identity was resolved with ``getattr(item, "id", getattr(item, "pk", id(item)))``.
``getattr`` reads an ATTRIBUTE; a dict has none, so a dict item fell through to
``id(item)`` — the CPython object address — which never equals a caller's id.
Dict items were therefore undeletable, silently.

Passing the dict itself instead raised ``TypeError: unhashable type: 'dict'``
from ``self._deleted_ids.add(item_id)``, despite the parameter being named
``item_or_id``.

Dicts are the natural shape here: ``stream_insert`` accepts anything, and
nothing signalled that dicts would not delete.
"""

from __future__ import annotations

import pytest

from djust.session_utils import Stream


def _stream(items):
    s = Stream("things", dom_id_fn=lambda i: f"things-{id(i)}")
    for item in items:
        s.insert(item)
    return s


class _Obj:
    def __init__(self, id_):
        self.id = id_


class _PkObj:
    def __init__(self, pk):
        self.pk = pk


# --- the bug -------------------------------------------------------------


def test_delete_dict_item_by_id():
    s = _stream([{"id": 1, "t": "a"}, {"id": 2, "t": "b"}])
    s.delete(1)
    assert [i["t"] for i in s.items] == ["b"]


def test_delete_dict_item_by_passing_the_item():
    """``item_or_id`` says the item is acceptable — it must not raise."""
    s = _stream([{"id": 1, "t": "a"}, {"id": 2, "t": "b"}])
    s.delete({"id": 1, "t": "a"})
    assert [i["t"] for i in s.items] == ["b"]


def test_delete_dict_item_by_pk_key():
    s = _stream([{"pk": 7, "t": "x"}, {"pk": 8, "t": "y"}])
    s.delete(7)
    assert [i["t"] for i in s.items] == ["y"]


# --- existing behavior must not regress ----------------------------------


def test_delete_object_by_id_still_works():
    a, b = _Obj(1), _Obj(2)
    s = _stream([a, b])
    s.delete(1)
    assert s.items == [b]


def test_delete_object_by_passing_the_object_still_works():
    a, b = _Obj(1), _Obj(2)
    s = _stream([a, b])
    s.delete(a)
    assert s.items == [b]


def test_delete_by_pk_attribute_still_works():
    a, b = _PkObj(10), _PkObj(11)
    s = _stream([a, b])
    s.delete(10)
    assert s.items == [b]


def test_unknown_id_removes_nothing():
    s = _stream([{"id": 1}, {"id": 2}])
    s.delete(999)
    assert len(s.items) == 2


def test_deleted_id_is_recorded():
    s = _stream([{"id": 1}])
    s.delete(1)
    assert 1 in s._deleted_ids


def test_dict_without_id_or_pk_is_not_matched_by_another_dict():
    """No id/pk on either side must not collapse to 'equal by identity'."""
    a, b = {"t": "a"}, {"t": "b"}
    s = _stream([a, b])
    s.delete({"t": "a"})  # distinct object, no id -> should match nothing
    assert len(s.items) == 2


@pytest.mark.parametrize("bad", [None, 0, ""])
def test_falsy_ids_are_handled(bad):
    s = _stream([{"id": bad, "t": "keep-me"}, {"id": 99, "t": "other"}])
    s.delete(bad)
    assert [i["t"] for i in s.items] == ["other"]


# --- the PUBLIC entry point must agree with Stream.delete (#1646) ----------
#
# Fixing Stream.delete alone left StreamsMixin.stream_delete and the
# default_dom_id factory on the old resolution, so insert and delete disagreed
# on what identifies a row. Stage 11 caught it: a dict item produced a dom_id
# of "<name>-{'id': 1, 't': 'a'}" (the dict's repr) on delete while inserting
# as "<name>-<address>". The client can never match those.


class _StreamHost:
    """Minimal StreamsMixin host — avoids a full LiveView for a unit test."""

    def __init__(self):
        self._streams = {}
        self._stream_operations = []


def _host():
    from djust.mixins.streams import StreamsMixin

    class Host(StreamsMixin, _StreamHost):
        pass

    return Host()


def test_public_stream_delete_emits_the_same_dom_id_as_insert():
    h = _host()
    h.stream("things", [{"id": 1, "t": "a"}, {"id": 2, "t": "b"}])
    insert_ids = [o["dom_id"] for o in h._stream_operations if o["type"] == "stream_insert"]

    h._stream_operations.clear()
    h.stream_delete("things", {"id": 1, "t": "a"})
    delete_id = [o["dom_id"] for o in h._stream_operations if o["type"] == "stream_delete"][0]

    assert delete_id == insert_ids[0], (
        f"delete emitted {delete_id!r} but the item was inserted as "
        f"{insert_ids[0]!r} — the client cannot match them"
    )
    assert "{" not in delete_id, f"dom_id contains a dict repr: {delete_id!r}"


def test_public_stream_delete_by_bare_id_matches_too():
    h = _host()
    h.stream("things", [{"id": 7, "t": "x"}])
    insert_id = [o["dom_id"] for o in h._stream_operations if o["type"] == "stream_insert"][0]
    h._stream_operations.clear()
    h.stream_delete("things", 7)
    delete_id = [o["dom_id"] for o in h._stream_operations if o["type"] == "stream_delete"][0]
    assert delete_id == insert_id


def test_falsy_id_zero_agrees_between_insert_and_delete():
    """default_dom_id used ``or`` — truthiness — so id=0 fell to the address."""
    h = _host()
    h.stream("things", [{"id": 0, "t": "zero"}])
    insert_id = [o["dom_id"] for o in h._stream_operations if o["type"] == "stream_insert"][0]
    assert insert_id == "things-0", f"id=0 inserted as {insert_id!r}"
    h._stream_operations.clear()
    h.stream_delete("things", 0)
    delete_id = [o["dom_id"] for o in h._stream_operations if o["type"] == "stream_delete"][0]
    assert delete_id == insert_id


# --- the unhashable-id guard (was untested) --------------------------------


def test_unhashable_id_still_removes_from_items():
    """The try/except around the tombstone add must not swallow the removal."""
    s = _stream([{"id": 1, "t": "a"}])
    # An item whose id is itself unhashable.
    weird = {"id": ["not", "hashable"], "t": "w"}
    s.insert(weird)
    s.delete(weird)
    assert [i["t"] for i in s.items] == ["a"], "removal must work even when the tombstone cannot"
