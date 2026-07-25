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
