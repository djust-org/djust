"""Regression test for #1784 — embedded ``{% live_render ... sticky=True %}``
must server-render on the initial HTTP GET (status 200, child present).

Before the fix, the page shell — including the ``{% live_render %}`` tag — was
rendered through the Rust engine with a JSON-serialized context that cannot
carry the live parent ``LiveView`` object. The ``live_render`` tag looked the
parent up via ``context.get("view") or context.get("self")`` (both absent in a
JSON context) and raised::

    TemplateSyntaxError: {% live_render %} must be called inside a LiveView
    template; no parent view in the current render context

which surfaced to the client as HTTP 500, making sticky / app-shell pages
impossible to server-render.

The fix registers the active parent view in a thread-local
(``set_active_parent_view`` in ``djust.templatetags.live_tags``) for the
duration of ``render_full_template`` and clears it in a ``finally``; the
``live_render`` tag falls back to that thread-local when the render context has
no ``view``/``self``.

These tests drive the REAL initial-GET path (``LiveView.as_view()`` → ``get``
→ ``render_full_template`` → Rust shell render) — not the in-process
``Context({"view": parent})`` shortcut the existing
``test_sticky_redirect_flow`` integration tests use, which bypasses the
server-render path entirely (the gap that let this bug ship unexercised).
"""

from __future__ import annotations

import pytest

from djust.live_view import LiveView


# ---------------------------------------------------------------------------
# Module-scope views — resolvable by dotted path for import_string().
# ---------------------------------------------------------------------------


class _HttpGetWidgetView(LiveView):
    """Sticky child embedded by the home page below."""

    sticky = True
    sticky_id = "http-get-widget"
    template = '<div dj-root><span class="widget-marker">widget value {{ wval }}</span></div>'

    def mount(self, request, **kwargs):
        self.wval = 99


class _HttpGetHomeView(LiveView):
    """App-shell home page that embeds the sticky child inline.

    Uses an inline ``template`` (no ``{% extends %}``) so the render takes the
    non-template-inheritance branch of ``render_full_template``; the
    template-inheritance branch is covered by the wired-in ``sticky_demo``
    dashboard page (see ``test_sticky_demo_dashboard_http_get_200`` below).
    """

    template = (
        "{% load live_tags %}"
        "<!DOCTYPE html><html><head><title>Home</title></head><body>"
        '<div dj-view dj-root><h1 class="home-marker">Home</h1>'
        '{% live_render "tests.integration.test_sticky_http_get_1784._HttpGetWidgetView" sticky=True %}'
        "</div></body></html>"
    )

    def mount(self, request, **kwargs):
        self.greeting = "hello"


@pytest.mark.django_db
def test_sticky_live_render_http_get_returns_200(rf):
    """Embedded ``sticky=True`` ``{% live_render %}`` renders on initial GET."""
    request = rf.get("/home/")
    _attach_session(request)

    response = _HttpGetHomeView.as_view()(request)

    assert response.status_code == 200, (
        "Initial HTTP GET of a page embedding "
        "{% live_render ... sticky=True %} must return 200, not 500 (#1784)."
    )


@pytest.mark.django_db
def test_sticky_live_render_http_get_includes_child_html(rf):
    """The embedded sticky child's rendered HTML is present in the shell."""
    request = rf.get("/home/")
    _attach_session(request)

    response = _HttpGetHomeView.as_view()(request)
    body = response.content.decode("utf-8")

    assert "home-marker" in body, "Parent shell content must be present."
    assert "widget value 99" in body, (
        "The embedded sticky child must be server-rendered into the shell "
        "HTML on the initial GET (#1784)."
    )


@pytest.mark.django_db
def test_sticky_demo_dashboard_http_get_200(client):
    """End-to-end: the wired-in sticky_demo dashboard page (which uses
    ``{% extends %}`` template inheritance) GETs 200 with both stickies
    server-rendered. This exercises the template-inheritance branch of
    ``render_full_template`` and the real URL routing (#1784)."""
    response = client.get("/sticky_demo/dashboard/")

    assert response.status_code == 200, (
        "sticky_demo dashboard (embeds two sticky {% live_render %} widgets "
        "via template inheritance) must GET 200, not 500 (#1784)."
    )
    body = response.content.decode("utf-8")
    # Both stickies declare their own template markup; the audio player's
    # track title and the notification center's seeded notifications are the
    # load-bearing "child rendered into the shell" signal.
    assert "Demo Track" in body, "Audio-player sticky must render into shell."
    assert "Welcome to the sticky demo" in body, (
        "Notification-center sticky must render into shell."
    )


def _attach_session(request):
    """Attach a DB-backed session to a RequestFactory request so the GET
    path's session writes work under pytest-django."""
    from importlib import import_module

    from django.conf import settings

    engine = import_module(settings.SESSION_ENGINE)
    request.session = engine.SessionStore()
    request.session.save()
