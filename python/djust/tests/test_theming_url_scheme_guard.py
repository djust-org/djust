"""Theming URL kwargs never render a script-bearing scheme (#F1).

Two policies, by where the URL comes from:

* A URL from app data (nav / sidebar / breadcrumb items, ``url_pattern``, an
  avatar ``src``, the auth and error pages' links) is *neutralised*: a
  ``javascript:`` / ``vbscript:`` / ``data:`` value renders as ``"#"`` and the
  rest of the component renders as before, using the theming denylist.
  One stored bad link must not turn every viewer's page into an error.
* A developer-supplied literal (``theme_button href=``, the ``**attrs``
  passthrough) still raises ``ValueError``: a loud error at development time.
"""

from __future__ import annotations

from collections import UserString

import pytest

from django.http import HttpResponse
from django.template import engines
from django.test import override_settings
from django.urls import path, reverse_lazy
from django.utils.functional import lazy

pytestmark = pytest.mark.theming

BAD = "javascript:alert(1)"
PNG = "data:image/png;base64,iVBORw0KGgo="

urlpatterns = [path("theming-safe/", lambda request: HttpResponse(), name="theming-url-safe")]


class _URLObject:
    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


def _wrapped_url(kind: str, value: str) -> object:
    if kind == "promise":
        return lazy(lambda: value, str)()
    if kind == "user_string":
        return UserString(value)
    return _URLObject(value)


_LIST_TAGS = [
    "{% theme_nav items=items %}",
    "{% theme_nav_group 'Group' items=items %}",
    "{% theme_sidebar_nav sections=sections %}",
    "{% theme_breadcrumb items=items %}",
]


def _render_list_url(source: str, url: object) -> str:
    items = [{"label": "Link", "url": url}, {"label": "Current"}]
    original = [dict(item) for item in items]
    sections = [{"title": "Group", "items": items}]
    html = render(source, items=items, sections=sections)
    assert items == original
    assert items[0]["url"] is url
    assert sections[0]["items"] is items
    return html


@pytest.mark.parametrize("source", _LIST_TAGS)
@pytest.mark.parametrize("kind", ["promise", "url_object", "user_string"])
def test_non_string_item_urls_are_neutralised_without_mutating_caller(source, kind):
    html = _render_list_url(source, _wrapped_url(kind, BAD))
    assert_neutralised(html)
    assert "Link" in html


@pytest.mark.parametrize("source", _LIST_TAGS)
@pytest.mark.parametrize("kind", ["promise", "url_object", "user_string"])
def test_safe_non_string_item_urls_are_escaped_once_without_mutating_caller(source, kind):
    html = _render_list_url(source, _wrapped_url(kind, "/safe/?a=1&b=2"))
    assert 'href="/safe/?a=1&amp;b=2"' in html
    assert "&amp;amp;" not in html


@pytest.mark.parametrize("source", _LIST_TAGS)
def test_reverse_lazy_item_url_is_preserved_without_mutating_caller(source):
    with override_settings(ROOT_URLCONF=__name__):
        html = _render_list_url(source, reverse_lazy("theming-url-safe"))
    assert 'href="/theming-safe/"' in html


def render(source: str, **ctx: object) -> str:
    template = engines["django"].from_string("{% load theme_components %}" + source)
    return template.render(ctx)


def render_pages(source: str, **ctx: object) -> str:
    template = engines["django"].from_string("{% load theme_pages %}" + source)
    return template.render(ctx)


def assert_neutralised(html: str) -> None:
    assert "javascript:" not in html.lower()
    assert "vbscript:" not in html.lower()
    assert 'href="#"' in html or 'action="#"' in html


class TestComponentUrlsNeutralised:
    def test_nav_item(self):
        html = render("{% theme_nav_item 'Home' url %}", url=BAD)
        assert_neutralised(html)
        assert "Home" in html

    def test_nav_items_list(self):
        items = [{"label": "Bad", "url": BAD}, {"label": "Docs", "url": "/docs/"}]
        html = render("{% theme_nav items=items %}", items=items)
        assert_neutralised(html)
        assert "Bad" in html
        assert 'href="/docs/"' in html and "Docs" in html

    def test_caller_items_are_not_modified(self):
        items = [{"label": "Bad", "url": BAD}]
        render("{% theme_nav items=items %}", items=items)
        assert items == [{"label": "Bad", "url": BAD}]

    def test_nav_group_items_list(self):
        items = [{"label": "Bad", "url": BAD}, {"label": "Ok", "url": "/ok/"}]
        html = render("{% theme_nav_group 'G' items=items %}", items=items)
        assert_neutralised(html)
        assert "Bad" in html and 'href="/ok/"' in html

    def test_sidebar_nav_nested_items(self):
        sections = [
            {"title": "Main", "items": [{"label": "Bad", "url": BAD}]},
            {"title": "More", "items": [{"label": "Ok", "url": "/ok/"}]},
        ]
        html = render("{% theme_sidebar_nav sections=sections %}", sections=sections)
        assert_neutralised(html)
        assert "Bad" in html and "Main" in html and 'href="/ok/"' in html
        assert sections[0]["items"][0]["url"] == BAD

    def test_breadcrumb_items(self):
        # The last item is the current page and renders without a link.
        items = [
            {"label": "Home", "url": "/"},
            {"label": "Bad", "url": BAD},
            {"label": "Here", "url": "/here/"},
        ]
        html = render("{% theme_breadcrumb items=items %}", items=items)
        assert_neutralised(html)
        assert "Bad" in html and 'href="/"' in html

    def test_pagination_url_pattern(self):
        html = render(
            "{% theme_pagination current_page=2 total_pages=3 url_pattern=pat %}",
            pat=BAD,
        )
        assert_neutralised(html)
        assert "javascript" not in html.lower()

    def test_pagination_normal_pattern(self):
        html = render(
            "{% theme_pagination current_page=1 total_pages=3 url_pattern=pat %}",
            pat="/items/?q=a&page={}",
        )
        # Escaped once by the template, not twice.
        assert 'href="/items/?q=a&amp;page=2"' in html

    def test_avatar_src(self):
        html = render("{% theme_avatar src=src alt='me' %}", src=BAD)
        assert "javascript:" not in html.lower()
        assert 'src="#"' in html

    def test_avatar_data_text_is_refused(self):
        # Only data:image/... is an image; data:text/html is not.
        html = render("{% theme_avatar src=src %}", src="data:text/html,<script>x</script>")
        assert 'src="#"' in html

    def test_avatar_data_image_renders(self):
        html = render("{% theme_avatar src=src alt='me' %}", src=PNG)
        assert f'src="{PNG}"' in html

    @pytest.mark.parametrize(
        "bad",
        [
            "javascript:alert(1)",
            "  JavaScript:alert(1)",
            "java\tscript:alert(1)",
            "java\nscript:alert(1)",
            "\x01javascript:alert(1)",
            "vbscript:x",
            "data:text/html,<script>alert(1)</script>",
        ],
    )
    def test_evasions_are_neutralised(self, bad):
        html = render("{% theme_nav_item 'Home' url %}", url=bad)
        assert 'href="#"' in html
        assert "alert" not in html

    def test_a_normal_url_still_renders(self):
        html = render("{% theme_nav items=items %}", items=[{"label": "Home", "url": "/"}])
        assert 'href="/"' in html

    def test_a_query_url_is_escaped_once(self):
        html = render("{% theme_nav_item 'S' url %}", url="/s/?a=1&b=2")
        assert 'href="/s/?a=1&amp;b=2"' in html

    def test_an_item_without_a_url_is_fine(self):
        html = render("{% theme_nav items=items %}", items=[{"label": "Home"}])
        assert "Home" in html


class TestDeveloperLiteralsStillRaise:
    def test_button_href(self):
        with pytest.raises(ValueError, match="refuses"):
            render("{% theme_button 'Go' href=u %}", u=BAD)

    def test_attrs_passthrough_href(self):
        with pytest.raises(ValueError, match="refuses"):
            render("{% theme_input name='q' formaction=u %}", u=BAD)


class TestPageUrlsNeutralised:
    def test_login_page_action(self):
        html = render_pages("{% theme_login_page action=u %}", u=BAD)
        assert_neutralised(html)
        assert 'action="#"' in html

    def test_login_page_link(self):
        html = render_pages(
            "{% theme_login_page forgot_password_url=u register_url='/reg/' %}", u=BAD
        )
        assert_neutralised(html)
        assert 'href="/reg/"' in html

    def test_register_page_terms(self):
        html = render_pages("{% theme_register_page terms_url=u %}", u=BAD)
        assert_neutralised(html)
        assert "Terms of Service" in html

    def test_password_reset_page(self):
        html = render_pages("{% theme_password_reset_page login_url=u %}", u=BAD)
        assert_neutralised(html)

    def test_password_confirm_page(self):
        html = render_pages("{% theme_password_confirm_page action=u %}", u=BAD)
        assert 'action="#"' in html
        assert "javascript:" not in html.lower()

    def test_404_page(self):
        html = render_pages("{% theme_404_page home_url=u %}", u=BAD)
        assert_neutralised(html)
        assert "Go home" in html

    def test_500_page_retry(self):
        html = render_pages("{% theme_500_page retry_url=u home_url='/' %}", u=BAD)
        assert_neutralised(html)
        assert 'href="/"' in html

    def test_403_page(self):
        html = render_pages("{% theme_403_page back_url=u %}", u=BAD)
        assert_neutralised(html)

    def test_empty_state_page(self):
        html = render_pages("{% theme_empty_state_page cta_url=u cta_text='Add' %}", u=BAD)
        assert_neutralised(html)
        assert "Add" in html

    def test_a_normal_action_still_renders(self):
        html = render_pages('{% theme_404_page home_url="/" %}')
        assert 'href="/"' in html


@pytest.mark.parametrize("url", ["sms:+15551234567", "geo:40,-70", "myapp://record/42"])
def test_non_script_schemes_are_preserved(url):
    html = _render_list_url("{% theme_nav items=items %}", url)
    assert f'href="{url}"' in html


def test_data_image_is_neutralised_for_links():
    html = render("{% theme_nav_item 'Image' url %}", url=PNG)
    assert 'href="#"' in html


@pytest.mark.parametrize("source", _LIST_TAGS)
@pytest.mark.parametrize("collection", [list, tuple, iter, lambda items: map(lambda x: x, items)])
def test_object_items_and_iterables_are_neutralised(source, collection):
    from types import SimpleNamespace

    item = SimpleNamespace(label="Object link", url=BAD, badge="7", active=True)
    items = collection([item, {"label": "Current"}])
    html = render(source, items=items, sections=iter([{"title": "Group", "items": items}]))
    assert_neutralised(html)
    assert "Object link" in html
    assert item.url == BAD
    assert item.badge == "7"


@pytest.mark.parametrize("collection", [tuple, iter])
def test_object_sidebar_sections_are_neutralised(collection):
    from types import SimpleNamespace

    item = SimpleNamespace(label="Object link", url=BAD)
    items = iter([item])
    section = SimpleNamespace(title="Object section", items=items)
    html = render("{% theme_sidebar_nav sections=sections %}", sections=collection([section]))
    assert_neutralised(html)
    assert "Object section" in html and "Object link" in html
    assert section.items is items
    assert item.url == BAD


@pytest.mark.parametrize("current,total", [(105, 110), (107, 110), (100, 106)])
def test_pagination_checks_formatted_urls(current, total):
    # At page 106, chr(106) supplies the missing 'j'.
    html = render(
        "{% theme_pagination current_page=current total_pages=total url_pattern=pat %}",
        current=current,
        total=total,
        pat="{:c}avascript:alert(1)",
    )
    assert 'href="#"' in html
    assert "javascript:" not in html.lower()


@pytest.mark.parametrize(
    "current,total,pattern,field",
    [
        (106, 110, "{:c}avascript:alert(1)", "page_range"),
        (105, 110, "{:c}avascript:alert(1)", "next_url"),
        (107, 110, "{:c}avascript:alert(1)", "prev_url"),
        (100, 106, "{:c}avascript:alert(1)", "last_url"),
        (100, 110, "{:c}javascript:alert(1)", "first_url"),
    ],
)
def test_all_pagination_context_urls_are_checked(monkeypatch, current, total, pattern, field):
    from django.template import Context
    from djust.theming.templatetags import theme_components

    captured = {}

    class CaptureTemplate:
        def render(self, ctx):
            captured.update(ctx)
            return ""

    monkeypatch.setattr(
        theme_components, "resolve_component_template", lambda *args: CaptureTemplate()
    )
    theme_components.theme_pagination(Context(), current, total, pattern)
    if field == "page_range":
        assert next(page["url"] for page in captured[field] if page["number"] == 106) == "#"
    else:
        assert captured[field] == "#"


def test_custom_url_attribute_and_other_object_attributes_are_preserved():
    from dataclasses import dataclass
    from djust.theming.templatetags.theme_components import _neutralise_item_urls

    @dataclass(frozen=True)
    class Item:
        destination: str
        label: str
        badge: str

    item = Item(BAD, "Link", "7")
    checked = _neutralise_item_urls([item], "destination")[0]
    assert checked.destination == "#"
    assert checked.label == item.label and checked.badge == item.badge
    assert item.destination == BAD


@pytest.mark.parametrize("items", ["javascript:alert(1)", b"javascript:alert(1)"])
def test_strings_are_not_materialised_as_items(items):
    from djust.theming.templatetags.theme_components import _neutralise_item_urls

    assert _neutralise_item_urls(items) is items


@pytest.mark.django_db
def test_queryset_model_items_are_neutralised():
    from django.contrib.auth import get_user_model
    from django.db.models import Value

    user = get_user_model().objects.create(username="Nav model")
    items = (
        get_user_model()
        .objects.filter(pk=user.pk)
        .annotate(url=Value(BAD), label=Value("Model link"))
    )
    html = render("{% theme_nav items=items %}", items=items)
    assert_neutralised(html)
    assert "Model link" in html
    assert items[0].url == BAD


@pytest.mark.parametrize("source", _LIST_TAGS)
def test_namedtuple_items_retain_their_attributes(source):
    from collections import namedtuple

    Item = namedtuple("Item", "label url")
    item = Item("Tuple link", BAD)
    items = [item, {"label": "Current"}]
    html = render(source, items=items, sections=[{"title": "Group", "items": items}])
    assert_neutralised(html)
    assert "Tuple link" in html
    assert item.url == BAD
