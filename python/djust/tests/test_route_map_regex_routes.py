"""``re_path()`` LiveView routes in the derived client route map.

The map took a regex leaf's text as a path: a catch-all 404 view at
``re_path(r"^", ...)`` entered as ``"/"`` and, coming after ``path("")``,
replaced the home page's entry, so ``dj-navigate`` to ``/`` mounted the 404
view. Found by a port of a production app. Now a regex route enters the map
only when it is a plain path with single-segment named groups, and the first
pattern for a client path wins, as Django's resolver does.
"""

from __future__ import annotations

import sys

import tests.conftest  # noqa: F401  -- configure Django settings

import pytest
from django.test import override_settings
from django.urls import clear_url_caches, path, re_path

from djust import LiveView
from djust.routing import _regex_route_path, _reset_route_map_cache, build_route_map_from_urlconf


class _Home(LiveView):
    template = "<div dj-root>home</div>"


class _Doc(LiveView):
    template = "<div dj-root>doc</div>"


class _Shadow(LiveView):
    template = "<div dj-root>shadow</div>"


class _NotFound(LiveView):
    template = "<div dj-root>404</div>"


for _cls in (_Home, _Doc, _Shadow, _NotFound):
    setattr(sys.modules[__name__], _cls.__name__, _cls)

urlpatterns = [
    path("", _Home.as_view()),
    re_path(r"^docs/(?P<slug>[-\w]+)$", _Doc.as_view()),
    path("docs/<slug:slug>", _Shadow.as_view()),
    re_path(r"^(?P<unknown_path>.*)$", _NotFound.as_view()),
    re_path(r"^", _NotFound.as_view()),
]


@pytest.fixture(autouse=True)
def _reset_state():
    _reset_route_map_cache()
    clear_url_caches()
    yield
    _reset_route_map_cache()
    clear_url_caches()


@override_settings(ROOT_URLCONF=__name__)
def test_a_catch_all_regex_does_not_take_the_home_route():
    route_map = build_route_map_from_urlconf()
    assert route_map["/"] == f"{__name__}._Home"
    assert f"{__name__}._NotFound" not in route_map.values()


@override_settings(ROOT_URLCONF=__name__)
def test_a_segment_regex_becomes_a_param_route_and_the_first_pattern_wins():
    route_map = build_route_map_from_urlconf()
    assert route_map["/docs/:slug"] == f"{__name__}._Doc"


@pytest.mark.parametrize(
    "regex, expected",
    [
        (r"^$", ""),
        (r"^agents$", "agents"),
        (r"^items/(?P<id>\d+)/edit/$", "items/<id>/edit/"),
        (r"^u/(?P<name>[^/]+)$", "u/<name>"),
        (r"^", None),
        (r"^(?P<unknown_path>.*)$", None),
        (r"^f/(?P<p>.+)$", None),
        (r"^a|b$", None),
    ],
)
def test_regex_route_path(regex, expected):
    assert _regex_route_path(regex) == expected
