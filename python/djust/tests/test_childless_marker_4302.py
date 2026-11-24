"""``register.djust_childless = True`` and its C044 check (#4302)."""

from __future__ import annotations

import pytest
from django.template import Library, TemplateSyntaxError

from djust import runtime, template_libraries
from djust.checks.configuration import _check_childless_inclusion_tags


def _simple(marker=None) -> Library:
    lib = Library()
    lib.simple_tag(lambda: "", name="sidebar")
    if marker is not None:
        lib.djust_childless = marker
    return lib


def _with_inclusion(marker=True) -> Library:
    lib = _simple(marker)
    lib.inclusion_tag("x/panel.html", name="panel")(lambda: {})
    return lib


@pytest.fixture
def libs(monkeypatch):
    known = {
        "app_marked": _simple(True),
        "app_unmarked": _simple(),
        "app_yes": _simple("yes"),
        "app_inclusion": _with_inclusion(),
        "app_unmarked_inclusion": _with_inclusion(None),
    }

    def find(name):
        if name not in known:
            raise TemplateSyntaxError(name)
        return known[name]

    monkeypatch.setattr(template_libraries, "_find_library", find, raising=False)
    monkeypatch.setattr(template_libraries, "_library_map", lambda: dict(known))
    return known


def test_marked_library_is_childless(libs):
    assert runtime._source_may_register_children("{% load app_marked %}") is False


def test_unmarked_library_may_register_children(libs):
    assert runtime._source_may_register_children("{% load app_unmarked %}") is True


def test_non_true_marker_is_not_honoured(libs):
    assert runtime._source_may_register_children("{% load app_yes %}") is True


def test_live_render_in_source_still_counts(libs):
    src = "{% load app_marked %}{% live_render 'x.Y' %}"
    assert runtime._source_may_register_children(src) is True


def test_setting_path_still_works(libs):
    assert (
        runtime._source_may_register_children("{% load app_unmarked %}", ("app_unmarked",)) is False
    )


def test_check_warns_for_inclusion_tag_in_marked_library(libs, settings):
    settings.DEBUG = True
    found = []
    _check_childless_inclusion_tags(found)
    assert [m.id for m in found] == ["djust.C044"]
    assert "panel" in found[0].msg and "app_inclusion" in found[0].msg


def test_check_silent_without_debug(libs, settings):
    settings.DEBUG = False
    found = []
    _check_childless_inclusion_tags(found)
    assert found == []


def test_check_silent_for_marked_library_with_only_simple_tags(monkeypatch, settings):
    settings.DEBUG = True
    known = {"app_marked": _simple(True)}
    monkeypatch.setattr(template_libraries, "_find_library", lambda n: known[n], raising=False)
    monkeypatch.setattr(template_libraries, "_library_map", lambda: dict(known))
    found = []
    _check_childless_inclusion_tags(found)
    assert found == []


def test_subset_load_from_marked_library(libs):
    assert runtime._source_may_register_children("{% load sidebar from app_marked %}") is False


def test_check_covers_libraries_listed_in_the_setting(libs, settings, monkeypatch):
    settings.DEBUG = True
    monkeypatch.setattr(
        runtime, "_project_childless_libraries", lambda: ("app_unmarked_inclusion",)
    )
    found = []
    _check_childless_inclusion_tags(found)
    assert any("app_unmarked_inclusion" in m.msg for m in found)


def test_check_respects_suppress_checks(libs, settings):
    settings.DEBUG = True
    settings.DJUST_CONFIG = {"suppress_checks": ["C044"]}
    found = []
    _check_childless_inclusion_tags(found)
    assert found == []


def test_only_the_inclusion_tag_is_reported(monkeypatch, settings):
    settings.DEBUG = True
    known = {"app_both": _with_inclusion()}
    monkeypatch.setattr(template_libraries, "_find_library", lambda n: known[n], raising=False)
    monkeypatch.setattr(template_libraries, "_library_map", lambda: dict(known))
    found = []
    _check_childless_inclusion_tags(found)
    assert len(found) == 1 and "'panel'" in found[0].msg
