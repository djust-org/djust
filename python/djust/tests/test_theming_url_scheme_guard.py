"""Theming URL kwargs refuse script-bearing schemes (#F1).

``theme_button`` and the ``**attrs`` passthrough already validate a URL with the
module's ``_check_url``; the *other* tags that render a caller-supplied URL into
an ``href``/``src``/``action`` did not, so a ``javascript:`` URL an app filled
from its own data (a CMS page, a user profile link) reached a live anchor. These
tests pin the guard onto every such tag: a ``javascript:`` / ``vbscript:`` /
``data:`` value is refused, a normal URL still renders.
"""

from __future__ import annotations

import pytest

from django.template import engines

pytestmark = pytest.mark.theming

BAD = "javascript:alert(1)"


def render(source: str, **ctx: object) -> str:
    template = engines["django"].from_string("{% load theme_components %}" + source)
    return template.render(ctx)


def render_pages(source: str, **ctx: object) -> str:
    template = engines["django"].from_string("{% load theme_pages %}" + source)
    return template.render(ctx)


class TestComponentUrlsRefused:
    def test_nav_item(self):
        with pytest.raises(ValueError, match="refuses"):
            render("{% theme_nav_item 'Home' url %}", url=BAD)

    def test_nav_items_list(self):
        with pytest.raises(ValueError, match="refuses"):
            render("{% theme_nav items=items %}", items=[{"label": "x", "url": BAD}])

    def test_nav_group_items_list(self):
        with pytest.raises(ValueError, match="refuses"):
            render("{% theme_nav_group 'G' items=items %}", items=[{"label": "x", "url": BAD}])

    def test_sidebar_nav_nested_items(self):
        with pytest.raises(ValueError, match="refuses"):
            render(
                "{% theme_sidebar_nav sections=sections %}",
                sections=[{"title": "t", "items": [{"label": "x", "url": BAD}]}],
            )

    def test_breadcrumb_items(self):
        with pytest.raises(ValueError, match="refuses"):
            render("{% theme_breadcrumb items=items %}", items=[{"label": "x", "url": BAD}])

    def test_pagination_url_pattern(self):
        with pytest.raises(ValueError, match="refuses"):
            render(
                "{% theme_pagination current_page=1 total_pages=3 url_pattern=pat %}",
                pat=BAD,
            )

    def test_avatar_src(self):
        with pytest.raises(ValueError, match="refuses"):
            render("{% theme_avatar src=src %}", src=BAD)

    @pytest.mark.parametrize("bad", ["javascript:alert(1)", "  JavaScript:alert(1)", "vbscript:x"])
    def test_evasions_are_refused(self, bad):
        with pytest.raises(ValueError, match="refuses"):
            render("{% theme_nav_item 'Home' url %}", url=bad)

    def test_a_normal_url_still_renders(self):
        html = render("{% theme_nav items=items %}", items=[{"label": "Home", "url": "/"}])
        assert 'href="/"' in html

    def test_an_item_without_a_url_is_fine(self):
        html = render("{% theme_nav items=items %}", items=[{"label": "Home"}])
        assert "Home" in html


class TestPageUrlsRefused:
    def test_login_page_action(self):
        with pytest.raises(ValueError, match="refuses"):
            render_pages("{% theme_login_page action=u %}", u=BAD)

    def test_login_page_link(self):
        with pytest.raises(ValueError, match="refuses"):
            render_pages("{% theme_login_page forgot_password_url=u %}", u=BAD)

    def test_register_page_terms(self):
        with pytest.raises(ValueError, match="refuses"):
            render_pages("{% theme_register_page terms_url=u %}", u=BAD)

    def test_password_reset_page(self):
        with pytest.raises(ValueError, match="refuses"):
            render_pages("{% theme_password_reset_page login_url=u %}", u=BAD)

    def test_password_confirm_page(self):
        with pytest.raises(ValueError, match="refuses"):
            render_pages("{% theme_password_confirm_page action=u %}", u=BAD)

    def test_404_page(self):
        with pytest.raises(ValueError, match="refuses"):
            render_pages("{% theme_404_page home_url=u %}", u=BAD)

    def test_500_page_retry(self):
        with pytest.raises(ValueError, match="refuses"):
            render_pages("{% theme_500_page retry_url=u %}", u=BAD)

    def test_403_page(self):
        with pytest.raises(ValueError, match="refuses"):
            render_pages("{% theme_403_page back_url=u %}", u=BAD)

    def test_empty_state_page(self):
        with pytest.raises(ValueError, match="refuses"):
            render_pages("{% theme_empty_state_page cta_url=u %}", u=BAD)

    def test_a_normal_action_still_renders(self):
        html = render_pages('{% theme_404_page home_url="/" %}')
        assert 'href="/"' in html
