"""Template-source reads on the render and mount paths (#4294, #4295).

#4294: ``template_paths()`` and ``serialize_for_template()`` read the cached
JIT source, as every internal caller does, and the unsupplied-name notice reads
the template once per view class (not twice per render) while ``DEBUG`` is on.

#4295: a live mount of an explicit page-shell view skips the full page render
when nothing in the page can mount a child view; its HTML is never sent.
"""

from __future__ import annotations

import copy
import logging

import pytest
from django.test import RequestFactory

from djust import LiveView, state
from djust import _exposure_unsupplied, runtime
from djust._exposure_unsupplied import warn_unsupplied_template_names
from djust._render_caches import clear_render_caches
from djust.mixins.jit import JITMixin

pytestmark = pytest.mark.django_db

MODULE = __name__


@pytest.fixture
def hot_reload(monkeypatch):
    """Hot reload running: a template edit clears the render caches."""
    from djust.dev_server import hot_reload_server

    monkeypatch.setattr(hot_reload_server, "is_running", lambda: True)


@pytest.fixture
def reads(monkeypatch):
    """Count calls of the uncached template-source resolution."""
    calls: list = []
    original = JITMixin._get_template_content

    def counting(self):
        calls.append(type(self).__name__)
        return original(self)

    monkeypatch.setattr(JITMixin, "_get_template_content", counting)
    clear_render_caches()
    yield calls
    clear_render_caches()


class Cards(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{% for c in cards %}{{ c.title }}{% endfor %}{{ held }}</div>"

    def mount(self, request, **kwargs):
        self.held = "x"


class _Card:
    title = "T"
    body = "B"


def test_template_paths_and_serialize_read_the_cached_source(settings, reads):
    settings.DEBUG = False
    view = Cards()
    for _ in range(3):
        assert view.template_paths()["cards"] == ["title"]
        assert view.serialize_for_template([_Card()], "cards") == [{"title": "T"}]
    assert reads == ["Cards"], "each call re-resolved the template source"


def test_debug_still_reads_the_source_each_call(settings, reads):
    """With ``DEBUG`` on, render caches are off and hot reload sees template edits."""
    settings.DEBUG = True
    view = Cards()
    view.template_paths()
    view.template_paths()
    assert len(reads) == 2


def test_debug_notice_reads_the_template_once_per_class(settings, reads, hot_reload, caplog):
    settings.DEBUG = True
    _exposure_unsupplied._REPORTED.clear()
    view = Cards()
    view.held = "x"
    with caplog.at_level(logging.WARNING, logger="djust.exposure"):
        for _ in range(3):
            warn_unsupplied_template_names(view, {"cards": []})
    assert reads == ["Cards"], "the notice re-read the template on every render"
    assert "held" in caplog.text

    # A template refresh (hot reload, override_settings) forgets the paths.
    clear_render_caches()
    warn_unsupplied_template_names(view, {"cards": []})
    assert reads == ["Cards", "Cards"]


def test_debug_notice_without_hot_reload_reads_once_per_render(settings, reads):
    """Nothing would clear a kept copy when a template is edited, so none is kept."""
    settings.DEBUG = True
    view = Cards()
    view.held = "x"
    for _ in range(3):
        warn_unsupplied_template_names(view, {"held": "x", "cards": []})
    assert len(reads) == 3, "one read per render (it was two: the notice and its helper)"


def test_debug_notice_paths_follow_a_changed_inline_template(settings, reads, hot_reload, caplog):
    settings.DEBUG = True
    _exposure_unsupplied._REPORTED.clear()
    view = Cards()
    view.held = "x"
    warn_unsupplied_template_names(view, {"held": "x", "cards": []})
    view.template = "<div dj-root>{{ other }}</div>"
    view.other = "y"
    with caplog.at_level(logging.WARNING, logger="djust.exposure"):
        warn_unsupplied_template_names(view, {})
    assert "other" in caplog.text
    assert len(reads) == 2


def test_error_mode_reads_the_template_once_per_render(settings, reads, hot_reload):
    settings.DEBUG = True
    settings.DJUST_UNSUPPLIED_NAMES = "error"
    view = Cards()
    view.held = "x"
    for _ in range(2):
        warn_unsupplied_template_names(view, {"held": "x", "cards": []})
    assert len(reads) == 2


def test_an_overridden_template_paths_is_asked_once_per_render(settings, hot_reload):
    settings.DEBUG = True
    calls = []

    class Counting(Cards):
        def template_paths(self):
            calls.append(1)
            return {"held": []}

    view = Counting()
    view.held = "x"
    for _ in range(2):
        warn_unsupplied_template_names(view, {"held": "x"})
    assert len(calls) == 2, "never kept, and asked once per render (it was twice)"


# --- #4295 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "source, expected",
    [
        ("<html><div dj-root>{{ n }}</div></html>", False),
        ("{% load static live_tags %}<div dj-root>{% static 'a.css' %}</div>", False),
        ("{% load i18n %}{% load humanize %}<div dj-root></div>", False),
        ("{% load naturaltime from humanize %}<div dj-root></div>", False),
        ('{% load live_tags %}{% live_render "a.B" %}<div dj-root></div>', True),
        ("{% load crispy_forms_tags %}<div dj-root></div>", True),
        ("{% load mytag from custom_lib %}<div dj-root></div>", True),
        (None, True),
    ],
)
def test_source_may_register_children(source, expected):
    assert runtime._source_may_register_children(source) is expected


SHELL_FILES = {
    "t4295_base.html": "<html><body><nav>{{ title }}</nav>{% block main %}{% endblock %}</body></html>",
    "t4295_page.html": '{% extends "t4295_base.html" %}{% load static %}{% block main %}'
    '<div dj-root>{% include "t4295_part.html" %}</div>{% endblock %}',
    "t4295_part.html": "Page={{ n }}",
    "t4295_child_base.html": '<html><body>{% include "t4295_child_nav.html" %}'
    "{% block main %}{% endblock %}</body></html>",
    "t4295_child_nav.html": '{% load live_tags %}{% live_render "x.Y" sticky=True %}',
    "t4295_child_page.html": '{% extends "t4295_child_base.html" %}{% block main %}'
    "<div dj-root>Page={{ n }}</div>{% endblock %}",
    # The review's cases: a library loaded at the top of the child (dropped by
    # inheritance resolution), and an include that itself extends a parent.
    "t4295_loads_page.html": '{% extends "t4295_base.html" %}{% load myapp_tags %}'
    "{% block main %}<div dj-root>{% sidebar %}</div>{% endblock %}",
    "t4295_pbase.html": '{% load live_tags %}{% live_render "x.Y" %}{% block b %}{% endblock %}',
    "t4295_pchild.html": '{% extends "t4295_pbase.html" %}{% block b %}x{% endblock %}',
    "t4295_incl_extends_page.html": '{% extends "t4295_base.html" %}{% block main %}'
    '<div dj-root>{% include "t4295_pchild.html" %}</div>{% endblock %}',
    "t4295_var_include_page.html": '{% extends "t4295_base.html" %}{% block main %}'
    "<div dj-root>{% include partial_name %}</div>{% endblock %}",
    "t4295_missing_page.html": '{% extends "t4295_base.html" %}{% block main %}'
    '<div dj-root>{% include "t4295_nowhere.html" %}</div>{% endblock %}',
}


def djust_handlers_only(monkeypatch):
    """Hide tag handlers other test modules register at import (``djust_floor_probe_2556``).

    Collecting the whole suite imports them, and a handler from outside djust
    keeps the full page-shell render by design.
    """
    from djust import template_tags

    real = template_tags.get_registered_handlers

    def owned():
        return {
            name: handler
            for name, handler in real().items()
            if (handler if isinstance(handler, type) else type(handler)).__module__.startswith(
                "djust."
            )
        }

    monkeypatch.setattr(template_tags, "get_registered_handlers", owned)


@pytest.fixture
def shell_templates(settings, tmp_path, monkeypatch):
    yield from shell_template_dir(settings, tmp_path, monkeypatch)


def shell_template_dir(settings, tmp_path, monkeypatch):
    """``SHELL_FILES`` in a template dir searched first (shared with #4298/#4299)."""
    djust_handlers_only(monkeypatch)
    templates = copy.deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"] = [str(tmp_path), *templates[0].get("DIRS", [])]
    settings.TEMPLATES = templates
    for name, text in SHELL_FILES.items():
        (tmp_path / name).write_text(text)
    from djust.utils import clear_template_dirs_cache

    clear_template_dirs_cache()
    clear_render_caches()
    yield tmp_path
    clear_template_dirs_cache()
    clear_render_caches()


class ShellPage(LiveView):
    exposure_policy = "explicit"
    template_name = "t4295_page.html"
    n = state(1)


class ChildShellPage(LiveView):
    exposure_policy = "explicit"
    template_name = "t4295_child_page.html"
    n = state(1)


@pytest.fixture
def full_renders(monkeypatch):
    calls: list = []
    from djust import _child_rendering

    monkeypatch.setattr(
        _child_rendering, "render_view_full_template", lambda view, request: calls.append(view)
    )
    return calls


@pytest.mark.parametrize("debug", [False, True])
def test_a_childless_shell_skips_the_full_render(settings, shell_templates, full_renders, debug):
    settings.DEBUG = debug
    request = RequestFactory().get("/")
    for _ in range(2):
        runtime._reconstruct_explicit_page_shell(ShellPage(), request)
    assert full_renders == []


@pytest.mark.parametrize("debug", [False, True])
def test_a_shell_child_in_an_include_keeps_the_full_render(
    settings, shell_templates, full_renders, debug
):
    settings.DEBUG = debug
    request = RequestFactory().get("/")
    for _ in range(2):
        runtime._reconstruct_explicit_page_shell(ChildShellPage(), request)
    assert len(full_renders) == 2


@pytest.mark.parametrize(
    "template_name",
    [
        "t4295_loads_page.html",
        "t4295_incl_extends_page.html",
        "t4295_var_include_page.html",
        "t4295_missing_page.html",
        "t4295_not_a_template.html",
    ],
)
def test_what_the_scan_cannot_rule_out_keeps_the_full_render(
    settings, shell_templates, template_name
):
    settings.DEBUG = False
    view = type("Page", (ShellPage,), {"template_name": template_name})()
    assert runtime._page_shell_may_register_children(view) is True


def test_engine_builtins_keep_the_full_render(settings, shell_templates):
    templates = copy.deepcopy(settings.TEMPLATES)
    templates[0].setdefault("OPTIONS", {})["builtins"] = ["myapp.templatetags.tags"]
    settings.TEMPLATES = templates
    assert runtime._page_shell_may_register_children(ShellPage()) is True


def test_a_tag_handler_registered_outside_djust_keeps_the_full_render(
    settings, shell_templates, monkeypatch
):
    from djust import template_tags

    class Outside:
        __module__ = "myapp.tags"

    handlers = {**template_tags.get_registered_handlers(), "sidebar": Outside()}
    monkeypatch.setattr(template_tags, "get_registered_handlers", lambda: handlers)
    assert runtime._page_shell_may_register_children(ShellPage()) is True


def test_the_shell_answer_is_cached_per_template(settings, shell_templates, monkeypatch):
    settings.DEBUG = False
    scans = []
    original = runtime._templates_may_register_children
    monkeypatch.setattr(
        runtime,
        "_templates_may_register_children",
        lambda n, *a: scans.append(n) or original(n, *a),
    )
    for _ in range(3):
        assert runtime._page_shell_may_register_children(ShellPage()) is False
    assert scans == ["t4295_page.html"]


def test_an_edited_shell_is_read_again_after_a_template_refresh(settings, shell_templates):
    settings.DEBUG = False
    assert runtime._page_shell_may_register_children(ShellPage()) is False
    (shell_templates / "t4295_part.html").write_text('{% load live_tags %}{% live_render "x.Y" %}')
    clear_render_caches()
    assert runtime._page_shell_may_register_children(ShellPage()) is True


def test_an_overridden_get_template_keeps_the_full_render(settings, shell_templates, full_renders):
    class Custom(ShellPage):
        def get_template(self):
            return super().get_template()

    runtime._reconstruct_explicit_page_shell(Custom(), RequestFactory().get("/"))
    assert len(full_renders) == 1
