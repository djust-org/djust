"""Directives the server routes to the page view, under its strict contract (#4225).

``dj-viewport-top`` / ``dj-viewport-bottom``, ``dj-mutation``,
``dj-copy-event`` and ``dj-dialog-close-event`` send no ``component_id`` and
no ``view_id``, so the runtime runs them on the page's view wherever the
element sits. The client resolved their strict contract under the nearest
component or embedded child instead, which lists the same names as open
handlers here, and sent arguments the page's closed strict handler refuses.

The built client runs in jsdom against the real server (``_auto_recover_bridge``):
a ``WebsocketCommunicator`` on ``LiveViewConsumer``, or the SSE stream and
message views. Nothing in between builds an event payload or a server frame.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView, state
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import event_handler
from djust.websocket import LiveViewConsumer

from ._auto_recover_bridge import Browser, SSEServer, ws_connect, ws_forward

CALLS: list = []

#: Each directive's markup; every one names a handler the page declares strict
#: and the component / child declare open (``**kwargs``).
MARKUP = {
    "viewport-top": '<div dj-viewport-top="load_more"><p id="vp-first">a</p><p>b</p></div>',
    "viewport-bottom": '<div dj-viewport-bottom="load_more"><p>a</p><p id="vp-last">b</p></div>',
    "mutation": (
        '<div id="mut" dj-mutation="mutated" dj-mutation-attr="class" '
        'dj-mutation-debounce="0"></div>'
    ),
    "copy": '<button id="cp" dj-copy="copied text" dj-copy-event="copied">Copy</button>',
    "dialog": (
        '<dialog id="dlg" dj-dialog="open" dj-dialog-close-event="closed" '
        'dj-value-reason="esc"></dialog>'
    ),
}
ALL_MARKUP = "".join(MARKUP.values())

#: Fires one directive in the page, as the browser would.
FIRE = {
    "viewport-top": "window.__intersect(document.getElementById('vp-first'))",
    "viewport-bottom": "window.__intersect(document.getElementById('vp-last'))",
    "mutation": "document.getElementById('mut').className = 'changed'",
    "copy": "document.getElementById('cp').click()",
    "dialog": "document.getElementById('dlg').dispatchEvent(new Event('close'))",
}

#: What the page's strict handler is called with.
EXPECTED = {
    "viewport-top": ("load_more", {}),
    "viewport-bottom": ("load_more", {}),
    "mutation": ("mutated", {"mutation": "attributes"}),
    "copy": ("copied", {}),
    "dialog": ("closed", {"reason": "esc"}),
}

#: jsdom has no IntersectionObserver or clipboard: stand-ins the test drives.
STUBS = """
window.__observers = [];
window.IntersectionObserver = class {
    constructor(cb) { this.cb = cb; this.targets = new Set(); window.__observers.push(this); }
    observe(el) { this.targets.add(el); }
    unobserve(el) { this.targets.delete(el); }
    disconnect() { this.targets.clear(); }
};
window.__intersect = target => window.__observers.forEach(
    o => o.targets.has(target) && o.cb([{target, isIntersecting: true}]));
Object.defineProperty(navigator, 'clipboard', {configurable: true,
    value: {writeText: () => Promise.resolve()}});
window.djust.initInfiniteScroll();
null
"""


class _NestedHandlers:
    """The page's handler names, as open (``**kwargs``) handlers of a nested owner."""

    @event_handler
    def load_more(self, **kwargs: Any) -> None:
        CALLS.append(("nested", "load_more", kwargs))

    @event_handler
    def mutated(self, **kwargs: Any) -> None:
        CALLS.append(("nested", "mutated", kwargs))

    @event_handler
    def copied(self, **kwargs: Any) -> None:
        CALLS.append(("nested", "copied", kwargs))

    @event_handler
    def closed(self, **kwargs: Any) -> None:
        CALLS.append(("nested", "closed", kwargs))


class DirectiveBox(_NestedHandlers, LiveComponent):
    """A component whose own markup holds every directive."""

    class State(TypedState):
        label: str = "box"

    template = "<div>" + ALL_MARKUP + "</div>"


class _PageHandlers:
    @event_handler(parameter_policy="strict")
    def load_more(self) -> None:
        CALLS.append(("page", "load_more", {}))

    @event_handler(parameter_policy="strict")
    def mutated(self, mutation: str) -> None:
        CALLS.append(("page", "mutated", {"mutation": mutation}))

    @event_handler(parameter_policy="strict")
    def copied(self) -> None:
        CALLS.append(("page", "copied", {}))

    @event_handler(parameter_policy="strict")
    def closed(self, reason: str) -> None:
        CALLS.append(("page", "closed", {"reason": reason}))

    @event_handler(parameter_policy="strict")
    def poke(self, n: int) -> None:
        CALLS.append(("page", "poke", {"n": n}))


class PageWithComponent(_PageHandlers, LiveView):
    exposure_policy = "explicit"
    login_required = False
    template = '<div dj-root>{{ box }}<p id="label">{{ label }}</p></div>'
    box = DirectiveBox()
    label = state("")


class DirectiveChild(_NestedHandlers, LiveView):
    """An embedded child view whose markup holds every directive."""

    exposure_policy = "explicit"
    login_required = False
    template = "<div>" + ALL_MARKUP + "</div>"


class PageWithChild(_PageHandlers, LiveView):
    exposure_policy = "explicit"
    login_required = False
    template = (
        "<div dj-root>{% load live_tags %}"
        '{% live_render "djust.tests.test_page_routed_strict_4225.DirectiveChild" %}'
        "</div>"
    )


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from djust import sse

    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    CALLS.clear()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True):
        yield
    CALLS.clear()


def _page(view_class: type) -> tuple[str, SessionStore]:
    session = SessionStore()
    session.create()
    request = RequestFactory().get("/4225/")
    request.user, request.session, request.tenant = AnonymousUser(), session, None
    response = view_class.as_view()(request)
    assert response.status_code == 200
    return response.content.decode(), session


def _application(params: dict) -> dict:
    return {k: v for k, v in params.items() if k != "_cacheRequestId"}


async def _fire(transport: str, view_class: type, directive: str) -> tuple[dict, Browser]:
    """Mount the page, fire one directive, deliver the event to the server."""
    html, session = await sync_to_async(_page)(view_class)
    browser = await Browser.open(html, transport)
    try:
        if transport == "ws":
            socket_id, socket = await ws_connect(browser, session, LiveViewConsumer)
            await browser.eval(STUBS)
            await browser.eval(FIRE[directive] + "; null")
            frame = await browser.expect(
                lambda m: m["op"] == "ws_send" and json.loads(m["data"]).get("type") == "event",
                "the directive's event",
            )
            await ws_forward(browser, socket_id, socket, frame)
            await socket.disconnect()
            message = json.loads(frame["data"])
        else:
            server = SSEServer(browser, session)
            try:
                await server.open_stream()
                await server.answer_posts(lambda body: body.get("type") == "mount")
                await browser.eval(STUBS)
                await browser.eval(FIRE[directive] + "; null")
                message = await server.answer_posts(lambda body: body.get("type") == "event")
            finally:
                await server.drop()
        return message, browser
    except BaseException:
        await browser.close()
        raise


def _errors(browser: Browser) -> list:
    return [log for log in browser.logs if log["level"] in ("error", "jsdomError")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["ws", "sse"])
@pytest.mark.parametrize("directive", sorted(MARKUP))
@pytest.mark.parametrize(
    "view_class", [PageWithComponent, PageWithChild], ids=["component", "child"]
)
async def test_a_page_routed_directive_reaches_the_page_strict_handler(
    transport, directive, view_class
):
    html, _session = await sync_to_async(_page)(view_class)
    nested = "data-component-id" if view_class is PageWithComponent else "data-djust-embedded"
    assert nested in html  # the owner's real wrapper markup
    message, browser = await _fire(transport, view_class, directive)
    try:
        name, params = EXPECTED[directive]
        assert message["event"] == name
        # No owner context: the server runs it on the page's view.
        assert _application(message["params"]) == params
        assert CALLS == [("page", name, params)]
        assert not _errors(browser)
    finally:
        await browser.close()
