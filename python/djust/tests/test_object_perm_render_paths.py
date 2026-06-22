"""Regression tests for object-permission enforcement on all render paths.

The ADR-017 object-permission check (``check_object_permission`` via
``get_object`` + ``has_object_permission``) was enforced on the WS mount and
event paths but NOT on the initial HTTP GET render (#11), SPA ``url_change``
navigation (#10), or ``{% live_render %}`` embedded children (#12) — leaking
denied objects. A shared ``enforce_object_permission`` chokepoint now covers
all of them.
"""

import asyncio

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from djust import LiveView
from djust.auth.core import enforce_object_permission

DOCS = {1: "ACME private (yours)", 2: "GLOBEX private (NOT yours)"}


class _DocView(LiveView):
    """Object-scoped view (ADR-017): only doc 1 is permitted."""

    template_name = None
    doc_id = 1

    def get_object(self):
        return type("Doc", (), {"id": self.doc_id})()

    def has_object_permission(self, request, obj):
        return obj.id == 1

    def mount(self, request, **kwargs):
        self.doc_id = int(kwargs.get("doc_id", self.doc_id))

    def handle_params(self, params, uri):
        if "id" in params:
            self.doc_id = int(params["id"])

    def get_context_data(self, **kwargs):
        return {"doc": DOCS[self.doc_id]}

    def render_with_diff(self):
        return (f"<main>{DOCS[self.doc_id]}</main>", None, 1)


class _PublicView(LiveView):
    """No custom get_object → object-perm lifecycle inactive (must stay no-op)."""

    template_name = None

    def get_context_data(self, **kwargs):
        return {}


def _req():
    r = RequestFactory().get("/doc/")
    r.user = AnonymousUser()
    return r


# --- enforce_object_permission helper ---


def test_helper_denies_forbidden_object():
    v = _DocView()
    v.doc_id = 2
    with pytest.raises(PermissionDenied):
        enforce_object_permission(v, _req())


def test_helper_allows_permitted_object():
    v = _DocView()
    v.doc_id = 1
    assert enforce_object_permission(v, _req()) is None


def test_helper_noop_for_non_object_scoped_view():
    assert enforce_object_permission(_PublicView(), _req()) is None


def test_helper_fail_closed_when_request_is_none():
    v = _DocView()
    v.doc_id = 2
    with pytest.raises(PermissionDenied):
        enforce_object_permission(v, None)


# --- #10: url_change (ViewRuntime.dispatch_url_change) ---


class _FakeTransport:
    session_id = "s1"
    client_ip = "127.0.0.1"

    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)

    async def send_error(self, error, **kwargs):
        self.sent.append({"error": error, **kwargs})

    def next_client_version(self, html, rust_version):
        # Transport Protocol hook (#1858): pass through the Rust version for this fake.
        return rust_version


@pytest.mark.django_db
def test_url_change_denies_forbidden_object():
    from djust.runtime import ViewRuntime

    view = _DocView()
    view.doc_id = 1
    view.request = _req()  # set at mount on the live path

    rt = ViewRuntime(transport=_FakeTransport(), scope={})
    rt.view_instance = view
    asyncio.run(rt.dispatch_url_change({"params": {"id": "2"}, "uri": "/doc/?id=2"}))

    errors = [m for m in rt.transport.sent if "error" in m]
    rendered = [m for m in rt.transport.sent if "html" in m or "patches" in m]
    assert errors, "url_change to a denied object must send an error, not render"
    assert not any("GLOBEX" in str(m) for m in rendered), "denied object was rendered"


@pytest.mark.django_db
def test_url_change_allows_permitted_object():
    from djust.runtime import ViewRuntime

    view = _DocView()
    view.doc_id = 1
    view.request = _req()
    rt = ViewRuntime(transport=_FakeTransport(), scope={})
    rt.view_instance = view
    asyncio.run(rt.dispatch_url_change({"params": {"id": "1"}, "uri": "/doc/?id=1"}))
    assert not [m for m in rt.transport.sent if "error" in m], "permitted object wrongly denied"


# --- #11: initial HTTP GET render (RequestMixin.get) ---


@pytest.mark.django_db
def test_http_get_denies_forbidden_object():
    from django.http import HttpResponseForbidden

    view = _DocView()
    req = _req()
    view.setup(req)
    req.session = __import__(
        "django.contrib.sessions.backends.db", fromlist=["SessionStore"]
    ).SessionStore()
    resp = view.get(req, doc_id=2)
    assert isinstance(resp, HttpResponseForbidden) or getattr(resp, "status_code", None) == 403, (
        "HTTP GET of a denied object must return 403, not render it"
    )


@pytest.mark.django_db
def test_streaming_aget_denies_forbidden_object(monkeypatch):
    """The streaming aget path must PROPAGATE the 403 — not downgrade it to a
    default-200 StreamingHttpResponse (Stage-11 review of #155).

    A RequestFactory request is a WSGIRequest, so aget would take its WSGI
    fallback (which just returns get()'s 403) and never reach the streaming
    branch. Force the ASGI path so the test exercises the real streaming
    code path that the fix guards.
    """
    view = _DocView()
    view.streaming_render = True
    # Force the ASGI branch so aget runs the streaming pipeline, not the
    # WSGI fallback (otherwise this test is tautological).
    monkeypatch.setattr(view, "_is_asgi_context", lambda request=None: True)
    req = _req()
    view.setup(req)
    req.session = __import__(
        "django.contrib.sessions.backends.db", fromlist=["SessionStore"]
    ).SessionStore()
    resp = asyncio.run(view.aget(req, doc_id=2))
    assert getattr(resp, "status_code", None) == 403, (
        "aget downgraded the object-permission 403 to %r" % getattr(resp, "status_code", None)
    )


# --- #12: {% live_render %} embedded child ---


class _DocChild(_DocView):
    """Templated object-scoped child for the live_render path."""

    template_name = "_objperm_child.html"


@pytest.fixture
def _child_template():
    """Make a renderable template for the embedded child discoverable in the
    project's first configured template DIRS entry."""
    from pathlib import Path

    from django.conf import settings

    dirs = [d for t in settings.TEMPLATES for d in t.get("DIRS", [])]
    assert dirs, "no configured template DIRS to drop the child template into"
    tpl = Path(dirs[0]) / "_objperm_child.html"
    tpl.write_text("<div dj-root>{{ doc }}</div>")
    try:
        yield
    finally:
        tpl.unlink(missing_ok=True)


@pytest.mark.django_db
def test_live_render_denies_forbidden_child(_child_template):
    from django.template import TemplateSyntaxError

    from djust.templatetags.live_tags import live_render

    parent = _PublicView()
    req = _req()
    parent._live_request = req
    path = f"{_DocChild.__module__}.{_DocChild.__name__}"
    # Denied object (id=2) must NOT render — live_render fails closed.
    with pytest.raises((TemplateSyntaxError, PermissionError, PermissionDenied)):
        live_render({"view": parent, "request": req}, path, doc_id=2)
