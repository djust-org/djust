"""A library tag djust refuses must not make every ``{% load %}`` re-bridge.

Reported downstream (Point Guard N-47, djust 1.3.0rc13): when a library
registers a tag name djust already provides, ``_bridge_tag`` refuses it, but
``_bridge_library`` still recorded the library as the name's owner.
``_still_bridged`` then probed the registry for the library's own entry, which
is never there (the built-in holds the name), so the #2668 early return never
fired: each ``{% load %}`` bridged the library again, logged the refusal again
(about 150 warnings per page for ``djust_components``) and moved the registry
generation during the parse, dropping every cached template.
"""

from __future__ import annotations

import logging
import sys
from types import ModuleType

import pytest
from django.template.library import Library

from djust import _rust
from djust import template_libraries
from djust.template import DjustTemplateBackend
from djust.template_libraries import rendering_with_backend

pytestmark = pytest.mark.django_db

BUILTIN = "rtr_builtin"  # a name djust "already provides" in these tests
MODULE = "rtr_app_templatetags_rtr_tags"


class _Builtin:
    def render(self, args, context):
        return "builtin"


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """A backend whose ``rtr_tags`` library registers ``BUILTIN`` too.

    The built-in is registered process-wide (namespace 0), the way djust's
    own tags are; the library bridges into the backend's own namespace,
    where ``registry_entry_is_local(BUILTIN, "tag")`` is False.
    """
    module = ModuleType(MODULE)
    module.register = Library()

    def clash():
        return "library"

    def own():
        return "own"

    def rtr_up(value):
        return str(value).upper()

    for fn in (clash, own, rtr_up):
        fn.__module__ = MODULE
    module.register.simple_tag(clash, name=BUILTIN)
    module.register.simple_tag(own, name="rtr_own")
    module.register.filter("rtr_up", rtr_up)
    monkeypatch.setitem(sys.modules, MODULE, module)

    previous = _rust.set_registry_namespace(0)
    _rust.register_tag_handler(BUILTIN, _Builtin())
    template_libraries._warned_refused_tags.clear()
    backend = DjustTemplateBackend(
        {
            "NAME": "rtr",
            "DIRS": [tmp_path],
            "APP_DIRS": False,
            "OPTIONS": {"libraries": {"rtr_tags": MODULE}},
        }
    )
    try:
        yield backend
    finally:
        _rust.set_registry_namespace(0)
        _rust.unregister_tag_handler(BUILTIN)
        _rust.set_registry_namespace(previous)
        template_libraries._warned_refused_tags.clear()


def _refusals(caplog) -> list:
    return [
        r
        for r in caplog.records
        if f"registers tag '{BUILTIN}', which djust already provides" in r.getMessage()
    ]


def test_second_load_does_not_rebridge_or_warn_again(engine, caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="djust.template_libraries")
    with rendering_with_backend(engine):
        template_libraries.load_libraries(["rtr_tags"])
        assert len(_refusals(caplog)) == 1

        bridged = []
        real = template_libraries._bridge_tag
        monkeypatch.setattr(
            template_libraries,
            "_bridge_tag",
            lambda label, name, fn: (bridged.append(name), real(label, name, fn))[1],
        )
        generation = _rust.registry_generation()
        for _ in range(3):
            template_libraries.load_libraries(["rtr_tags"])

        assert bridged == [], "an already-bridged library was bridged again"
        assert _rust.registry_generation() == generation, "a repeat {% load %} moved the generation"
    assert len(_refusals(caplog)) == 1


def test_refusal_warns_once_per_process_across_engines(engine, tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="djust.template_libraries")
    other = DjustTemplateBackend(
        {
            "NAME": "rtr2",
            "DIRS": [tmp_path],
            "APP_DIRS": False,
            "OPTIONS": {"libraries": {"rtr_tags": MODULE}},
        }
    )
    for backend in (engine, other, engine, other):
        with rendering_with_backend(backend):
            template_libraries.load_libraries(["rtr_tags"])

    assert len(_refusals(caplog)) == 1


def test_refused_name_is_not_recorded_as_the_library_s(engine):
    with rendering_with_backend(engine):
        template_libraries.load_libraries(["rtr_tags"])
        owners = template_libraries._engine_state("_tag_owner", template_libraries._tag_owner)
        assert BUILTIN not in owners
        assert owners["rtr_own"] == "rtr_tags"


def test_template_loading_the_library_is_a_cache_hit(engine):
    src = "{% load rtr_tags %}{% rtr_own %}{% rtr_builtin %}{{ x|rtr_up }}-refused-tag"
    engine.from_string(src)
    engine.from_string(src)
    generation = _rust.registry_generation()
    for _ in range(3):
        assert engine.from_string(src).render({"x": "a"}) == "ownbuiltinA-refused-tag"
    assert _rust.registry_generation() == generation


def test_the_library_bridges_its_version_once_the_builtin_is_gone(engine):
    with rendering_with_backend(engine):
        template_libraries.load_libraries(["rtr_tags"])
    previous = _rust.set_registry_namespace(0)
    _rust.unregister_tag_handler(BUILTIN)
    _rust.set_registry_namespace(previous)

    with rendering_with_backend(engine):
        template_libraries.load_libraries(["rtr_tags"])
        owners = template_libraries._engine_state("_tag_owner", template_libraries._tag_owner)
        assert owners[BUILTIN] == "rtr_tags"
        assert _rust.registry_entry_is_local(BUILTIN, "tag")
