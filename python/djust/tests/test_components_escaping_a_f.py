"""Python-rendered components (a–f) HTML-escape the values they interpolate.

Each case builds a component with an html-like value in one field and checks
that the rendered markup carries the escaped form. Values the developer marked
safe (``mark_safe``) pass through unchanged, and URL attributes keep ordinary
links while non-navigation schemes become ``#``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

import django
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        SECRET_KEY="test-secret-key-components-escaping-a-f",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ],
    )
    django.setup()

from django.utils.safestring import mark_safe  # noqa: E402

from djust.components.components.accordion import Accordion  # noqa: E402
from djust.components.components.activity_feed import ActivityFeed  # noqa: E402
from djust.components.components.alert import Alert  # noqa: E402
from djust.components.components.announcement_bar import AnnouncementBar  # noqa: E402
from djust.components.components.app_shell import AppShell  # noqa: E402
from djust.components.components.aspect_ratio import AspectRatio  # noqa: E402
from djust.components.components.audit_log import AuditLog  # noqa: E402
from djust.components.components.avatar import Avatar  # noqa: E402
from djust.components.components.avatar_group import AvatarGroup  # noqa: E402
from djust.components.components.badge import Badge  # noqa: E402
from djust.components.components.breadcrumb import Breadcrumb  # noqa: E402
from djust.components.components.breadcrumb_dropdown import BreadcrumbDropdown  # noqa: E402
from djust.components.components.button import Button  # noqa: E402
from djust.components.components.calendar_heatmap import CalendarHeatmap  # noqa: E402
from djust.components.components.callout import Callout  # noqa: E402
from djust.components.components.card import Card  # noqa: E402
from djust.components.components.carousel import Carousel  # noqa: E402
from djust.components.components.chat_bubble import ChatBubble  # noqa: E402
from djust.components.components.collapsible import Collapsible  # noqa: E402
from djust.components.components.command_palette import CommandPalette  # noqa: E402
from djust.components.components.content_loader import ContentLoader  # noqa: E402
from djust.components.components.context_menu import ContextMenu  # noqa: E402
from djust.components.components.cookie_consent import CookieConsent  # noqa: E402
from djust.components.components.dashboard_grid import DashboardGrid  # noqa: E402
from djust.components.components.data_card_grid import DataCardGrid  # noqa: E402
from djust.components.components.dropdown import Dropdown  # noqa: E402
from djust.components.components.dropdown_menu import DropdownMenu  # noqa: E402
from djust.components.components.error_page import ErrorPage  # noqa: E402
from djust.components.components.fieldset import Fieldset  # noqa: E402
from djust.components.components.file_dropzone import FileDropzone  # noqa: E402
from djust.components.components.filter_bar import FilterBar  # noqa: E402
from djust.components.components.form_group import FormGroup  # noqa: E402

TEXT = "<img src=x onerror=alert(1)>"
TEXT_ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"
ATTR = 'x" onmouseover="y'
ATTR_ESCAPED = "x&quot; onmouseover=&quot;y"
SAFE = "<b>ok</b>"


# Each factory takes one value and puts it in the field under test.
Factory = Callable[[Any], Any]

#: Fields rendered as element content.
TEXT_CASES: List[Tuple[str, Factory]] = [
    (
        "accordion_content",
        lambda v: Accordion(items=[{"id": "a", "title": "T", "content": v}], active="a"),
    ),
    ("alert_icon", lambda v: Alert("msg", icon=v)),
    ("announcement_bar_content", lambda v: AnnouncementBar(content=v)),
    ("app_shell_sidebar", lambda v: AppShell(sidebar=v)),
    ("app_shell_header", lambda v: AppShell(header=v)),
    ("app_shell_content", lambda v: AppShell(content=v)),
    ("aspect_ratio_content", lambda v: AspectRatio(content=v)),
    ("button_icon_left", lambda v: Button("Go", icon=v)),
    ("button_icon_right", lambda v: Button("Go", icon=v, icon_position="right")),
    ("callout_content", lambda v: Callout(content=v)),
    ("card_content", lambda v: Card(content=v)),
    ("card_header", lambda v: Card(content="c", header=v)),
    ("card_footer", lambda v: Card(content="c", footer=v)),
    ("card_image", lambda v: Card(content="c", image=v)),
    ("collapsible_content", lambda v: Collapsible(content=v)),
    ("command_palette_content", lambda v: CommandPalette(content=v)),
    ("content_loader_content", lambda v: ContentLoader(loaded=True, content=v)),
    ("content_loader_placeholder", lambda v: ContentLoader(placeholder=v)),
    ("context_menu_content", lambda v: ContextMenu(content=v)),
    (
        "dashboard_grid_panel_content",
        lambda v: DashboardGrid(panels=[{"id": "p", "title": "P", "content": v}]),
    ),
    ("dropdown_content", lambda v: Dropdown(content=v, is_open=True)),
    ("fieldset_content", lambda v: Fieldset(content=v)),
    ("file_dropzone_max_size", lambda v: FileDropzone(max_size_mb=v)),
    ("filter_bar_content", lambda v: FilterBar(content=v)),
    ("form_group_content", lambda v: FormGroup(content=v)),
]

#: Fields rendered inside a quoted attribute (class fragments, type, fill).
ATTR_CASES: List[Tuple[str, Factory]] = [
    ("alert_variant", lambda v: Alert("msg", variant=v)),
    ("audit_log_column", lambda v: AuditLog(entries=[{v: "1"}], columns=[v])),
    ("badge_variant", lambda v: Badge("b", variant=v)),
    ("badge_size", lambda v: Badge("b", size=v)),
    ("button_variant", lambda v: Button("Go", variant=v)),
    ("button_size", lambda v: Button("Go", size=v)),
    ("button_type", lambda v: Button("Go", type=v)),
    ("calendar_heatmap_color_empty", lambda v: CalendarHeatmap(year=2026, color_empty=v)),
    ("card_variant", lambda v: Card(content="c", variant=v)),
    ("card_padding", lambda v: Card(content="c", padding=v)),
    ("cookie_consent_position", lambda v: CookieConsent(position=v)),
    (
        "dropdown_menu_align",
        lambda v: DropdownMenu(items=[{"label": "A", "event": "a"}], open=True, align=v),
    ),
]

#: Navigation URLs (href).
LINK_CASES: List[Tuple[str, Factory]] = [
    ("breadcrumb_url", lambda v: Breadcrumb(items=[{"label": "Home", "url": v}, "Here"])),
    (
        "breadcrumb_dropdown_url",
        lambda v: BreadcrumbDropdown(items=[{"label": "Home", "url": v}, {"label": "Here"}]),
    ),
    (
        "breadcrumb_dropdown_collapsed_url",
        lambda v: BreadcrumbDropdown(
            items=[
                {"label": "Home", "url": "/"},
                {"label": "Hidden", "url": v},
                {"label": "B", "url": "/b"},
                {"label": "C"},
            ],
            max_visible=3,
        ),
    ),
    ("cookie_consent_privacy_url", lambda v: CookieConsent(privacy_url=v)),
    ("error_page_action_url", lambda v: ErrorPage(action_url=v)),
]

#: Image sources (img src).
IMAGE_CASES: List[Tuple[str, Factory]] = [
    ("activity_feed_avatar", lambda v: ActivityFeed(events=[{"user": "Al", "avatar": v}])),
    ("avatar_src", lambda v: Avatar(src=v, alt="a")),
    ("avatar_group_avatar", lambda v: AvatarGroup(users=[{"name": "Al", "avatar": v}])),
    ("carousel_src", lambda v: Carousel(images=[{"src": v, "alt": "a"}])),
    ("carousel_plain_src", lambda v: Carousel(images=[v])),
    (
        "chat_bubble_avatar",
        lambda v: ChatBubble(message={"name": "Al", "text": "hi", "avatar": v}),
    ),
    ("data_card_grid_image", lambda v: DataCardGrid(items=[{"title": "T", "image": v}])),
]


def _render(factory: Factory, value: Any) -> str:
    return str(factory(value).render())


def _ids(cases: List[Tuple[str, Factory]]) -> List[str]:
    return [name for name, _ in cases]


@pytest.mark.parametrize("name,factory", TEXT_CASES, ids=_ids(TEXT_CASES))
def test_text_value_is_html_escaped(name: str, factory: Factory) -> None:
    out = _render(factory, TEXT)
    assert TEXT not in out
    assert TEXT_ESCAPED in out


@pytest.mark.parametrize("name,factory", TEXT_CASES, ids=_ids(TEXT_CASES))
def test_marked_safe_text_value_passes_through(name: str, factory: Factory) -> None:
    out = _render(factory, mark_safe(SAFE))
    assert SAFE in out


@pytest.mark.parametrize("name,factory", ATTR_CASES, ids=_ids(ATTR_CASES))
def test_attribute_value_is_html_escaped(name: str, factory: Factory) -> None:
    out = _render(factory, ATTR)
    assert ATTR not in out
    assert ATTR_ESCAPED in out


@pytest.mark.parametrize("name,factory", ATTR_CASES, ids=_ids(ATTR_CASES))
def test_plain_attribute_value_is_unchanged(name: str, factory: Factory) -> None:
    out = _render(factory, "plain-value")
    assert "plain-value" in out


@pytest.mark.parametrize("name,factory", LINK_CASES, ids=_ids(LINK_CASES))
def test_link_with_script_scheme_becomes_hash(name: str, factory: Factory) -> None:
    out = _render(factory, "javascript:alert(1)")
    assert "javascript:" not in out
    assert 'href="#"' in out


@pytest.mark.parametrize("name,factory", LINK_CASES, ids=_ids(LINK_CASES))
def test_ordinary_link_is_kept(name: str, factory: Factory) -> None:
    out = _render(factory, "/path?a=1&b=2")
    assert 'href="/path?a=1&amp;b=2"' in out


@pytest.mark.parametrize("name,factory", LINK_CASES, ids=_ids(LINK_CASES))
def test_link_quote_is_escaped(name: str, factory: Factory) -> None:
    out = _render(factory, '/p" onmouseover="y')
    assert 'onmouseover="y' not in out
    assert "&quot;" in out


@pytest.mark.parametrize("name,factory", LINK_CASES, ids=_ids(LINK_CASES))
def test_marked_safe_link_passes_through(name: str, factory: Factory) -> None:
    out = _render(factory, mark_safe("/safe?a=1&b=2"))
    assert 'href="/safe?a=1&b=2"' in out


@pytest.mark.parametrize("name,factory", IMAGE_CASES, ids=_ids(IMAGE_CASES))
def test_image_source_with_script_scheme_becomes_hash(name: str, factory: Factory) -> None:
    out = _render(factory, "javascript:alert(1)")
    assert "javascript:" not in out
    assert 'src="#"' in out


@pytest.mark.parametrize("name,factory", IMAGE_CASES, ids=_ids(IMAGE_CASES))
def test_image_source_keeps_ordinary_and_data_image_urls(name: str, factory: Factory) -> None:
    assert 'src="/img/a.png?s=1&amp;t=2"' in _render(factory, "/img/a.png?s=1&t=2")
    data_uri = "data:image/png;base64,iVBORw0KGgo="
    assert f'src="{data_uri}"' in _render(factory, data_uri)


@pytest.mark.parametrize("name,factory", IMAGE_CASES, ids=_ids(IMAGE_CASES))
def test_image_source_quote_is_escaped(name: str, factory: Factory) -> None:
    out = _render(factory, '/a.png" onerror="y')
    assert 'onerror="y' not in out
    assert "&quot;" in out


def test_content_loader_default_placeholder_is_unchanged() -> None:
    out = str(ContentLoader().render())
    assert '<span class="dj-spinner" role="status" aria-label="Loading"></span>' in out


def test_benign_values_render_unchanged() -> None:
    out = str(Card(content="Hello", header="Head", variant="elevated", padding="lg").render())
    assert 'class="dj-card dj-card-elevated dj-card-p-lg"' in out
    assert '<div class="dj-card-header">Head</div>' in out
    assert '<div class="dj-card-content">Hello</div>' in out
    rendered: Dict[str, str] = {
        "button": str(Button("Go", variant="danger", size="sm", type="submit").render()),
    }
    assert 'class="dj-btn dj-btn-danger dj-btn-sm"' in rendered["button"]
    assert 'type="submit"' in rendered["button"]
