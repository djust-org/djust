"""Regression tests for object-permission enforcement on the HTTP-API + SSE-legacy paths.

The ADR-017 object-permission check (``enforce_object_permission`` →
``get_object`` + ``has_object_permission``) was enforced on the WS mount,
runtime/url_change, HTTP-GET, and ``{% live_render %}`` paths (#10/#11/#12),
but NOT on:

* the HTTP-API dispatch path (``api/dispatch.py:dispatch_api`` and the sibling
  ``dispatch_server_function``) — both instantiate + mount an object-scoped view
  and run ``check_view_auth`` / ``check_handler_permission`` but never the
  object-level check, so a denied object's handler ran and could leak/mutate
  the object (IDOR, finding #10/#11/#12 on the API transport); and
* the legacy SSE mount path (``sse.py:_sse_mount_view``) — 0 object-perm calls,
  so it rendered a denied object's initial HTML (IDOR on the SSE transport).

The shared ``enforce_object_permission`` chokepoint now covers both: the API
paths return **403 ``permission_denied``** and the SSE path pushes an **error
frame** + aborts the mount (return False), without running the handler /
rendering the denied object.

Mirrors ``test_object_perm_render_paths.py`` (the #10/#11/#12 fix on the
HTTP-GET / url_change / live_render paths). Reproduce-first + gate-off (#1468):
neutering the new ``enforce_object_permission`` call on either path makes the
matching ``denies_forbidden`` test pass-through (the handler runs / the object
renders), proving the assertion is non-tautological.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.auth.core import enforce_object_permission
from djust.decorators import event_handler, server_function

pytestmark = pytest.mark.django_db

DOCS = {1: "ACME private (yours)", 2: "GLOBEX private (NOT yours)"}


# --------------------------------------------------------------------------- #
# Shared object-scoped view: only doc 1 is permitted (mirrors _DocView).
# --------------------------------------------------------------------------- #


class _DocAPIView(LiveView):
    """Object-scoped view exposing an API handler + a server function.

    ``mount`` reads ``doc_id`` from kwargs so the access-determining state that
    ``get_object()`` reads exists by the time the object-perm check runs (the
    real path binds it pre-handler). The default ``doc_id = 2`` means the
    *denied* object is the one a forbidden caller mounts unless overridden.
    """

    template_name = None
    api_name = "objperm.doc"
    doc_id = 2  # default to the DENIED object so the gap is the default path

    def get_object(self):
        return type("Doc", (), {"id": self.doc_id})()

    def has_object_permission(self, request, obj):
        return obj.id == 1

    def mount(self, request, **kwargs):
        if "doc_id" in kwargs:
            self.doc_id = int(kwargs["doc_id"])

    def get_context_data(self, **kwargs):
        return {"doc": DOCS[self.doc_id]}

    def render_with_diff(self):
        return (f"<main>{DOCS[self.doc_id]}</main>", None, 1)

    # --- side-effecting handlers that MUST NOT run for a denied object ---

    @event_handler(expose_api=True)
    def read_doc(self, **kwargs):
        """Leaks the denied object's content if reached."""
        return DOCS[self.doc_id]

    @server_function
    def fetch_doc(self, **kwargs):
        return DOCS[self.doc_id]


class _PublicAPIView(LiveView):
    """No custom get_object → object-perm lifecycle inactive (must stay no-op)."""

    template_name = None
    api_name = "objperm.public"

    @event_handler(expose_api=True)
    def ping(self, **kwargs):
        return "pong"

    @server_function
    def call(self, **kwargs):
        return "pong"

    def get_context_data(self, **kwargs):
        return {}


# --------------------------------------------------------------------------- #
# HTTP-API: dispatch_api + dispatch_server_function
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _clean_api_state():
    from djust.api.dispatch import reset_rate_buckets
    from djust.api.registry import reset_registry

    reset_registry()
    reset_rate_buckets()
    yield
    reset_registry()
    reset_rate_buckets()


def _api_request(body=None):
    """Authenticated, CSRF-exempt POST with a real session (request.user works)."""
    rf = RequestFactory(enforce_csrf_checks=False)
    User = get_user_model()
    user = User.objects.create_user(username="alice", password="pw")
    body_bytes = json.dumps(body or {}).encode("utf-8")
    request = rf.post("/djust/api/x/y/", data=body_bytes, content_type="application/json")
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = user
    request._dont_enforce_csrf_checks = True
    return request


def test_api_dispatch_denies_forbidden_object():
    from djust.api.dispatch import dispatch_api
    from djust.api.registry import register_api_view

    register_api_view("objperm.doc", _DocAPIView)
    resp = dispatch_api(_api_request(), "objperm.doc", "read_doc")

    assert resp.status_code == 403, (
        "API dispatch of a denied object must be 403, not run the handler"
    )
    payload = json.loads(resp.content)
    assert payload["error"] == "permission_denied"
    assert "GLOBEX" not in resp.content.decode("utf-8"), (
        "denied object content leaked in API response"
    )


def test_api_dispatch_allows_permitted_object():
    from djust.api.dispatch import dispatch_api
    from djust.api.registry import register_api_view

    # api_name carries doc_id=1 path via a subclass so mount permits it.
    Permitted = type("_DocAPIViewAllowed", (_DocAPIView,), {"api_name": "objperm.ok", "doc_id": 1})
    register_api_view("objperm.ok", Permitted)
    resp = dispatch_api(_api_request(), "objperm.ok", "read_doc")

    assert resp.status_code == 200, "permitted object wrongly denied on the API path"
    assert json.loads(resp.content)["result"] == DOCS[1]


def test_api_dispatch_noop_for_non_object_scoped_view():
    from djust.api.dispatch import dispatch_api
    from djust.api.registry import register_api_view

    register_api_view("objperm.public", _PublicAPIView)
    resp = dispatch_api(_api_request(), "objperm.public", "ping")

    assert resp.status_code == 200, "non-object-scoped API view must be unaffected (pure no-op)"
    assert json.loads(resp.content)["result"] == "pong"


def test_server_function_denies_forbidden_object():
    from djust.api.dispatch import dispatch_server_function
    from djust.api.registry import register_api_view

    register_api_view("objperm.doc", _DocAPIView)
    resp = dispatch_server_function(_api_request({"params": {}}), "objperm.doc", "fetch_doc")

    assert resp.status_code == 403, "server_function of a denied object must be 403, not run the fn"
    assert json.loads(resp.content)["error"] == "permission_denied"
    assert "GLOBEX" not in resp.content.decode("utf-8"), (
        "denied object content leaked in RPC response"
    )


def test_server_function_allows_permitted_object():
    from djust.api.dispatch import dispatch_server_function
    from djust.api.registry import register_api_view

    Permitted = type(
        "_DocAPIViewAllowed2", (_DocAPIView,), {"api_name": "objperm.ok2", "doc_id": 1}
    )
    register_api_view("objperm.ok2", Permitted)
    resp = dispatch_server_function(_api_request({"params": {}}), "objperm.ok2", "fetch_doc")

    assert resp.status_code == 200, "permitted object wrongly denied on the server_function path"
    assert json.loads(resp.content)["result"] == DOCS[1]


def test_server_function_noop_for_non_object_scoped_view():
    from djust.api.dispatch import dispatch_server_function
    from djust.api.registry import register_api_view

    register_api_view("objperm.public", _PublicAPIView)
    resp = dispatch_server_function(_api_request({"params": {}}), "objperm.public", "call")

    assert resp.status_code == 200, "non-object-scoped server_function must be unaffected (no-op)"
    assert json.loads(resp.content)["result"] == "pong"


# --------------------------------------------------------------------------- #
# Legacy SSE: _sse_mount_view
# --------------------------------------------------------------------------- #


# Object-scoped SSE view. Resolves doc_id from URL kwargs (pk-style) the way
# the SSE mount path supplies mount_kwargs from the resolved URL match. Uses a
# real inline ``template`` so the REAL SSE render path runs (#1650 fidelity:
# the SSE path calls _initialize_rust_view → get_template, unlike the WS/runtime
# render-path test which overrides render_with_diff). The object-perm check must
# fire BEFORE this render, so a denied object never reaches the renderer.
class _DocSSEView(LiveView):
    template = "<div dj-root><main>{{ doc }}</main></div>"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.doc_id = 2  # default to the DENIED object

    def get_object(self):
        return type("Doc", (), {"id": self.doc_id})()

    def has_object_permission(self, request, obj):
        return obj.id == 1

    def mount(self, request, **kwargs):
        if "doc_id" in kwargs:
            self.doc_id = int(kwargs["doc_id"])

    def get_context_data(self, **kwargs):
        return {"doc": DOCS[self.doc_id]}


class _PublicSSEView(LiveView):
    template = "<div dj-root>public</div>"

    def get_context_data(self, **kwargs):
        return {}


def _sse_session():
    from djust.sse import SSESession

    session = SSESession("sse-objperm")
    session._owner_user_pk = 7
    return session


def _sse_request(doc_id=None):
    rf = RequestFactory()
    query = f"?view=x&doc_id={doc_id}" if doc_id is not None else "?view=x"
    request = rf.get(f"/djust/sse/sse-objperm/{query}")
    request.user = AnonymousUser()
    return request


def _drain(session):
    msgs = []
    while not session.queue.empty():
        msgs.append(session.queue.get_nowait())
    return msgs


def _allowlist():
    """Permissive allowlist so the test view path resolves through the F22 gate."""
    return override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__.split(".")[0]])


# Post-#1887 (ADR-022 Iter 1) the SSE mount converged onto
# ``session.runtime.dispatch_mount`` (deleting the legacy ``_sse_mount_view``).
# The object-permission check still fires post-mount + pre-render via the SAME
# shared ``enforce_object_permission`` chokepoint inside ``dispatch_mount``
# (Iter 0 / #1885). The 'mounted' verdict is now ``runtime.view_instance is not
# None`` (it replaced the legacy bool return). These tests drive the converged
# path with the real request stashed on the session (``session._request``, as
# the GET stream does) so the runtime mounts against the real request.user.
def _run_sse_mount(session, request, view_path):
    session._request = request
    # Mirror DjustSSEStreamView.get: 'params' carries the query-string items the
    # GET stream merges into mount_kwargs (every GET item except the 'view'
    # selector), so URL params like doc_id reach mount() exactly as before.
    params = {k: v for k, v in request.GET.items() if k != "view"}
    asyncio.run(
        session.runtime.dispatch_mount(
            {"type": "mount", "view": view_path, "url": request.path, "params": params}
        )
    )
    return session.runtime.view_instance is not None


def test_sse_mount_denies_forbidden_object():
    sys.modules[__name__]._DocSSEView = _DocSSEView  # type: ignore[attr-defined]
    session = _sse_session()
    path = f"{__name__}._DocSSEView"

    with _allowlist():
        mounted = _run_sse_mount(session, _sse_request(doc_id=2), path)

    msgs = _drain(session)
    assert mounted is False, "SSE mount of a denied object must abort (no view_instance)"
    assert any(m.get("type") == "error" for m in msgs), "denied SSE mount must push an error frame"
    assert not any(m.get("type") == "mount" for m in msgs), (
        "denied object must NOT be mounted/rendered"
    )
    assert not any("GLOBEX" in str(m) for m in msgs), "denied object content leaked over SSE"


def test_sse_mount_allows_permitted_object():
    sys.modules[__name__]._DocSSEView = _DocSSEView  # type: ignore[attr-defined]
    session = _sse_session()
    path = f"{__name__}._DocSSEView"

    with _allowlist():
        mounted = _run_sse_mount(session, _sse_request(doc_id=1), path)

    msgs = _drain(session)
    assert mounted is True, "permitted object wrongly denied on the SSE path"
    assert any(m.get("type") == "mount" for m in msgs), "permitted object must mount"
    assert any("ACME" in str(m) for m in msgs), "permitted object should have rendered"


def test_sse_mount_noop_for_non_object_scoped_view():
    sys.modules[__name__]._PublicSSEView = _PublicSSEView  # type: ignore[attr-defined]
    session = _sse_session()
    path = f"{__name__}._PublicSSEView"

    with _allowlist():
        mounted = _run_sse_mount(session, _sse_request(), path)

    msgs = _drain(session)
    assert mounted is True, "non-object-scoped SSE view must be unaffected (pure no-op)"
    assert any(m.get("type") == "mount" for m in msgs), "public view must still mount"


# --------------------------------------------------------------------------- #
# Helper-level sanity (mirrors test_object_perm_render_paths.py)
# --------------------------------------------------------------------------- #


def test_helper_denies_forbidden_object_for_api_view():
    v = _DocAPIView()
    v.doc_id = 2
    req = RequestFactory().get("/")
    req.user = AnonymousUser()
    with pytest.raises(PermissionDenied):
        enforce_object_permission(v, req)
