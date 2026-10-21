"""djust's injected inline scripts carry the request's CSP nonce.

``_inject_client_script`` emitted the "djust configuration" script (and, with
DEBUG, the debug-info script) without a nonce, so a policy without
``'unsafe-inline'`` blocked both on every page: the client ran on its defaults
and the DEBUG panel had no view info. The handler-metadata script already used
the nonce (mixins/template.py); these two now do too.
"""

import re

from django.test import RequestFactory

from djust import LiveView

PAGE = "<html><head></head><body><div dj-root></div></body></html>"


class _View(LiveView):
    template = "<div>x</div>"


def _inline_scripts(html: str) -> list[str]:
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html)


def _inject(nonce=None, debug=False, settings=None):
    settings.DEBUG = debug
    request = RequestFactory().get("/")
    if nonce is not None:
        request.csp_nonce = nonce
    view = _View()
    view.request = request
    return view._inject_client_script(PAGE)


def test_config_script_carries_the_nonce(settings):
    html = _inject(nonce="abc123", settings=settings)
    config = [t for t in _inline_scripts(html)]
    assert config, html
    assert all('nonce="abc123"' in tag for tag in config), config


def test_debug_info_script_carries_the_nonce(settings):
    html = _inject(nonce="abc123", debug=True, settings=settings)
    assert "window.DJUST_DEBUG_INFO" in html
    tags = _inline_scripts(html)
    assert len(tags) == 2 and all('nonce="abc123"' in tag for tag in tags), tags


def test_no_nonce_attribute_without_a_nonce(settings):
    html = _inject(settings=settings)
    assert all("nonce=" not in tag for tag in _inline_scripts(html))


def test_the_nonce_is_an_escaped_attribute(settings):
    html = _inject(nonce='x"><script>alert(1)</script>', settings=settings)
    assert '"><script>alert(1)' not in html
    assert 'nonce="x&quot;&gt;&lt;script&gt;' in html
