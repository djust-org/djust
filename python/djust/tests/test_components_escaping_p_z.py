"""Python-rendered components (p–z) HTML-escape the values they interpolate.

Each case builds a component with an html-like value in one field and checks
that the rendered markup carries the escaped form. Values the developer marked
safe (``mark_safe``) pass through unchanged, and URL attributes keep ordinary
links while non-navigation schemes become ``#``.
"""

from __future__ import annotations

from typing import Any, Callable, List, Tuple

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
        SECRET_KEY="test-secret-key-components-escaping-p-z",
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

from djust.components.components.page_alert import PageAlert  # noqa: E402
from djust.components.components.page_header import PageHeader  # noqa: E402
from djust.components.components.popover import Popover  # noqa: E402
from djust.components.components.presence_avatars import PresenceAvatars  # noqa: E402
from djust.components.components.progress import Progress  # noqa: E402
from djust.components.components.progress_circle import ProgressCircle  # noqa: E402
from djust.components.components.resizable_panel import ResizablePanel  # noqa: E402
from djust.components.components.responsive_image import ResponsiveImage  # noqa: E402
from djust.components.components.rich_select import RichSelect  # noqa: E402
from djust.components.components.scroll_area import ScrollArea  # noqa: E402
from djust.components.components.segmented_progress import SegmentedProgress  # noqa: E402
from djust.components.components.sheet import Sheet  # noqa: E402
from djust.components.components.sidebar import Sidebar  # noqa: E402
from djust.components.components.sortable_grid import SortableGrid  # noqa: E402
from djust.components.components.source_citation import SourceCitation  # noqa: E402
from djust.components.components.sparkline import Sparkline  # noqa: E402
from djust.components.components.spinner import Spinner  # noqa: E402
from djust.components.components.split_pane import SplitPane  # noqa: E402
from djust.components.components.stat_card import StatCard  # noqa: E402
from djust.components.components.status_dot import StatusDot  # noqa: E402
from djust.components.components.status_indicator import StatusIndicator  # noqa: E402
from djust.components.components.sticky_header import StickyHeader  # noqa: E402
from djust.components.components.tabs import Tabs  # noqa: E402
from djust.components.components.tag import Tag  # noqa: E402
from djust.components.components.timeline import Timeline  # noqa: E402
from djust.components.components.toolbar import Toolbar  # noqa: E402
from djust.components.components.tooltip import Tooltip  # noqa: E402

TEXT = "<img src=x onerror=alert(1)>"
ATTR = 'x" onmouseover="y'
SAFE = "<b>ok</b>"

Factory = Callable[[Any], Any]


def _render(component: Any) -> str:
    return str(component.render())


# Fields that are placed in element content (including slots that accept
# markup through mark_safe).
TEXT_CASES: List[Tuple[str, Factory]] = [
    ("page_header_actions", lambda v: PageHeader(title="T", actions=v)),
    ("popover_content", lambda v: Popover(content=v)),
    ("resizable_panel_content", lambda v: ResizablePanel(content=v)),
    ("scroll_area_content", lambda v: ScrollArea(content=v)),
    ("sheet_content", lambda v: Sheet(content=v)),
    ("sidebar_content", lambda v: Sidebar(content=v)),
    ("split_pane_left", lambda v: SplitPane(left=v)),
    ("split_pane_right", lambda v: SplitPane(right=v)),
    ("stat_card_icon", lambda v: StatCard(label="L", value="1", icon=v)),
    ("sticky_header_content", lambda v: StickyHeader(content=v)),
    ("tabs_content", lambda v: Tabs(tabs=[{"id": "a", "label": "A"}], content=v)),
    ("timeline_content", lambda v: Timeline(content=v)),
    ("timeline_item_content", lambda v: Timeline(items=[{"title": "T", "content": v}])),
    ("toolbar_content", lambda v: Toolbar(content=v)),
    ("tooltip_content", lambda v: Tooltip(text="tip", content=v)),
]

# Fields that are placed inside a quoted attribute (class / title / style).
ATTR_CASES: List[Tuple[str, Factory]] = [
    ("page_alert_type", lambda v: PageAlert(message="m", type=v)),
    ("progress_variant", lambda v: Progress(value=1, variant=v)),
    ("progress_size", lambda v: Progress(value=1, size=v)),
    ("progress_circle_size", lambda v: ProgressCircle(value=1, size=v)),
    ("progress_circle_color", lambda v: ProgressCircle(value=1, color=v)),
    ("segmented_progress_size", lambda v: SegmentedProgress(steps=["a"], size=v)),
    ("sparkline_stroke_width", lambda v: Sparkline(data=[1, 2, 3], stroke_width=v)),
    ("spinner_size", lambda v: Spinner(size=v)),
    ("spinner_variant", lambda v: Spinner(variant=v)),
    ("split_pane_initial", lambda v: SplitPane(initial=v)),
    ("stat_card_variant", lambda v: StatCard(label="L", value="1", variant=v)),
    ("stat_card_trend", lambda v: StatCard(label="L", value="1", trend=v)),
    ("status_dot_variant", lambda v: StatusDot("running", variant=v)),
    ("status_dot_size", lambda v: StatusDot("running", size=v)),
    ("status_dot_animate", lambda v: StatusDot("running", animate=v)),
    ("status_dot_tooltip", lambda v: StatusDot("running", tooltip=v)),
    ("status_indicator_size", lambda v: StatusIndicator(status="online", size=v)),
    ("tag_variant", lambda v: Tag("x", variant=v)),
    ("tag_size", lambda v: Tag("x", size=v)),
]

# URL-bearing attributes: (name, factory, attribute name).
URL_CASES: List[Tuple[str, Factory, str]] = [
    (
        "presence_avatars_avatar",
        lambda u: PresenceAvatars(users=[{"name": "Ann", "avatar": u}]),
        "src",
    ),
    ("responsive_image_src", lambda u: ResponsiveImage(src=u, alt="a"), "src"),
    (
        "responsive_image_placeholder",
        lambda u: ResponsiveImage(src="/static/a.jpg", alt="a", placeholder=u),
        "src",
    ),
    (
        "rich_select_image",
        lambda u: RichSelect(name="n", options=[{"value": "a", "label": "A", "image": u}]),
        "src",
    ),
    ("sidebar_item_href", lambda u: Sidebar(items=[{"label": "L", "href": u}]), "href"),
    (
        "sortable_grid_thumbnail",
        lambda u: SortableGrid(items=[{"id": "1", "label": "L", "thumbnail": u}]),
        "src",
    ),
    ("source_citation_url", lambda u: SourceCitation(title="T", url=u), "href"),
]


@pytest.mark.parametrize("name,factory", TEXT_CASES, ids=[c[0] for c in TEXT_CASES])
def test_content_value_is_html_escaped(name: str, factory: Factory) -> None:
    out = _render(factory(TEXT))
    assert TEXT not in out
    assert "&lt;img src=x onerror=alert(1)&gt;" in out


@pytest.mark.parametrize("name,factory", TEXT_CASES, ids=[c[0] for c in TEXT_CASES])
def test_marked_safe_content_passes_through(name: str, factory: Factory) -> None:
    out = _render(factory(mark_safe(SAFE)))
    assert SAFE in out
    assert "&lt;b&gt;" not in out


@pytest.mark.parametrize("name,factory", ATTR_CASES, ids=[c[0] for c in ATTR_CASES])
def test_attribute_value_is_html_escaped(name: str, factory: Factory) -> None:
    out = _render(factory(ATTR))
    assert ATTR not in out
    assert "x&quot; onmouseover=&quot;y" in out


@pytest.mark.parametrize("name,factory,attr", URL_CASES, ids=[c[0] for c in URL_CASES])
def test_non_navigation_url_scheme_becomes_hash(name: str, factory: Factory, attr: str) -> None:
    out = _render(factory("javascript:alert(1)"))
    assert f'{attr}="#"' in out
    assert f'{attr}="javascript:' not in out


@pytest.mark.parametrize("name,factory,attr", URL_CASES, ids=[c[0] for c in URL_CASES])
def test_ordinary_url_is_kept(name: str, factory: Factory, attr: str) -> None:
    out = _render(factory("/path?a=1"))
    assert f'{attr}="/path?a=1"' in out


@pytest.mark.parametrize("name,factory,attr", URL_CASES, ids=[c[0] for c in URL_CASES])
def test_url_quote_characters_are_escaped(name: str, factory: Factory, attr: str) -> None:
    out = _render(factory('/a" onerror="b'))
    assert '/a" onerror="b' not in out
    assert "/a&quot; onerror=&quot;b" in out


def test_inline_data_image_is_kept_for_img_src() -> None:
    uri = "data:image/png;base64,AAAA"
    out = _render(ResponsiveImage(src=uri, alt="a"))
    assert f'src="{uri}"' in out


def test_plain_values_render_unchanged() -> None:
    assert 'class="dj-tag dj-tag-success dj-tag-sm"' in _render(
        Tag("x", variant="success", size="sm")
    )
    assert 'class="dj-spinner dj-spinner-lg dj-spinner-primary"' in _render(
        Spinner(size="lg", variant="primary")
    )
    assert 'title="Agent is up"' in _render(StatusDot("running", tooltip="Agent is up"))
    assert 'style="width:30%"' in _render(SplitPane(initial=30))
    assert '<div class="popover-content">plain words</div>' in _render(
        Popover(content="plain words")
    )
    assert 'href="https://example.com/x"' in _render(
        Sidebar(items=[{"label": "L", "href": "https://example.com/x"}])
    )
