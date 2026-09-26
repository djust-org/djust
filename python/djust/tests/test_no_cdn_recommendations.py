"""Task 10 (vendored assets): djust must not generate or recommend loading
third-party code from the Tailwind CDN.

The scaffold's example template (``djust_theme.py``) used to write
``<link href="https://cdn.tailwindcss.com" ...>`` into generated projects,
and the C011 system check's development hint used to suggest the CDN as a
fallback. Both now point users at the Tailwind CLI build instead.

Controller ruling R17 (2026-09-25): an earlier version of this file ran a
repo-wide ``git grep -n "cdn.tailwindcss.com" -- python/djust`` sweep. That
over-broad test also flagged the C010 detector's legitimate, literal use of
the CDN host it scans *user* templates for -- detector code has to name the
host it detects. These are targeted tests of what djust itself emits or
recommends instead.
"""

import pytest
from django.core.management import call_command

from djust.tests.theming_conftest import *  # noqa: F401,F403 — ensure Django is configured

pytestmark = pytest.mark.theming


def test_scaffold_example_template_has_no_cdn_and_links_static_output(tmp_path, monkeypatch):
    """`djust_theme init --with-examples` must not write a CDN <link>; it
    should reference the static, Tailwind-CLI-built output.css instead.
    """
    monkeypatch.chdir(tmp_path)
    call_command("djust_theme", "init", "--with-examples")

    content = (tmp_path / "templates" / "examples" / "theme_example.html").read_text()
    assert "cdn.tailwindcss.com" not in content
    assert "{% load static" in content
    assert "{% static 'css/output.css' %}" in content


def _setup_djust_tailwind_project(tmp_path, settings, monkeypatch, debug):
    """Minimal project layout that makes C011 (missing/stale output.css) fire.

    Mirrors the helper in python/tests/test_checks.py::TestC011MissingCompiledCss.
    """
    config_file = tmp_path / "tailwind.config.js"
    config_file.write_text("module.exports = { content: ['./templates/**/*.html'] }")
    static_dir = tmp_path / "static" / "css"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "input.css").write_text("@import 'tailwindcss';")
    monkeypatch.chdir(tmp_path)
    settings.DEBUG = debug
    settings.STATICFILES_DIRS = [str(tmp_path / "static")]
    settings.ASGI_APPLICATION = "myproject.asgi.application"
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]


def test_c011_dev_hint_has_no_cdn_recommendation(tmp_path, settings, monkeypatch):
    """C011's development-mode hint no longer suggests the Tailwind CDN as
    a fallback.
    """
    _setup_djust_tailwind_project(tmp_path, settings, monkeypatch, debug=True)

    from djust.checks import check_configuration

    errors = check_configuration(None)
    c011 = [e for e in errors if e.id == "djust.C011"]
    assert len(c011) == 1
    assert "cdn.tailwindcss.com" not in c011[0].hint
    assert "Tailwind CDN" not in c011[0].hint


def test_c011_production_hint_has_no_cdn_and_keeps_tailwind_cli(tmp_path, settings, monkeypatch):
    """C011's production-mode hint has never recommended the CDN, and must
    keep pointing at the Tailwind CLI build.
    """
    _setup_djust_tailwind_project(tmp_path, settings, monkeypatch, debug=False)

    from djust.checks import check_configuration

    errors = check_configuration(None)
    c011 = [e for e in errors if e.id == "djust.C011"]
    assert len(c011) == 1
    assert "cdn.tailwindcss.com" not in c011[0].hint
    assert "tailwindcss -i" in c011[0].hint
