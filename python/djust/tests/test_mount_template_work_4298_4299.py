"""Template work on a live mount (#4298, #4299).

#4298: the #4295 page-shell skip also applies when a page loads djust's own
component, flash or theme library, a library that registers only filters, or a
library the project lists in ``LIVEVIEW_CONFIG['childless_tag_libraries']``.
The templates djust's libraries render are scanned like includes, so an
overridden copy that mounts a child still keeps the full render.

#4299: ``get_template()`` is kept per view class while render caches are on, a
skipped page-shell render no longer calls it, and the mount's two handler
configs share one decorator scan.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.template import Library, TemplateSyntaxError
from django.test import RequestFactory

from djust import runtime, template_libraries
from djust._render_caches import clear_render_caches
from djust.mixins.template import _GET_TEMPLATE_ATTRS, TemplateMixin
from djust.tests.test_template_source_cache_4294_4295 import ShellPage, shell_template_dir

pytestmark = pytest.mark.django_db

PACKAGE = Path(runtime.__file__).parent


@pytest.fixture
def shell_templates(settings, tmp_path, monkeypatch):
    yield from shell_template_dir(settings, tmp_path, monkeypatch)


# --- #4298: which loads are child-free ----------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "{% load djust_components %}<div dj-root>{% card %}x{% endcard %}</div>",
        "{% load djust_flash static %}<div dj-root>{% dj_flash %}</div>",
        "{% load theme_tags %}<div dj-root>{% theme_switcher %}</div>",
        "{% load theme_head from theme_tags %}<div dj-root></div>",
    ],
)
def test_djust_libraries_are_child_free(source):
    assert runtime._source_may_register_children(source) is False


@pytest.fixture
def libraries(monkeypatch):
    """``{% load %}`` names resolved to hand-built libraries."""
    filters_only = Library()
    filters_only.filter("shout", lambda value: str(value).upper())
    mixed = Library()
    mixed.filter("shout", lambda value: str(value).upper())
    mixed.simple_tag(lambda: "", name="sidebar")
    known = {"app_filters": filters_only, "app_mixed": mixed}

    def find(name):
        if name not in known:
            raise TemplateSyntaxError(name)
        return known[name]

    monkeypatch.setattr(template_libraries, "_find_library", find, raising=False)
    return known


@pytest.mark.parametrize(
    "source, expected",
    [
        ("{% load app_filters %}", False),
        ("{% load shout from app_mixed %}", False),
        ("{% load app_mixed %}", True),
        ("{% load sidebar from app_mixed %}", True),
        ("{% load shout sidebar from app_mixed %}", True),
        ("{% load app_unknown %}", True),
        ("{% load app_filters app_mixed %}", True),
    ],
)
def test_a_library_without_tags_is_child_free(libraries, source, expected):
    assert runtime._source_may_register_children(source) is expected


def test_a_project_can_vouch_for_a_library(settings, shell_templates):
    settings.DEBUG = False
    view = type("Page", (ShellPage,), {"template_name": "t4295_loads_page.html"})()
    assert runtime._page_shell_may_register_children(view) is True
    settings.LIVEVIEW_CONFIG = {"childless_tag_libraries": ["myapp_tags"]}
    assert runtime._page_shell_may_register_children(view) is False


def _page(tmp_path: Path, name: str, loads: str) -> type:
    (tmp_path / name).write_text(
        '{% extends "t4295_base.html" %}{% load ' + loads + " %}{% block main %}"
        "<div dj-root>{{ n }}</div>{% endblock %}"
    )
    return type("Page", (ShellPage,), {"template_name": name})


@pytest.mark.parametrize("debug", [False, True])
def test_a_page_of_djust_libraries_skips_the_full_render(settings, shell_templates, debug):
    settings.DEBUG = debug
    page = _page(
        shell_templates, "t4298_libs.html", "static djust_components djust_flash theme_tags"
    )
    assert runtime._page_shell_may_register_children(page()) is False


@pytest.mark.parametrize(
    "override",
    [
        "djust_components/toast.html",
        "djust_theming/theme_switcher.html",
        "djust_theming/themes/t4298/components/theme_panel.html",
    ],
)
def test_an_overridden_library_template_that_mounts_a_child_keeps_the_render(
    settings, shell_templates, override
):
    settings.DEBUG = False
    page = _page(shell_templates, "t4298_libs.html", "djust_components theme_tags")
    path = shell_templates / override
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{% load live_tags %}{% live_render "x.Y" %}')
    clear_render_caches()
    assert runtime._page_shell_may_register_children(page()) is True


def _literals(pattern: str, *paths: Path) -> set:
    return {m for path in paths for m in re.findall(pattern, path.read_text())}


def test_the_rendered_template_table_matches_the_libraries():
    """Each template a child-free djust library renders is on its scan list."""
    rendered = runtime._LIBRARY_RENDERED_TEMPLATES
    components = sorted((PACKAGE / "components" / "templatetags").glob("*.py"))
    assert _literals(r'inclusion_tag\(\s*"([^"]+)"', *components) == set(
        rendered["djust_components"]
    )
    renders = r"\b(?:render_to_string|get_template|select_template)\("
    for path in [*components, PACKAGE / "templatetags" / "djust_flash.py"]:
        assert not re.search(renders, path.read_text()), f"{path.name} renders a template"

    theme_tags = PACKAGE / "theming" / "templatetags" / "theme_tags.py"
    theme_components = PACKAGE / "theming" / "components.py"
    expected = {
        f"djust_theming/{n}"
        for n in _literals(r'render_to_string\(\s*"djust_theming/([^"]+)"', theme_tags)
    }
    for name in _literals(r'resolve_theme_template\(\s*\w+,\s*"([^"]+)"', theme_tags) | _literals(
        r'_get_theme_template_candidates\(\s*[\w.]+,\s*"([^"]+)"', theme_components
    ):
        expected |= {f"djust_theming/{name}.html", f"djust_theming/themes/*/{name}.html"}
    for name in _literals(r'_get_component_candidates\(\s*[\w.]+,\s*"([^"]+)"', theme_components):
        expected |= {
            f"djust_theming/components/{name}.html",
            f"djust_theming/themes/*/components/{name}.html",
        }
    assert expected == set(rendered["theme_tags"])
    # Every other render in theme_tags goes through those resolvers.
    assert len(re.findall(renders, theme_tags.read_text())) == 1  # render_to_string(theme_head)


# --- #4299: get_template() kept per class ----------------------------------------


@pytest.fixture
def builds(monkeypatch):
    calls: list = []
    original = TemplateMixin._build_template_from_name

    def counting(self):
        calls.append(type(self).__name__)
        return original(self)

    monkeypatch.setattr(TemplateMixin, "_build_template_from_name", counting)
    return calls


def test_get_template_is_built_once_per_class(settings, shell_templates, builds):
    settings.DEBUG = False
    first = ShellPage()
    result = first.get_template()
    second = ShellPage()
    assert second.get_template() == result
    assert builds == ["ShellPage"]
    for attr in _GET_TEMPLATE_ATTRS:
        assert getattr(second, attr) == getattr(first, attr), attr
    assert second._full_template


def test_debug_builds_get_template_each_call(settings, shell_templates, builds):
    settings.DEBUG = True
    ShellPage().get_template()
    ShellPage().get_template()
    assert len(builds) == 2


def test_a_template_refresh_rebuilds_get_template(settings, shell_templates, builds):
    settings.DEBUG = False
    assert "Edited" not in ShellPage().get_template()
    (shell_templates / "t4295_page.html").write_text(
        '{% extends "t4295_base.html" %}{% block main %}<div dj-root>Edited {{ n }}</div>'
        "{% endblock %}"
    )
    from django.template import engines

    for backend in engines.all():  # what a hot-reload refresh also resets
        for loader in getattr(getattr(backend, "engine", None), "template_loaders", ()):
            getattr(loader, "reset", lambda: None)()
    clear_render_caches()
    assert "Edited" in ShellPage().get_template()
    assert len(builds) == 2


def test_get_template_is_kept_per_view_class(settings, shell_templates, builds):
    settings.DEBUG = False
    Other = type("Other", (ShellPage,), {})
    ShellPage().get_template()
    Other().get_template()
    assert builds == ["ShellPage", "Other"]


def test_a_skipped_page_shell_render_does_not_build_the_template(
    settings, shell_templates, builds, monkeypatch
):
    settings.DEBUG = True  # no kept copy: every get_template() would build
    from djust import _child_rendering

    renders: list = []
    monkeypatch.setattr(
        _child_rendering, "render_view_full_template", lambda v, r: renders.append(v)
    )
    runtime._reconstruct_explicit_page_shell(ShellPage(), RequestFactory().get("/"))
    assert builds == [] and renders == []


# --- #4299: one decorator scan per mount -----------------------------------------


def test_the_mount_scans_decorators_once(monkeypatch):
    pytest.importorskip("channels")
    from django.test import override_settings

    from djust.tests import test_handler_config_mount_frame_2656 as frame_tests

    scans: list = []
    original = runtime._public_decorator_metadata

    def counting(view):
        scans.append(type(view).__name__)
        return original(view)

    monkeypatch.setattr(runtime, "_public_decorator_metadata", counting)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[frame_tests.MOD], DEBUG=False):
        frame = frame_tests._run()
    assert frame.get("cache_config", {}).get("cached_only")
    assert set(frame["handler_config"]) == {"search", "on_scroll"}
    assert scans == ["RateLimited2656"]


# --- review follow-ups ---------------------------------------------------------


def test_a_djust_library_name_served_by_another_module_is_not_vouched_for(monkeypatch):
    """An app's own ``theme_tags`` (or an ``OPTIONS['libraries']`` alias) may mount a child."""
    impostor = Library()
    impostor.simple_tag(lambda: "", name="sidebar")
    real = template_libraries._find_library
    monkeypatch.setattr(
        template_libraries,
        "_find_library",
        lambda name: impostor if name == "theme_tags" else real(name),
    )
    assert runtime._source_may_register_children("{% load theme_tags %}") is True
    assert runtime._source_may_register_children("{% load djust_flash %}") is False


@pytest.mark.parametrize(
    "source, expected",
    [
        ('{% load theme_tags %}{% component_preview "card" %}', True),
        ('{% load theme_tags %}{% component_thumbnail "card" %}', True),
        ("{% load component_thumbnail from theme_tags %}{% component_thumbnail 'x' %}", True),
        ("{% load theme_tags %}{% theme_head %}", False),
        ("{% load theme_head from theme_tags %}<p>component_preview</p>", False),
    ],
)
def test_catalogue_preview_tags_keep_the_render(source, expected):
    assert runtime._source_may_register_children(source) is expected


def test_a_class_overriding_a_build_helper_is_not_kept(settings, shell_templates, builds):
    settings.DEBUG = False

    class Custom(ShellPage):
        def _extract_liveview_root_with_wrapper(self, template):
            return super()._extract_liveview_root_with_wrapper(template)

    Custom().get_template()
    Custom().get_template()
    assert builds == ["Custom", "Custom"]


def test_a_cache_hit_renders_the_same_page(settings, shell_templates, rf):
    settings.DEBUG = False
    pages = []
    for _ in range(2):
        view = ShellPage()
        view.request = rf.get("/")
        view.get_template()
        pages.append((view._full_template, view.render_full_template(rf.get("/"))))
    assert pages[0] == pages[1]
    assert "Page=1" in pages[1][1]


def test_every_theme_tag_that_renders_the_catalogue_is_unscanned():
    import ast

    path = PACKAGE / "theming" / "templatetags" / "theme_tags.py"
    source = path.read_text()
    reaching = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and any(
            "register" in ast.unparse(d) for d in node.decorator_list
        ):
            body = ast.get_source_segment(source, node) or ""
            if re.search(r"gallery|_preview_template|theme_components|_thumbnail_html", body):
                reaching.add(node.name)
    assert reaching == set(runtime._LIBRARY_UNSCANNED_TAGS["theme_tags"])
