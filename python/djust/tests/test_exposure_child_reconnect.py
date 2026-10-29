"""Explicit page-shell child reconstruction on WebSocket reconnect (ADR-038 E3-6).

The page shell (markup outside ``dj-root``) stays in the browser across a
reconnect, but the server builds a new root instance. The explicit shell child
must be reconstructed on the new connection with its stored state, and events
addressed to it must route afterwards.
"""

import copy

import django
import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, event_handler
from djust._exposure_children import child_state_key
from djust.decorators import state

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MODULE = __name__
PATH = "/shell-reconnect/"
WS_SETTINGS = dict(
    LIVEVIEW_ALLOWED_MODULES=[MODULE], DEBUG=False, DJUST_CONFIG={}, DJUST_TENANTS={}
)


class ShellWidget(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "shell"
    count = state(1, persist="server")
    secret = state("SHELL_SECRET_SENTINEL", persist="server")
    template = '<div>Shell={{ count }}<button dj-click="increment">+</button></div>'

    def mount(self, request, **kwargs):
        self.count = 1

    @event_handler()
    def increment(self):
        self.count += 1

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class ShellPage(LiveView):
    exposure_policy = "explicit"
    template_name = "e3_6_shell_page.html"
    n = state(0, persist="server")

    @event_handler()
    def bump(self):
        self.n += 1

    def get_context_data(self, **kwargs):
        return super().get_context_data(n=self.n, **kwargs)


class LegacyShellWidget(LiveView):
    sticky = True
    sticky_id = "legacy-shell"
    template = '<div><button dj-click="poke">x</button></div>'

    @event_handler()
    def poke(self):
        pass


class LegacyShellPage(LiveView):
    template_name = "e3_6_legacy_page.html"


@pytest.fixture(autouse=True)
async def close_reconnect_worker_connections(transactional_db):
    """Release connections opened by reconnect session creation on the async worker."""
    from asgiref.sync import sync_to_async
    from django.db import connections
    from django.db.backends.base.base import BaseDatabaseWrapper

    def snapshot():
        return {
            id(conn): conn.connection is not None for conn in connections.all(initialized_only=True)
        }

    def close_new_connections(before):
        for conn in connections.all(initialized_only=True):
            if conn.connection is None or before.get(id(conn), False):
                continue
            if conn.vendor == "sqlite" and conn.is_in_memory_db():
                BaseDatabaseWrapper.close(conn)
            else:
                conn.close()

    before = await sync_to_async(snapshot)()
    yield
    await sync_to_async(close_new_connections)(before)


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings, tmp_path):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [MODULE]
    templates = copy.deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"] = [str(tmp_path), *templates[0].get("DIRS", [])]
    settings.TEMPLATES = templates
    (tmp_path / "e3_6_base.html").write_text(
        "{% load live_tags %}<html><body>"
        f'{{% live_render "{MODULE}.ShellWidget" sticky=True %}}'
        "{% block main %}{% endblock %}</body></html>"
    )
    (tmp_path / "e3_6_shell_page.html").write_text(
        '{% extends "e3_6_base.html" %}{% block main %}'
        "<div dj-root>Page={{ n }}</div>{% endblock %}"
    )
    (tmp_path / "e3_6_legacy_base.html").write_text(
        "{% load live_tags %}<html><body>"
        f'{{% live_render "{MODULE}.LegacyShellWidget" sticky=True %}}'
        "{% block main %}{% endblock %}</body></html>"
    )
    (tmp_path / "e3_6_legacy_page.html").write_text(
        '{% extends "e3_6_legacy_base.html" %}{% block main %}'
        "<div dj-root>Legacy</div>{% endblock %}"
    )


def new_session():
    session = SessionStore()
    session.create()
    return session.session_key


async def connect(session_key, view):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(session_key)
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from(timeout=5)
    await comm.send_json_to({"type": "mount", "view": f"{MODULE}.{view}", "url": PATH})
    frame = await comm.receive_json_from(timeout=5)
    assert frame["type"] == "mount", frame
    return comm


async def event_reply(comm, event, **params):
    await comm.send_json_to({"type": "event", "event": event, "params": params})
    while True:
        frame = await comm.receive_json_from(timeout=5)
        if frame.get("type") in {"embedded_update", "error", "patch", "html_update", "noop"}:
            return frame


async def stored_shell(session_key):
    stored = await sync_to_async(SessionStore(session_key).load)()
    return stored.get(child_state_key(PATH, ("shell",)), {}).get("state", {}).get("values")


async def test_shell_child_is_reconstructed_with_state_and_routes_after_reconnect():
    session_key = await sync_to_async(new_session)()
    with override_settings(**WS_SETTINGS):
        comm = await connect(session_key, "ShellPage")
        try:
            reply = await event_reply(comm, "increment", view_id="shell")
            assert reply["type"] == "embedded_update", reply
            assert reply["view_id"] == "shell" and "Shell=2" in reply["html"]
            # A parent turn must not prune the shell child's stored state.
            reply = await event_reply(comm, "bump")
            assert reply["type"] in {"patch", "html_update"}, reply
        finally:
            await comm.disconnect()
        assert (await stored_shell(session_key))["count"] == 2

        comm = await connect(session_key, "ShellPage")
        try:
            reply = await event_reply(comm, "increment", view_id="shell")
            assert reply["type"] == "embedded_update", reply
            assert reply["view_id"] == "shell" and "Shell=3" in reply["html"]
            assert "SHELL_SECRET_SENTINEL" not in json.dumps(reply)
        finally:
            await comm.disconnect()
        assert (await stored_shell(session_key))["count"] == 3


async def test_legacy_shell_page_reconnect_behavior_is_unchanged():
    """Legacy control: a legacy page still mounts only its live root on reconnect."""
    session_key = await sync_to_async(new_session)()
    with override_settings(**WS_SETTINGS):
        comm = await connect(session_key, "LegacyShellPage")
        try:
            reply = await event_reply(comm, "poke", view_id="legacy-shell")
            assert reply["type"] == "error" and reply["error"] == "Embedded view not found"
        finally:
            await comm.disconnect()


class IgnoredWidget(ShellWidget):
    sticky_id = "ignored"
    count = state(1, persist="server")
    secret = state("SHELL_SECRET_SENTINEL", persist="server")


class IgnoredPage(ShellPage):
    n = state(0, persist="server")
    template_name = "ignored_reconnect_page.html"


@pytest.mark.skipif(django.VERSION < (6, 0), reason="Django 6 authenticated page-graph rendering")
async def test_cold_reconnect_owns_only_current_children_and_revokes_stale_routes(
    tmp_path, monkeypatch
):
    (tmp_path / "ignored_reconnect_page.html").write_text(
        '{% extends "e3_6_base.html" %}{% block main %}'
        '{% include "ignored_reconnect_root.html" %}{% endblock %}'
    )
    (tmp_path / "ignored_reconnect_root.html").write_text(
        '{% load live_tags %}<main dj-root><section id="widget" dj-update="ignore">'
        '{% if n == 0 %}{% live_render "' + MODULE + '.IgnoredWidget" '
        "sticky=True %}{% endif %}</section><p>{{ n }}</p></main>"
    )
    from channels.testing import WebsocketCommunicator
    from djust.websocket import LiveViewConsumer

    from djust.runtime import ViewRuntime

    failures = []
    original_fail = ViewRuntime._fail_mount

    async def capture_failure(self, view, exc, response):
        failures.append(exc)
        await original_fail(self, view, exc, response)

    monkeypatch.setattr(ViewRuntime, "_fail_mount", capture_failure)

    async def mounted(key):
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope["session"] = SessionStore(key)
        socket.scope["user"] = AnonymousUser()
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=5)
        await socket.send_json_to(
            {
                "type": "mount",
                "view": MODULE + ".IgnoredPage",
                "url": PATH,
                "has_prerendered": True,
            }
        )
        frame = await socket.receive_json_from(timeout=5)
        if frame["type"] != "mount":
            await socket.disconnect()
        assert frame["type"] == "mount", (frame, failures)
        return socket, frame

    key = await sync_to_async(new_session)()
    with override_settings(**WS_SETTINGS):
        socket, frame = await mounted(key)
        try:
            assert [slot for slot, _ in frame["owned_children"]] == ["ignored"]
            assert "SHELL_SECRET_SENTINEL" not in json.dumps(frame)
            reply = await event_reply(socket, "bump")
            assert reply["type"] in {"patch", "html_update"}, reply
            # Warm ignore reuse preserves the current authenticated instance.
            reply = await event_reply(socket, "increment", view_id="ignored")
            assert reply["type"] == "embedded_update", reply
        finally:
            await socket.disconnect()
        socket, frame = await mounted(key)
        try:
            assert frame["owned_children"] == []
            assert 'data-djust-embedded="ignored"' not in frame["html"]
            reply = await event_reply(socket, "increment", view_id="ignored")
            assert reply["type"] == "error" and reply["error"] == "Embedded view not found"
        finally:
            await socket.disconnect()
