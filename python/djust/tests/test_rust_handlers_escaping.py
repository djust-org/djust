"""Rust-engine component handlers HTML-escape context values.

Each case calls a handler from ``djust.components.rust_handlers`` directly, the
way the Rust template engine does (``render(args, context)`` for inline tags,
``render(args, content, context)`` for block tags), with the value under test
supplied through the template context.

- Text and attribute values are HTML-escaped; values marked safe pass through.
- ``href`` values go through the URL helper: non-navigation schemes
  (``javascript:``) become ``#``; relative and ``https`` URLs are kept.
- ``<img src>`` values go through the same helper with ``image=True``, which
  also keeps inline ``data:image/...`` URIs.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest
from django.utils.safestring import mark_safe

from djust.components import rust_handlers as rh

MARKUP_ATTR = 'x" onmouseover="y'
MARKUP_TEXT = "<img src=x onerror=alert(1)>"


def _call(handler: Any, block: bool, args: list[str], context: dict[str, object]) -> str:
    if block:
        return str(handler.render(args, "", context))
    return str(handler.render(args, context))


# (id, handler, is_block, args, context_builder(value) -> context)
HREF_CASES: list[tuple[str, Any, bool, list[str], Callable[[object], dict[str, object]]]] = [
    (
        "breadcrumb",
        rh.BreadcrumbHandler(),
        False,
        ["items=items"],
        lambda v: {"items": [{"label": "Home", "url": v}, {"label": "Here"}]},
    ),
    (
        "sidebar_item",
        rh.SidebarItemHandler(),
        True,
        ["label='Docs'", "href=u"],
        lambda v: {"u": v},
    ),
    (
        "nav_menu_brand",
        rh.NavMenuHandler(),
        True,
        ["brand='Site'", "brand_href=u"],
        lambda v: {"u": v},
    ),
    (
        "nav_item",
        rh.NavItemHandler(),
        True,
        ["label='Docs'", "href=u"],
        lambda v: {"u": v},
    ),
    (
        "source_citation",
        rh.SourceCitationHandler(),
        False,
        ["index=1", "title='Doc'", "url=u"],
        lambda v: {"u": v},
    ),
    (
        "cookie_consent",
        rh.CookieConsentHandler(),
        True,
        ["privacy_url=u"],
        lambda v: {"u": v},
    ),
    (
        "error_page",
        rh.ErrorPageHandler(),
        False,
        ["code=404", "action_url=u"],
        lambda v: {"u": v},
    ),
    (
        "breadcrumb_dropdown",
        rh.BreadcrumbDropdownHandler(),
        False,
        ["items=items"],
        lambda v: {"items": [{"label": "Home", "url": v}, {"label": "Here"}]},
    ),
    (
        "breadcrumb_dropdown_collapsed",
        rh.BreadcrumbDropdownHandler(),
        False,
        ["items=items", "max_visible=2"],
        lambda v: {
            "items": [
                {"label": "A", "url": "/a"},
                {"label": "B", "url": v},
                {"label": "C", "url": "/c"},
                {"label": "D"},
            ]
        },
    ),
]

IMG_CASES: list[tuple[str, Any, bool, list[str], Callable[[object], dict[str, object]]]] = [
    ("avatar", rh.AvatarHandler(), False, ["src=u", "alt='A'"], lambda v: {"u": v}),
    ("responsive_image_src", rh.ResponsiveImageHandler(), False, ["src=u"], lambda v: {"u": v}),
    (
        "responsive_image_placeholder",
        rh.ResponsiveImageHandler(),
        False,
        ["src='/a.png'", "placeholder=u"],
        lambda v: {"u": v},
    ),
    (
        "avatar_group",
        rh.AvatarGroupHandler(),
        False,
        ["users=users"],
        lambda v: {"users": [{"name": "Ann Lee", "avatar": v}]},
    ),
    (
        "chat_bubble",
        rh.ChatBubbleHandler(),
        False,
        ["message=m"],
        lambda v: {"m": {"name": "Ann", "text": "hi", "avatar": v}},
    ),
    (
        "presence_avatars",
        rh.PresenceAvatarsHandler(),
        False,
        ["users=users"],
        lambda v: {"users": [{"name": "Ann", "avatar": v}]},
    ),
    (
        "mentions_input",
        rh.MentionsInputHandler(),
        False,
        ["users=users"],
        lambda v: {"users": [{"id": 1, "name": "Ann", "avatar": v}]},
    ),
    (
        "sortable_grid",
        rh.SortableGridHandler(),
        False,
        ["items=items"],
        lambda v: {"items": [{"id": 1, "label": "A", "thumbnail": v}]},
    ),
    ("image_cropper", rh.ImageCropperHandler(), False, ["src=u"], lambda v: {"u": v}),
    (
        "lightbox",
        rh.LightboxHandler(),
        False,
        ["images=imgs", "open=True"],
        lambda v: {"imgs": [{"src": v, "alt": "A"}]},
    ),
    (
        "org_chart",
        rh.OrgChartHandler(),
        False,
        ["nodes=nodes"],
        lambda v: {"nodes": [{"id": "1", "name": "Ann", "avatar": v}]},
    ),
    (
        "live_indicator",
        rh.LiveIndicatorHandler(),
        False,
        ["user=u"],
        lambda v: {"u": {"name": "Ann", "avatar": v}},
    ),
    (
        "activity_feed",
        rh.ActivityFeedHandler(),
        False,
        ["events=ev"],
        lambda v: {"ev": [{"user": "Ann", "action": "did", "avatar": v}]},
    ),
    (
        "image_upload_preview",
        rh.ImageUploadPreviewHandler(),
        False,
        ["previews=p"],
        lambda v: {"p": [v]},
    ),
    (
        "data_card_grid",
        rh.DataCardGridHandler(),
        False,
        ["items=items"],
        lambda v: {"items": [{"title": "T", "image": v}]},
    ),
]

URL_CASES = HREF_CASES + IMG_CASES
URL_IDS = [c[0] for c in URL_CASES]


@pytest.mark.parametrize("case", URL_CASES, ids=URL_IDS)
def test_javascript_url_becomes_hash(case: Any) -> None:
    _id, handler, block, args, ctx = case
    html = _call(handler, block, args, ctx("javascript:alert(1)"))
    assert '="javascript:' not in html
    assert '="#"' in html


@pytest.mark.parametrize("case", URL_CASES, ids=URL_IDS)
def test_url_attribute_is_html_escaped(case: Any) -> None:
    _id, handler, block, args, ctx = case
    html = _call(handler, block, args, ctx("/p" + MARKUP_ATTR))
    assert MARKUP_ATTR not in html
    assert "&quot;" in html


@pytest.mark.parametrize("case", URL_CASES, ids=URL_IDS)
def test_relative_url_is_kept(case: Any) -> None:
    _id, handler, block, args, ctx = case
    html = _call(handler, block, args, ctx("/path?a=1"))
    assert '"/path?a=1"' in html


@pytest.mark.parametrize("case", URL_CASES, ids=URL_IDS)
def test_https_url_is_kept(case: Any) -> None:
    _id, handler, block, args, ctx = case
    html = _call(handler, block, args, ctx("https://example.com/x.png"))
    assert '"https://example.com/x.png"' in html


@pytest.mark.parametrize("case", URL_CASES, ids=URL_IDS)
def test_marked_safe_url_passes_through(case: Any) -> None:
    _id, handler, block, args, ctx = case
    html = _call(handler, block, args, ctx(mark_safe("/ok?a=1&b=2")))
    assert '"/ok?a=1&b=2"' in html


@pytest.mark.parametrize("case", IMG_CASES, ids=[c[0] for c in IMG_CASES])
def test_inline_image_data_uri_is_kept(case: Any) -> None:
    _id, handler, block, args, ctx = case
    html = _call(handler, block, args, ctx("data:image/png;base64,AAAA"))
    assert 'src="data:image/png;base64,AAAA"' in html


@pytest.mark.parametrize("case", HREF_CASES, ids=[c[0] for c in HREF_CASES])
def test_data_uri_link_becomes_hash(case: Any) -> None:
    _id, handler, block, args, ctx = case
    html = _call(handler, block, args, ctx("data:text/html,hello"))
    assert '="data:text/html' not in html
    assert 'href="#"' in html


# --- text values -------------------------------------------------------------


def _data_table_stats_ctx(v: object) -> dict[str, object]:
    return {
        "r": [{"a": 1}],
        "c": [{"key": "a", "label": "A", "stats": True}],
        "s": {"a": {"count": 1, "min": v, "max": 2, "avg": 1.5}},
    }


TEXT_CASES: list[tuple[str, Any, bool, list[str], Callable[[object], dict[str, object]]]] = [
    (
        "data_table_stats",
        rh.DataTableHandler(),
        False,
        ["rows=r", "columns=c", "column_stats=s"],
        _data_table_stats_ctx,
    ),
    (
        "meter_legend_value",
        rh.MeterHandler(),
        False,
        ["segments=segs"],
        lambda v: {"segs": [{"label": "Used", "value": v}]},
    ),
]
TEXT_IDS = [c[0] for c in TEXT_CASES]


@pytest.mark.parametrize("case", TEXT_CASES, ids=TEXT_IDS)
def test_text_value_is_html_escaped(case: Any) -> None:
    _id, handler, block, args, ctx = case
    html = _call(handler, block, args, ctx(MARKUP_TEXT))
    assert MARKUP_TEXT not in html
    assert "&lt;img" in html


@pytest.mark.parametrize("case", TEXT_CASES, ids=TEXT_IDS)
def test_marked_safe_text_passes_through(case: Any) -> None:
    _id, handler, block, args, ctx = case
    html = _call(handler, block, args, ctx(mark_safe("<b>ok</b>")))
    assert "<b>ok</b>" in html


@pytest.mark.parametrize("case", TEXT_CASES, ids=TEXT_IDS)
def test_numeric_text_value_unchanged(case: Any) -> None:
    _id, handler, block, args, ctx = case
    html = _call(handler, block, args, ctx(7))
    assert ">7<" in html
