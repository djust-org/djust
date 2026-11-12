"""The `runtime.py` decode site, over a REAL WebSocket mount (#2252).

`python/tests/test_decimal_state_tag_2252.py` covers the sites reachable
synchronously. The one in `runtime.py` is not: it lives inside
`ViewRuntime.dispatch_mount`, which since the #1919 mount flip is what EVERY
WebSocket mount routes through (`RUNTIME_OWNED_VERBS`). So the faithful harness
is a real `WebsocketCommunicator` against `LiveViewConsumer.as_asgi()` —
a `dispatch_mount`-direct call would exercise the same code, but the wire is
where the value has to arrive (#1650, reproduction fidelity).

**The session saved-state restore** reads `liveview_<url>` written by the
per-event save and `safe_setattr`s each key. Serializer: Django's session
`JSONSerializer`. It is gated on `enable_state_snapshot = True`, so the
fixture opts in. (Its twin, the legacy signed back-navigation snapshot, was
removed in 2.0.)

Gate-off (#1468): reverting the `decode_state_roundtrip(...)` call in
`runtime.py` reddens the matching test here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView

_ALLOWED = "djust.tests.test_decimal_state_tag_runtime_2252"
_ALLOWLIST = override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED])

HUGE = Decimal("12345678901234567890.123456789")
TAG = "__djust_decimal__"


class BootstrapView(LiveView):
    """Trivial view mounted only to obtain a live consumer."""

    exposure_policy = "legacy"
    template = f'<div dj-root dj-view="{_ALLOWED}.BootstrapView" dj-id="0">boot</div>'

    def mount(self, request, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {}


class PriceView(LiveView):
    """Opt-in view carrying a `Decimal`. The template renders `price` bare and
    `total` through `|floatformat:2`, so a dict, a string and a float each
    produce a DIFFERENT wrong answer from the `Decimal`."""

    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = (
        f'<div dj-root dj-view="{_ALLOWED}.PriceView" dj-id="0">'
        "p={{ price }}|f={{ price|floatformat:2 }}|h={{ huge }}</div>"
    )

    def mount(self, request, **kwargs):
        self.price = Decimal("1.00")
        self.huge = Decimal("0")

    def get_context_data(self, **kwargs):
        return {"price": self.price, "huge": self.huge}


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _connect(url):
    """Connect a real communicator and bootstrap it to a live consumer."""
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create)()
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()  # drain connect frame
    return communicator, session_key


async def _mount(communicator, view: str, url: str, **extra):
    frame = {"type": "mount", "view": view, "url": url}
    frame.update(extra)
    with _ALLOWLIST:
        await communicator.send_json_to(frame)
        for _ in range(8):
            msg = await communicator.receive_json_from()
            if msg.get("type") == "mount":
                return msg
    raise AssertionError("no mount frame arrived")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestRuntimeSessionRestore:
    """Site 1 — `dispatch_mount`'s `saved_state` loop."""

    async def test_a_tagged_decimal_in_the_session_restores_as_a_decimal(self) -> None:
        url = "/rt-session-2252/"
        communicator, session_key = await _connect(url)
        try:
            from django.contrib.sessions.backends.db import SessionStore

            def _seed():
                s = SessionStore(session_key=session_key)
                # EXACTLY what the per-event save writes post-#2252.
                s[f"liveview_{url}"] = {
                    "price": {TAG: "19.90"},
                    "huge": {TAG: str(HUGE)},
                }
                s.save()

            await sync_to_async(_seed)()
            # Bootstrap first so the consumer is live, then mount the real view.
            await _mount(communicator, f"{_ALLOWED}.BootstrapView", "/rt-boot-2252/")
            frame = await _mount(communicator, f"{_ALLOWED}.PriceView", url)
            html = frame.get("html", "")

            assert "p=19.90" in html, (
                f"the restored Decimal must render with its exponent intact; got {html!r}"
            )
            assert "f=19.90" in html, f"|floatformat:2 must see a number; got {html!r}"
            assert f"h={HUGE}" in html, f"every digit must survive; got {html!r}"
            assert TAG not in html, f"an undecoded tag reached the DOM: {html!r}"
        finally:
            await communicator.disconnect()

    async def test_an_untagged_float_from_an_older_session_still_restores(self) -> None:
        """Backward compatibility on the real path: a session written by a
        pre-#2252 release restores exactly as it used to."""
        url = "/rt-legacy-2252/"
        communicator, session_key = await _connect(url)
        try:
            from django.contrib.sessions.backends.db import SessionStore

            def _seed():
                s = SessionStore(session_key=session_key)
                s[f"liveview_{url}"] = {"price": 19.9, "huge": 1.2345678901234567e19}
                s.save()

            await sync_to_async(_seed)()
            await _mount(communicator, f"{_ALLOWED}.BootstrapView", "/rt-boot2-2252/")
            frame = await _mount(communicator, f"{_ALLOWED}.PriceView", url)
            assert "p=19.9" in frame.get("html", "")
        finally:
            await communicator.disconnect()
