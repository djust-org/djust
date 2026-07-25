"""A custom ``dom_id=`` factory must reach delete, not just insert (#2121).

``stream(name, items, dom_id=my_fn)`` lets an app supply its own dom-id
factory. Both insert paths called it; ``stream_delete`` did not — it resolved
independently through ``Stream.resolve_id``. So a stream created with
``dom_id=lambda m: m["slug"]`` inserted rows as ``rows-hello-world`` and
deleted ``rows-1``. The client can never match those, and the delete silently
does nothing on screen.

The #1646 shape one level out: PR #2118 made the three sites agree for the
DEFAULT factory, which left a custom callable as a third resolution the
shared helpers knew nothing about. The cure here is a single chokepoint —
``Stream.dom_id_for`` — that every op goes through.

The tests below assert AGREEMENT between the emitted ops rather than
particular strings, so they keep their meaning if the id format ever changes.
"""

from __future__ import annotations

import logging

import pytest

from djust.mixins.streams import StreamsMixin
from djust.session_utils import Stream


class _View(StreamsMixin):
    def __init__(self):
        self._streams = {}
        self._stream_operations = []


class _Row:
    def __init__(self, id_, slug):
        self.id = id_
        self.slug = slug


def _ids(view, op_type):
    return [o["dom_id"] for o in view._stream_operations if o["type"] == op_type]


# --- the bug -------------------------------------------------------------


def test_delete_uses_the_custom_factory_for_a_dict_item():
    v = _View()
    item = {"id": 1, "slug": "hello-world"}
    v.stream("rows", [item], dom_id=lambda m: m["slug"])
    v.stream_delete("rows", item)

    assert _ids(v, "stream_insert") == _ids(v, "stream_delete"), (
        "insert and delete must emit the same dom_id for the same item"
    )


def test_delete_uses_the_custom_factory_for_an_object_item():
    v = _View()
    row = _Row(1, "hello-world")
    v.stream("rows", [row], dom_id=lambda r: r.slug)
    v.stream_delete("rows", row)

    assert _ids(v, "stream_insert") == _ids(v, "stream_delete")


def test_all_three_emission_sites_agree_under_a_custom_factory():
    # stream() inserts, stream_insert() inserts, stream_delete() deletes.
    # Every one of them is a place the dom_id is computed; a fix that cures
    # two of three is the bug this issue is (#1646).
    v = _View()
    a = {"id": 1, "slug": "first"}
    b = {"id": 2, "slug": "second"}
    v.stream("rows", [a], dom_id=lambda m: m["slug"])
    v.stream_insert("rows", b)
    v.stream_delete("rows", a)
    v.stream_delete("rows", b)

    assert _ids(v, "stream_insert") == ["rows-first", "rows-second"]
    assert _ids(v, "stream_delete") == ["rows-first", "rows-second"]


def test_the_default_factory_still_agrees():
    # The #2116 cure must survive this one.
    v = _View()
    item = {"id": 0, "t": "zero"}  # id=0: the truthiness trap from #2116
    v.stream("rows", [item])
    v.stream_delete("rows", item)

    assert _ids(v, "stream_insert") == _ids(v, "stream_delete") == ["rows-0"]


def test_a_later_explicit_dom_id_rebinds_the_stream():
    # Re-calling stream() with a new factory used to emit ids from the NEW
    # callable while stream_insert/stream_delete kept using the OLD one —
    # the same disagreement, one call later.
    v = _View()
    v.stream("rows", [{"id": 1, "slug": "a"}])
    v.stream("rows", [{"id": 2, "slug": "b"}], dom_id=lambda m: m["slug"])
    v.stream_delete("rows", {"id": 2, "slug": "b"})

    assert _ids(v, "stream_insert")[-1] == "rows-b"
    assert _ids(v, "stream_delete") == ["rows-b"]


# --- the case the framework genuinely cannot fix -------------------------


def test_a_bare_id_under_a_custom_factory_warns_instead_of_failing_silently(caplog):
    # The framework cannot invert an arbitrary callable, so a bare id cannot
    # produce the custom dom_id. That is inherent — but it was SILENT, which
    # is the part that is fixable.
    v = _View()
    v.stream("rows", [{"id": 1, "slug": "hello"}], dom_id=lambda m: m["slug"])

    with caplog.at_level(logging.WARNING, logger="djust"):
        v.stream_delete("rows", 1)

    assert "custom dom_id" in caplog.text
    assert "rows" in caplog.text


def test_a_bare_id_under_the_default_factory_does_not_warn(caplog):
    # The default factory resolves a bare id correctly, so warning there
    # would be noise on the common path.
    v = _View()
    v.stream("rows", [{"id": 1}])

    with caplog.at_level(logging.WARNING, logger="djust"):
        v.stream_delete("rows", 1)

    assert caplog.text == ""
    assert _ids(v, "stream_insert") == _ids(v, "stream_delete") == ["rows-1"]


# --- the chokepoint itself -----------------------------------------------


def test_dom_id_for_is_the_only_place_streams_py_builds_a_dom_id():
    # Structural pin: the fix is only durable if no site re-derives the id.
    # A future op that formats its own f"{name}-{...}" reintroduces #2121.
    import pathlib

    src = pathlib.Path("python/djust/mixins/streams.py").read_text()
    assert 'f"{name}-' not in src, (
        "streams.py must build dom ids via Stream.dom_id_for, not by "
        "formatting the name prefix itself (#2121)"
    )
    assert src.count("stream_obj.dom_id_for(") == 3, (
        "expected exactly 3 dom_id_for call sites (stream, stream_insert, "
        "stream_delete); a new one needs a corresponding agreement test"
    )


@pytest.mark.parametrize(
    "arg,expected",
    [
        ({"id": 1}, True),
        ({"slug": "x"}, True),  # a mapping is an item even with no id
        (_Row(1, "a"), True),
        (1, False),
        ("abc", False),
        (None, False),
    ],
)
def test_looks_like_item_discrimination(arg, expected):
    assert Stream._looks_like_item(arg) is expected
