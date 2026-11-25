"""Render-only context: rendered on every transport, saved by none.

Reported downstream (Point Guard, djust 1.3.0rc11):

- N-35: no public way to give a template a model that djust renders (through
  the JIT, by the template's paths) but never persists. The mechanism existed
  as a private ``djust.forms`` hook (``_djust_render_only_context``).
  ``get_render_only_context()`` is the public hook.
- N-31: the HTTP GET saved a QuerySet that a ``get_context_data()`` override
  put in the context as a list of dicts with EVERY column of each row.
- Phase 2 decision 185: the GET saved the whole context but its render-only
  keys, so content an override added (a chat's messages, a run's preview)
  landed in ``django_session``.

2.0 removed the legacy view's session save, so nothing of it is saved.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler

SECRET = "the-secret-content"


def _session_request(factory_method, path, **kwargs):
    request = factory_method(path, **kwargs)
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _session_text(session) -> str:
    return json.dumps({k: session[k] for k in session.keys()}, default=str)


def _get(view_class, path="/ro/"):
    view = view_class()
    request = _session_request(RequestFactory().get, path)
    response = view.get(request)
    assert response.status_code == 200
    return view, request, response.content.decode()


def _post(view_class, get_request, event, path="/ro/"):
    request = RequestFactory().post(
        path,
        data=json.dumps({"event": event, "params": {}}),
        content_type="application/json",
    )
    request.session = get_request.session
    response = view_class().post(request)
    assert response.status_code == 200, response.content
    return json.loads(response.content.decode())


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class OverrideAddsView(LiveView):
    """N-31 / decision 185: an override adds a QuerySet and content."""

    exposure_policy = "explicit"
    template = (
        '<div dj-root dj-view="tests.OverrideAddsView">'
        "{{ count }} {{ secret_note }}"
        "{% for u in people %}<i>{{ u.username }}</i>{% endfor %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.count = 1

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["people"] = User.objects.filter(username__startswith="n35-").order_by("username")
        context["secret_note"] = SECRET
        return context


class RenderOnlyView(LiveView):
    """N-35: the public hook."""

    exposure_policy = "explicit"
    template = (
        '<div dj-root dj-view="tests.RenderOnlyView">'
        "{{ count }} {{ owner.username }} {{ note }}"
        "{% for u in people %}<i>{{ u.username }}</i>{% endfor %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.count = 1

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    def get_render_only_context(self):
        return {
            "owner": User.objects.get(username="n35-ada"),
            "people": list(User.objects.filter(username__startswith="n35-").order_by("username")),
            "note": SECRET,
        }


@pytest.fixture
def people(db):
    User.objects.create(username="n35-ada", email="ada@example.com", first_name="Ada")
    User.objects.create(username="n35-grace", email="grace@example.com", first_name="Grace")


# ---------------------------------------------------------------------------
# N-31 / decision 185: what an override adds is not saved
# ---------------------------------------------------------------------------


def test_get_saves_no_key_an_override_added(people):
    view, request, html = _get(OverrideAddsView)

    # 2.0: a legacy view saves nothing of itself in the session.
    assert "liveview_/ro/" not in request.session
    assert "ada@example.com" not in _session_text(request.session)
    assert SECRET not in _session_text(request.session)
    # ...and the page still shows them.
    assert "<i>n35-ada</i>" in html and SECRET in html


def test_fallback_post_saves_no_key_an_override_added(people):
    _view, request, _html = _get(OverrideAddsView)

    answer = _post(OverrideAddsView, request, "bump")

    assert "liveview_/ro/" not in request.session
    assert SECRET not in _session_text(request.session)
    assert "ada@example.com" not in _session_text(request.session)
    assert answer


# ---------------------------------------------------------------------------
# N-35: get_render_only_context()
# ---------------------------------------------------------------------------


def test_get_and_post_never_save_render_only_values(people):
    _view, request, html = _get(RenderOnlyView)
    assert "<i>n35-ada</i><i>n35-grace</i>" in html and SECRET in html

    assert "liveview_/ro/" not in request.session

    _post(RenderOnlyView, request, "bump")

    assert "liveview_/ro/" not in request.session
    assert SECRET not in _session_text(request.session)
    assert "ada@example.com" not in _session_text(request.session)


def test_a_render_only_key_wins_over_an_attribute_and_is_not_saved(people):
    class Shadow(RenderOnlyView):
        def mount(self, request, **kwargs):
            super().mount(request, **kwargs)
            self.note = "attribute value"

    _view, request, html = _get(Shadow)

    assert SECRET in html and "attribute value" not in html
    assert "liveview_/ro/" not in request.session


def test_default_hook_is_empty():
    assert LiveView().get_render_only_context() == {}
