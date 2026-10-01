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
        assert with_nonce.replace(' nonce="n1"', "") == plain

    def test_it_works_in_a_template_with_the_request_in_context(self):
        template = engines["django"].from_string(
            "{% load theme_tags %}{% theme_head nonce=request.csp_nonce %}"
        )
        html = template.render({"request": _request(nonce="tpl-nonce")})
        assert '<script nonce="tpl-nonce">' in html
