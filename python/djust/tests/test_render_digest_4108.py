"""The render digest the page GET and every fallback answer carry (#4108).

``djust._render_digest``: a MAC over the rendered root, bound to the session,
the signed-in user, the path and the view class. These tests pin what makes it
equal (the same state rendered again, a re-masked CSRF token, renumbered
``dj-id``s) and what makes it differ (content, session, user, page, key).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

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
    assert digest and len(digest) == 22 + 1 + 16
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


GOOD_DIGEST = "A" * 22
GOOD_SHAPE = "0" * 16


@pytest.mark.parametrize(
    "value",
    [
        "",
        "short",
        GOOD_DIGEST,  # no shape
        GOOD_DIGEST + ".",
        "." + GOOD_SHAPE,
        "A" * 21 + "." + GOOD_SHAPE,
        "A" * 23 + "." + GOOD_SHAPE,
        "A" * 5000,
        "A" * 21 + "=." + GOOD_SHAPE,
        "A" * 21 + "%." + GOOD_SHAPE,
        "A" * 19 + "%41." + GOOD_SHAPE,
        "A" * 21 + "\u00e9." + GOOD_SHAPE,
        " " + "A" * 21 + "." + GOOD_SHAPE,
        GOOD_DIGEST + "." + "0" * 15,
        GOOD_DIGEST + "." + "0" * 17,
        GOOD_DIGEST + "." + "G" * 16,
        GOOD_DIGEST + "." + "A" * 16,  # hex is lower case
        GOOD_DIGEST + "-" + GOOD_SHAPE,
        GOOD_DIGEST + "." + "0" * 7 + "." + "0" * 8,
    ],
)
def test_a_value_not_shaped_like_a_token_is_no_token(value):
    request = RequestFactory().post("/", HTTP_X_DJUST_RENDER_DIGEST=value)
    assert _render_digest.supplied_token(request) is None


def test_a_well_formed_token_is_read_and_both_halves_are_compared():
    request_for_html = _request()
    digest = _render_digest.render_digest(_ViewA(), request_for_html, HTML)
    token = _render_digest.render_token(_ViewA(), request_for_html, HTML)
    shape = _render_digest.dom_shape(HTML)
    assert token == digest + "." + shape
    request = RequestFactory().post("/", HTTP_X_DJUST_RENDER_DIGEST=token)
    assert _render_digest.supplied_token(request) == (digest, shape)
    assert _render_digest.supplied_token(RequestFactory().post("/")) is None

    def match(html, expected, supplied):
        return _render_digest.tokens_match(html, request_for_html, expected, supplied)

    assert match(HTML, digest, (digest, shape))
    other_digest = digest[:-1] + ("A" if digest[-1] != "A" else "B")
    assert not match(HTML, digest, (other_digest, shape))
    # The right digest with a DOM of another shape: the client's page is not
    # laid out as the render is.
    assert not match(HTML, digest, (digest, "0" * 16))
    assert not match(HTML, digest, None)
    assert not match(HTML, None, (digest, shape))
    # A render this reader cannot follow has no shape, so nothing matches it.
    assert not match("<div><p></div>", digest, (digest, shape))


def test_the_digest_comparison_is_constant_time(monkeypatch):
    calls = []
    real = _render_digest.hmac.compare_digest

    def recording(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(_render_digest.hmac, "compare_digest", recording)
    shape = _render_digest.dom_shape(HTML)
    assert _render_digest.tokens_match(HTML, _request(), "a" * 22, ("a" * 22, shape))
    assert calls == [(b"a" * 22, b"a" * 22)]


# --------------------------------------------------------------------------- #
# The shape of a render
# --------------------------------------------------------------------------- #

ROOT = '<div dj-root="" dj-id="0">%s</div>'
BASE = ROOT % '<h1 dj-id="1">Rows</h1><ul dj-id="2"><li dj-id="3">a</li><li dj-id="4">b</li></ul>'


def _shape(inner: str) -> str:
    shape = _render_digest.dom_shape(ROOT % inner)
    assert shape is not None and len(shape) == 16
    return shape


def test_the_shape_ignores_what_client_code_writes_on_the_servers_render():
    base = _render_digest.dom_shape(BASE)
    same = [
        # presentation, widget state, generated ids, the root's own attributes
        ROOT
        % (
            '<h1 dj-id="9" class="x" style="color:red" aria-live="polite">Rows</h1>'
            '<ul dj-id="8" hidden data-djust-locked="1" data-dj-src="t.html:3">'
            '<li tabindex="0" role="note">a</li><li data-open="1" disabled>b</li></ul>'
        ),
        '<div dj-root="" dj-view="app.View" class="page"><h1>Rows</h1><ul><li>a</li><li>b</li></ul></div>',
        # a comment that is not a dj-if boundary, indentation, whitespace runs in text
        ROOT % "<h1>Rows</h1><!-- note -->\n  <ul><li>a</li>\n<li>  b\n</li></ul>",
    ]
    assert [_render_digest.dom_shape(html) for html in same] == [base] * len(same)


@pytest.mark.parametrize(
    "inner",
    [
        "<ul><li>a</li><li>b</li></ul>",  # an element removed
        "<h1>Rows</h1><p>new</p><ul><li>a</li><li>b</li></ul>",  # one inserted
        "<h1>Rows</h1><ul><li>a</li></ul>",  # a row fewer
        "<h1>Rows</h1><ul><li>a</li><li>b</li><li>c</li></ul>",  # a row more
        "<h2>Rows</h2><ul><li>a</li><li>b</li></ul>",  # another tag
        "<h1>Rows</h1><ol><li>a</li><li>b</li></ol>",
        "<h1></h1><ul><li>a</li><li>b</li></ul>",  # a text child gone
        "<h1>Rows</h1><ul><li>a</li><li><b>b</b></li></ul>",  # deeper
        "<h1>Rows</h1><ul><li>a</li></ul><li>b</li>",  # same tags, other nesting
        '<h1 id="t">Rows</h1><ul><li>a</li><li>b</li></ul>',  # an attribute
        '<h1>Rows</h1><ul><li data-key="1">a</li><li>b</li></ul>',
        '<h1>Rows</h1><ul><li dj-value-id="1">a</li><li>b</li></ul>',
        '<h1 title="a > b">Rows</h1><ul><li>a</li><li>b</li></ul>',
        '<h1>Rows</h1><!--dj-if id="if-1"--><!--/dj-if--><ul><li>a</li><li>b</li></ul>',
        "<h1>Rows</h1> <ul><li>a</li><li>b</li></ul>",  # a kept single space
        "<h1>Other</h1><ul><li>a</li><li>b</li></ul>",  # a text
        "<h1>Rows</h1><ul><li>b</li><li>a</li></ul>",  # the rows' order
    ],
)
def test_the_shape_changes_with_anything_else(inner):
    assert _shape(inner) != _render_digest.dom_shape(BASE)


def test_rows_in_another_order_have_another_shape_unless_they_are_equal():
    """What makes a position trustworthy: reordered rows show, whatever tells them apart."""
    for one, two in [
        ('<li data-key="1">a</li>', '<li data-key="2">a</li>'),  # a key
        (
            '<li dj-value-id="1"><button>Delete</button></li>',
            '<li dj-value-id="2"><button>Delete</button></li>',
        ),
        ("<li>a</li>", "<li>b</li>"),  # text only
        ('<li><a href="/1">open</a></li>', '<li><a href="/2">open</a></li>'),  # a nested attribute
        ('<li><input name="q" value="1"></li>', '<li><input name="q" value="2"></li>'),
    ]:
        assert _shape("<ul>%s%s</ul>" % (one, two)) != _shape("<ul>%s%s</ul>" % (two, one)), one
    # Rows equal in every counted attribute and text: either order is the same page.
    assert _shape('<ul><li class="x">a</li><li>a</li></ul>') == _shape(
        '<ul><li>a</li><li class="x">a</li></ul>'
    )


def test_attribute_order_and_quoting_do_not_matter_and_values_are_read_as_the_browser_reads_them():
    assert _shape('<input type="text" name="q" value="v">') == _shape(
        "<input value='v' name=q type=\"text\" />"
    )
    assert _shape('<a href="/x?a=1&amp;b=2">l</a>') != _shape('<a href="/x?a=1">l</a>')
    # A name or a value cannot pass for structure.
    assert _shape('<p data-a="1 data-b=2">x</p>') != _shape('<p data-a="1" data-b="2">x</p>')
    assert _shape("<p>a</p><p>b</p>") != _shape("<p>a&lt;/p&gt;&lt;p&gt;b</p>")


def test_the_csrf_inputs_value_is_not_part_of_the_shape():
    one = _shape('<form><input type="hidden" name="csrfmiddlewaretoken" value="A"></form>')
    two = _shape('<form><input type="hidden" name="csrfmiddlewaretoken" value="B"></form>')
    assert one == two
    assert _shape('<input name="other" value="A">') != _shape('<input name="other" value="B">')


def test_void_and_raw_text_elements_and_whitespace_keeping_ones():
    # A void element has no children, with or without the closing slash.
    assert _shape('<input name="a" /><br><p>x</p>') == _shape('<input name="a"><br /><p>x</p>')
    # Text that looks like markup inside <script> / <textarea> is text.
    assert _shape("<script>if (a < b) { x = '<p>'; }</script><p>x</p>") != _shape(
        "<script>y</script><p>x</p>"
    )
    assert _shape("<script>if (a < b) { x = '<p>'; }</script><p>x</p>") is not None
    assert _shape("<textarea>&lt;b&gt;not a tag&lt;/b&gt;</textarea>") == _shape(
        "<textarea><b>not a tag</b></textarea>"
    )
    # Whitespace is a child inside <pre>, and not inside <p>.
    assert _shape("<pre> </pre>") != _shape("<pre></pre>")
    # ... except the one newline the HTML parser drops after <pre>.
    assert _shape("<pre>\n</pre>") == _shape("<pre></pre>")
    assert _shape("<pre>\n\n</pre>") != _shape("<pre></pre>")
    # A character reference is the character it names.
    assert _shape("<p>&#10;<b>x</b></p>") == _shape("<p>\n<b>x</b></p>")
    assert _shape("<p>caf&eacute;</p>") == _shape("<p>caf\u00e9</p>")
    assert _shape("<p>\n</p>") == _shape("<p></p>")


def test_a_comment_closed_by_bang_ends_where_the_browser_ends_it():
    """``--!>`` closes a comment in the HTML parser as ``-->`` does (CodeQL 3151).

    The server patches only when the client's shape equals the render's, so the
    server must end a comment exactly where the browser does. Otherwise its shape
    describes a tree the client does not have. The shared vectors
    ``comment_bang_close`` and ``comment_bang_close_keeps_element`` hold the
    client to the same values.
    """
    assert _shape("<p>a</p><!-- n --!><p>b</p>") == _shape("<p>a</p><!-- n --><p>b</p>")
    # Read on to the next "-->" and the <p>x</p> between the two comments would
    # vanish from the server's shape while the browser keeps it.
    kept = "<p>a</p><!-- n --!><p>x</p><!-- m --><p>b</p>"
    assert _shape(kept) == _shape("<p>a</p><p>x</p><p>b</p>")
    assert _shape(kept) != _shape("<p>a</p><p>b</p>")


VECTORS = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "js"
        / "fixtures"
        / "dom_shape_vectors_4108.json"
    ).read_text(encoding="utf-8")
)["vectors"]


@pytest.mark.parametrize("vector", VECTORS, ids=[vector["name"] for vector in VECTORS])
def test_the_shape_of_each_shared_vector(vector):
    """One spec, two implementations: ``tests/js/dom_shape_vectors_4108.test.js``
    holds the client's ``domShape`` to the same file."""
    assert _render_digest.dom_shape(vector["html"]) == vector["shape"]
    assert vector["client"] in ("same", "differs")
    assert (vector["client"] == "differs") == bool(vector.get("reason"))


def test_markup_this_reader_cannot_follow_has_no_shape():
    for html in ("<div><p></div>", "<div>", "</div>", "<div></div></div>", None, 7):
        assert _render_digest.dom_shape(html) is None
    assert (
        _render_digest.dom_shape("") is None or _render_digest.dom_shape("") == "0000000000000001"
    )


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


def _client_skipped_lists() -> tuple[set[str], tuple[str, ...]]:
    """``_SHAPE_SKIPPED`` and ``_SHAPE_SKIPPED_PREFIX`` as the client source spells them."""
    source = (
        Path(__file__).resolve().parents[1] / "static" / "djust" / "src" / "12-vdom-patch.js"
    ).read_text(encoding="utf-8")
    block = re.search(r"const _SHAPE_SKIPPED = new Set\(\((.*?)\)\.split", source, re.DOTALL)
    prefixes = re.search(r"const _SHAPE_SKIPPED_PREFIX = /\^\(([^)]*)\)/;", source)
    assert block and prefixes, "the client's skipped-attribute lists moved"
    names = set(" ".join(re.findall(r"'([^']*)'", block.group(1))).split())
    return names, tuple(prefixes.group(1).split("|"))


def test_the_client_and_the_server_leave_out_the_same_attributes():
    """#4114: one list on each side, held equal here and by the shared vector
    ``every_skipped_attribute`` (which the client must shape like ``<p>x</p>``)."""
    names, prefixes = _client_skipped_lists()
    assert names == set(_render_digest._SKIPPED_ATTRS)
    assert prefixes == _render_digest._SKIPPED_ATTR_PREFIXES
    by_name = {vector["name"]: vector for vector in VECTORS}
    every = by_name["every_skipped_attribute"]
    for name in _render_digest._SKIPPED_ATTRS - {"dj-id"}:
        assert ' %s="v"' % name in every["html"], name
    for prefix in _render_digest._SKIPPED_ATTR_PREFIXES:
        assert ' %sx="v"' % prefix in every["html"], prefix
    assert every["shape"] == by_name["every_skipped_attribute_left_out"]["shape"]
