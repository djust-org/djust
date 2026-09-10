"""Every ``djust.components.ui`` LiveComponent whose rendered ``dj-click`` names
a method on itself must decorate that method and route to itself (#2756).

Link N+1 of #2748 (chain-shaped, #2142). PR #2755 fixed ``ModalComponent`` and
pinned only the modal. The class is package-wide: ``AlertComponent.dismiss`` was
undecorated, so under the default ``event_security = "strict"`` its close
button was rejected exactly like the modal's; ``BadgeComponent.dismiss`` was
undecorated AND its close button carried no ``data-component-id``, so the click
would have been dispatched to the parent view rather than the badge.

Two WS reproducers (real ``WebsocketCommunicator`` through ``component_id``
routing, harness lifted from ``test_ws_event_flip_parity_1896.py``), and ONE
structural pin that derives the component list from the package (not a
restated list — #2727), renders every framework branch, and asserts each
self-targeting ``dj-click`` is (a) ``@event_handler``-decorated and (b) paired
with ``data-component-id`` on the same element.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust import config as config_module
from djust.components.base import LiveComponent
from djust.components.ui.alert import AlertComponent
from djust.components.ui.badge import BadgeComponent
from djust.decorators import is_event_handler

_MODULE = "djust.tests.test_ui_dismiss_handlers_2756"
_FRAMEWORKS = ("bootstrap5", "tailwind", "plain")


# ---------------------------------------------------------------------------
# Harness (lifted from test_ws_event_flip_parity_1896.py)
# ---------------------------------------------------------------------------


async def _receive_until(communicator, wanted_type, *, tries=8, timeout=3):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _connect_and_mount(view_path: str, url: str = "/ui/"):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)

    connected, _ = await communicator.connect()
    assert connected, "WebsocketCommunicator must connect"
    await communicator.receive_json_from(timeout=2)  # drain connect frame

    await communicator.send_json_to({"type": "mount", "view": view_path, "url": url})
    mount_frame = await _receive_until(communicator, "mount")
    assert mount_frame.get("type") == "mount", f"expected mount, got {mount_frame!r}"
    return communicator, mount_frame


class AlertHostView(LiveView):
    template = (
        f'<div dj-root dj-view="{_MODULE}.AlertHostView" dj-id="0"><p>visible={{{{ v }}}}</p></div>'
    )

    def mount(self, request, **kwargs):
        self.alert = AlertComponent(component_id="a1", message="Saved", dismissible=True)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["v"] = "yes" if self.alert.visible else "no"
        return ctx


class BadgeHostView(LiveView):
    template = (
        f'<div dj-root dj-view="{_MODULE}.BadgeHostView" dj-id="0"><p>visible={{{{ v }}}}</p></div>'
    )

    def mount(self, request, **kwargs):
        self.badge = BadgeComponent(component_id="b1", text="tag", dismissible=True)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["v"] = "yes" if self.badge.visible else "no"
        return ctx


async def _dismiss_over_ws(view_name: str, component_id: str):
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
        communicator, mount_frame = await _connect_and_mount(f"{_MODULE}.{view_name}")
        assert "visible=yes" in mount_frame.get("html", ""), mount_frame
        await communicator.send_json_to(
            {
                "type": "event",
                "event": "dismiss",
                "params": {"component_id": component_id},
                "ref": 1,
            }
        )
        resp = await communicator.receive_json_from(timeout=3)
        await communicator.disconnect()
        return resp


@pytest.mark.django_db
@pytest.mark.asyncio
class TestDismissOverWebSocket:
    async def test_alert_close_button_dismisses(self):
        resp = await _dismiss_over_ws("AlertHostView", "a1")
        assert resp.get("type") != "error", f"#2756: alert 'dismiss' must be handled; got {resp!r}"
        assert "visible=no" in resp.get("html", ""), resp

    async def test_badge_close_button_dismisses(self):
        resp = await _dismiss_over_ws("BadgeHostView", "b1")
        assert resp.get("type") != "error", f"#2756: badge 'dismiss' must be handled; got {resp!r}"
        assert "visible=no" in resp.get("html", ""), resp

    async def test_unknown_event_still_errors(self):
        """Contrast (#1468): an event the component does NOT define is rejected."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
            communicator, _ = await _connect_and_mount(f"{_MODULE}.AlertHostView")
            await communicator.send_json_to(
                {"type": "event", "event": "not_a_handler", "params": {"component_id": "a1"}}
            )
            resp = await communicator.receive_json_from(timeout=3)
            await communicator.disconnect()
        assert resp.get("type") == "error", resp


# ---------------------------------------------------------------------------
# Package-wide structural pin
# ---------------------------------------------------------------------------

# Kwargs that make each component's interactive controls render. Anything not
# listed is instantiated with defaults. This is CONFIG for the sweep, not the
# component list — the list is derived from the package below (#2727).
_RENDER_KWARGS: Dict[str, Dict[str, Any]] = {
    "ModalComponent": {"title": "T", "body": "B", "show": True},
    "AlertComponent": {"message": "M", "dismissible": True},
    "BadgeComponent": {"text": "B", "dismissible": True},
}

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TAG_WITH_CLICK = re.compile(r"<[a-zA-Z][^>]*\bdj-click=\"([^\"]*)\"[^>]*>")


def _ui_live_components() -> List[type]:
    """Every LiveComponent subclass defined in a ``djust.components.ui`` module."""
    import djust.components.ui as ui_pkg

    found: Dict[str, type] = {}
    for info in pkgutil.iter_modules(ui_pkg.__path__):
        mod = importlib.import_module(f"{ui_pkg.__name__}.{info.name}")
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(cls, LiveComponent)
                and cls is not LiveComponent
                and cls.__module__ == mod.__name__
            ):
                found[cls.__name__] = cls
    return [found[k] for k in sorted(found)]


def _render_under(component: LiveComponent, framework: str) -> str:
    with patch.object(config_module.config, "get", lambda key, default=None: framework):
        return str(component.render())


def _self_targeting_controls() -> List[Tuple[type, str, str, str]]:
    """``(cls, framework, event, tag_html)`` for every rendered ``dj-click`` whose
    target is a bare identifier (a handler name, not a client expression)."""
    rows = []
    for cls in _ui_live_components():
        for framework in _FRAMEWORKS:
            comp = cls(component_id="cid", **_RENDER_KWARGS.get(cls.__name__, {}))
            html = _render_under(comp, framework)
            for m in _TAG_WITH_CLICK.finditer(html):
                event = m.group(1)
                # With no parent-handler kwargs supplied, every identifier target
                # is the component's own; an expression (``open = !open``) is
                # client-side and out of scope.
                if _IDENT.match(event):
                    rows.append((cls, framework, event, m.group(0)))
    return rows


class TestEveryUiComponentSelfTargetIsADecoratedRoutedHandler:
    def test_sweep_discovers_the_package(self):
        classes = _ui_live_components()
        assert len(classes) >= 3, classes
        controls = _self_targeting_controls()
        assert controls, "the sweep must find at least one self-targeting dj-click"
        # Non-vacuous: every framework branch contributed at least one control.
        assert {fw for _, fw, _, _ in controls} == set(_FRAMEWORKS)

    @pytest.mark.parametrize(
        "cls,framework,event,tag",
        _self_targeting_controls(),
        ids=lambda v: v.__name__ if isinstance(v, type) else (v if len(v) < 24 else "tag"),
    )
    def test_target_is_decorated_and_routed(self, cls, framework, event, tag):
        handler = getattr(cls, event, None)
        assert callable(handler), (
            f"{cls.__name__} [{framework}]: dj-click={event!r} names NO method on the "
            "component — the original #2748 shape (No handler found for event)"
        )
        assert is_event_handler(handler), (
            f"{cls.__name__} [{framework}]: dj-click={event!r} names an UNDECORATED method; "
            "event_security defaults to strict, so the click is rejected"
        )
        assert 'data-component-id="cid"' in tag, (
            f"{cls.__name__} [{framework}]: dj-click={event!r} targets the component but the "
            f"element carries no data-component-id, so it routes to the parent view: {tag}"
        )
