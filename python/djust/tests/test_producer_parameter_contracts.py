"""ADR-036 P2: every DOM-carrying frame carries the rendered tree's contracts.

The hot-reload patch frame and ``StreamingMixin.push_state`` sent patch/HTML
frames without a contract snapshot. A strict client invalidates its scope on
such a frame, so a strict page stopped working after a hot reload or a
``push_state``; a reload that changes declarations would also leave stale
rules. Both now capture the snapshot in the same operation as the render.
Legacy sessions keep their previous frame shape.
"""

import pytest

from djust import LiveView
from djust.decorators import event_handler
from djust.tests.test_ws_send_version_1788 import _receive_until

_BODY = ["Count: {{ count }}"]


async def _connect_and_mount(view_suffix, url):
    """The #1788 harness, mounting a view from this module."""
    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        session = SessionStore()
        session.create()
        return session.session_key

    class _ScopeSession:
        def __init__(self, key):
            self.session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(await sync_to_async(_create_session)())
    assert (await communicator.connect())[0]
    await communicator.receive_json_from(timeout=2)
    await communicator.send_json_to(
        {"type": "mount", "view": f"{__name__}.{view_suffix}", "url": url}
    )
    mount = await _receive_until(communicator, "mount")
    assert mount.get("type") == "mount", mount
    return communicator, mount


class _StrictHotView(LiveView):
    @property
    def template(self):
        return (
            '<div dj-view="djust.tests.test_producer_parameter_contracts._StrictHotView" '
            f'dj-id="0">{_BODY[0]}</div>'
        )

    def get_template(self):
        return self.template

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler(parameter_policy="strict")
    def bump(self) -> None:
        self.count += 1


class _LegacyHotView(_StrictHotView):
    @property
    def template(self):
        return (
            '<div dj-view="djust.tests.test_producer_parameter_contracts._LegacyHotView" '
            f'dj-id="0">{_BODY[0]}</div>'
        )

    @event_handler(parameter_policy="legacy")
    def bump(self, **kwargs) -> None:
        self.count += 1


class _StrictPushView(LiveView):
    template = (
        '<div dj-view="djust.tests.test_producer_parameter_contracts._StrictPushView" '
        'dj-id="0">Step: {{ step }}</div>'
    )

    def mount(self, request, **kwargs):
        self.step = 0

    @event_handler(parameter_policy="strict")
    async def work(self) -> None:
        self.step = 1
        await self.push_state()
        self.step = 2


class _LegacyPushView(_StrictPushView):
    template = (
        '<div dj-view="djust.tests.test_producer_parameter_contracts._LegacyPushView" '
        'dj-id="0">Step: {{ step }}</div>'
    )

    @event_handler(parameter_policy="legacy")
    async def work(self, **kwargs) -> None:
        self.step = 1
        await self.push_state()
        self.step = 2


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["_StrictHotView", "_LegacyHotView"])
async def test_hotreload_patch_carries_the_contract_snapshot(view):
    from channels.layers import get_channel_layer
    from django.test import override_settings

    _BODY[0] = "Count: {{ count }}"
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True):
        communicator, mount = await _connect_and_mount(view_suffix=view, url="/hot/")
        try:
            _BODY[0] = "Counter: {{ count }}"
            await get_channel_layer().group_send(
                "djust_hotreload", {"type": "hotreload", "file": "x.html"}
            )
            frame = await _receive_until(communicator, "patch")
            assert frame.get("hotreload") is True, frame
            if view == "_StrictHotView":
                assert frame["parameter_contracts"] == mount["parameter_contracts"]
                assert frame["parameter_contract_view"] == f"{__name__}.{view}"
            else:
                assert "parameter_contracts" not in frame
        finally:
            _BODY[0] = "Count: {{ count }}"
            await communicator.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_hotreload_with_undiscoverable_contracts_reloads_the_page(monkeypatch):
    from channels.layers import get_channel_layer
    from django.test import override_settings

    from djust import _parameter_metadata

    _BODY[0] = "Count: {{ count }}"
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True):
        communicator, _ = await _connect_and_mount(view_suffix="_StrictHotView", url="/hot/")
        try:
            _BODY[0] = "Counter: {{ count }}"

            def fail(view):
                raise _parameter_metadata.ContractError("broken reload")

            monkeypatch.setattr(_parameter_metadata, "parameter_contract_manifest", fail)
            await get_channel_layer().group_send(
                "djust_hotreload", {"type": "hotreload", "file": "x.html"}
            )
            frame = await _receive_until(communicator, "reload")
            assert frame["type"] == "reload", frame
            assert "broken reload" not in str(frame)
        finally:
            _BODY[0] = "Count: {{ count }}"
            await communicator.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["_StrictPushView", "_LegacyPushView"])
async def test_push_state_frame_carries_the_contract_snapshot(view):
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, mount = await _connect_and_mount(view_suffix=view, url="/push/")
        try:
            await communicator.send_json_to(
                {"type": "event", "event": "work", "params": {}, "ref": 1}
            )
            pushed = await communicator.receive_json_from(timeout=3)
            assert pushed["type"] in ("patch", "html_update"), pushed
            assert "Step: 1" in str(pushed) or pushed.get("patches"), pushed
            if view == "_StrictPushView":
                assert pushed["parameter_contracts"] == mount["parameter_contracts"]
                assert pushed["parameter_contract_view"] == f"{__name__}.{view}"
            else:
                assert "parameter_contracts" not in pushed
        finally:
            await communicator.disconnect()


from djust.components.base import LiveComponent  # noqa: E402

COMPONENT_CALLS: list = []


class _StrictPick(LiveComponent):
    template = "<span>strict</span>"

    @event_handler(parameter_policy="strict")
    def pick(self, item_id: int) -> None:
        COMPONENT_CALLS.append(("strict", item_id))


class _LegacyPick(LiveComponent):
    template = "<span>legacy</span>"

    @event_handler(parameter_policy="legacy")
    def pick(self, **kwargs) -> None:
        COMPONENT_CALLS.append(("legacy", kwargs.get("item_id")))


class _OwnerLifecycleView(LiveView):
    template = (
        '<div dj-view="djust.tests.test_producer_parameter_contracts._OwnerLifecycleView" '
        'dj-id="0">Owners: {{ owners }}</div>'
    )

    def mount(self, request, **kwargs):
        self.owners = "none"

    @event_handler(parameter_policy="strict")
    def create(self) -> None:
        self._components["menu"] = _StrictPick(component_id="menu")
        self.owners = "strict"

    @event_handler(parameter_policy="strict")
    def replace(self) -> None:
        self._components["menu"] = _LegacyPick(component_id="menu")
        self.owners = "legacy"

    @event_handler(parameter_policy="strict")
    def remove(self) -> None:
        del self._components["menu"]
        self.owners = "none"


def _owner(frame, component_id):
    owners = frame["parameter_contracts"]["owners"]
    return next((o for o in owners if o["component_id"] == component_id), None)


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_component_creation_replacement_and_removal_follow_the_render():
    from django.test import override_settings

    COMPONENT_CALLS.clear()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, mount = await _connect_and_mount("_OwnerLifecycleView", "/owners/")
        try:
            assert _owner(mount, "menu") is None

            async def send(event, params=None, component=None):
                payload = dict(params or {})
                if component:
                    payload["component_id"] = component
                await communicator.send_json_to(
                    {"type": "event", "event": event, "params": payload, "ref": 1}
                )
                return await communicator.receive_json_from(timeout=3)

            created = await send("create")
            assert _owner(created, "menu")["handlers"]["pick"]["policy"] == "strict"
            await send("pick", {"item_id": "7"}, component="menu")
            rejected = await send("pick", {"item_id": "7x"}, component="menu")
            assert rejected["type"] == "error"

            replaced = await send("replace")
            assert _owner(replaced, "menu")["handlers"]["pick"] == {"policy": "legacy"}
            await send("pick", {"item_id": "7x"}, component="menu")

            removed = await send("remove")
            assert _owner(removed, "menu") is None
            gone = await send("pick", {"item_id": "1"}, component="menu")
            assert gone["type"] == "error"
            assert COMPONENT_CALLS == [("strict", 7), ("legacy", "7x")]
        finally:
            await communicator.disconnect()
