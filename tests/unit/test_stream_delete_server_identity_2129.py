"""Server-side deletion must use the same identity the dom_id does (#2129).

Filed from the Stage 11 review of PR #2127. That PR converged the three *dom_id*
emission sites onto ``Stream.dom_id_for()``; a fourth resolution survived in
``Stream.delete()``, which filtered ``items`` via ``resolve_id``/``_identity``
and never consulted the factory. So the emitted op could name a dom_id the
client can match while the item stayed on the server — and per #2121's scope
note, the server-side list is what actually drives rendered output today.

Two independent causes, measured before fixing:

1. ``resolve_id`` returned ``.id`` whenever the ATTRIBUTE existed, without
   ``_identity``'s ``is not None`` discipline. An object with ``id=None,
   pk=5`` resolved to ``None`` on one side and ``5`` on the other; an unsaved
   row (both ``None``) resolved to ``None`` vs its object address. ``delete()``
   compares one against the other, so neither was ever removed.

2. Nothing consulted a custom factory. A factory reading CONTENT
   (``lambda m: m["slug"]``) is the only thing that can identify id-less rows —
   which is the canonical reason to supply one — so a look-alike argument
   produced a matching dom_id and left the item in place.

Every test asserts what the CALLER observes (the item is gone from the stream),
not an internal id, so they survive a change of identity scheme.
"""

from __future__ import annotations

import pytest

from djust.mixins.streams import StreamsMixin
from djust.session_utils import Stream


class _View(StreamsMixin):
    def __init__(self):
        self._streams = {}
        self._stream_operations = []


class _Unsaved:
    """An unsaved model row: the attributes exist, both are None."""

    def __init__(self, tag):
        self.id = None
        self.pk = None
        self.tag = tag


class _IdNonePkSet:
    """`id` present but None, `pk` carrying the real value."""

    def __init__(self, pk, tag):
        self.id = None
        self.pk = pk
        self.tag = tag


def _items(view, name="rows"):
    return view._streams[name].items


# --- cause 1: resolve_id / _identity disagreed ---------------------------


def test_an_unsaved_row_deletes_by_identity():
    v = _View()
    row = _Unsaved("draft")
    v.stream("rows", [row])

    v.stream_delete("rows", row)

    assert _items(v) == [], "passing the item itself must remove it"


def test_a_row_with_id_none_but_pk_set_deletes():
    v = _View()
    row = _IdNonePkSet(5, "x")
    v.stream("rows", [row])

    v.stream_delete("rows", row)

    assert _items(v) == []


def test_the_two_resolvers_agree_for_every_item_shape():
    # The structural statement of cause 1: whatever resolve_id says an ITEM's
    # identity is, _identity must say the same — delete() compares one to the
    # other, so any divergence is an item that cannot be deleted.
    for item in (
        {"id": 1},
        {"pk": 2},
        {"slug": "no-id"},
        _Unsaved("draft"),
        _IdNonePkSet(5, "x"),
    ):
        assert Stream.resolve_id(item) == Stream._identity(item), (
            f"resolve_id and _identity disagree for {item!r}"
        )


def test_a_bare_id_is_still_the_id_itself():
    # The unification must NOT swallow the bare-id case: an argument that is
    # not an item is the id, verbatim.
    for bare in (1, 0, "abc", None):
        assert Stream.resolve_id(bare) == bare


# --- cause 2: a custom factory defines what a row IS ---------------------


def test_a_custom_factory_identifies_id_less_rows_for_deletion():
    # Identifying id-less rows is the canonical reason to supply a factory.
    v = _View()
    v.stream("rows", [{"slug": "a"}, {"slug": "b"}], dom_id=lambda m: m["slug"])

    v.stream_delete("rows", {"slug": "a"})  # a LOOK-ALIKE, not the same object

    assert _items(v) == [{"slug": "b"}]


def test_a_look_alike_does_not_match_under_the_default_factory():
    # Contrast: without a factory there is nothing to identify an id-less row
    # BY, so a look-alike must not match. That asymmetry is intended — the
    # factory is what supplies the missing identity.
    v = _View()
    v.stream("rows", [{"slug": "a"}])

    v.stream_delete("rows", {"slug": "a"})

    assert _items(v) == [{"slug": "a"}]


def test_a_bare_id_still_deletes_from_a_custom_factory_stream():
    # Matching on EITHER identity rather than switching wholesale to the
    # factory. A bare id cannot be run through the factory, so switching
    # outright would have stopped these deletes working — a regression traded
    # for a fix.
    v = _View()
    v.stream("rows", [{"id": 1, "slug": "a"}], dom_id=lambda m: m["slug"])

    v.stream_delete("rows", 1)

    assert _items(v) == []


def test_a_factory_that_rejects_an_item_simply_does_not_match():
    # Deletion must not raise while scanning items the factory cannot process
    # (possible after a mid-stream rebind); a non-match is the same outcome as
    # any other non-match.
    v = _View()
    v.stream("rows", [{"id": 1, "slug": "a"}], dom_id=lambda m: m["slug"])
    v._streams["rows"].items.append({"id": 2})  # no "slug"

    v.stream_delete("rows", {"id": 1, "slug": "a"})

    assert _items(v) == [{"id": 2}]


# --- the op and the server must not disagree -----------------------------


@pytest.mark.parametrize(
    "items,dom_id,arg",
    [
        ([_Unsaved("draft")], None, "SAME"),
        ([_IdNonePkSet(5, "x")], None, "SAME"),
        ([{"slug": "a"}], lambda m: m["slug"], {"slug": "a"}),
        ([{"id": 1}], None, {"id": 1}),
    ],
)
def test_a_matching_dom_id_always_means_the_item_is_gone(items, dom_id, arg):
    # The invariant the issue is really about: it is never acceptable to emit
    # a delete op the client CAN match while the item survives on the server.
    v = _View()
    if dom_id is None:
        v.stream("rows", items)
    else:
        v.stream("rows", items, dom_id=dom_id)

    target = items[0] if arg == "SAME" else arg
    v.stream_delete("rows", target)

    inserted = [o["dom_id"] for o in v._stream_operations if o["type"] == "stream_insert"]
    deleted = [o["dom_id"] for o in v._stream_operations if o["type"] == "stream_delete"]

    if deleted[0] in inserted:
        assert _items(v) == [], (
            f"the delete op emitted {deleted[0]!r}, which matches an inserted row, "
            f"but the item survived on the server: {_items(v)!r}"
        )
