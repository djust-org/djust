"""Per-render template costs, kept per template while templates are cached.

Reported downstream (Point Guard, djust 1.3.0rc11, Phase 2 decision 198):

- N-37: the JIT built its extraction source again on every render (the
  ``{% extends %}`` chain in Rust, every ``{% include %}`` from disk, a SHA-256
  of the result), and keyed its hash cache by ``id()`` of that new string.
- N-38: two template libraries registering one tag or filter name take it from
  each other on every ``{% load %}``, which drops every parsed template. Now a
  ``manage.py check`` warning (``djust.T026``) and a one-time runtime warning.
- N-39: the dj-model allowlist walked the whole template and its includes
  after every render.

The caches apply with DEBUG off only; DEBUG on (hot reload) rebuilds every
render, and a settings change or a hot-reload refresh clears them.
"""

from __future__ import annotations

import hashlib
import logging
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.template.library import Library

from djust import LiveView
from djust import _render_caches
from djust.mixins import jit
from djust.mixins.jit import JITMixin, _jit_template_content, _template_hash
from djust.utils import clear_template_dirs_cache


@pytest.fixture
def page_dir(tmp_path, settings):
    (tmp_path / "rc").mkdir()
    (tmp_path / "rc" / "base.html").write_text(
        "<html><body>{% block content %}{% endblock %}</body></html>"
    )
    (tmp_path / "rc" / "_row.html").write_text('<i>{{ u.username }}</i><input dj-model="query">')
    (tmp_path / "rc" / "page.html").write_text(
        '{% extends "rc/base.html" %}{% block content %}'
        '<div dj-root dj-view="tests.CachedPage">'
        '{% for u in people %}{% include "rc/_row.html" %}{% endfor %}'
        "</div>{% endblock %}"
    )
    templates = [dict(t) for t in settings.TEMPLATES]
    templates[0] = {**templates[0], "DIRS": [str(tmp_path), *templates[0].get("DIRS", [])]}
    settings.TEMPLATES = templates
    clear_template_dirs_cache()
    _render_caches.clear_render_caches()
    yield tmp_path
    clear_template_dirs_cache()
    _render_caches.clear_render_caches()


class CachedPage(LiveView):
    template_name = "rc/page.html"

    def mount(self, request, **kwargs):
        self.people = list(User.objects.filter(username__startswith="n37-").order_by("username"))


# ---------------------------------------------------------------------------
# N-37: the JIT's extraction source
# ---------------------------------------------------------------------------


def test_extraction_source_is_built_once_per_template_with_debug_off(page_dir, settings):
    settings.DEBUG = False
    calls = []
    original = JITMixin._inline_includes

    def counting(content, dirs):
        calls.append(1)
        return original(content, dirs)

    with mock.patch.object(JITMixin, "_inline_includes", staticmethod(counting)):
        first = _jit_template_content(CachedPage())
        second = _jit_template_content(CachedPage())

    assert "{{ u.username }}" in first
    assert second is first
    assert len(calls) == 1


def test_extraction_source_is_rebuilt_every_render_with_debug_on(page_dir, settings):
    settings.DEBUG = True
    first = _jit_template_content(CachedPage())
    (page_dir / "rc" / "_row.html").write_text("<b>{{ u.email }}</b>")
    second = _jit_template_content(CachedPage())

    assert "{{ u.username }}" in first
    assert "{{ u.email }}" in second


def test_a_settings_change_clears_the_cache(page_dir, settings):
    settings.DEBUG = False
    first = _jit_template_content(CachedPage())
    (page_dir / "rc" / "_row.html").write_text("<b>{{ u.email }}</b>")
    assert _jit_template_content(CachedPage()) is first  # still cached

    settings.TEMPLATES = [dict(t) for t in settings.TEMPLATES]  # setting_changed
    clear_template_dirs_cache()

    assert "{{ u.email }}" in _jit_template_content(CachedPage())


def test_hot_reload_template_refresh_clears_the_cache(page_dir, settings):
    from djust.websocket import LiveViewConsumer

    settings.DEBUG = False
    _jit_template_content(CachedPage())
    (page_dir / "rc" / "_row.html").write_text("<b>{{ u.email }}</b>")

    LiveViewConsumer._clear_template_caches()

    assert "{{ u.email }}" in _jit_template_content(CachedPage())


def test_an_override_of_get_template_content_is_called_every_time(page_dir, settings):
    settings.DEBUG = False

    class Dynamic(CachedPage):
        n = 0

        def _get_template_content(self):
            type(self).n += 1
            return "{{ people.%d }}" % type(self).n

    assert _jit_template_content(Dynamic()) != _jit_template_content(Dynamic())

    view = CachedPage()
    view._get_template_content = lambda: "{{ instance_patch }}"
    assert _jit_template_content(view) == "{{ instance_patch }}"


@pytest.mark.django_db
def test_renders_serialize_the_same_with_the_cache(page_dir, settings):
    User.objects.create(username="n37-ada", email="ada@example.com")
    rows = {}
    for debug in (True, False, False):
        settings.DEBUG = debug
        view = CachedPage()
        view.mount(None)
        rows.setdefault(debug, []).append(view.get_context_data()["people"])
    assert rows[True][0] == rows[False][0] == rows[False][1]
    assert rows[False][0][0]["username"] == "n37-ada"
    assert "email" not in rows[False][0][0]


def test_template_hash_is_keyed_by_the_source_not_its_id():
    a = "".join(["{{ a }}", "x" * 50])
    b = "".join(["{{ b }}", "y" * 50])
    assert _template_hash(a) == hashlib.sha256(a.encode()).hexdigest()[:8]
    assert _template_hash(b) == hashlib.sha256(b.encode()).hexdigest()[:8]
    assert all(isinstance(key, str) for key in jit._template_hash_cache)
    assert _template_hash("".join(["{{ a }}", "x" * 50])) == _template_hash(a)


def test_template_hash_cache_is_bounded():
    jit._template_hash_cache.clear()
    for i in range(jit._TEMPLATE_HASH_CACHE_MAX + 10):
        _template_hash("{{ v%d }}" % i)
    assert len(jit._template_hash_cache) <= jit._TEMPLATE_HASH_CACHE_MAX


@pytest.mark.django_db
def test_a_name_served_as_a_queryset_then_a_model_keeps_its_paths():
    """The model serializer had the QuerySet's (paths, optimization) slot."""
    User.objects.create(username="n37-ada", email="ada@example.com")
    template = "{% for u in who %}{{ u.username }}{% endfor %}{{ who.username }}"
    mixin = JITMixin()

    mixin._jit_serialize_queryset(User.objects.filter(username__startswith="n37-"), template, "who")
    as_model = mixin._jit_serialize_model(
        User.objects.get(username__startswith="n37-"), template, "who"
    )

    assert as_model["username"] == "n37-ada"
    assert "email" not in as_model


# ---------------------------------------------------------------------------
# N-39: the dj-model allowlist
# ---------------------------------------------------------------------------


def _rust_view(page_dir):
    from djust._rust import RustLiveView
    from djust.utils import get_template_dirs

    source = '<div dj-root>{% include "rc/_row.html" %}<input dj-model="title"></div>'
    return RustLiveView(source, get_template_dirs())


def test_dj_model_fields_cache_returns_the_walk_and_skips_the_next(page_dir):
    from djust._rust import clear_dj_model_fields_cache

    clear_dj_model_fields_cache()
    view = _rust_view(page_dir)
    walked = view.dj_model_fields()
    assert walked == ["query", "title"]
    assert view.dj_model_fields(cache=True) == walked

    # The include changes on disk: a cached answer stays, a walk sees it.
    (page_dir / "rc" / "_row.html").write_text('<input dj-model="other">')
    assert view.dj_model_fields(cache=True) == walked
    assert view.dj_model_fields() == ["other", "title"]

    clear_dj_model_fields_cache()
    assert view.dj_model_fields(cache=True) == ["other", "title"]


def test_dj_model_fields_cache_is_keyed_by_the_exact_source(page_dir):
    from djust._rust import RustLiveView, clear_dj_model_fields_cache
    from djust.utils import get_template_dirs

    clear_dj_model_fields_cache()
    one = RustLiveView('<div dj-root><input dj-model="a"></div>', get_template_dirs())
    two = RustLiveView('<div dj-root><input dj-model="b"></div>', get_template_dirs())
    assert one.dj_model_fields(cache=True) == ["a"]
    assert two.dj_model_fields(cache=True) == ["b"]


@pytest.mark.parametrize("debug, cached", [(False, True), (True, False)])
def test_record_dj_model_fields_caches_only_with_debug_off(settings, debug, cached):
    settings.DEBUG = debug
    seen = {}

    class FakeRustView:
        def dj_model_fields(self, cache=False):
            seen["cache"] = cache
            return ["name"]

    view = LiveView()
    view._record_dj_model_fields_from_rust(FakeRustView())

    assert seen["cache"] is cached
    assert view._dj_model_fields == frozenset({"name"})


# ---------------------------------------------------------------------------
# N-38: one tag or filter name in two libraries
# ---------------------------------------------------------------------------


def _library(module: str, filter_name: str, tag_name: str) -> Library:
    library = Library()

    def shared(value):
        return value

    def tag():
        return ""

    shared.__module__ = module
    tag.__module__ = module
    library.filter(filter_name, shared)
    library.simple_tag(tag, name=tag_name)
    return library


@pytest.fixture
def two_libraries(monkeypatch):
    from djust import template_libraries

    one = _library("n38_app_one.templatetags.one_tags", "n38_at", "n38_badge")
    two = _library("n38_app_two.templatetags.two_tags", "n38_at", "n38_other")
    known = {"one_tags": one, "two_tags": two, "one_alias": one}
    monkeypatch.setattr(template_libraries, "_library_map", lambda: dict(known))

    def forget():
        template_libraries._warned_shared_names.clear()
        for state, default in (
            ("_tag_owner", template_libraries._tag_owner),
            ("_filter_owner", template_libraries._filter_owner),
            ("_loaded", template_libraries._loaded),
        ):
            entries = template_libraries._engine_state(state, default)
            for key in [k for k in entries if k.startswith("n38_") or k in known]:
                del entries[key]

    forget()
    yield template_libraries, one, two
    forget()


def test_shared_library_names_finds_the_shared_filter(two_libraries):
    template_libraries, _one, _two = two_libraries
    # One module under two labels is one library: the first label (sorted).
    assert template_libraries.shared_library_names() == [
        ("filter", "n38_at", ("one_alias", "two_tags")),
    ]
    assert template_libraries.shared_library_names(["one_tags", "one_alias"]) == []
    assert template_libraries.shared_library_names(["one_tags", "two_tags"]) == [
        ("filter", "n38_at", ("one_tags", "two_tags")),
    ]


def test_bridging_the_second_library_warns_once(two_libraries, caplog):
    template_libraries, one, two = two_libraries
    caplog.set_level(logging.WARNING, logger="djust.template_libraries")

    template_libraries._bridge_library("one_tags", one)
    template_libraries._bridge_library("two_tags", two)
    template_libraries._bridge_library("one_tags", one)
    template_libraries._bridge_library("two_tags", two)

    warnings = [r.getMessage() for r in caplog.records if "n38_at" in r.getMessage()]
    assert len(warnings) == 1
    assert "'one_tags' and 'two_tags' both register the filter 'n38_at'" in warnings[0]


def test_one_module_under_two_labels_does_not_warn(two_libraries, caplog):
    template_libraries, one, _two = two_libraries
    caplog.set_level(logging.WARNING, logger="djust.template_libraries")

    template_libraries._bridge_library("one_tags", one)
    template_libraries._bridge_library("one_alias", one)

    assert not [r for r in caplog.records if "n38_" in r.getMessage()]


def test_check_t026_reports_libraries_the_templates_load(two_libraries, tmp_path, monkeypatch):
    from djust.checks import templates as template_checks

    (tmp_path / "a.html").write_text("{% load one_tags %}{{ x|n38_at }}")
    (tmp_path / "b.html").write_text("{% load n38_at from two_tags %}{{ y|n38_at }}")
    monkeypatch.setattr(template_checks, "_get_template_dirs", lambda: [str(tmp_path)])

    messages = template_checks.check_shared_template_library_names(None)

    assert [m.id for m in messages] == ["djust.T026"]
    assert "'one_tags', 'two_tags'" in messages[0].msg
    assert "filter 'n38_at'" in messages[0].msg


def test_check_t026_ignores_a_library_no_template_loads(two_libraries, tmp_path, monkeypatch):
    from djust.checks import templates as template_checks

    (tmp_path / "a.html").write_text("{% load one_tags i18n %}{# {% load two_tags %} #}")
    monkeypatch.setattr(template_checks, "_get_template_dirs", lambda: [str(tmp_path)])

    assert template_checks.check_shared_template_library_names(None) == []


def test_check_t026_can_be_suppressed(two_libraries, tmp_path, monkeypatch, settings):
    from djust.checks import templates as template_checks

    (tmp_path / "a.html").write_text("{% load one_tags two_tags %}")
    monkeypatch.setattr(template_checks, "_get_template_dirs", lambda: [str(tmp_path)])
    settings.DJUST_CONFIG = {"suppress_checks": ["T026"]}

    assert template_checks.check_shared_template_library_names(None) == []
