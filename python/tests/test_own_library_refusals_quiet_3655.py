"""djust's own template libraries must not log a warning per refused tag (#3655).

``djust_components`` and ``theme_tags`` ship inside djust. Many of their tags have a
native Rust handler too, so the bridge refuses the library's version and the native
one wins. That is a design choice, not a collision a developer can act on, yet each
refusal logged a WARNING: about 206 on the first page of an app that loads both
libraries, burying a real clash with a third-party library.

The refusal is unchanged; only the log level of the expected case is (DEBUG).
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

OWN = {
    "djust_components": "djust.components.templatetags.djust_components",
    "theme_tags": "djust.theming.templatetags.theme_tags",
}
LOGGER = "djust.template_libraries"
CLASH = "t3655_builtin"


def _backend(tmp_path, libraries):
    return DjustTemplateBackend(
        {
            "NAME": "own3655",
            "DIRS": [tmp_path],
            "APP_DIRS": False,
            "OPTIONS": {"libraries": libraries},
        }
    )


def _refusal_messages(caplog, level):
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == level and "already provide" in r.getMessage()
    ]


@pytest.fixture(autouse=True)
def _fresh_warned_set():
    template_libraries._warned_refused_tags.clear()
    yield
    template_libraries._warned_refused_tags.clear()


def test_loading_both_djust_libraries_logs_no_refusal_warning(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    backend = _backend(tmp_path, OWN)
    with rendering_with_backend(backend):
        template_libraries.load_libraries(list(OWN))

    assert _refusal_messages(caplog, logging.WARNING) == []


def test_the_expected_refusals_are_still_made_and_logged_at_debug(tmp_path, caplog):
    """Behaviour is unchanged: the native handler still wins, and the refusal is recorded."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    backend = _backend(tmp_path, OWN)
    with rendering_with_backend(backend):
        template_libraries.load_libraries(list(OWN))
        refused = template_libraries._engine_state(
            "_refused_tags", template_libraries._refused_tags
        )
        debug = _refusal_messages(caplog, logging.DEBUG)

    assert refused.get("djust_components"), "djust_components refused no native tag"
    assert debug, "the expected refusals vanished from the log entirely"
    assert all("djust's own template library" in message for message in debug)


def test_the_refusal_is_logged_once_per_tag_not_once_per_load(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    backend = _backend(tmp_path, OWN)
    with rendering_with_backend(backend):
        template_libraries.load_libraries(list(OWN))
        first = len(_refusal_messages(caplog, logging.DEBUG))
        for _ in range(3):
            template_libraries.load_libraries(list(OWN))

    assert len(_refusal_messages(caplog, logging.DEBUG)) == first


class _Builtin:
    def render(self, args, context):
        return "builtin"


def _third_party(tmp_path, monkeypatch, module_name):
    """A library defined in ``module_name`` that registers the name djust provides."""
    module = ModuleType(module_name)
    module.register = Library()

    def clash():
        return "library"

    clash.__module__ = module_name
    module.register.simple_tag(clash, name=CLASH)
    monkeypatch.setitem(sys.modules, module_name, module)
    return _backend(tmp_path, {"clash_tags": module_name})


@pytest.fixture
def native_clash_tag():
    previous = _rust.set_registry_namespace(0)
    _rust.register_tag_handler(CLASH, _Builtin())
    try:
        yield
    finally:
        _rust.set_registry_namespace(0)
        _rust.unregister_tag_handler(CLASH)
        _rust.set_registry_namespace(previous)


@pytest.mark.parametrize(
    "module_name",
    [
        "thirdparty_pkg.templatetags.clash_tags",
        # Looks like djust's namespace but is not under ``djust.``: still a real clash.
        "djust_extras.templatetags.clash_tags",
        "djustlike.templatetags.clash_tags",
    ],
)
def test_a_third_party_clash_still_warns_once(
    tmp_path, monkeypatch, caplog, native_clash_tag, module_name
):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    backend = _third_party(tmp_path, monkeypatch, module_name)
    for _ in range(3):
        with rendering_with_backend(backend):
            template_libraries.load_libraries(["clash_tags"])

    warnings = _refusal_messages(caplog, logging.WARNING)
    assert len(warnings) == 1, warnings
    assert f"registers tag '{CLASH}'" in warnings[0]
    assert _refusal_messages(caplog, logging.DEBUG) == []


def test_a_real_clash_is_not_buried_among_djust_s_own(
    tmp_path, monkeypatch, caplog, native_clash_tag
):
    """The point of the change: load both djust libraries AND a clashing third-party one."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    module_name = "thirdparty_pkg.templatetags.clash_tags"
    third = _third_party(tmp_path, monkeypatch, module_name)
    libraries = dict(OWN, clash_tags=module_name)
    backend = _backend(tmp_path, libraries)
    assert third is not None
    with rendering_with_backend(backend):
        template_libraries.load_libraries(list(libraries))

    warnings = _refusal_messages(caplog, logging.WARNING)
    assert len(warnings) == 1, warnings
    assert f"registers tag '{CLASH}'" in warnings[0]
