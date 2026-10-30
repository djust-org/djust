"""Render-only context: rendered on every transport, saved by none.

Reported downstream (Point Guard, djust 1.3.0rc11):

- N-35: no public way to give a template a model that djust renders (through
  the JIT, by the template's paths) but never persists. The mechanism existed
  as private ``djust.forms`` hooks (``_djust_render_only_context`` /
  ``_djust_render_only_context_keys``). ``get_render_only_context()`` is the
  public hook.
- N-31: the HTTP GET saved a QuerySet that a ``get_context_data()`` override
  put in the context as a list of dicts with EVERY column of each row.
- Phase 2 decision 185: the GET saved the whole context but its render-only
  keys, so content an override added (a chat's messages, a run's preview)
  landed in ``django_session``.

A legacy save keeps what the base ``get_context_data()`` built from the view's
attributes; render-only values and keys an override adds are never saved.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import uuid
from typing import Any, Dict, List, Optional

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

    saved = request.session["liveview_/ro/"]
    assert saved["count"] == 1
    assert "people" not in saved
    assert "secret_note" not in saved
    assert "ada@example.com" not in _session_text(request.session)
    assert SECRET not in _session_text(request.session)
    # ...and the page still shows them.
    assert "<i>n35-ada</i>" in html and SECRET in html


def test_fallback_post_saves_no_key_an_override_added(people):
    _view, request, _html = _get(OverrideAddsView)

    answer = _post(OverrideAddsView, request, "bump")

    saved = request.session["liveview_/ro/"]
    assert saved["count"] == 2
    assert "people" not in saved and "secret_note" not in saved
    assert SECRET not in _session_text(request.session)
    assert "ada@example.com" not in _session_text(request.session)
    assert answer


# ---------------------------------------------------------------------------
# N-35: get_render_only_context()
# ---------------------------------------------------------------------------


def test_render_only_values_render_and_go_through_the_jit(people):
    view = RenderOnlyView()
    view.mount(None)

    context = view.get_context_data()

    assert context["owner"]["username"] == "n35-ada"
    assert "email" not in context["owner"]
    assert [row["username"] for row in context["people"]] == ["n35-ada", "n35-grace"]
    assert all("email" not in row for row in context["people"])
    assert context["note"] == SECRET


def test_get_and_post_never_save_render_only_values(people):
    _view, request, html = _get(RenderOnlyView)
    assert "<i>n35-ada</i><i>n35-grace</i>" in html and SECRET in html

    saved = request.session["liveview_/ro/"]
    assert saved["count"] == 1
    for key in ("owner", "people", "people_count", "note"):
        assert key not in saved

    _post(RenderOnlyView, request, "bump")

    saved = request.session["liveview_/ro/"]
    assert saved["count"] == 2
    for key in ("owner", "people", "people_count", "note"):
        assert key not in saved
    assert SECRET not in _session_text(request.session)
    assert "ada@example.com" not in _session_text(request.session)


def test_a_render_only_key_wins_over_an_attribute_and_is_not_saved(people):
    class Shadow(RenderOnlyView):
        def mount(self, request, **kwargs):
            super().mount(request, **kwargs)
            self.note = "attribute value"

    _view, request, html = _get(Shadow)

    assert SECRET in html and "attribute value" not in html
    assert "note" not in request.session["liveview_/ro/"]


def test_default_hook_is_empty():
    assert LiveView().get_render_only_context() == {}


def test_legacy_unsaved_keys_without_a_recorded_render():
    """A view whose override never calls super() keeps the old behaviour."""
    from djust.mixins.context import legacy_unsaved_keys

    class NoSuper(LiveView):
        template = "<div dj-root>{{ a }}</div>"

        def get_context_data(self, **kwargs):
            return {"a": 1, "b": 2}

    view = NoSuper()
    assert legacy_unsaved_keys(view, view.get_context_data()) == frozenset()


# ---------------------------------------------------------------------------
# The socket turn's save (runtime.py) filters the same way
# ---------------------------------------------------------------------------


class _Transport:
    def __init__(self):
        self._session_id = str(uuid.uuid4())
        self.sent: List[Dict[str, Any]] = []

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def client_ip(self) -> Optional[str]:
        return None

    async def send(self, data):
        self.sent.append(data)

    async def send_error(self, error, **kwargs):
        self.sent.append({"type": "error", "error": error, **kwargs})

    async def close(self, code=1000):
        pass

    def next_client_version(self, html, rust_version):
        return rust_version

    def build_request(self):
        return None

    def on_view_mounted(self, view_instance):
        pass

    async def on_event_recorded(self, view, snapshot):
        pass

    @contextlib.asynccontextmanager
    async def event_context(self, view):
        yield


class _StashedRequest:
    def __init__(self, session, path):
        self.session = session
        self.path = path


class SocketRenderOnlyView(RenderOnlyView):
    enable_state_snapshot = True


class SocketOverrideAddsView(OverrideAddsView):
    enable_state_snapshot = True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "view_class, unsaved",
    [
        (SocketRenderOnlyView, ("owner", "people", "people_count", "note")),
        (SocketOverrideAddsView, ("people", "secret_note")),
    ],
)
async def test_socket_turn_save_keeps_only_the_view_state(
    generous_save_timeout, view_class, unsaved
):
    from asgiref.sync import sync_to_async
    from django.contrib.sessions.backends.db import SessionStore

    from djust.runtime import ViewRuntime

    def setup():
        User.objects.create(username="n35-ada", email="ada@example.com")
        session = SessionStore()
        session.create()
        view = view_class()
        view.mount(None)
        view._djust_mount_request = _StashedRequest(session, "/ro/")
        return session, view

    session, view = await sync_to_async(setup)()
    runtime = ViewRuntime(_Transport(), scope=None)
    runtime.view_instance = view

    await runtime.dispatch_event({"type": "event", "event": "bump", "params": {}, "ref": 1})

    saved = await session.aget("liveview_/ro/")
    assert saved is not None and saved["count"] == 2
    for key in unsaved:
        assert key not in saved
    assert SECRET not in json.dumps(saved, default=str)


def test_every_legacy_save_site_filters_through_legacy_unsaved_keys():
    """One producer (#1646): the four legacy save sites use the same filter."""
    package = pathlib.Path(__file__).resolve().parents[1]
    expected = {
        "mixins/request.py": 2,
        "runtime.py": 1,
        "_lazy_containers.py": 1,
    }
    for relative, count in expected.items():
        source = (package / relative).read_text()
        assert source.count("legacy_unsaved_keys(") == count, relative
        assert "legacy_render_only_keys(" not in source, relative
