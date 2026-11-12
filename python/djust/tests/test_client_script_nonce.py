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
    exposure_policy = "legacy"
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


# ---- #3991: the scripts djust loads by URL carry the nonce too ----------------
#
# Under a nonce-only (or ``'strict-dynamic'``) ``script-src`` the browser refuses a
# ``<script src>`` without the nonce exactly as it refuses an inline one, so the
# client, the extensions and the DEBUG scripts never loaded.


def _external_scripts(html: str) -> list[str]:
    return re.findall(r"<script[^>]*\bsrc=[^>]*>", html)


def test_the_client_script_carries_the_nonce(settings):
    tags = _external_scripts(_inject(nonce="abc123", settings=settings))
    assert len(tags) == 1 and "djust/client" in tags[0], tags
    assert 'nonce="abc123"' in tags[0], tags


def test_every_debug_script_carries_the_nonce(settings):
    tags = _external_scripts(_inject(nonce="abc123", debug=True, settings=settings))
    names = [re.search(r'src="[^"]*/([^"/]+)"', tag).group(1) for tag in tags]
    assert names == ["client.js", "debug-panel.js", "client-dev.js"], names
    assert all('nonce="abc123"' in tag for tag in tags), tags


def test_an_extension_script_carries_the_nonce(settings, monkeypatch):
    monkeypatch.setattr(
        "djust.extensions.get_extension_static_paths", lambda: ["djust/ext/dj-chart.js"]
    )
    tags = _external_scripts(_inject(nonce="abc123", settings=settings))
    ext = [tag for tag in tags if "dj-chart.js" in tag]
    assert len(ext) == 1, tags
    assert 'nonce="abc123"' in ext[0], ext


def test_external_scripts_have_no_nonce_attribute_without_a_nonce(settings, monkeypatch):
    monkeypatch.setattr(
        "djust.extensions.get_extension_static_paths", lambda: ["djust/ext/dj-chart.js"]
    )
    tags = _external_scripts(_inject(debug=True, settings=settings))
    assert len(tags) == 4, tags
    assert all("nonce=" not in tag for tag in tags), tags


def test_the_nonce_on_an_external_script_is_escaped(settings):
    html = _inject(nonce='x"><script>alert(1)</script>', settings=settings)
    assert all('nonce="x&quot;&gt;&lt;script&gt;' in tag for tag in _external_scripts(html))
