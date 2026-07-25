"""Stream context captures must not alias the live item list (#2119).

``_get_streams_context()`` returned ``stream_obj.items`` — the SAME list
object the stream keeps mutating. ``_reset_streams()`` then calls
``Stream.clear()``, which does ``items.clear()`` IN PLACE, so anything holding
the context sees its data vanish underneath it.

Change-detection cannot see that either: the "before" and "after" it compares
are the same object, so a reset is invisible to it. The render happened to
stay correct only because two behaviors cancelled — Action #1039, the
mutation-after-capture class.

The rule from #1039: a capture must not share references with its source.
Test by mutating the source AFTER capturing and asserting the capture is
unchanged.
"""

from __future__ import annotations

from djust import LiveView


class _V(LiveView):
    template_name = "unused.html"

    def mount(self, request, **kwargs):
        self.stream("messages", [{"id": 1, "t": "a"}, {"id": 2, "t": "b"}])


def _mounted():
    v = _V()
    v.mount(None)
    return v


def test_context_capture_survives_a_reset():
    """THE BUG: the captured context must not empty when streams reset."""
    v = _mounted()
    captured = v.get_context_data()["streams"]["messages"]
    assert len(captured) == 2

    v._reset_streams()

    assert len(captured) == 2, (
        "the captured context aliased the live item list, so clearing the "
        "stream emptied a value that had already been handed out"
    )


def test_context_capture_survives_a_later_insert():
    """The other direction: a capture must not grow either."""
    v = _mounted()
    captured = v.get_context_data()["streams"]["messages"]

    v.stream_insert("messages", {"id": 3, "t": "c"})

    assert len(captured) == 2
    # ...while a FRESH read does see the new item.
    assert len(v.get_context_data()["streams"]["messages"]) == 3


def test_capture_is_not_the_same_object_as_the_live_list():
    v = _mounted()
    captured = v.get_context_data()["streams"]["messages"]
    assert captured is not v._streams["messages"].items


def test_two_captures_are_independent():
    v = _mounted()
    first = v.get_context_data()["streams"]["messages"]
    v.stream_insert("messages", {"id": 3, "t": "c"})
    second = v.get_context_data()["streams"]["messages"]
    assert first is not second
    assert len(first) == 2 and len(second) == 3


def test_reset_still_clears_the_live_stream():
    """The reset must keep doing its job — freeing the live items."""
    v = _mounted()
    v._reset_streams()
    assert v._streams["messages"].items == []
    assert v.get_context_data()["streams"]["messages"] == []
