"""The injected client bootstrap carries the CSRF cookie name and token.

The HTTP event fallback and ``djust.call`` read these through
``window.djust.csrfToken()`` so they work when a project renames the CSRF
cookie (``CSRF_COOKIE_NAME``) or keeps it out of JavaScript's reach
(``CSRF_COOKIE_HTTPONLY``, ``CSRF_USE_SESSIONS``). Before this, the fallback
sent an empty ``X-CSRFToken`` and Django rejected the POST with 403.
"""

from __future__ import annotations

import re

from django.middleware.csrf import CsrfViewMiddleware, _check_token_format, _unmask_cipher_token
from django.conf import settings
from django.test import RequestFactory, override_settings

from djust.mixins.post_processing import PostProcessingMixin


class _FakeView(PostProcessingMixin):
    def get_debug_info(self):
        return {}


def _inject(html: str, request=None) -> str:
    view = _FakeView()
    if request is not None:
        view.request = request
    return view._inject_client_script(html)


def _meta(html: str, name: str) -> str | None:
    match = re.search(rf'<meta name="{name}" content="([^"]*)">', html)
    return match.group(1) if match else None


def test_meta_tags_go_in_head_with_configured_cookie_name():
    request = RequestFactory().get("/")
    html = _inject("<html><head></head><body></body></html>", request)

    head = html.split("</head>")[0]
    assert _meta(head, "djust-csrf-cookie") == settings.CSRF_COOKIE_NAME
    token = _meta(head, "djust-csrf-token")
    assert token
    _check_token_format(token)  # a well-formed (masked) Django token


@override_settings(CSRF_COOKIE_NAME="djust_demo_csrftoken")
def test_meta_carries_renamed_cookie():
    request = RequestFactory().get("/")
    html = _inject("<html><head></head><body></body></html>", request)
    assert _meta(html, "djust-csrf-cookie") == "djust_demo_csrftoken"


def test_meta_token_matches_the_request_secret():
    request = RequestFactory().get("/")
    html = _inject("<html><head></head><body></body></html>", request)
    token = _meta(html, "djust-csrf-token")

    # get_token() stored the secret on the request; the emitted token must
    # unmask to it, i.e. a POST carrying it would pass CsrfViewMiddleware.
    secret = request.META["CSRF_COOKIE"]
    assert _unmask_cipher_token(token) == secret


def test_emitted_token_is_accepted_by_csrf_middleware():
    factory = RequestFactory()
    get = factory.get("/")
    html = _inject("<html><head></head><body></body></html>", get)
    token = _meta(html, "djust-csrf-token")

    post = factory.post("/", data="{}", content_type="application/json", HTTP_X_CSRFTOKEN=token)
    post.COOKIES[settings.CSRF_COOKIE_NAME] = get.META["CSRF_COOKIE"]
    middleware = CsrfViewMiddleware(lambda r: None)
    assert middleware.process_view(post, lambda r: None, (), {}) is None


def test_no_head_puts_meta_before_the_scripts():
    request = RequestFactory().get("/")
    html = _inject("<html><body></body></html>", request)
    assert html.index('name="djust-csrf-token"') < html.index("<script")


def test_no_request_emits_no_meta():
    html = _inject("<html><head></head><body></body></html>")
    assert "djust-csrf-token" not in html
    assert "djust-csrf-cookie" not in html


# #2987: the meta tags and the debug CSS go into the document's real <head>,
# once, not before the first (or every) "</head>" string in the page.
_TRICKY_PAGE = (
    "<html><head>"
    '<script>var shell = "<html><head></head><body></body></html>";</script>'
    "<!-- </head> in a comment -->"
    "<title>Not </head> either</title>"
    "</head><body>"
    "<script>document.write('</head>')</script>"
    "</body></html>"
)


def _real_head(html: str) -> str:
    # The document's head ends at the last "</head><body>" (the script string has one too).
    return html[: html.rindex("</head><body>")]


def test_meta_skips_head_strings_in_scripts_and_comments():
    html = _inject(_TRICKY_PAGE, RequestFactory().get("/"))
    assert html.count('name="djust-csrf-token"') == 1
    assert html.count('name="djust-csrf-cookie"') == 1
    head = _real_head(html)
    # Inserted immediately before the real </head>, after the tricky content.
    assert head.index('name="djust-csrf-token"') > head.index("<title>")
    assert head.endswith(">") and 'name="djust-csrf-token"' in head
    # The script strings are untouched.
    assert 'var shell = "<html><head></head><body></body></html>";' in html
    assert "document.write('</head>')" in html


@override_settings(DEBUG=True)
def test_debug_css_goes_in_the_real_head_once():
    html = _inject(_TRICKY_PAGE, RequestFactory().get("/"))
    assert html.count("debug-panel.css") == 1
    head = _real_head(html)
    assert "debug-panel.css" in head
    assert head.index("debug-panel.css") > head.index('name="djust-csrf-token"')


def test_uppercase_head_close_is_found():
    html = _inject("<HTML><HEAD></HEAD><BODY></BODY></HTML>", RequestFactory().get("/"))
    assert html.index('name="djust-csrf-token"') < html.index("</HEAD>")


def test_head_string_only_after_body_falls_back_to_scripts():
    page = "<html><body><script>var s = '</head>';</script></body></html>"
    html = _inject(page, RequestFactory().get("/"))
    assert "var s = '</head>';" in html
    assert html.index('name="djust-csrf-token"') < html.index("<script src=")
    assert html.count('name="djust-csrf-token"') == 1


def test_client_scripts_go_before_the_real_body_close_once():
    html = _inject(_TRICKY_PAGE, RequestFactory().get("/"))
    assert html.count("client.min.js") + html.count("djust/client.js") == 1
    assert html.rindex("djust/client") > html.index("document.write('</head>')")
    assert html.endswith("</body></html>")


def test_script_end_tag_with_trailing_junk_is_still_a_close():
    # Browsers close a script at "</script foo>" too; the "</head>" inside it is text.
    page = '<html><head><script>x = "</head>"</script\tfoo></head><body></body></html>'
    html = _inject(page, RequestFactory().get("/"))
    assert 'x = "</head>"' in html
    assert html.index('name="djust-csrf-token"') > html.index("</script\tfoo>")


def test_custom_element_named_body_something_is_not_body():
    page = "<html><head><body-shell></body-shell></head><body></body></html>"
    html = _inject(page, RequestFactory().get("/"))
    assert html.index('name="djust-csrf-token"') < html.index("</head>")
    assert html.index('name="djust-csrf-token"') > html.index("</body-shell>")


def test_unterminated_regions_scan_in_linear_time():
    import time

    for opener in ("<!--", "<script>", "<title>", "<script"):
        page = "<html><head></head><body>" + opener * 8000 + "</body></html>"
        start = time.perf_counter()
        _inject(page, RequestFactory().get("/"))
        # Quadratic scanning took 5-12 s on these inputs; linear is milliseconds.
        assert time.perf_counter() - start < 1.0, opener
