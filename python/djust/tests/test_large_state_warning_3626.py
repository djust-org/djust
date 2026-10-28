"""The ``Large LiveView state`` warning reports its parts and gives advice that works (#3626).

The warning measures ``len(RustLiveView.serialize_msgpack())``: the template, the
context the Rust renderer holds, and the rendered tree the next turn diffs
against. It used to say "use temporary_assigns or streams", and on a toy list
page none of the four approaches the issue tried lowered the number. These
tests run those approaches through a real WebSocket mount and events, then pin
every claim the warning's advice makes against what was measured, so the text
cannot drift away from the behaviour again.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import msgpack
import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.state_backends.base import (
    DjustPerformanceWarning,
    describe_snapshot,
    large_state_warning_message,
)
from djust.state_backends.memory import InMemoryStateBackend

pytestmark = [pytest.mark.django_db]

ROWS = [{"id": i, "body": ("x%d" % i) * 1000} for i in range(30)]  # about 2 KB of markup each
MODULE = __name__


def _root(name: str, body: str) -> str:
    return f'<div dj-view="{MODULE}.{name}" dj-id="0">{body}</div>'


_LIST = '<div id="list">{% for r in rows %}<div id="row-{{ r.id }}">{{ r.body }}</div>{% endfor %}</div>|{{ n }}'


class PlainList(LiveView):
    template = _root("PlainList", _LIST)

    def mount(self, request, **kwargs):
        self.rows = ROWS
        self.n = 0

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


class TemporaryAssigns(PlainList):
    temporary_assigns = {"rows": []}
    template = _root("TemporaryAssigns", _LIST.replace('id="list"', 'id="list" dj-update="append"'))


class Streamed(PlainList):
    template = _root(
        "Streamed",
        '<div id="list" dj-stream="rows">{% for r in streams.rows %}'
        '<div id="rows-{{ r.id }}">{{ r.body }}</div>{% endfor %}</div>|{{ n }}',
    )

    def mount(self, request, **kwargs):
        self.stream("rows", ROWS)
        self.n = 0


class RenderOnly(PlainList):
    template = _root("RenderOnly", _LIST)

    def mount(self, request, **kwargs):
        self.n = 0

    def get_render_only_context(self):
        return {"rows": ROWS}


class FewRows(PlainList):
    template = _root("FewRows", _LIST)

    def mount(self, request, **kwargs):
        self.rows = ROWS[:5]
        self.n = 0


class LeanRows(PlainList):
    template = _root("LeanRows", _LIST.replace("{{ r.body }}", ""))

    def mount(self, request, **kwargs):
        self.rows = [{"id": r["id"]} for r in ROWS]
        self.n = 0


def _parts(view) -> dict:
    """The three parts and the state's keys, via ``describe_snapshot`` on the real blob."""
    blob = view.serialize_msgpack()
    parts = describe_snapshot(blob, top=50)
    assert parts is not None
    return {
        "total": len(blob),
        "state": parts["state"],
        "tree": parts["tree"],
        "keys": dict(parts["keys"]),
    }


async def _mount_and_bump(view_class) -> list[dict]:
    """Mount through the real consumer, send two events, and return what the backend
    held after the mount and after each event (``refresh`` is the post-render store)."""
    from unittest.mock import patch

    from djust.websocket import LiveViewConsumer

    from .test_ws_send_version_1788 import _receive_until

    def make_key():
        session = SessionStore()
        session.create()
        return session.session_key

    key = await sync_to_async(make_key)()

    class Session:
        session_key = key

    stored: list[dict] = []
    real_refresh = InMemoryStateBackend.refresh

    def recording_refresh(self, cache_key, view):
        stored.append(_parts(view))
        return real_refresh(self, cache_key, view)

    communicator = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(),
        "/ws/",
        headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
    )
    communicator.scope["session"] = Session()
    with patch.object(InMemoryStateBackend, "refresh", recording_refresh):
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[MODULE]):
            connected, _ = await communicator.connect()
            assert connected
            await communicator.receive_json_from(timeout=2)
            await communicator.send_json_to(
                {"type": "mount", "view": f"{MODULE}.{view_class.__name__}", "url": "/"}
            )
            await _receive_until(communicator, "mount")
            for ref in (1, 2):
                await communicator.send_json_to(
                    {"type": "event", "event": "bump", "params": {}, "ref": ref}
                )
                await _receive_until(communicator, "patch")
            await communicator.disconnect()
    assert len(stored) >= 3, "the backend was not re-stored after the mount and each event"
    return stored


# --------------------------------------------------------------------------
# describe_snapshot
# --------------------------------------------------------------------------


class TestDescribeSnapshot:
    def _blob(self):
        from djust._rust import RustLiveView

        view = RustLiveView("<div dj-root>{{ a }}|{{ b }}</div>", [])
        view.update_state({"a": "x" * 5000, "b": "y" * 50})
        view.render_with_diff()
        return view.serialize_msgpack()

    def test_the_parts_are_the_exact_spans_of_the_blob(self):
        blob = self._blob()
        template, state, tree, _version, _ts = msgpack.unpackb(
            blob, raw=False, strict_map_key=False
        )
        parts = describe_snapshot(blob, top=10)
        assert parts is not None
        # Derived, not typed in: each span equals the canonical re-encoding of that element.
        assert parts["template"] == len(msgpack.packb(template))
        assert parts["state"] == len(msgpack.packb(state))
        assert parts["tree"] == len(msgpack.packb(tree))
        assert dict(parts["keys"]) == {k: len(msgpack.packb(v)) for k, v in state.items()}

    def test_the_keys_are_largest_first_and_capped(self):
        from djust._rust import RustLiveView

        view = RustLiveView("<div dj-root>{{ k0 }}</div>", [])
        # Eight sizes, none in an order a map would hand back by accident.
        view.update_state(
            {
                "k%d" % i: "v" * size
                for i, size in enumerate([40, 900, 10, 5000, 300, 70, 2000, 600])
            }
        )
        view.render_with_diff()
        blob = view.serialize_msgpack()
        everything = describe_snapshot(blob, top=50)
        assert everything is not None and len(everything["keys"]) >= 8
        sizes = [size for _name, size in everything["keys"]]
        assert sizes == sorted(sizes, reverse=True)
        capped = describe_snapshot(blob, top=2)
        assert capped is not None and capped["keys"] == everything["keys"][:2]
        assert capped["keys"][0][0] == "k3"

    def test_a_blob_with_another_layout_gives_none_not_a_wrong_split(self):
        assert describe_snapshot(msgpack.packb([1, 2])) is None
        assert describe_snapshot(msgpack.packb({"a": 1})) is None
        assert describe_snapshot(b"\xc1\xc1\xc1") is None
        assert describe_snapshot(b"") is None
        assert describe_snapshot(msgpack.packb([1, {1: 2}, 3, 4, 5])) is None  # non-str state key


# --------------------------------------------------------------------------
# What the warning says, and what each piece of advice does
# --------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheAdviceMatchesWhatReducesTheNumber:
    async def test_the_four_approaches_in_the_issue_store_about_the_same(self):
        """Reproduces the issue: a plain list, temporary_assigns, a stream and
        get_render_only_context() all hold the rows after the mount."""
        sizes = {}
        for view_class in (PlainList, TemporaryAssigns, Streamed, RenderOnly):
            sizes[view_class.__name__] = (await _mount_and_bump(view_class))[0]["total"]
        plain = sizes["PlainList"]
        for name, size in sizes.items():
            assert abs(size - plain) < plain * 0.05, sizes

    async def test_temporary_assigns_empties_the_state_copy_but_not_the_tree(self):
        mounted, *after_events = await _mount_and_bump(TemporaryAssigns)
        assert mounted["keys"]["rows"] > 10_000  # the rows were in the state at the mount
        assert after_events[-1]["keys"]["rows"] < mounted["keys"]["rows"] * 0.05
        assert after_events[-1]["state"] < mounted["state"] * 0.25
        assert after_events[-1]["tree"] == mounted["tree"]  # the page is still what the tree says

    async def test_a_stream_and_render_only_context_keep_the_rows_in_the_snapshot(self):
        for view_class, key in ((Streamed, "streams"), (RenderOnly, "rows")):
            mounted, *after_events = await _mount_and_bump(view_class)
            assert mounted["keys"][key] > 10_000
            assert after_events[-1]["keys"][key] >= mounted["keys"][key] * 0.95, view_class
            assert after_events[-1]["tree"] == mounted["tree"], view_class

    async def test_rendering_fewer_and_leaner_rows_is_what_shrinks_it(self):
        full = (await _mount_and_bump(PlainList))[0]["total"]
        few = (await _mount_and_bump(FewRows))[0]["total"]
        lean = (await _mount_and_bump(LeanRows))[0]["total"]
        assert few < full * 0.3, (few, full)
        assert lean < full * 0.1, (lean, full)


class TestTheWarningItself:
    def _snapshot_of_a_big_view(self, rows=200):
        from djust._rust import RustLiveView

        view = RustLiveView("<div dj-root>{% for r in rows %}<p>{{ r }}</p>{% endfor %}</div>", [])
        view.update_state({"rows": ["row-%d-" % i + "z" * 400 for i in range(rows)], "small": 1})
        view.render_with_diff()
        return view

    def test_set_warns_with_the_breakdown_and_the_largest_key(self):
        view = self._snapshot_of_a_big_view()
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            backend.set("k", view)
        messages = [
            str(w.message) for w in caught if issubclass(w.category, DjustPerformanceWarning)
        ]
        assert len(messages) == 1
        text = messages[0]
        parts = _parts(view)
        assert "%.1fKB" % (parts["total"] / 1024) in text
        assert "%.1fKB" % (parts["state"] / 1024) in text
        assert "%.1fKB" % (parts["tree"] / 1024) in text
        assert "rows %.1fKB" % (parts["keys"]["rows"] / 1024) in text

    def test_a_message_for_a_blob_it_cannot_split_still_names_the_total(self):
        text = large_state_warning_message("k", 300 * 1024, 100, snapshot=b"\xc1")
        assert "300.0KB" in text and "threshold: 100KB" in text
        assert "stored snapshot holds" not in text

    def test_no_warning_below_the_threshold(self):
        view = self._snapshot_of_a_big_view(rows=2)
        backend = InMemoryStateBackend(state_size_warning_kb=100)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            backend.set("k", view)
        assert [w for w in caught if issubclass(w.category, DjustPerformanceWarning)] == []

    def test_the_advice_names_each_thing_the_tests_above_measured(self):
        text = large_state_warning_message("k", 300 * 1024, 100)
        assert "render fewer" in text and "paginate" in text
        assert (
            "temporary_assigns empties the context copy after the next render but leaves the tree"
            in text
        )
        assert "streams and get_render_only_context() do not shrink this snapshot" in text
        assert "session save" in text
        # Rows added to a dj-update container (#3639) stay in the tree too.
        assert 'dj-update="append" container keeps every row it was given in the tree' in text

    def test_the_guide_the_warning_points_at_has_that_section(self):
        guide = Path(__file__).resolve().parents[3] / "docs/website/guides/BEST_PRACTICES.md"
        headings = re.findall(r"^#{2,4} (?:\d+\. )?(.+)$", guide.read_text(), re.M)
        assert "Large LiveView state" in headings
