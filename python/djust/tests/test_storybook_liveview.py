"""The storybook's component previews must be a real djust view.

They were a plain Django view, which renders a component's markup but cannot
make it *work*: `dj-click` is a server event and a plain view ships no server to
reach, so the accordion showed a chevron and did nothing when clicked. The page
whose entire purpose is demonstrating djust was demonstrating a dead component.

The first attempt at this was a client-side shim that intercepted `dj-click` and
toggled a class locally. That is worse than doing nothing — it makes the
component *look* live while the page demonstrates the opposite of the framework
— and it was discarded.

These tests drive a real ``WebsocketCommunicator`` against the real consumer, so
they exercise the same path the browser does: mount over the socket, send the
event the markup declares, and assert the frame that comes back.

``LIVEVIEW_ALLOWED_MODULES`` is overridden to ``None`` throughout. The project
these tests run under sets it to its own app plus nothing else, and setting it
**replaces** the framework's ``"djust"`` fallback — so djust's own views cannot
mount until the project allowlists them. That is the bug filed as #2889; this
suite is about the storybook, and the override keeps the two independent.
"""

import pytest
from django.test import override_settings

VIEW_PATH = "djust.theming.gallery.live_views.StorybookDetailView"

# Two overrides, for two reasons documented above:
#   LIVEVIEW_ALLOWED_MODULES  — #2889, so djust's own views may mount at all
#   ROOT_URLCONF             — the gallery templates reverse `djust_theming:*`
_BASE = override_settings(LIVEVIEW_ALLOWED_MODULES=None, ROOT_URLCONF="djust.tests.urls_theming")


async def _mount(component_name: str):
    """Mount the storybook LiveView over a real WebSocket."""
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await communicator.connect()
    assert connected, "the WebSocket did not accept the connection"

    try:
        await communicator.receive_json_from(timeout=3)  # connect ack
    except Exception:  # noqa: BLE001 — absent on some transports
        pass

    await communicator.send_json_to(
        {
            "type": "mount",
            "view": VIEW_PATH,
            "params": {"component_name": component_name},
        }
    )
    return communicator, await communicator.receive_json_from(timeout=5)


def _html_of(payload: dict) -> str:
    """The rendered markup out of a mount/update frame."""
    for key in ("html", "html_update"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    patches = payload.get("patches")
    if isinstance(patches, list):
        return "\n".join(str(p) for p in patches)
    return ""


@_BASE
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_storybook_view_mounts_over_the_socket():
    """The view must resolve and mount — the allowlist is the only thing between it and the wire."""
    communicator, mounted = await _mount("accordion")
    try:
        assert mounted.get("type") != "error", (
            f"the mount was refused, so nothing is listening to dj-click: {mounted!r}"
        )
        assert mounted.get("type") != "navigate", f"unexpected redirect: {mounted!r}"
        assert mounted.get("view") or _html_of(mounted) or mounted.get("type") == "mount", (
            f"no render in the mount frame: {mounted!r}"
        )
    finally:
        await communicator.disconnect()


@_BASE
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_accordion_toggle_reaches_the_server():
    """The regression: this event used to reach no server at all.

    The example starts with item 1 open. Clicking item 2 sends
    `accordion_toggle` with its `data-value`; the descriptor sets `active` and
    the server answers. Asserting the response is a patch or an html update —
    not an error — is what distinguishes a mounted view from a rendered one.
    """
    communicator, _mounted = await _mount("accordion")
    try:
        await communicator.send_json_to(
            {
                "type": "event",
                "event": "accordion_toggle",
                "params": {"value": "2"},
                "ref": 1,
            }
        )
        updated = await communicator.receive_json_from(timeout=5)
    finally:
        await communicator.disconnect()

    assert updated.get("type") != "error", f"the toggle was refused by the server: {updated!r}"
    assert updated.get("type") in ("patch", "html_update", "html_recovery"), (
        f"unexpected frame for a toggle: {updated!r}"
    )


@_BASE
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_unknown_component_is_refused_not_rendered():
    """A bad component name must not silently render an empty page."""
    communicator, mounted = await _mount("not_a_component")
    try:
        assert mounted.get("type") == "error", (
            f"an unknown component should be refused: {mounted!r}"
        )
    finally:
        await communicator.disconnect()
