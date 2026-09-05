"""#2599 — a ``use_actors=True`` mount must render the view's template.

``SessionActor::handle_mount`` built its ``ViewActor`` with ``ViewActor::new``,
whose backend carries an EMPTY template, so the actor mount frame was
``<html><head></head><body></body></html>`` — the #1911 parity test could only
assert "some HTML with ids", never the template body. Reproduced through the
real path: a ``WebsocketCommunicator`` mount of an actor view.

Harness lifted from ``test_ws_mount_flip_parity_1911.py`` (#1077).
"""

from __future__ import annotations

import re

import pytest
from django.test import override_settings

from djust import LiveView

from .test_ws_mount_flip_parity_1911 import _connect_and_mount

_ALLOWED = "djust.tests.test_actor_mount_template_2599"


class ActorTemplateView(LiveView):
    use_actors = True
    template = (
        f'<div dj-root dj-view="{_ALLOWED}.ActorTemplateView" dj-id="0">'
        "<p>label={{ label }}</p>{% if show %}<b>shown</b>{% endif %}</div>"
    )

    def mount(self, request, **kwargs):
        self.label = "actor-body-2599"
        self.show = True


_BODY = "<p>label={{ label }}</p>{% if show %}<b>shown</b>{% endif %}"


class ActorTwinView(LiveView):
    use_actors = True
    template = f'<div dj-root dj-view="{_ALLOWED}.ActorTwinView" dj-id="0">{_BODY}</div>'

    def mount(self, request, **kwargs):
        self.label = "twin"
        self.show = True


class PlainTwinView(LiveView):
    template = f'<div dj-root dj-view="{_ALLOWED}.PlainTwinView" dj-id="0">{_BODY}</div>'

    def mount(self, request, **kwargs):
        self.label = "twin"
        self.show = True


@pytest.mark.django_db
@pytest.mark.asyncio
class TestActorMountRendersTemplate:
    async def test_actor_frame_html_equals_the_non_actor_frame_html(self):
        """The actor mount frame must go through the same strip + dj-root
        extraction as the non-actor frame — the client does
        ``container.innerHTML = html``, so a verbatim ``<div dj-root>`` wrapper
        nests a second root and puts every patch one level off."""
        pytest.importorskip("channels")
        from djust._rust import create_session_actor  # noqa: F401

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            actor_comm, actor = await _connect_and_mount(f"{_ALLOWED}.ActorTwinView")
            plain_comm, plain = await _connect_and_mount(f"{_ALLOWED}.PlainTwinView")
            try:
                # The dj-if prefix hashes the template SOURCE (which carries the
                # class name), so it is the one legitimately differing byte run.
                norm = lambda h, cls: re.sub(r"if-[0-9a-f]+-", "if-H-", h.replace(cls, "V"))  # noqa: E731
                a = norm(actor["html"], "ActorTwinView")
                p = norm(plain["html"], "PlainTwinView")
                assert a == p, f"actor:\n{a}\nplain:\n{p}"
                assert "dj-root" not in a, a
            finally:
                await actor_comm.disconnect()
                await plain_comm.disconnect()

    async def test_mount_frame_carries_the_template_body(self):
        pytest.importorskip("channels")
        from djust._rust import create_session_actor  # noqa: F401

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, frame = await _connect_and_mount(f"{_ALLOWED}.ActorTemplateView")
            try:
                html = frame.get("html", "")
                assert frame.get("type") == "mount", frame
                assert "label=actor-body-2599" in html, (
                    "the actor mount rendered an empty template (#2599): " + html[:200]
                )
                assert ">shown</b>" in html, html[:200]
            finally:
                await communicator.disconnect()
