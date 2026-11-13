"""The render digest the page GET and every fallback answer carry (#4108).

``djust._render_digest``: a MAC over the rendered root, bound to the session,
the signed-in user, the path and the view class. These tests pin what makes it
equal (the same state rendered again, a re-masked CSRF token, renumbered
``dj-id``s) and what makes it differ (content, session, user, page, key).
"""

from __future__ import annotations

import json
import re

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory, override_settings
from django.urls import path

from djust import LiveView, _render_digest
from djust.decorators import event_handler, state
from djust.tests import http_fallback_matrix as matrix

pytestmark = [pytest.mark.django_db(transaction=True)]


class WithCsrf(LiveView):
    """Prints Django's CSRF token inside the root, twice."""

    exposure_policy = "explicit"
    template = matrix.PAGE % (
        '\n<form method="post">{% csrf_token %}<input name="q"></form>\n'
        '<p data-token="{{ csrf_token }}">Value: <b>{{ count }}</b></p>\n'
    )
    count = state(0, persist="server")

    @event_handler
    def touch(self, **kwargs):
        pass

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


urlpatterns = [*matrix.urlpatterns, path("m/csrf/", WithCsrf.as_view())]

SETTINGS = {"ROOT_URLCONF": __name__, "ALLOWED_HOSTS": ["*"], "DEBUG": False}

_TOKEN = re.compile(r'data-token="([0-9A-Za-z]{64})"')


def _post(client: Client, url: str, event: str, **headers) -> dict:
    response = client.post(
        url, data="{}", content_type="application/json", HTTP_X_DJUST_EVENT=event, **headers
    )
    assert response.status_code == 200, response.content[:300]
    return json.loads(response.content)


def _request(session: SessionStore | None = None, url: str = "/page/", **session_values):
    """A request on ``session`` (a new saved one by default)."""
    request = RequestFactory().get(url)
    if session is None:
        session = SessionStore()
        session.create()
    request.session = session
    for key, value in session_values.items():
        request.session[key] = value
    return request


def _session() -> SessionStore:
    session = SessionStore()
    session.create()
    return session


class _ViewA:
    pass


class _ViewB:
    pass


# --------------------------------------------------------------------------- #
# Where the digest travels
# --------------------------------------------------------------------------- #


@override_settings(**SETTINGS)
def test_the_page_get_carries_the_digest_outside_the_root():
    document = Client().get("/m/counter/").content.decode()
    digest = matrix.meta_digest(document)
    assert digest and len(digest) == 22
    head, _, rest = document.partition("</head>")
    assert 'name="djust-render-digest"' in head
    assert "djust-render-digest" not in rest


@override_settings(**SETTINGS)
def test_every_render_answer_names_its_digest_and_is_still_a_whole_page():
    client = Client()
    first = matrix.meta_digest(client.get("/m/counter/").content.decode())
    answer = _post(client, "/m/counter/", "increment")
    # No behaviour change yet: one render, the whole page, v1.
    assert "patches" not in answer and answer["version"] == 1
    assert answer["render_digest"] and answer["render_digest"] != first


@override_settings(**SETTINGS)
def test_equal_state_renders_to_an_equal_digest_on_the_get_and_on_a_later_request():
    """The property the patch path rests on: a fresh request reproduces the page."""
    client = Client()
    first = matrix.meta_digest(client.get("/m/csrf/").content.decode())
    unchanged = _post(client, "/m/csrf/", "touch")
    assert unchanged["render_digest"] == first
    changed = _post(client, "/m/csrf/", "increment")
    assert changed["render_digest"] != first
    # The state is saved: the next request renders the changed page again.
    assert _post(client, "/m/csrf/", "touch")["render_digest"] == changed["render_digest"]
    # A page GET is a new page load: the view mounts again, and so does the
    # request after it.
    reloaded = matrix.meta_digest(client.get("/m/csrf/").content.decode())
    assert reloaded == first
    assert _post(client, "/m/csrf/", "touch")["render_digest"] == first


@override_settings(**SETTINGS)
def test_a_remasked_csrf_token_does_not_change_the_digest():
    client = Client()
    one = client.get("/m/csrf/").content.decode()
    two = client.get("/m/csrf/").content.decode()
    # Django masks the token again on every render ...
    assert _TOKEN.search(one).group(1) != _TOKEN.search(two).group(1)
    # ... and the digest is over the page without it.
    assert matrix.meta_digest(one) == matrix.meta_digest(two)


# --------------------------------------------------------------------------- #
# The canonical form
# --------------------------------------------------------------------------- #


def test_canonical_form_drops_generated_ids_and_only_this_requests_csrf_token():
    request = _request()
    token = get_token(request)
    other = "A" * 64  # shaped like a token, but not this request's
    html = '<div dj-id="0"><p dj-id="1a" data-a="%s" data-b="%s">x</p></div>' % (token, other)
    assert _render_digest.canonical_html(html, request) == (
        '<div><p data-a="csrf-token" data-b="%s">x</p></div>' % other
    )
    # A token inside a longer run of token characters is not a token.
    glued = "<p>%sZ</p>" % token
    assert _render_digest.canonical_html(glued, request) == glued


def test_the_local_unmask_agrees_with_djangos_masking():
    request = _request()
    for _ in range(20):
        assert _render_digest._unmask(get_token(request)) == request.META["CSRF_COOKIE"]
    assert _render_digest._unmask("!" * 64) is None


def test_a_request_without_a_csrf_secret_leaves_token_shaped_text_alone():
    request = _request()
    html = "<p>%s</p>" % ("b" * 64)
    assert _render_digest.canonical_html(html, request) == html


# --------------------------------------------------------------------------- #
# The binding
# --------------------------------------------------------------------------- #

HTML = '<div dj-root=""><p>one</p></div>'


def test_the_digest_is_bound_to_content_session_user_path_and_view():
    session = _session()
    base = _render_digest.render_digest(_ViewA(), _request(session), HTML)
    assert base == _render_digest.render_digest(_ViewA(), _request(session), HTML)
    changed = HTML.replace("one", "two")
    different = {
        "content": _render_digest.render_digest(_ViewA(), _request(session), changed),
        "session": _render_digest.render_digest(_ViewA(), _request(), HTML),
        "path": _render_digest.render_digest(_ViewA(), _request(session, url="/other/"), HTML),
        "view": _render_digest.render_digest(_ViewB(), _request(session), HTML),
        "user": _render_digest.render_digest(_ViewA(), _request(session, _auth_user_id="7"), HTML),
        "user hash": _render_digest.render_digest(
            _ViewA(), _request(session, _auth_user_id="7", _auth_user_hash="h"), HTML
        ),
    }
    values = [base, *different.values()]
    assert all(values) and len(set(values)) == len(values), different


def test_the_binding_fields_cannot_run_into_each_other():
    """``("12", "3")`` and ``("1", "23")`` are different bindings."""
    session = _session()
    one = _render_digest.render_digest(
        _ViewA(), _request(session, _auth_user_id="12", _auth_user_hash="3"), HTML
    )
    two = _render_digest.render_digest(
        _ViewA(), _request(session, _auth_user_id="1", _auth_user_hash="23"), HTML
    )
    assert one and two and one != two


def test_the_key_comes_from_secret_key_and_nothing_per_process():
    session = _session()
    with override_settings(SECRET_KEY="one-key"):
        first = _render_digest.render_digest(_ViewA(), _request(session), HTML)
        again = _render_digest.render_digest(_ViewA(), _request(session), HTML)
    with override_settings(SECRET_KEY="another-key"):
        other = _render_digest.render_digest(_ViewA(), _request(session), HTML)
    assert first and first == again != other


def test_no_session_key_means_no_digest():
    assert _render_digest.digest_binding(_ViewA(), _request(SessionStore())) is None
    assert _render_digest.render_digest(_ViewA(), _request(SessionStore()), HTML) is None
    assert _render_digest.render_digest(_ViewA(), RequestFactory().get("/"), HTML) is None
    assert _render_digest.render_digest(_ViewA(), _request(), None) is None


# --------------------------------------------------------------------------- #
# What the client sends back
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [
        "",
        "short",
        "A" * 21,
        "A" * 23,
        "A" * 5000,
        "A" * 21 + "=",
        "A" * 21 + "%",
        "A" * 20 + "%41",
        "A" * 21 + "é",
        " " + "A" * 21,
    ],
)
def test_a_value_not_shaped_like_a_digest_is_no_digest(value):
    request = RequestFactory().post("/", HTTP_X_DJUST_RENDER_DIGEST=value)
    assert _render_digest.supplied_digest(request) is None


def test_a_well_formed_value_is_read_and_compared_exactly():
    digest = _render_digest.render_digest(_ViewA(), _request(), HTML)
    request = RequestFactory().post("/", HTTP_X_DJUST_RENDER_DIGEST=digest)
    assert _render_digest.supplied_digest(request) == digest
    assert _render_digest.supplied_digest(RequestFactory().post("/")) is None
    assert _render_digest.digests_match(digest, digest)
    assert not _render_digest.digests_match(
        digest, digest[:-1] + ("A" if digest[-1] != "A" else "B")
    )
    assert not _render_digest.digests_match(digest, None)
    assert not _render_digest.digests_match(None, digest)
    assert not _render_digest.digests_match(None, None)


def test_the_comparison_is_constant_time(monkeypatch):
    calls = []
    real = _render_digest.hmac.compare_digest

    def recording(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(_render_digest.hmac, "compare_digest", recording)
    assert _render_digest.digests_match("a" * 22, "a" * 22)
    assert calls == [(b"a" * 22, b"a" * 22)]


def test_path_addressed_removes_the_id_hints_and_nothing_else():
    patches = [
        {"type": "SetText", "path": [0, 1], "d": "1a", "text": "x"},
        {"type": "InsertChild", "path": [0], "d": "0", "ref_d": "3", "index": 1, "node": {"d": 1}},
        {"type": "RemoveChild", "path": [0], "child_d": "4", "index": 2},
        {"type": "SetAttr", "path": [], "key": "class", "value": "d"},
    ]
    assert _render_digest.path_addressed(patches) == [
        {"type": "SetText", "path": [0, 1], "text": "x"},
        {"type": "InsertChild", "path": [0], "index": 1, "node": {"d": 1}},
        {"type": "RemoveChild", "path": [0], "index": 2},
        {"type": "SetAttr", "path": [], "key": "class", "value": "d"},
    ]
    # The input is not changed.
    assert patches[0]["d"] == "1a"
