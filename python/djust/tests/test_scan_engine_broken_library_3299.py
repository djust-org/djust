"""#3299: the Djust-only scan engine registers installed-app tag libraries, and a
library that fails to import must cost the scan only that library.

``get_installed_libraries`` raises ``InvalidTemplateLibrary`` for the first broken
``templatetags`` module; used as-is it aborted ``manage.py check`` and the
strict-policy recovery scan for a project that never had a problem before.
"""

from __future__ import annotations

import sys
import textwrap

import pytest
from django.apps import apps
from django.test import override_settings

from djust import LiveView
from djust._template_bindings import django_engine
from djust.checks.bindings import check_event_bindings
from djust.validation import _RECOVERY_HANDLERS, _recovery_scan

APP = "broken_lib_app_3299"


@pytest.fixture
def project(tmp_path):
    package = tmp_path / APP / "templatetags"
    package.mkdir(parents=True)
    (tmp_path / APP / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "broken.py").write_text("import a_missing_optional_dependency_3299\n")
    (package / "fine.py").write_text(
        textwrap.dedent(
            """
            from django import template

            register = template.Library()


            @register.simple_tag
            def ok():
                return "ok"
            """
        )
    )
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "base.html").write_text(
        "{% load static fine %}<div dj-root>{% block c %}{% endblock %}</div>"
    )
    (templates / "page.html").write_text(
        '{% extends "base.html" %}{% block c %}<button dj-auto-recover="restore">x</button>{% endblock %}'
    )
    sys.path.insert(0, str(tmp_path))
    from django.conf import settings

    config = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(templates)],
            "APP_DIRS": False,
            "OPTIONS": {},
        }
    ]
    try:
        with override_settings(TEMPLATES=config, INSTALLED_APPS=[*settings.INSTALLED_APPS, APP]):
            yield
    finally:
        sys.path.remove(str(tmp_path))
        for name in [m for m in sys.modules if m == APP or m.startswith(APP + ".")]:
            del sys.modules[name]
        apps.clear_cache()
        _RECOVERY_HANDLERS.clear()


def test_a_broken_templatetags_module_does_not_stop_the_scan(project):
    engine = django_engine()
    assert engine is not None
    assert "static" in engine.template_libraries  # still registered
    assert "fine" in engine.template_libraries
    assert "broken" not in engine.template_libraries
    check_event_bindings(None)  # must not raise


def test_recovery_scan_still_reads_templates_that_load_the_working_libraries(project):
    class Page(LiveView):
        template_name = "page.html"

    _RECOVERY_HANDLERS.clear()
    assert _recovery_scan(Page) == (frozenset({"restore"}), True)


def test_an_engine_that_cannot_be_built_skips_the_scan_instead_of_raising(project, monkeypatch):
    import django.template

    def explode(*args, **kwargs):
        raise RuntimeError("no engine")

    monkeypatch.setattr(django.template, "Engine", explode)

    class Page(LiveView):
        template_name = "page.html"

    _RECOVERY_HANDLERS.clear()
    assert django_engine() is None
    assert _recovery_scan(Page) == (frozenset(), False)
    check_event_bindings(None)  # degrades to the T023 skip, never raises
