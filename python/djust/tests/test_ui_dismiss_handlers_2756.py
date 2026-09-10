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

Extended by #2776: ``TableComponent`` (``djust.components.data``) had the same
defect and the ``ui``-only walk could not see it. The pin now derives every
HTML-rendering ``LiveComponent`` subclass under ``djust.components`` (every
subpackage, so a new one is covered by default), covers call-form targets
(``toggle(3)``) and ``dj-change`` / ``dj-input`` as well as ``dj-click``, and
runs the params the control would send through the real
``validate_handler_params`` so a ``data-column`` → ``column_key`` name mismatch
is caught too.
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
from djust.validation import validate_handler_params
from djust.websocket_utils import get_handler_coerce_setting

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
class _FakeRow:
    """A ``pk``-bearing object so the ``forms`` selects render options without
    a database (``get_options`` slices + iterates the queryset)."""

    def __init__(self, pk: int) -> None:
        self.pk = pk

    def __str__(self) -> str:
        return f"row{self.pk}"


_RENDER_KWARGS: Dict[str, Dict[str, Any]] = {
    "ModalComponent": {"title": "T", "body": "B", "show": True},
    "AlertComponent": {"message": "M", "dismissible": True},
    "BadgeComponent": {"text": "B", "dismissible": True},
    "TableComponent": {
        "columns": [{"key": "id", "label": "ID", "sortable": True}, {"key": "n", "label": "N"}],
        "rows": [{"id": 1, "n": "a"}],
    },
    "PaginationComponent": {"current_page": 2, "total_pages": 5},
    "TabsComponent": {"tabs": [{"id": "one", "label": "One"}, {"id": "two", "label": "Two"}]},
    "NavbarComponent": {"items": []},
    "ForeignKeySelect": {"name": "fk", "queryset": [_FakeRow(1)], "searchable": True},
    "ManyToManySelect": {
        "name": "m2m",
        "queryset": [_FakeRow(1), _FakeRow(2)],
        "render_as": "checkboxes",
        "searchable": True,
    },
}

# Subpackages that are not component catalogs (test suites, the gallery site,
# management commands, the ttyd bridge). Everything else under
# ``djust.components`` is walked, so a NEW subpackage is covered by default.
_NOT_A_CATALOG = ("tests", "gallery", "management", "ttyd")

_EVENT_ATTRS = ("dj-click", "dj-change", "dj-input")
# A handler target: bare ``name`` or call-form ``name(args)``. Anything else
# (``open = !open``, ``x; open = false``) is a client expression, out of scope.
_TARGET = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\((.*)\))?$")
_TAG = re.compile(r"<[a-zA-Z][^>]*>")
_ATTR = re.compile(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)="([^"]*)"')


def _live_components() -> List[type]:
    """Every HTML-rendering LiveComponent subclass defined anywhere under
    ``djust.components`` (#2776: not just ``ui``)."""
    import djust.components as pkg

    found: Dict[str, type] = {}
    for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        head = info.name[len(pkg.__name__) + 1 :].split(".")[0]
        if head in _NOT_A_CATALOG:
            continue
        mod = importlib.import_module(info.name)
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(cls, LiveComponent)
                and cls is not LiveComponent
                and cls.__module__ == mod.__name__
                # Descriptor-based components (``descriptors/``) contribute
                # state via ``__get__`` and never render markup of their own.
                and cls.render is not LiveComponent.render
            ):
                found[f"{cls.__module__}.{cls.__name__}"] = cls
    return [found[k] for k in sorted(found)]


def _client_params(attrs: Dict[str, str]) -> Dict[str, Any]:
    """What ``extractTypedParams`` (08-event-parsing.js) would send for the
    element: every ``data-*`` (except the routing attribute) as a snake_case key."""
    params: Dict[str, Any] = {}
    for name, value in attrs.items():
        if name.startswith("data-") and name != "data-component-id":
            params[name[5:].replace("-", "_")] = value
    return params


def _render_under(component: LiveComponent, framework: str) -> str:
    with patch.object(config_module.config, "get", lambda key, default=None: framework):
        return str(component.render())


def _self_targeting_controls() -> List[Tuple[type, str, str, str, str]]:
    """``(cls, framework, attr, target, tag_html)`` for every rendered
    ``dj-click`` / ``dj-change`` / ``dj-input`` whose target is a handler name
    (bare or call-form), across every HTML-rendering component in the package."""
    rows = []
    for cls in _live_components():
        for framework in _FRAMEWORKS:
            comp = cls(component_id="cid", **_RENDER_KWARGS.get(cls.__name__, {}))
            html = _render_under(comp, framework)
            for m in _TAG.finditer(html):
                attrs = dict(_ATTR.findall(m.group(0)))
                for attr in _EVENT_ATTRS:
                    target = attrs.get(attr)
                    # With no parent-handler kwargs supplied, every handler-name
                    # target is the component's own; an expression is
                    # client-side and out of scope.
                    if target is not None and _TARGET.match(target):
                        rows.append((cls, framework, attr, target, m.group(0)))
    return rows


def _row_id(v: Any) -> str:
    if isinstance(v, type):
        return v.__name__
    return v if len(v) < 24 else "tag"


class TestEveryComponentSelfTargetIsADecoratedRoutedHandler:
    def test_sweep_discovers_the_package(self):
        classes = _live_components()
        assert len(classes) >= 3, classes
        # Non-vacuous (#1859): the walk reaches OUTSIDE ``ui`` — the #2776 gap.
        packages = {cls.__module__.split(".")[2] for cls in classes}
        assert {"ui", "data", "layout", "forms"} <= packages, packages
        controls = _self_targeting_controls()
        assert controls, "the sweep must find at least one self-targeting control"
        # Every framework branch and every attribute kind contributed a control.
        assert {fw for _, fw, _, _, _ in controls} == set(_FRAMEWORKS)
        assert {attr for _, _, attr, _, _ in controls} == set(_EVENT_ATTRS)
        # Both target shapes are represented (bare ``sort_by`` and ``toggle(1)``).
        assert {"(" in target for _, _, _, target, _ in controls} == {True, False}

    @pytest.mark.parametrize(
        "cls,framework,attr,target,tag", _self_targeting_controls(), ids=_row_id
    )
    def test_target_is_decorated_and_routed(self, cls, framework, attr, target, tag):
        event, args = _TARGET.match(target).groups()
        handler = getattr(cls, event, None)
        assert callable(handler), (
            f"{cls.__name__} [{framework}]: {attr}={target!r} names NO method on the "
            "component — the original #2748 shape (No handler found for event)"
        )
        assert is_event_handler(handler), (
            f"{cls.__name__} [{framework}]: {attr}={target!r} names an UNDECORATED method; "
            "event_security defaults to strict, so the event is rejected"
        )
        assert 'data-component-id="cid"' in tag, (
            f"{cls.__name__} [{framework}]: {attr}={target!r} targets the component but the "
            f"element carries no data-component-id, so it routes to the parent view: {tag}"
        )
        # The params the control sends must be accepted by the handler's
        # signature (#2776: ``data-column`` vs ``column_key``). Call-form args
        # are positional, as the client sends them (``_args``); a bare ``value``
        # argument on dj-change/dj-input is the element's value.
        params = _client_params(dict(_ATTR.findall(tag)))
        positional = [a.strip().strip("'\"") for a in args.split(",")] if args else None
        result = validate_handler_params(
            handler,
            params,
            event,
            coerce=get_handler_coerce_setting(handler),
            positional_args=positional,
        )
        assert result["valid"], (
            f"{cls.__name__} [{framework}]: {attr}={target!r} sends {params!r} / "
            f"{positional!r} but the handler rejects them: {result['error']}"
        )
