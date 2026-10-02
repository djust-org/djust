"""#3036: live navigation falls back to a full page load when page shells differ.

A live navigation swaps only the ``dj-root``, so the ``<head>`` assets and the
scripts outside the root stay the previous page's. The server sends a
fingerprint of the destination's page shell on the ``live_redirect_mount``
reply and renders the current page's into its ``<head>``; the client (see
``tests/js/page_shell_fallback_3036.test.js``) compares them.
"""

import json
import re
import uuid

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings
from django.urls import path

from djust import LiveView
from djust._page_shell import _CACHE, page_shell, shell_fingerprint
from djust.websocket import LiveViewConsumer

from ._ws_frames import drain_extra, receive_until, types_of

SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={})


@pytest.fixture(autouse=True)
def _fresh_cache():
    _CACHE.clear()
    yield
    _CACHE.clear()


def _doc(head="", before="", body="<p>x</p>", after=""):
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>T</title>{head}</head>"
        f"<body>{before}<div dj-root>{body}</div>{after}</body></html>"
    )


class TestFingerprint:
    def test_same_shell_different_root_is_equal(self):
        a = _doc(head='<link rel="stylesheet" href="/a.css">', body="<p>one</p>")
        b = _doc(head='<link rel="stylesheet" href="/a.css">', body="<h1>two</h1>")
        assert shell_fingerprint(a) == shell_fingerprint(b)
        assert shell_fingerprint(a) is not None

    def test_title_and_meta_do_not_count(self):
        a = _doc(head='<meta name="description" content="a">')
        b = _doc(head='<meta name="description" content="b"><meta name="x" content="y">')
        assert shell_fingerprint(a) == shell_fingerprint(b)

    def test_a_different_stylesheet_differs(self):
        a = _doc(head='<link rel="stylesheet" href="/a.css">')
        b = _doc(head='<link rel="stylesheet" href="/examples.css">')
        c = _doc(head="")
        assert len({shell_fingerprint(a), shell_fingerprint(b), shell_fingerprint(c)}) == 3

    def test_a_non_asset_link_does_not_count(self):
        a = _doc(head='<link rel="icon" href="/a.ico">')
        b = _doc(head='<link rel="icon" href="/b.ico"><link rel="canonical" href="/x">')
        assert shell_fingerprint(a) == shell_fingerprint(b)

    def test_head_scripts_and_outside_root_scripts_count(self):
        base = shell_fingerprint(_doc())
        head_script = shell_fingerprint(_doc(head='<script src="/h.js"></script>'))
        after_script = shell_fingerprint(_doc(after='<script src="/tail.js"></script>'))
        before_script = shell_fingerprint(_doc(before='<script src="/pre.js"></script>'))
        assert len({base, head_script, after_script, before_script}) == 4

    def test_inline_script_content_counts_whitespace_does_not(self):
        a = shell_fingerprint(_doc(after="<script>init( 1 )</script>"))
        same = shell_fingerprint(_doc(after="<script>\n  init( 1 )\n</script>"))
        other = shell_fingerprint(_doc(after="<script>init(2)</script>"))
        assert a == same
        assert a != other

    def test_scripts_inside_the_root_do_not_count(self):
        a = _doc(body="<p>a</p>")
        b = _doc(body='<p>a</p><script src="/inside.js"></script><script>x()</script>')
        assert shell_fingerprint(a) == shell_fingerprint(b)

    def test_head_style_counts_body_style_does_not(self):
        base = shell_fingerprint(_doc())
        assert shell_fingerprint(_doc(head="<style>a{b:c}</style>")) != base
        assert shell_fingerprint(_doc(after="<style>a{b:c}</style>")) == base

    def test_head_include_counts(self):
        a = shell_fingerprint(_doc(head='{% include "head_a.html" %}'))
        b = shell_fingerprint(_doc(head='{% include "head_b.html" %}'))
        assert a != b

    def test_document_order_matters(self):
        a = shell_fingerprint(
            _doc(head='<script src="/1.js"></script><script src="/2.js"></script>')
        )
        b = shell_fingerprint(
            _doc(head='<script src="/2.js"></script><script src="/1.js"></script>')
        )
        assert a != b

    def test_a_script_after_an_unclosed_root_child_is_still_outside_the_root(self):
        """Templates leave ``<p>`` / ``<li>`` open; the root still closes."""
        html = _doc(body="<ul><li>one<li>two</ul><p>open", after='<script src="/t.js"></script>')
        assert shell_fingerprint(html) != shell_fingerprint(_doc())

    def test_a_tag_shaped_attribute_value_does_not_end_the_root(self):
        html = _doc(
            body='<a title="</div><script src=/in.js>">x</a>', after='<script src="/t.js"></script>'
        )
        assert shell_fingerprint(html) != shell_fingerprint(_doc())

    def test_dj_view_stands_in_for_a_missing_dj_root(self):
        html = "<html><head><link rel='stylesheet' href='/a.css'></head><body><div dj-view='x'></div></body></html>"
        assert shell_fingerprint(html) is not None

    def test_no_root_means_no_fingerprint(self):
        assert shell_fingerprint("<html><head></head><body><p>x</p></body></html>") is None
        assert shell_fingerprint(None, "") is None

    def test_a_wrapper_template_contributes(self):
        view = "<div dj-root>x</div>"
        wrapper_a = _doc(head='<link rel="stylesheet" href="/a.css">', body="")
        wrapper_b = _doc(head='<link rel="stylesheet" href="/b.css">', body="")
        assert shell_fingerprint(view, wrapper_a) != shell_fingerprint(view, wrapper_b)
        assert shell_fingerprint(view, wrapper_a) != shell_fingerprint(view)

    def test_is_a_short_hex_hash_carrying_no_content(self):
        fp = shell_fingerprint(_doc(head='<link rel="stylesheet" href="/secret-name.css">'))
        assert re.fullmatch(r"[0-9a-f]{16}", fp)


class SourcePage(LiveView):
    template = _doc(head='<link rel="stylesheet" href="/app.css">', body="<p>home</p>")


class SameShellPage(LiveView):
    template = _doc(head='<link rel="stylesheet" href="/app.css">', body="<p>same shell</p>")


class OtherShellPage(LiveView):
    template = _doc(
        head='<link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/examples.css">',
        after='<script src="/examples.js"></script>',
        body="<p>other shell</p>",
    )


class FragmentPage(LiveView):
    template = "<p dj-root>no document</p>"


urlpatterns = [
    path("src/", SourcePage.as_view()),
    path("same/", SameShellPage.as_view()),
    path("other/", OtherShellPage.as_view()),
]


def _get(view_class, url):
    request = RequestFactory().get(url)
    request.session = SessionStore()
    request.session.save()
    request.user = AnonymousUser()
    request.tenant = None
    return view_class.as_view()(request)


def _meta_shell(html):
    found = re.findall(r'<meta name="djust-page-shell" content="([^"]*)">', html)
    assert len(found) <= 1
    return found[0] if found else None


@pytest.mark.django_db
class TestPageCarriesItsFingerprint:
    def test_the_http_page_has_the_meta_in_its_head(self):
        with override_settings(**SETTINGS):
            html = _get(SourcePage, "/src/").content.decode()
        head = html.split("</head>")[0]
        assert _meta_shell(head) == page_shell(SourcePage())
        assert re.fullmatch(r"[0-9a-f]{16}", _meta_shell(head))

    def test_a_fragment_page_without_a_head_has_none(self):
        with override_settings(**SETTINGS):
            html = _get(FragmentPage, "/frag/").content.decode()
        assert _meta_shell(html) is None

    def test_template_files_with_inheritance(self, tmp_path):
        (tmp_path / "base.html").write_text(
            "<!DOCTYPE html><html><head>{% block head %}{% endblock %}</head>"
            "<body>{% block content %}{% endblock %}</body></html>"
        )
        (tmp_path / "a.html").write_text(
            '{% extends "base.html" %}{% block head %}<link rel="stylesheet" href="/a.css">'
            "{% endblock %}{% block content %}<div dj-root>a</div>{% endblock %}"
        )
        (tmp_path / "b.html").write_text(
            '{% extends "base.html" %}{% block head %}<link rel="stylesheet" href="/b.css">'
            "{% endblock %}{% block content %}<div dj-root>b</div>{% endblock %}"
        )
        (tmp_path / "a2.html").write_text(
            '{% extends "base.html" %}{% block head %}<link rel="stylesheet" href="/a.css">'
            "{% endblock %}{% block content %}<div dj-root><h1>other body</h1></div>{% endblock %}"
        )
        templates = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path)],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ]

        class A(LiveView):
            template_name = "a.html"

        class B(LiveView):
            template_name = "b.html"

        class A2(LiveView):
            template_name = "a2.html"

        with override_settings(TEMPLATES=templates, **SETTINGS):
            fa, fb, fa2 = page_shell(A()), page_shell(B()), page_shell(A2())
        assert fa and fb and fa2
        assert fa != fb
        assert fa == fa2

    def test_a_template_that_cannot_load_gives_none_and_never_raises(self):
        class Missing(LiveView):
            template_name = "does-not-exist.html"

        with override_settings(**SETTINGS):
            assert page_shell(Missing()) is None


def _session():
    session = SessionStore()
    session.save()
    return session


async def _redirect(target: str):
    """Mount SourcePage, live-redirect to ``target``; return (initial mount
    frame, the redirect's mount frame)."""
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    try:
        await socket.send_json_to(
            {"type": "mount", "view": f"{__name__}.SourcePage", "url": "/src/"}
        )
        first = await receive_until(
            socket, lambda f: "mount" in types_of(f), what="the initial mount"
        )
        await socket.send_json_to(
            {
                "type": "live_redirect_mount",
                "view": f"{__name__}.{target}",
                "url": "/dest/",
                "params": {},
            }
        )
        second = await receive_until(
            socket, lambda f: "mount" in types_of(f), what="the redirect mount"
        )
        second += await drain_extra(socket)
    finally:
        await socket.disconnect()
    mount = lambda frames: next(f for f in frames if f.get("type") == "mount")  # noqa: E731
    return mount(first), mount(second)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestRedirectMountFrame:
    async def test_a_redirect_mount_names_the_destination_shell(self):
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
            _, other = await _redirect("OtherShellPage")
            _, same = await _redirect("SameShellPage")
        assert other["page_shell"] == page_shell(OtherShellPage())
        assert same["page_shell"] == page_shell(SameShellPage())
        # Different shells differ; identical shells match the page they came from.
        assert other["page_shell"] != page_shell(SourcePage())
        assert same["page_shell"] == page_shell(SourcePage())

    async def test_the_frame_agrees_with_the_http_page_of_the_same_view(self):
        """The HTTP load and the redirect compute it the same way, or a
        same-shell navigation would take the slow path forever."""
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
            _, frame = await _redirect("OtherShellPage")
            html = (await sync_to_async(_get)(OtherShellPage, "/other/")).content.decode()
        assert _meta_shell(html.split("</head>")[0]) == frame["page_shell"]

    async def test_the_initial_mount_carries_no_fingerprint(self):
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
            first, _ = await _redirect("SameShellPage")
        assert "page_shell" not in first

    async def test_a_fragment_destination_has_an_empty_shell_fingerprint(self):
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
            _, frame = await _redirect("FragmentPage")
        assert frame["page_shell"]  # dj-root present: an empty shell still has a fingerprint
        assert frame["page_shell"] == shell_fingerprint(FragmentPage.template)


class TestSseTransportFlag:
    def test_flag_follows_the_session(self):
        from djust.runtime import SSESessionTransport

        class _Session:
            _replacing_view = False

        session = _Session()
        transport = SSESessionTransport(session)
        assert transport.capture_page_shell is False
        session._replacing_view = True
        assert transport.capture_page_shell is True


# --------------------------------------------------------------------------
# Real SSE endpoints: a replacement mount names the destination's shell.
# --------------------------------------------------------------------------

from django.contrib.auth import get_user  # noqa: E402
from django.utils.functional import SimpleLazyObject  # noqa: E402

from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions  # noqa: E402


def _sse_request(method, url, body, key=None):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url, data=body)
    else:
        request = factory.post(url, data=json.dumps(body), content_type="application/json")
    request.session = SessionStore(key)
    if key is None:
        request.session.create()
    request.session = SessionStore(request.session.session_key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    return request


def _drain_queue(session):
    frames = []
    while not session.queue.empty():
        frame = session.queue.get_nowait()
        if frame is not None:
            frames.append(frame)
    return frames


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_sse_replacement_mount_names_the_destination_shell(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    with override_settings(
        ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=[__name__, "djust"], **SETTINGS
    ):
        sid = str(uuid.uuid4())
        request = await sync_to_async(_sse_request)(
            "GET", f"/djust/sse/{sid}/", {"view": f"{__name__}.SourcePage", "_djust_url": "/src/"}
        )
        response = await DjustSSEStreamView().get(request, session_id=sid)
        assert response.status_code == 200
        session = _sse_sessions[sid]
        try:
            initial = [f for f in _drain_queue(session) if f.get("type") == "mount"]
            assert initial and "page_shell" not in initial[0]
            post = await sync_to_async(_sse_request)(
                "POST",
                f"/djust/sse/{sid}/message/",
                {"type": "live_redirect_mount", "view": "x", "url": "/other/", "params": {}},
                request.session.session_key,
            )
            assert (await DjustSSEMessageView().post(post, session_id=sid)).status_code == 200
            mounts = [f for f in _drain_queue(session) if f.get("type") == "mount"]
            assert mounts, "no mount frame after live_redirect_mount"
            assert mounts[-1]["page_shell"] == page_shell(OtherShellPage())
            assert session._replacing_view is False
        finally:
            _sse_sessions.pop(sid, None)
