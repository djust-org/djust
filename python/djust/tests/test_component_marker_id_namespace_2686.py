"""#2686 — two instances of one ``template_name`` LiveComponent must not render
identical ``<!--dj-if id=...-->`` marker ids (link N+1 of #2530).

After #2530 a ``template_name`` component renders on its OWN ``RustLiveView``,
so its ``{% if %}`` blocks carry boundary markers. The id is
``if-<template-SOURCE-hash>-<ordinal>`` and each instance is a fresh view whose
ordinal restarts at 0 — so two instances of one component class in one parent
both emit ``if-<hash>-0``. The client resolves ``RemoveSubtree`` /
``InsertSubtree`` / ``MoveSubtree`` by FIRST matching id
(``static/djust/src/12-vdom-patch.js``), so a toggle inside the SECOND instance
lands on the first.

Same failure class as #1832 (one parsed ``{% if %}`` rendered once per ``{% for %}``
iteration), on the instance axis instead of the iteration axis — and cured the
same way: a render-time suffix from a ``Context`` FIELD with a validated
grammar, never a context key (#2529). The parse-time prefix is deliberately
NOT salted: ``template_hash_hex(source)`` must keep equalling the parse prefix
(the Redis state-cache key contract, #1362) and the parse cache is keyed on
source.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List

import pytest
from django.test import override_settings

from djust import LiveView
from djust.components.base import LiveComponent, _dj_if_id_namespace
from djust.decorators import event_handler
from djust.testing import LiveViewTestClient
from djust.utils import clear_template_dirs_cache

pytestmark = [pytest.mark.django_db]

MOD = __name__

# The id is opaque; match the whole thing rather than a shape.
_MARKER = re.compile(r'<!--dj-if id="(if-[0-9A-Za-z_-]+)"-->(.*?)<!--/dj-if-->', re.S)


class Card2686(LiveComponent):
    template_name = "comp_2686/card.html"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.show = False
        self.label = "?"

    def get_context_data(self) -> Dict[str, Any]:
        return {"show": self.show, "label": self.label}


class TwoCards2686(LiveView):
    template = (
        f'<div dj-root dj-view="{MOD}.TwoCards2686" dj-id="0">'
        "<div id='a'>{{ first }}</div><div id='b'>{{ second }}</div></div>"
    )

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.first = Card2686()
        self.first.label = "first"
        self.second = Card2686()
        self.second.label = "second"

    @event_handler()
    def toggle_second(self, **kwargs: Any) -> None:
        self.second.show = not self.second.show
        self.set_changed_keys("second")


@pytest.fixture
def templates(tmp_path: Any) -> Any:
    d = tmp_path / "templates" / "comp_2686"
    d.mkdir(parents=True)
    (d / "card.html").write_text('<p class="card">{{ label }}{% if show %}<b>on</b>{% endif %}</p>')
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path / "templates")],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ],
        LIVEVIEW_ALLOWED_MODULES=[MOD],
    ):
        # `get_template_dirs()` is lru-cached (#1801).
        clear_template_dirs_cache()
        try:
            yield
        finally:
            clear_template_dirs_cache()


class TestTwoInstancesGetDistinctMarkerIds:
    def test_the_two_instances_do_not_share_a_marker_id(self, templates: Any) -> None:
        client = LiveViewTestClient(TwoCards2686)
        client.mount()
        html = client.render()
        ids = [mid for mid, _ in _MARKER.findall(html)]
        assert len(ids) == 2, f"expected one marker per instance, got {ids} in {html}"
        assert len(set(ids)) == 2, (
            f"both instances of one component rendered the SAME dj-if id {ids[0]!r} — "
            "the client resolves subtree patches by first match, so a toggle in the "
            "second instance would land on the first (#2686)."
        )

    def test_the_toggled_body_belongs_to_the_second_instance(self, templates: Any) -> None:
        """The identity is not merely distinct — it tracks the right subtree.

        Deliberately positional (a LIST of pairs, not a dict): under the bug the
        two ids are EQUAL, and a dict keyed on the id silently collapses them to
        one entry, after which ``changed_id in second_instance_html`` is true
        because the same id string also appears in the first instance. That
        version of this test stayed green while the bug was live (#2135's
        "green while destroying what it guards"), so every assertion below is
        by POSITION.
        """
        client = LiveViewTestClient(TwoCards2686)
        client.mount()
        before = _MARKER.findall(client.render())
        client.send_event("toggle_second")
        after = _MARKER.findall(client.render())

        assert len(before) == len(after) == 2, (before, after)
        # Position 0 is the first instance, position 1 the second.
        assert [mid for mid, _ in before] == [mid for mid, _ in after], (before, after)
        assert before[0][1] == after[0][1] == "", (
            f"the FIRST instance's subtree changed on a toggle of the SECOND: {before} -> {after}"
        )
        assert before[1][1] == "" and after[1][1] == "<b>on</b>", (before, after)

        # The changed marker's id must be the one that is NOT the first
        # instance's — which is only meaningful because the ids differ.
        assert after[1][0] != after[0][0], (
            "the two instances still share a marker id, so the client would "
            f"resolve the second instance's patch onto the first (#2686): {after}"
        )

    def test_ids_are_stable_across_renders_of_the_same_instances(self, templates: Any) -> None:
        """The client keys DOM subtrees on these ids, so they must not churn."""
        client = LiveViewTestClient(TwoCards2686)
        client.mount()
        first = [mid for mid, _ in _MARKER.findall(client.render())]
        client.send_event("toggle_second")
        second = [mid for mid, _ in _MARKER.findall(client.render())]
        assert first == second, (first, second)


class TestOverTheRealWebSocket:
    """The same assertion through a real ``WebsocketCommunicator`` mount."""

    def test_ws_mount_html_has_distinct_ids_per_instance(self, templates: Any) -> None:
        html = _ws_mount_html("TwoCards2686")
        ids = [mid for mid, _ in _MARKER.findall(html)]
        assert len(ids) == 2, f"expected one marker per instance, got {ids} in {html}"
        assert len(set(ids)) == 2, f"duplicate dj-if id over the WS mount path: {ids}"


class TestNamespaceIsRefusedUnlessSafe:
    """The namespace is interpolated RAW into an HTML comment (#2529)."""

    @pytest.mark.parametrize(
        "raw",
        ['a"--><script>x</script>', "a-->b", "a b", "a<b", "a-b", "café"],
        ids=["comment-terminator", "arrow", "space", "lt", "hyphen", "non-ascii"],
    )
    def test_python_side_maps_disallowed_characters(self, raw: str) -> None:
        ns = _dj_if_id_namespace(raw)
        assert re.fullmatch(r"[A-Za-z0-9_]*", ns), (raw, ns)

    @pytest.mark.parametrize("raw", ['x"--><script>', "x-y", "x y"])
    def test_rust_setter_refuses_anything_outside_the_alphabet(self, raw: str) -> None:
        """Belt and braces: the sink refuses, it does not escape."""
        from djust._rust import RustLiveView

        view = RustLiveView("<div>{% if a %}<b>x</b>{% endif %}</div>", [])
        view.set_dj_if_id_namespace(raw)
        assert view.dj_if_id_namespace() == "", (
            f"the Rust setter accepted {raw!r} — a namespace outside [A-Za-z0-9_] "
            "can forge live markup inside the marker comment (#2529)."
        )

    def test_rust_setter_accepts_the_allowed_alphabet(self) -> None:
        from djust._rust import RustLiveView

        view = RustLiveView("<div></div>", [])
        view.set_dj_if_id_namespace("card_9fA0")
        assert view.dj_if_id_namespace() == "card_9fA0"


class TestPlainViewsAreUnchanged:
    def test_a_view_with_no_namespace_emits_the_pre_2686_id_shape(self) -> None:
        """Empty namespace = no extra segment, so nothing else moves."""
        from djust._rust import RustLiveView

        view = RustLiveView("<div>{% if a %}<b>x</b>{% endif %}</div>", [])
        view.update_state({"a": True})
        html = view.render()
        ids = [mid for mid, _ in _MARKER.findall(html)]
        assert ids and all(re.fullmatch(r"if-[0-9a-f]{8}-\d+", i) for i in ids), ids


def _ws_mount_html(cls_name: str) -> str:
    async def _drive() -> str:
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer

        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        connected, _ = await comm.connect()
        assert connected
        await comm.receive_json_from(timeout=5)
        try:
            await comm.send_json_to({"type": "mount", "view": f"{MOD}.{cls_name}", "url": "/x/"})
            frames: List[Dict[str, Any]] = []
            for _ in range(6):
                frame = await comm.receive_json_from(timeout=5)
                frames.append(frame)
                if frame.get("type") == "mount":
                    return str(frame.get("html") or "")
            raise AssertionError(f"no mount frame: {frames}")
        finally:
            await comm.disconnect()

    pytest.importorskip("channels")
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_drive())
    finally:
        loop.close()
