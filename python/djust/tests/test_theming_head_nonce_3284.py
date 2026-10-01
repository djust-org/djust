"""The CSP nonce reaches the anti-flash script (#3284)."""

from __future__ import annotations

import re

import pytest
from django.template import engines
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


class TestThemeHeadNonce:
    def test_explicit_nonce_is_on_the_script(self):
        head = _head(_request(), nonce="abc123")
        assert head.count('<script nonce="abc123">') == 1

    def test_request_csp_nonce_is_used_automatically(self):
        head = _head(_request(nonce="from-django-csp"))
        assert '<script nonce="from-django-csp">' in head

    def test_a_lazy_csp_nonce_object_works(self):
        from django.utils.functional import SimpleLazyObject

        head = _head(_request(nonce=SimpleLazyObject(lambda: "lazy-nonce")))
        assert '<script nonce="lazy-nonce">' in head

    def test_explicit_nonce_wins_over_the_request(self):
        head = _head(_request(nonce="from-request"), nonce="explicit")
        assert 'nonce="explicit"' in head and "from-request" not in head

    def test_empty_explicit_nonce_turns_it_off(self):
        head = _head(_request(nonce="from-request"), nonce="")
        assert "nonce" not in head

    def test_the_nonce_is_escaped(self):
        head = _head(_request(), nonce='x"><script>alert(1)</script>')
        assert 'nonce="x&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in head
        assert "<script>alert(1)" not in head

    def test_inline_style_blocks_carry_it_too(self):
        head = _head(_request(), nonce="n1")
        assert 'nonce="n1"' in head
        for tag in re.findall(r"<style[^>]*>", head):
            assert 'nonce="n1"' in tag

    def test_without_a_nonce_the_output_is_unchanged(self):
        plain = _head(_request())
        assert "nonce" not in plain
        assert "<script>\n(function() {" in plain

    def test_the_script_is_otherwise_identical(self):
        plain = _head(_request())
        with_nonce = _head(_request(), nonce="n1")
        # The only additions are the ``nonce`` attributes and the one
        # ``window.__djust_theme_nonce`` assignment theme.js reads (#3310).
        stripped = re.sub(
            r"\n *(?:/\*[^*]*\*/\n *)?window\.__djust_theme_nonce = \"n1\";", "", with_nonce
        )
        assert stripped.replace(' nonce="n1"', "") == plain

    def test_it_works_in_a_template_with_the_request_in_context(self):
        template = engines["django"].from_string(
            "{% load theme_tags %}{% theme_head nonce=request.csp_nonce %}"
        )
        html = template.render({"request": _request(nonce="tpl-nonce")})
        assert '<script nonce="tpl-nonce">' in html


class TestTheOtherPaths:
    """#3284 was first closed for the tag only; every path that emits the head or
    an inline ``<style>`` carries the nonce."""

    def test_the_theme_mixin_head_carries_it(self):
        from djust.theming.mixins import ThemeMixin

        view = ThemeMixin()
        view.mount(_request(nonce="mixin-nonce"))
        assert '<script nonce="mixin-nonce">' in str(view.theme_head)

    def test_the_theme_mixin_head_has_none_without_one(self):
        from djust.theming.mixins import ThemeMixin

        view = ThemeMixin()
        view.mount(_request())
        assert "nonce" not in str(view.theme_head)

    def test_the_context_processor_head_carries_it(self):
        from djust.theming.context_processors import theme_context

        ctx = theme_context(_request(nonce="cp-nonce"))
        assert '<script nonce="cp-nonce">' in str(ctx["theme_head"])

    def test_theme_css_carries_it(self):
        from djust.theming.templatetags.theme_tags import theme_css

        assert '<style data-djust-theme nonce="css-nonce">' in str(
            theme_css({"request": _request(nonce="css-nonce")})
        )
        assert '<style data-djust-theme nonce="explicit">' in str(
            theme_css({"request": _request()}, nonce="explicit")
        )
        assert "nonce" not in str(theme_css({"request": _request()}))

    def test_framework_overrides_carry_it(self, settings):
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

        from unittest import mock

        with (
            mock.patch.object(theme_tags, "get_theme_manager", return_value=Manager()),
            mock.patch("djust.theming.pack_css_generator.ThemePackCSSGenerator", Gen),
        ):
            html = theme_tags.theme_framework_overrides({"request": _request(nonce="fw")})
        assert '<style data-djust-framework-overrides nonce="fw">' in html
