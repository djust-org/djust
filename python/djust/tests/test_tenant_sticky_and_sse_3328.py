"""A sticky child's saved state, and a token echoed over SSE, are tenant-bound.

Two tenants (resolved from the Host), one session, one URL:

- a sticky child saved under tenant A is not restored when the parent renders
  under tenant B (HTTP: the sweep that saves it and the ``{% live_render %}``
  render that restores it are the real code);
- a snapshot token minted under tenant A and echoed on an SSE mount under
  tenant B is ignored (fresh mount), and still restores under tenant A.
"""

from __future__ import annotations

import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.template import Context, Template
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.tenants.mixin import TenantMixin

pytestmark = [pytest.mark.django_db, pytest.mark.tenants]

_URL = "/tenant-sticky/"
_MOD = __name__


class _StickyChild(LiveView):
    sticky = True
    sticky_id = "counter"
    enable_state_snapshot = True
    template = "<div>Counter: {{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {"view": self, "count": getattr(self, "count", 0)}


class _TenantParent(TenantMixin, LiveView):
    enable_state_snapshot = True
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        f'{{% live_render "{_MOD}._StickyChild" sticky=True %}}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.title = "Parent"

    @event_handler()
    def poke(self, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {"view": self, "title": getattr(self, "title", "Parent")}


class _TenantTokenView(TenantMixin, LiveView):
    enable_state_snapshot = True
    template = '<div dj-root dj-id="0">[{{ secret }}]</div>'

    def mount(self, request, **kwargs):
        self.secret = "fresh-" + self.tenant.id


@pytest.fixture(autouse=True)
def _settings():
    with override_settings(
        ALLOWED_HOSTS=[".example.com", "testserver"],
        DJUST_CONFIG={"TENANT_RESOLVER": "subdomain"},
        LIVEVIEW_ALLOWED_MODULES=[_MOD],
        DJUST_LIVE_RENDER_ALLOWED_MODULES=[_MOD],
    ):
        yield


def _new_session() -> SessionStore:
    store = SessionStore()
    store.create()
    return store


def _request(method: str, host: str, session: SessionStore):
    factory = RequestFactory()
    if method == "POST":
        request = factory.post(
            _URL,
            data=json.dumps({"event": "poke", "params": {}}),
            content_type="application/json",
            HTTP_HOST=host,
        )
    else:
        request = factory.get(_URL, HTTP_HOST=host)
    request.user = AnonymousUser()
    request.session = session
    return request


def _render_parent(host: str, session: SessionStore) -> "_TenantParent":
    """A fresh parent renders its template: ``{% live_render %}`` builds and
    restores the sticky child, as a reconnect or a GET render does."""
    request = _request("GET", host, session)
    parent = _TenantParent()
    parent.request = request
    parent._ensure_tenant(request)
    parent.mount(request)
    parent._djust_mount_request = request
    Template(parent.template).render(Context({"view": parent, "request": request}))
    return parent


class TestStickyChildIsTenantBound:
    def test_child_saved_under_tenant_a_is_not_restored_under_tenant_b(self):
        session = _new_session()
        # The HTTP fallback POST's sweep saves the sticky child under the
        # tenant's own key.
        post = _request("POST", "acme.example.com", session)
        assert _TenantParent().post(post).status_code == 200
        post.session.save()

        keys = [k for k in SessionStore(session.session_key).keys() if "__sticky__" in k]
        assert keys == ["liveview_tenant:acme:/tenant-sticky/__sticky__counter"], keys

        stored = SessionStore(session.session_key)
        stored[keys[0]] = {"count": 5}  # a non-default state, as after clicks
        stored.save()

        own = _render_parent("acme.example.com", SessionStore(session.session_key))
        assert own._get_all_child_views()["counter"].count == 5, "tenant A restores its own"

        other = _render_parent("globex.example.com", SessionStore(session.session_key))
        assert other._get_all_child_views()["counter"].count == 0, (
            "tenant B must mount the child fresh"
        )

    def test_unresolved_tenant_neither_saves_nor_restores_a_child(self):
        class _Optional(_TenantParent):
            tenant_required = False

        session = _new_session()
        session["liveview_/tenant-sticky/__sticky__counter"] = {"count": 9}
        session.save()
        request = _request("GET", "testserver", SessionStore(session.session_key))
        parent = _Optional()
        parent.request = request
        parent._ensure_tenant(request)
        parent.mount(request)
        parent._djust_mount_request = request
        Template(parent.template).render(Context({"view": parent, "request": request}))
        assert parent._get_all_child_views()["counter"].count == 0


async def _sse_mount(host: str, session: SessionStore, snapshot: str | None = None) -> dict:
    from djust.sse import SSESession

    sse = SSESession(str(uuid.uuid4()))
    sse._request = await sync_to_async(_request)("GET", host, session)
    data = {"type": "mount", "view": f"{_MOD}._TenantTokenView", "url": _URL}
    if snapshot is not None:
        data["state_snapshot"] = {"view_slug": f"{_MOD}._TenantTokenView", "state_json": snapshot}
    await sse.runtime.dispatch_mount(data)
    frames = []
    while not sse.queue.empty():
        frame = sse.queue.get_nowait()
        if frame is not None:
            frames.append(frame)
    return next(f for f in frames if f.get("type") == "mount")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_sse_token_from_tenant_a_is_ignored_under_tenant_b():
    from djust.security import sign_snapshot

    session = await sync_to_async(_new_session)()
    # The token the framework would mint under tenant A, holding a state that
    # a fresh mount cannot produce.
    token = sign_snapshot(
        json.dumps({"secret": "planted-in-acme"}),
        f"{_MOD}._TenantTokenView",
        session.session_key,
        "tenant:acme",
    )

    own = await _sse_mount("acme.example.com", session, snapshot=token)
    assert "[planted-in-acme]" in own["html"], own["html"]  # tenant A restores it

    other = await _sse_mount("globex.example.com", session, snapshot=token)
    assert "planted-in-acme" not in other["html"], other["html"]
    assert "[fresh-globex]" in other["html"], other["html"]
