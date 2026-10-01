"""``theme_head`` hands theme.js what it needs to nonce live preset switches (#3310).

theme.js builds ``<style id="djust-theme-css">`` on a live preset switch. That
element is blocked under ``style-src`` without ``'unsafe-inline'`` unless it
carries the page's nonce, so ``theme_head`` must (a) publish the nonce to
theme.js and (b) give its own theme element the id theme.js looks up, so the
switch updates it in place. The JS half is pinned in
``tests/js/theming_live_switch_csp_nonce_3310.test.js``.
"""

from __future__ import annotations

import re

import pytest
from django.test import RequestFactory

pytestmark = pytest.mark.theming


def _request(nonce=None):
    request = RequestFactory().get("/")
    request.session = {}
    if nonce is not None:
        request.csp_nonce = nonce
    return request


def _head(request=None, **kwargs) -> str:
    from djust.theming.templatetags.theme_tags import theme_head

    return str(theme_head({"request": request}, **kwargs))


class TestNonceReachesThemeJs:
    def test_the_nonce_is_published_as_a_js_global(self):
        head = _head(_request(), nonce="abc123")
        assert 'window.__djust_theme_nonce = "abc123";' in head

    def test_the_global_sits_inside_the_nonced_script(self):
        head = _head(_request(), nonce="abc123")
        script = re.search(r'<script nonce="abc123">(.*?)</script>', head, re.S)
        assert script and "window.__djust_theme_nonce" in script.group(1)

    def test_request_csp_nonce_is_used(self):
        assert 'window.__djust_theme_nonce = "from-csp";' in _head(_request(nonce="from-csp"))

    def test_no_nonce_no_global(self):
        assert "__djust_theme_nonce" not in _head(_request())
        assert "__djust_theme_nonce" not in _head(_request(nonce="x"), nonce="")

    def test_a_hostile_nonce_cannot_break_out_of_the_script(self):
        head = _head(_request(), nonce='x";</script><script>alert(1)</script>')
        assert "</script><script>alert(1)" not in head
        line = next(ln for ln in head.splitlines() if "__djust_theme_nonce" in ln)
        assert "<" not in line and ">" not in line


class TestThemeElementHasTheId:
    def test_plain_inline_style_carries_the_id(self, settings):
        settings.LIVEVIEW_CONFIG = {"theme": {"critical_css": False}}
        head = _head(_request(), nonce="n1")
        assert re.search(r'<style id="djust-theme-css" data-djust-theme nonce="n1">', head)

    def test_link_variant_carries_the_id(self):
        from django.test import override_settings
        from django.urls import clear_url_caches

        with override_settings(
            ROOT_URLCONF="python.djust.tests.test_theming_live_switch_nonce_3310"
        ):
            clear_url_caches()
            head = _head(_request(), link_css=True)
        clear_url_caches()
        assert re.search(
            r'<link rel="stylesheet" href="[^"]+" id="djust-theme-css" data-djust-theme>', head
        )


from django.urls import include, path  # noqa: E402

urlpatterns = [path("djust-theming/", include("djust.theming.urls"))]


class TestLazyNonceObject:
    """``{% theme_head nonce=request.csp_nonce %}`` hands django-csp's
    ``SimpleLazyObject`` straight in; ``json.dumps`` rejects one."""

    @staticmethod
    def _lazy(value="lazy-n"):
        from django.utils.functional import SimpleLazyObject

        return SimpleLazyObject(lambda: value)

    def test_theme_head_accepts_a_lazy_explicit_nonce(self):
        head = _head(_request(), nonce=self._lazy())
        assert 'window.__djust_theme_nonce = "lazy-n";' in head
        assert '<script nonce="lazy-n">' in head

    def test_documented_template_form_works_with_a_lazy_request_nonce(self):
        from django.template import engines

        template = engines["django"].from_string(
            "{% load theme_tags %}{% theme_head nonce=request.csp_nonce %}"
        )
        html = template.render({"request": _request(nonce=self._lazy("tpl-lazy"))})
        assert 'window.__djust_theme_nonce = "tpl-lazy";' in html

    def test_theme_css_accepts_a_lazy_nonce(self):
        from djust.theming.templatetags.theme_tags import theme_css

        out = str(theme_css({"request": _request()}, nonce=self._lazy()))
        assert '<style data-djust-theme nonce="lazy-n">' in out

    def test_framework_overrides_accepts_a_lazy_nonce(self):
        from unittest import mock

        from djust.theming.templatetags import theme_tags

        class State:
            pack = "anything"

        class Manager:
            def get_state(self):
                return State()

        class Gen:
            def __init__(self, pack_name):
                pass

            def _generate_framework_css(self):
                return ".a{color:red}"

        with (
            mock.patch.object(theme_tags, "get_theme_manager", return_value=Manager()),
            mock.patch("djust.theming.pack_css_generator.ThemePackCSSGenerator", Gen),
        ):
            html = theme_tags.theme_framework_overrides({"request": _request()}, nonce=self._lazy())
        assert '<style data-djust-framework-overrides nonce="lazy-n">' in html

    def test_an_empty_lazy_nonce_means_none(self):
        head = _head(_request(), nonce=self._lazy(""))
        assert "nonce" not in head
