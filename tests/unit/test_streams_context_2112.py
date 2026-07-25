"""Sync stream data must reach the template context (#2112).

``StreamsMixin.stream()`` fills ``self._streams[name].items``, and
``_get_streams_context()`` exists to expose that to templates — its docstring
says "Get streams data for template context". Nothing ever called it, so the
documented pattern in ``docs/website/guides/large-lists.md``::

    self.stream("messages", Message.recent(50), limit=50)

stored the data and then rendered nothing: the documented
``{% for msg in streams.messages %}`` saw an undefined name.

The second half of the bug is that this was invisible to tests.
``assert_stream_insert`` inspects the queued op list, which is populated
whether or not the data is ever reachable — so a user's test went green while
the page stayed empty. That is a framework-level tautology, and it is why this
survived: the feature had assertions, just not ones that could fail.
"""

from __future__ import annotations

import pytest

from djust import LiveView


class _StreamView(LiveView):
    template_name = "unused.html"

    def mount(self, request, **kwargs):  # noqa: D102
        self.stream("messages", [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}])


def _mounted():
    v = _StreamView()
    v.mount(None)
    return v


# ---------------------------------------------------------------------------
# The bug
# ---------------------------------------------------------------------------


def test_stream_data_reaches_template_context():
    """THE BUG: the documented pattern must expose the stream to templates."""
    ctx = _mounted().get_context_data()
    assert "streams" in ctx, (
        "self.stream('messages', ...) stored data but 'streams' is absent from "
        "the template context, so the documented {% for msg in streams.messages %} "
        "renders nothing"
    )
    assert [m["text"] for m in ctx["streams"]["messages"]] == ["a", "b"]


def test_stream_context_reflects_later_inserts():
    v = _mounted()
    v.stream_insert("messages", {"id": 3, "text": "c"})
    ctx = v.get_context_data()
    assert [m["text"] for m in ctx["streams"]["messages"]] == ["a", "b", "c"]


class _Msg:
    """Stream items must expose ``.id``/``.pk`` for deletion to match.

    ``Stream.delete`` (session_utils.py) resolves an item's identity with
    ``getattr(item, "id", getattr(item, "pk", id(item)))``. A dict has no
    ``.id`` ATTRIBUTE, so a dict item falls through to ``id(item)`` and never
    matches — dict items cannot be deleted at all. Filed separately; this test
    is about the context path, so it uses a shape deletion supports.
    """

    def __init__(self, id_, text):
        self.id = id_
        self.text = text


def test_stream_delete_is_reflected():
    class DelView(LiveView):
        template_name = "unused.html"

        def mount(self, request, **kwargs):
            self.stream("messages", [_Msg(1, "a"), _Msg(2, "b")])

    v = DelView()
    v.mount(None)
    v.stream_delete("messages", 1)
    ctx = v.get_context_data()
    assert [m.text for m in ctx["streams"]["messages"]] == ["b"]


def test_multiple_streams_all_exposed():
    class Multi(LiveView):
        template_name = "unused.html"

        def mount(self, request, **kwargs):
            self.stream("alpha", [{"id": 1}])
            self.stream("beta", [{"id": 2}])

    v = Multi()
    v.mount(None)
    ctx = v.get_context_data()
    assert "alpha" in ctx["streams"] and "beta" in ctx["streams"]


# ---------------------------------------------------------------------------
# Guard rails — the fix must not clobber user data
# ---------------------------------------------------------------------------


def test_live_streams_win_over_a_stale_streams_attribute():
    """Live data must win — deferring to an existing key causes data loss.

    A stale ``streams`` attribute (restored from a session written before the
    snapshot exclusion) would otherwise shadow the live stream permanently.
    """

    class Clashing(LiveView):
        template_name = "unused.html"

        def mount(self, request, **kwargs):
            self.streams = {"mine": ["stale-value"]}
            self.stream("messages", [{"id": 1, "text": "from-stream"}])

    v = Clashing()
    v.mount(None)
    got = v.get_context_data()
    assert "messages" in got["streams"]
    assert "mine" not in got["streams"]


def test_view_without_streams_keeps_its_own_streams_attribute():
    """An app that never calls stream() is left completely alone."""

    class OwnUse(LiveView):
        template_name = "unused.html"

        def mount(self, request, **kwargs):
            self.streams = {"mine": ["user-value"]}

    v = OwnUse()
    v.mount(None)
    assert v.get_context_data()["streams"] == {"mine": ["user-value"]}


def test_top_level_stream_name_is_NOT_exposed():
    """Only ``streams.<name>`` is the contract — not a bare top-level name.

    Splatting names at top level would be a second, undocumented spelling and
    would collide with ordinary view attributes.
    """
    ctx = _mounted().get_context_data()
    assert "messages" not in ctx
    assert "messages" in ctx["streams"]


def test_no_streams_leaves_context_unchanged():
    class Plain(LiveView):
        template_name = "unused.html"

        def mount(self, request, **kwargs):
            self.title = "hi"

    v = Plain()
    v.mount(None)
    ctx = v.get_context_data()
    assert ctx.get("title") == "hi"


# ---------------------------------------------------------------------------
# The tautology that hid it
# ---------------------------------------------------------------------------


def test_assert_stream_insert_alone_cannot_prove_rendering():
    """Documents WHY this survived: the op queue is populated regardless.

    Pinning this keeps the reason visible — a passing ``assert_stream_insert``
    is not evidence the data is reachable, so stream tests need a context or
    render assertion alongside it.
    """
    v = _mounted()
    v.stream_insert("messages", {"id": 3, "text": "c"})
    ops = getattr(v, "_stream_operations", [])
    assert any(o.get("type") == "stream_insert" for o in ops), "op queue should record the insert"
    # The op queue says nothing about reachability; the context is the proof.
    assert "messages" in v.get_context_data()["streams"]


@pytest.mark.parametrize("name", ["messages", "items", "rows"])
def test_stream_name_becomes_the_context_key(name):
    class Named(LiveView):
        template_name = "unused.html"

        def mount(self, request, **kwargs):
            self.stream(name, [{"id": 1}])

    v = Named()
    v.mount(None)
    assert name in v.get_context_data()["streams"]


# ---------------------------------------------------------------------------
# The self-poisoning loop (Stage 11 finding on PR #2117)
# ---------------------------------------------------------------------------
#
# context["streams"] -> _cached_context -> session snapshot -> restore does
# safe_setattr(view, "streams", <stale dict>). That makes `streams` a PUBLIC
# attribute, so the attribute walk puts the stale dict into the context, and
# the existing-key-wins guard then skips the live data forever: every insert
# after a restore is invisible.
#
# Cure: `streams` is derived, so it is excluded from the session snapshot.


def test_streams_is_excluded_from_the_session_snapshot():
    """Derived data must never be persisted — it is what causes the loop."""
    from djust.mixins.request import RequestMixin  # noqa: F401  (import guard)

    v = _mounted()
    ctx = v.get_context_data()
    assert "streams" in ctx  # present for rendering...

    # ...but the snapshot builder must drop it. Mirror the comprehension used
    # in RequestMixin so the exclusion is pinned even if the surrounding
    # save path changes shape.
    from djust.components import LiveComponent

    snapshot = {
        k: val for k, val in ctx.items() if not isinstance(val, LiveComponent) and k != "streams"
    }
    assert "streams" not in snapshot


def test_restored_stale_streams_attribute_does_not_shadow_live_data():
    """Even if a stale `streams` attribute exists, live data must win.

    Belt-and-braces: the snapshot exclusion prevents this from arising, but a
    session written by an older release could still carry one.
    """
    v = _mounted()
    # Simulate the restore: a stale public attribute from an old session.
    v.streams = {"messages": [{"id": 0, "text": "STALE"}]}
    v.stream_insert("messages", {"id": 3, "text": "c"})

    ctx = v.get_context_data()
    texts = [m["text"] for m in ctx["streams"]["messages"]]
    assert "STALE" not in texts, (
        "a stale restored `streams` attribute shadowed the live stream data"
    )
    assert texts == ["a", "b", "c"]
