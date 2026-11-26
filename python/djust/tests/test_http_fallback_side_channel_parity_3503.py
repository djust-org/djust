"""#3503: what the HTTP fallback drains, against what the socket flushes.

The issue asked whether the page-POST fallback silently loses ``push_event``,
deferred work, layout, accessibility or i18n effects that the WebSocket and SSE
paths flush. Reproduced first, with a handler calling ``push_event`` over the
stateless fallback: it is carried (since #3541; see
``test_http_side_channels_push_layout_defer.py`` for one case per frame). What
this file pins is the part nothing pinned: that the answer carries **every**
queue a view can fill, in one turn, and that a queue added to the socket's
flush cannot be forgotten here.

Since #3658 every transport takes the effects off the one outbound queue with
the same drain (``djust._render_frame``), and the HTTP answer is the same
``render`` frame as the socket's. The parity checks derive their sets from the
code (the kinds a view can queue, the drain every wire calls) rather than
restating a count.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler
from djust.mixins import layout as layout_mixin
from djust import state

PATH = "/channels/"
PACKAGE = pathlib.Path(__file__).resolve().parents[1]

RAN: list = []


class EverythingView(LiveView):
    exposure_policy = "explicit"
    message = state("start")
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
        assert data["type"] == "render"
        assert data["effects"] == [
            {"kind": "push_event", "event": "chart:update", "payload": {"points": [1, 2]}},
            {"kind": "flash", "action": "put", "level": "info", "message": "saved"},
            {"kind": "page_metadata", "action": "title", "value": "Saved"},
            {"kind": "layout", "path": "layouts/fullscreen.html", "html": "<main/>"},
            {
                "kind": "navigation",
                "action": "live_patch",
                "replace": False,
                "params": {"tab": "two"},
            },
        ]
        assert RAN == ["deferred"], "defer() callbacks run once the answer is built"


def _attribute_names(node: ast.AST) -> set:
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def _function(path: pathlib.Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError("%s not found in %s" % (name, path.name))


class TestParityWithTheSocketFlush:
    def test_every_kind_a_view_can_queue_has_a_rank(self):
        """A new effect kind must have its place in the drain's order."""
        from djust.mixins.commands import EFFECT_RANKS, QUEUED_KINDS

        assert QUEUED_KINDS, "no queued kinds: the derivation is broken"
        assert QUEUED_KINDS <= set(EFFECT_RANKS)

    def test_the_fallback_and_the_socket_drain_the_same_way(self):
        """Both answers take their effects with the one drain (``_render_frame``)."""
        http = _function(PACKAGE / "mixins" / "request.py", "_drain_http_side_channels")
        assert {"drain_effects", "_run_http_deferred"} <= _names(http)
        for path in (PACKAGE / "websocket.py", PACKAGE / "runtime.py"):
            send = _function(path, "_send_render")
            finish = _function(path, "_finish_turn_effects")
            assert "adrain_effects" in _names(send), path.name
            assert "_flush_deferred" in _attribute_names(finish), path.name
        # Nothing but the drain reads the one queue in the transports.
        for path in (
            PACKAGE / "websocket.py",
            PACKAGE / "runtime.py",
            PACKAGE / "mixins" / "request.py",
        ):
            assert "_drain_outbound" not in _attribute_names(ast.parse(path.read_text())), path.name


def _names(node: ast.AST) -> set:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | _attribute_names(node)
