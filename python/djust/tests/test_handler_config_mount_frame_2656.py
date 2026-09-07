"""#2656 — the server half of ``@debounce`` / ``@throttle``.

The decorators used to stamp metadata nothing read. The client half now lives
in ``static/djust/src/05-handler-rate-limit.js``; this file pins the SERVER
contract it consumes — the ``handler_config`` key on the mount frame, shipped
by the same route ``@cache`` already uses for ``cache_config``
(``ViewRuntime._extract_cache_config`` -> mount frame -> ``setCacheConfig``).

The N-collapses-to-1 assertion itself is necessarily client-side (the timer
that drops the intermediate events runs in the browser), and lives in
``tests/js/handler_rate_limit_2656.test.js``. What is provable here is the
half that decides whether the client gate ever engages: does a real
``WebsocketCommunicator`` mount frame carry the decorator config, in the
shape the client reads, and ONLY for handlers that actually declare it.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest
from django.test import override_settings

from djust import LiveView
from djust.decorators import cache, debounce, event_handler, rate_limit, throttle

pytestmark = [pytest.mark.django_db]

MOD = __name__


class RateLimited2656(LiveView):
    """One handler per decorator shape the client gate can consume."""

    template = f'<div dj-view="{MOD}.RateLimited2656" dj-id="0"><p>{{{{ q }}}}</p></div>'

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.q = ""

    @event_handler()
    @debounce(wait=0.5, max_wait=2.0)
    def search(self, value: str = "", **kwargs: Any) -> None:
        self.q = value

    @event_handler()
    @throttle(interval=0.25, leading=True, trailing=False)
    def on_scroll(self, **kwargs: Any) -> None:
        pass

    @event_handler()
    @cache(ttl=30)
    def cached_only(self, **kwargs: Any) -> None:
        pass

    @event_handler()
    @rate_limit(rate=5)
    def server_limited_only(self, **kwargs: Any) -> None:
        pass

    @event_handler()
    def plain(self, **kwargs: Any) -> None:
        pass


async def _mount_frame() -> Dict[str, Any]:
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from(timeout=5)
    try:
        await comm.send_json_to({"type": "mount", "view": f"{MOD}.RateLimited2656", "url": "/x/"})
        return await comm.receive_json_from(timeout=5)
    finally:
        await comm.disconnect()


def _run() -> Dict[str, Any]:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_mount_frame())
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _env() -> Any:
    pytest.importorskip("channels")
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False):
        yield


class TestMountFrameCarriesHandlerConfig:
    """The real WebSocket mount frame, not a hand-built dict."""

    def test_mount_frame_carries_handler_config(self) -> None:
        frame = _run()
        assert frame.get("type") == "mount", frame
        cfg = frame.get("handler_config")
        assert cfg is not None, (
            "the mount frame carries no handler_config — the client rate-limit "
            "gate (src/05-handler-rate-limit.js) never engages, which is the "
            "inert state #2656 exists to end"
        )
        assert cfg["search"]["debounce"] == {"wait": 0.5, "max_wait": 2.0}
        assert cfg["on_scroll"]["throttle"] == {
            "interval": 0.25,
            "leading": True,
            "trailing": False,
        }

    def test_only_rate_limit_decorators_ride_the_mount_frame(self) -> None:
        """Every mount pays for this dict, so it carries only what is read.

        ``@cache`` has its own ``cache_config`` key, ``@rate_limit`` is
        enforced server-side and means nothing to the client, and an
        undecorated handler has nothing to say at all.
        """
        cfg = _run()["handler_config"]
        assert set(cfg) == {"search", "on_scroll"}, (
            f"handler_config should carry only @debounce/@throttle handlers; got {sorted(cfg)}"
        )
        for entry in cfg.values():
            assert set(entry) <= {"debounce", "throttle"}, entry

    def test_cache_config_still_ships_separately(self) -> None:
        """The new key must not have displaced the one it was modelled on."""
        frame = _run()
        assert frame.get("cache_config", {}).get("cached_only") == {
            "ttl": 30,
            "key_params": [],
        }


class TestExtractHandlerConfig:
    """Unit-level shape, including the empty case."""

    def test_view_with_no_rate_limit_decorators_ships_nothing(self) -> None:
        from djust.runtime import ViewRuntime

        class Bare2656(LiveView):
            template = "<div></div>"

            @event_handler()
            def plain(self, **kwargs: Any) -> None:
                pass

        runtime = ViewRuntime.__new__(ViewRuntime)
        assert runtime._extract_handler_config(Bare2656()) is None, (
            "a view with no @debounce/@throttle must add no mount-frame key at all"
        )

    def test_the_extractor_and_the_client_agree_on_which_keys_travel(self) -> None:
        """A pin coupling the server's allowlist to the client's reader.

        ``_CLIENT_RATE_LIMIT_KEYS`` is the only thing deciding what crosses;
        if a future decorator is added to it with no client consumer, every
        mount pays for a field nothing reads — the exact shape of #2656.
        """
        from pathlib import Path

        from djust.runtime import ViewRuntime

        module = (
            Path(__file__).resolve().parents[1] / "static/djust/src/05-handler-rate-limit.js"
        ).read_text()
        for key in ViewRuntime._CLIENT_RATE_LIMIT_KEYS:
            assert f"cfg.{key}" in module, (
                f"runtime ships '{key}' on the mount frame but "
                f"05-handler-rate-limit.js never reads cfg.{key}"
            )
