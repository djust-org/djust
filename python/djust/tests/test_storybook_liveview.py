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

import re

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


def _tag_sequence(html: str) -> list[str]:
    """Tag names in document order, comments stripped — the tree's shape."""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    # Extract the CONTENTS of the mount root, in a shape both sides share.
    #
    # Two things this must not assume. The attributes are stamped server-side
    # (`mixins/request.py` adds `dj-view` after `dj-root`), so matching
    # `<div dj-view=...>` literally misses. And the root now wraps the whole
    # layout, so the region does not end at `</main>` — it ends at the root's
    # own closing `</div>`, which is the last one before `</body>`.
    m = re.search(r"<div[^>]*\bdj-root\b[^>]*>", html)
    if not m:
        return re.findall(r"<(/?[a-zA-Z][a-zA-Z0-9]*)", html)

    start = m.end()
    body_close = html.find("</body>", start)
    if body_close == -1:
        # A WS mount frame: already the region, with no document shell.
        region = html[start:]
    else:
        root_close = html.rfind("</div>", start, body_close)
        region = html[start:root_close] if root_close != -1 else html[start:body_close]

    return re.findall(r"<(/?[a-zA-Z][a-zA-Z0-9]*)", region)


@_BASE
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_http_render_and_ws_mount_agree_structurally():
    """A patch path computed server-side must resolve against the client's DOM.

    The two frames come from different code paths — the HTTP GET renders the
    page shell and replaces the dj-root region, the WS mount renders the same
    region for diffing — and if they disagree about the tree, every patch
    targets a path that does not exist. The client's symptom is exactly that:

        Path traversal failed at index 7, only 1 children (parent: MAIN)
        Patch failed (SetText): node not found at path=7/1/3/1/0

    followed by a `html_recovery` morph, so a toggle "works" by re-rendering the
    whole region instead of patching it.

    The cause was the mount attribute sitting on a `<main>`. `_DJ_VIEW_RE` and
    `_DJ_ROOT_RE` (`mixins/template.py:32`, `:48`) both require a `<div>`, so the
    dj-root normalisation was skipped and the initial-GET HTML kept the comments
    and whitespace the WS frame had already stripped — the mismatch docstring
    #1737 describes. This pins the invariant rather than the div: whatever the
    mount element is, the two frames must describe the same tree.
    """
    from asgiref.sync import sync_to_async
    from django.test import Client

    # sync_to_async: the test client is synchronous and this test is not.
    def _fetch() -> str:
        return Client().get("/theme/gallery/storybook/accordion/").content.decode()

    http_html = await sync_to_async(_fetch)()
    _communicator, mounted = await _mount("accordion")
    await _communicator.disconnect()

    http_shape = _tag_sequence(http_html)
    ws_shape = _tag_sequence(mounted.get("html", ""))

    assert http_shape, "the HTTP render had no tags to compare"
    assert http_shape == ws_shape, (
        "the HTTP render and the WS mount disagree about the tree, so patch "
        "paths will not resolve. First divergence: "
        f"{next((i for i, (a, b) in enumerate(zip(http_shape, ws_shape)) if a != b), 'length')}"
    )


# (component, event, params) — the descriptors the storybook attaches, and the
# event each one's markup emits. `_INTERACTIVE` in live_views.py is the source
# of truth for the set; this list is what proves each one answers.
_INTERACTIVE_EVENTS = [
    ("accordion", "accordion_toggle", {"value": "2"}),
    ("tabs", "set_tab", {"value": "2"}),
    ("collapsible", "toggle_collapsible", {"value": "1"}),
    ("dropdown", "toggle_dropdown", {}),
    ("modal", "toggle_modal", {"value": "1"}),
    ("sheet", "toggle_sheet", {"value": "1"}),
    ("tooltip", "toggle_tooltip", {"value": "1"}),
    ("carousel", "carousel_go", {"value": "1"}),
]


@_BASE
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("component,event,params", _INTERACTIVE_EVENTS)
async def test_interactive_component_answers_its_event(component, event, params):
    """Rendering is not functionality: the event has to reach a handler.

    Each of these components emits a `dj-click` naming its own event. Before the
    storybook became a LiveView, that click reached nothing at all — the markup
    was correct and the component was inert. Mounting and asserting a non-error
    frame is what separates the two.
    """
    communicator, _mounted = await _mount(component)
    try:
        await communicator.send_json_to(
            {"type": "event", "event": event, "params": params, "ref": 1}
        )
        response = await communicator.receive_json_from(timeout=5)
    finally:
        await communicator.disconnect()

    assert response.get("type") != "error", (
        f"{component}: {event} was refused by the server: {response!r}"
    )
