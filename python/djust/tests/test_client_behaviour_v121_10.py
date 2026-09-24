"""v1.2.1-10 — the server halves of the client-behaviour bucket.

* #2971 — ``clear_draft()`` re-arms every time (it was ignored after the
  first call in a session).
* #2964 — ``stream(..., limit=)`` and ``stream_prune()`` cap the rendered
  list, so the pruned rows leave the page through the normal VDOM diff.

The client halves (#2965, #2971's patch path, #2949, #2966) are pinned in
``tests/js/client-behaviour-v121-10.test.js``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from djust import LiveView
from djust.drafts import DraftModeMixin


class DraftView(DraftModeMixin, LiveView):
    template = (
        '<div dj-root><form data-draft-enabled data-draft-key="{{ draft_key }}"'
        "{% if draft_clear %} data-draft-clear{% endif %}>x</form></div>"
    )
    draft_key = "post_2971"

    def mount(self, request: Any, **kwargs: Any) -> None:
        pass


class TestClearDraftReArms:
    def test_each_clear_draft_reaches_one_render(self):
        view = DraftView()
        view.mount(None)
        view.clear_draft()
        assert view.get_context_data().get("draft_clear") is True
        assert "draft_clear" not in view.get_context_data()
        # The second clear in the same session used to be ignored.
        view.clear_draft()
        assert view.get_context_data().get("draft_clear") is True

    def test_clear_draft_pushes_the_clear_event(self):
        """Over a live connection the flag alone can miss: when the previous
        render already carried it, the next one produces no patch."""
        view = DraftView()
        view.mount(None)
        view.clear_draft()
        events = [e for e in view._pending_push_events if e[0] == "djust:draft-clear"]
        assert events == [("djust:draft-clear", {"key": "post_2971"})]

    def test_the_render_carries_the_flag_once(self):
        view = DraftView()
        view.mount(None)
        view.render_with_diff()
        view.clear_draft()
        html, patches, _ = view.render_with_diff()
        assert "data-draft-clear" in (html or "") + json.dumps(patches or "")
        html, patches, _ = view.render_with_diff()
        assert "data-draft-clear" not in (html or "")


class Feed(LiveView):
    template = (
        '<div dj-root><ul dj-stream="messages">'
        '{% for m in streams.messages %}<li id="messages-{{ m.id }}">{{ m.t }}</li>{% endfor %}'
        "</ul></div>"
    )

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.stream("messages", [{"id": i, "t": f"m{i}"} for i in range(3)])


def _rows(html: str) -> list:
    import re

    return re.findall(r'id="messages-(\d+)"', html)


class TestStreamLimitCapsTheRenderedList:
    def test_append_with_limit_drops_the_oldest_rows(self):
        view = Feed()
        view.mount(None)
        html, _, _ = view.render_with_diff()
        assert _rows(html) == ["0", "1", "2"]
        view.stream("messages", [{"id": 9, "t": "m9"}], limit=2)
        html, patches, _ = view.render_with_diff()
        assert _rows(html) == ["2", "9"]
        # The pruned rows leave the page through the diff.
        assert "Remove" in json.dumps(patches), patches

    def test_prepend_with_limit_drops_the_newest_rows(self):
        view = Feed()
        view.mount(None)
        view.render_with_diff()
        view.stream("messages", [{"id": 7, "t": "m7"}], at=0, limit=2)
        html, _, _ = view.render_with_diff()
        assert _rows(html) == ["7", "0"]

    @pytest.mark.parametrize("edge, expected", [("top", ["1", "2"]), ("bottom", ["0", "1"])])
    def test_stream_prune(self, edge, expected):
        view = Feed()
        view.mount(None)
        view.render_with_diff()
        view.stream_prune("messages", limit=2, edge=edge)
        html, _, _ = view.render_with_diff()
        assert _rows(html) == expected
        ops = [o for o in view._stream_operations if o["type"] == "stream_prune"]
        assert ops[-1] == {"type": "stream_prune", "stream": "messages", "limit": 2, "edge": edge}

    def test_limit_above_the_count_changes_nothing(self):
        view = Feed()
        view.mount(None)
        view.render_with_diff()
        view.stream_prune("messages", limit=10)
        html, _, _ = view.render_with_diff()
        assert _rows(html) == ["0", "1", "2"]
