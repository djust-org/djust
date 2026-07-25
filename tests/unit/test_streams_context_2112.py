"""Sync stream data must reach the template context (#2112).

``StreamsMixin.stream()`` fills ``self._streams[name].items``, and
``_get_streams_context()`` exists to expose that to templates — its docstring
says "Get streams data for template context". Nothing ever called it, so the
documented pattern in ``docs/website/guides/large-lists.md``::

    self.stream("messages", Message.recent(50), limit=50)

stored the data and then rendered nothing: ``{% for m in messages %}`` saw an
undefined name.

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
    assert "messages" in ctx, (
        "self.stream('messages', ...) stored data but 'messages' is absent from "
        "the template context, so {% for m in messages %} renders nothing"
    )
    assert [m["text"] for m in ctx["messages"]] == ["a", "b"]


def test_stream_context_reflects_later_inserts():
    v = _mounted()
    v.stream_insert("messages", {"id": 3, "text": "c"})
    ctx = v.get_context_data()
    assert [m["text"] for m in ctx["messages"]] == ["a", "b", "c"]


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
    assert [m.text for m in ctx["messages"]] == ["b"]


def test_multiple_streams_all_exposed():
    class Multi(LiveView):
        template_name = "unused.html"

        def mount(self, request, **kwargs):
            self.stream("alpha", [{"id": 1}])
            self.stream("beta", [{"id": 2}])

    v = Multi()
    v.mount(None)
    ctx = v.get_context_data()
    assert "alpha" in ctx and "beta" in ctx


# ---------------------------------------------------------------------------
# Guard rails — the fix must not clobber user data
# ---------------------------------------------------------------------------


def test_user_context_wins_over_stream_of_same_name():
    """An explicit get_context_data value must not be silently overwritten."""

    class Clashing(LiveView):
        template_name = "unused.html"

        def mount(self, request, **kwargs):
            self.stream("messages", [{"id": 1, "text": "from-stream"}])

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx["messages"] = [{"id": 9, "text": "from-user"}]
            return ctx

    v = Clashing()
    v.mount(None)
    got = v.get_context_data()
    assert [m["text"] for m in got["messages"]] == ["from-user"]


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
    assert "messages" in v.get_context_data()


@pytest.mark.parametrize("name", ["messages", "items", "rows"])
def test_stream_name_becomes_the_context_key(name):
    class Named(LiveView):
        template_name = "unused.html"

        def mount(self, request, **kwargs):
            self.stream(name, [{"id": 1}])

    v = Named()
    v.mount(None)
    assert name in v.get_context_data()
