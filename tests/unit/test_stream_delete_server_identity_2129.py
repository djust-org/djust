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


# --- the OTHER failure direction: a non-target must SURVIVE ---------------
#
# The first version of this fix had none of these, and shipped silent
# destructive over-deletion because of it. Every test above asks "did the
# target get deleted?"; deletion has two failure directions and a suite that
# exercises one of them is the #1543 shape. Deletion is irreversible, so
# over-deleting is the worse direction.


def test_a_value_that_merely_stringifies_alike_is_not_the_same_row():
    # The first version compared the FORMATTED dom_id (f"{name}-{value}"), so
    # any two values with equal str() collapsed: deleting the row keyed 5 also
    # destroyed the row keyed "5".
    v = _View()
    v.stream("rows", [{"id": 5, "t": "a"}, {"id": "5", "t": "b"}], dom_id=lambda m: m["id"])

    v.stream_delete("rows", {"id": 5, "t": "a"})

    assert _items(v) == [{"id": "5", "t": "b"}], "the str-alike row must survive"


def test_a_uuid_does_not_destroy_its_own_string_form():
    # The realistic version of the above — a UUIDField pk alongside the string
    # that arrives from an event-handler param is ordinary Django.
    import uuid

    u = uuid.uuid4()
    v = _View()
    v.stream("rows", [{"id": u, "t": "a"}, {"id": str(u), "t": "b"}], dom_id=lambda m: m["id"])

    v.stream_delete("rows", {"id": u, "t": "a"})

    assert _items(v) == [{"id": str(u), "t": "b"}]


def test_a_bare_id_is_not_compared_against_factory_output():
    # A bare id never goes through the factory, so comparing the factory's
    # output against it is comparing different things. Here row id=9 carries
    # the factory value 5, which must not be destroyed by `delete(5)`.
    v = _View()
    v.stream("rows", [{"id": 5, "g": "g1"}, {"id": 9, "g": 5}], dom_id=lambda m: m["g"])

    v.stream_delete("rows", 5)

    assert _items(v) == [{"id": 9, "g": 5}], "the row whose factory value is 5 must survive"


def test_deleting_one_row_leaves_every_other_row():
    # The blunt version of the invariant. The population deliberately mixes
    # str and int keys that stringify alike — 20 distinct slugs could never
    # collide, so that version could not observe an over-match at all and
    # only ever failed for under-deletion reasons.
    v = _View()
    rows = [{"k": f"s{i}"} for i in range(18)] + [{"k": 7}, {"k": "7"}]
    v.stream("rows", rows, dom_id=lambda m: m["k"])

    v.stream_delete("rows", {"k": 7})

    assert len(_items(v)) == 19, f"exactly one row must go; got {_items(v)!r}"
    assert {"k": "7"} in _items(v), "the str-alike row must survive"


def test_scanning_an_item_the_factory_rejects_stays_silent(caplog):
    # The scan runs the factory over EVERY item. Warning there emits one record
    # per unmatched row — a single delete on a stream with many such rows
    # produced thousands of traceback-bearing warnings inside an event handler.
    # The caller's own rejected argument is still reported, once, by dom_id_for.
    import logging

    v = _View()
    v.stream("rows", [{"id": 1, "slug": "a"}], dom_id=lambda m: m["slug"])
    v._streams["rows"].items.extend({"id": i} for i in range(2, 60))  # no "slug"

    with caplog.at_level(logging.WARNING, logger="djust"):
        v.stream_delete("rows", {"id": 1, "slug": "a"})

    assert caplog.text == "", (
        "scanning factory-rejecting items must not warn per item; got:\n" + caplog.text
    )
    assert len(_items(v)) == 58


def test_the_target_key_is_computed_once_not_once_per_item():
    # It does not depend on the item being scanned. Re-deriving it inside the
    # comprehension made one delete call the user's factory 2n+1 times.
    calls = []

    def factory(m):
        calls.append(m)
        return m["g"]

    v = _View()
    v.stream("rows", [{"id": i, "g": f"g{i}"} for i in range(50)], dom_id=factory)
    calls.clear()

    v.stream_delete("rows", {"id": 25, "g": "g25"})

    assert len(calls) <= 51, (
        f"expected at most one factory call per item plus one for the argument; got {len(calls)}"
    )


def test_a_bare_id_is_not_compared_against_a_TOLERANT_factory_either():
    # The sibling above only fails the gate because `m["g"]` RAISES on an int,
    # so the fallback path hides the missing guard. A defensive user factory
    # that accepts both shapes exposes it: without the _looks_like_item gate,
    # the argument 5 produces the factory key 5 and destroys the row whose
    # factory value is 5. Gate-off proved the sibling could not see this.
    def tolerant(m):
        return m["g"] if isinstance(m, dict) else m

    v = _View()
    # NO row has id 5, deliberately. An earlier version included one, and the
    # identity arm then matched it first — so `identity_hit` short-circuited
    # the factory arm and the test passed with the _looks_like_item gate
    # removed. Isolating the factory arm is the only way to pin it.
    v.stream("rows", [{"id": 9, "g": 5}], dom_id=tolerant)

    v.stream_delete("rows", 5)

    assert _items(v) == [{"id": 9, "g": 5}], (
        "a bare id must be matched by identity only — the factory was never "
        "applied to it, so comparing its output against one compares different things"
    )


def test_a_factory_returning_none_does_not_collapse_every_keyless_row():
    # `getattr(m, "code", None)` is a MORE natural defensive factory than the
    # raising `m["code"]` — and the raising form is the safe one, because the
    # scan's except skips it. With None treated as a value, every keyless row
    # shares one key: measured 3 rows destroyed for 1 targeted.
    #
    # This mirrors _identity's own `is not None` discipline, which exists for
    # exactly this reason ("treating it as a value would give every unsaved
    # item the SAME identity, colliding their dom_ids").
    class Row:
        def __init__(self, pk, code=None):
            self.pk = pk
            self.id = pk
            if code is not None:
                self.code = code

        def __repr__(self):
            return f"Row({self.pk})"

    v = _View()
    rows = [Row(1, "AAA"), Row(2), Row(3), Row(4)]
    v.stream("rows", rows, dom_id=lambda m: getattr(m, "code", None))

    v.stream_delete("rows", rows[1])

    assert [r.pk for r in _items(v)] == [1, 3, 4], (
        f"only the targeted row may go; got {_items(v)!r}. Two checks enforce "
        "this — one in _factory_key_for_argument, one in _is_delete_target — "
        "and either alone suffices, so this fails only when BOTH are gone."
    )


def test_an_int_enum_row_deletes_from_a_plain_int_argument():
    # The other direction, and the reason the predicate is not a type guard.
    # A `type(a) is type(b)` check rejects these — yet they emit an IDENTICAL
    # dom_id, so the client matches and the server would keep the row, which
    # is precisely what this module's headline invariant forbids.
    import enum

    class Color(enum.IntEnum):
        RED = 1

    v = _View()
    v.stream("rows", [{"c": Color.RED, "t": "a"}], dom_id=lambda m: m["c"])

    v.stream_delete("rows", {"c": 1, "t": "a"})

    assert _items(v) == [], "matching dom_ids must mean the item is gone"


def test_a_safestring_key_deletes_from_a_plain_str_argument():
    from django.utils.safestring import mark_safe

    v = _View()
    v.stream("rows", [{"k": mark_safe("a"), "t": "x"}], dom_id=lambda m: m["k"])

    v.stream_delete("rows", {"k": "a", "t": "x"})

    assert _items(v) == []


def test_a_bool_key_does_not_match_an_int_argument():
    # `True == 1` in Python, but they render as "True" and "1", so the client
    # could never have matched them. The dom_id half of the predicate is what
    # separates them.
    v = _View()
    v.stream("rows", [{"k": True, "t": "a"}, {"k": 1, "t": "b"}], dom_id=lambda m: m["k"])

    v.stream_delete("rows", {"k": 1, "t": "b"})

    assert _items(v) == [{"k": True, "t": "a"}]


# --- the class, not the spellings ----------------------------------------
#
# Three rounds of review found the same silent destructive over-deletion in
# nine different factory return values. Value-by-value patching was not
# converging, so the fix became an invariant: a delete op names ONE dom_id,
# and a dom_id addresses at most one element, so the factory arm may remove at
# most one row. Ambiguity means "cannot tell which row you meant" — fall back
# to identity alone.


class _Coded:
    """A row that may or may not carry the attribute a factory reads."""

    def __init__(self, pk, code=None):
        self.pk = pk
        self.id = pk
        if code is not None:
            self.code = code

    def __repr__(self):
        return f"_Coded({self.pk})"


class _AlwaysEqual:
    def __eq__(self, other):
        return True

    def __hash__(self):
        return 0


class _RaisesOnEqual:
    def __eq__(self, other):
        raise RuntimeError("comparison exploded")

    def __hash__(self):
        return 0


@pytest.mark.parametrize(
    "label,default",
    [
        ("None", None),
        ("empty string", ""),
        ("empty list", []),
        ("empty dict", {}),
        ("empty tuple", ()),
        ("zero", 0),
        ("False", False),
        ("a shared string", "unknown"),
        ("a shared object", _AlwaysEqual()),
        ("a value whose __eq__ raises", _RaisesOnEqual()),
    ],
)
def test_a_shared_factory_key_never_deletes_more_than_the_target(label, default):
    # `getattr(m, "code", DEFAULT)` is one idiom; the framework must not treat
    # its spellings differently. Every one of these gave three rows the same
    # key, and eight of the ten destroyed all three.
    #
    # Two arguments, because the two arms fix different halves and each
    # SHADOWS the other if tested alone:
    #
    #   by the item itself -> identity resolves it, the factory arm never runs
    #   by a look-alike    -> identity misses, so the at-most-one bound decides
    #
    # An earlier version only did the first, which made the bound unreachable:
    # gating it off failed nothing.
    v = _View()
    rows = [_Coded(1, "AAA"), _Coded(2), _Coded(3), _Coded(4)]
    v.stream("rows", rows, dom_id=lambda m, d=default: getattr(m, "code", d))

    v.stream_delete("rows", rows[1])

    assert [r.pk for r in _items(v)] == [1, 3, 4], (
        f"factory default {label}: identity must resolve the item itself; got {_items(v)!r}"
    )

    # Now the factory arm alone: an argument identity cannot resolve, whose
    # key three surviving rows share. Ambiguous, so it must remove none.
    v2 = _View()
    rows2 = [_Coded(1, "AAA"), _Coded(2), _Coded(3), _Coded(4)]
    v2.stream("rows", rows2, dom_id=lambda m, d=default: getattr(m, "code", d))

    v2.stream_delete("rows", _Coded(99))  # no such id; key is the shared default

    assert [r.pk for r in _items(v2)] == [1, 2, 3, 4], (
        f"factory default {label}: an ambiguous key must remove NOTHING, not "
        f"an arbitrary row; got {_items(v2)!r}"
    )


def test_an_unambiguous_shared_value_still_deletes():
    # The bound is "at most one", not "never". When exactly ONE row carries the
    # key, the argument and that row genuinely do name the same dom_id and the
    # delete must land — which is why this is better than special-casing None:
    # a None-guard would refuse here.
    v = _View()
    rows = [_Coded(1, "AAA"), _Coded(2)]  # only Row 2 is keyless
    v.stream("rows", rows, dom_id=lambda m: getattr(m, "code", None))

    # Delete by a LOOK-ALIKE, so the identity arm cannot be what matches.
    v.stream_delete("rows", _Coded(99))

    assert [r.pk for r in _items(v)] == [1], (
        "a uniquely-keyed row must be deletable by its factory key"
    )


def test_a_factory_whose_eq_raises_does_not_abort_the_delete():
    # `delete` is called from an event handler; a user value with an exploding
    # __eq__ must not propagate. The comparison lives inside the same try as
    # the factory call for exactly this reason.
    v = _View()
    rows = [_Coded(1, "AAA"), _Coded(2)]
    v.stream("rows", rows, dom_id=lambda m: getattr(m, "code", _RaisesOnEqual()))

    v.stream_delete("rows", rows[0])  # must not raise

    assert [r.pk for r in _items(v)] == [2]


# --- a delete removes at most ONE row, across BOTH arms ------------------


def test_identity_and_factory_arms_cannot_both_fire():
    # The bound governed the factory arm alone, but a delete is
    # `identity OR factory` — so the total was (identity matches) + (0 or 1),
    # not 1. No shared keys and no dom_id collision are needed to see it: a
    # perfectly unique factory and an argument whose id and slug point at
    # DIFFERENT rows (row 1 re-read after a rename) destroyed both.
    v = _View()
    x = {"id": 1, "slug": "alpha"}
    y = {"id": 2, "slug": "beta"}
    v.stream("rows", [x, y], dom_id=lambda m: m["slug"])

    v.stream_delete("rows", {"id": 1, "slug": "beta"})

    ids = [o["dom_id"] for o in v._stream_operations if o["type"] == "stream_delete"]
    assert len(ids) == 1, "one delete op"
    assert _items(v) == [y], (
        "one op naming one dom_id must remove at most one row; identity wins "
        f"because the factory arm exists to identify rows identity cannot. Got {_items(v)!r}"
    )


def test_identity_wins_so_a_stale_row_is_still_deletable():
    # The inverse rule — dom_id authoritative — would break this: the stream
    # holds an OLD copy of the row and the caller passes the updated item.
    # Identity still resolves it, and must keep doing so.
    v = _View()
    stale = {"id": 1, "slug": "old-slug"}
    v.stream("rows", [stale, {"id": 2, "slug": "other"}], dom_id=lambda m: m["slug"])

    v.stream_delete("rows", {"id": 1, "slug": "new-slug"})

    assert _items(v) == [{"id": 2, "slug": "other"}]


def test_an_ambiguous_dom_id_declines_and_says_so(caplog):
    # Declining is right — the op names one dom_id and cannot say which row —
    # but two rows sharing a dom_id is an app bug the developer wants to know
    # about. Fires once per DELETE, not once per row, so the flood that keeps
    # the scan silent does not apply.
    import logging

    v = _View()
    rows = [{"slug": "dup", "n": 1}, {"slug": "dup", "n": 2}]
    v.stream("rows", rows, dom_id=lambda m: m["slug"])

    with caplog.at_level(logging.WARNING, logger="djust"):
        v.stream_delete("rows", {"slug": "dup", "n": 3})

    assert len(_items(v)) == 2, "an ambiguous delete must remove nothing"
    assert "share the dom_id" in caplog.text
    assert "rows" in caplog.text


def test_a_factory_that_appends_during_the_scan_terminates():
    # New surface: calling the user's factory during the scan is what this
    # change introduced (main never called it in delete() at all). Iterating
    # `self.items` live meant a factory with an appending side effect grew the
    # list it was walking and never finished — and a hang inside an event
    # handler is worse than an exception, because nothing times it out and the
    # connection simply stops responding.
    #
    # The precondition is absurd — such a factory misbehaves at insert too —
    # but the fix is a shallow copy, so there is no reason to leave it.
    v = _View()

    def appending(m):
        stream = v._streams.get("rows")
        if stream is not None:
            stream.items.append({"slug": "spawned"})
        return m.get("slug") if isinstance(m, dict) else None

    v.stream("rows", [{"slug": "a"}], dom_id=appending)

    # Would not return at all before the snapshot.
    v.stream_delete("rows", {"slug": "no-such-row"})

    assert any(i.get("slug") == "a" for i in _items(v)), "the original row survives"
