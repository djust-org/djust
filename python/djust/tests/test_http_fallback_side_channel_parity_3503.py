"""#3503: what the HTTP fallback drains, against what the socket flushes.

The issue asked whether the page-POST fallback silently loses ``push_event``,
deferred work, layout, accessibility or i18n effects that the WebSocket and SSE
paths flush. Reproduced first, with a handler calling ``push_event`` over the
stateless fallback: it is carried (since #3541; see
``test_http_side_channels_push_layout_defer.py`` for one case per frame). What
this file pins is the part nothing pinned: that the answer carries **every**
queue a view can fill, in one turn, and that a queue added to the socket's
flush cannot be forgotten here.

The parity checks derive their sets from the code (the view's own ``_drain_*``
methods, the names the socket's flush calls) rather than restating a count.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.mixins import layout as layout_mixin

STATELESS = override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False})
PATH = "/channels/"
PACKAGE = pathlib.Path(__file__).resolve().parents[1]

RAN: list = []


class EverythingView(LiveView):
    template = "<div dj-root dj-id='0'><p>{{ message }}</p></div>"

    def mount(self, request, **kwargs):
        self.message = "start"

    @event_handler()
    def all_at_once(self, **kwargs):
        self.message = "done"
        self.push_event("chart:update", {"points": [1, 2]})
        self.put_flash("info", "saved")
        self.page_title = "Saved"
        self.set_layout("layouts/fullscreen.html")
        self.defer(RAN.append, "deferred")
        self.live_patch(params={"tab": "two"})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["message"] = self.message
        return ctx


setattr(sys.modules[__name__], "EverythingView", EverythingView)


@pytest.mark.django_db
class TestOneTurnCarriesEveryQueue:
    def test_the_stateless_fallback_answers_with_every_side_effect(self, monkeypatch):
        monkeypatch.setattr(layout_mixin, "render_pending_layout", lambda view, path: "<main/>")
        RAN.clear()
        with STATELESS:
            initial = RequestFactory().get(PATH)
            initial.user, initial.tenant = AnonymousUser(), None
            initial.session = SessionStore()
            initial.session.create()
            EverythingView().get(initial)

            request = RequestFactory().post(
                PATH,
                data=json.dumps({}),
                content_type="application/json",
                HTTP_X_DJUST_EVENT="all_at_once",
            )
            request.user, request.session, request.tenant = initial.user, initial.session, None
            response = EverythingView().post(request)

        assert response.status_code == 200, response.content
        data = json.loads(response.content)
        assert data["_push_events"] == [{"event": "chart:update", "payload": {"points": [1, 2]}}]
        assert [f["message"] for f in data["_flash"]] == ["saved"]
        assert data["_page_metadata"]
        assert data["_layout"] == {"path": "layouts/fullscreen.html", "html": "<main/>"}
        [navigation] = data["_navigation"]
        assert navigation["action"] == "live_patch" and navigation["params"] == {"tab": "two"}
        assert RAN == ["deferred"], "defer() callbacks run once the answer is built"


def _attribute_names(node: ast.AST) -> set:
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def _function(path: pathlib.Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError("%s not found in %s" % (name, path.name))


def _drain_refs(path: pathlib.Path, func_names: set) -> set:
    """``_drain_*`` attribute and string references inside the named functions."""
    tree = ast.parse(path.read_text())
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in func_names:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Attribute) and inner.attr.startswith("_drain_"):
                    names.add(inner.attr)
                elif (
                    isinstance(inner, ast.Constant)
                    and isinstance(inner.value, str)
                    and inner.value.startswith("_drain_")
                ):
                    names.add(inner.value)
    return names


def _drains_in(path: pathlib.Path, func_prefix: str) -> set:
    """``_drain_*`` names referenced by every function in ``path`` named ``func_prefix*``."""
    tree = ast.parse(path.read_text())
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            func_prefix
        ):
            names |= {a for a in _attribute_names(node) if a.startswith("_drain_")}
    return names


class TestParityWithTheSocketFlush:
    def test_every_queue_a_view_defines_is_drained_on_the_fallback(self):
        """A new ``_drain_*`` queue on the view must reach the HTTP answer."""
        defined = {n for n in dir(LiveView) if n.startswith("_drain_")}
        assert defined, "LiveView defines no _drain_* queue: the derivation is broken"
        # Inside the functions that build the answer, not anywhere in the file:
        # the TYPE_CHECKING stubs in request.py name every queue too.
        drained = _drain_refs(
            PACKAGE / "mixins" / "request.py",
            {"_drain_http_side_channels", "_run_http_deferred", "_post_turn_body"},
        )
        missing = sorted(defined - drained)
        assert not missing, (
            "queues a view can fill that the HTTP fallback never drains "
            "(mixins/request.py): %s" % missing
        )

    def test_every_socket_queue_has_a_framework_producer(self):
        """Socket and fallback must cover the same framework-produced queues.

        The old duck-typed accessibility drains were removed in 2.0, so the
        socket no longer has two extra names. A new drain on either the view or
        socket must still be accounted for by both parity checks.
        """
        socket_flushed = _drains_in(PACKAGE / "websocket.py", "_flush_")
        defined = {n for n in dir(LiveView) if n.startswith("_drain_")}
        assert socket_flushed, "the socket flushes no _drain_* queue: the derivation is broken"
        assert socket_flushed == defined, "socket-only drains: %s; view-only drains: %s" % (
            sorted(socket_flushed - defined),
            sorted(defined - socket_flushed),
        )
