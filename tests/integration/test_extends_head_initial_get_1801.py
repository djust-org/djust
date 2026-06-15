"""Regression test for #1801 — pages using ``{% extends %}`` must serve the
base template's ``<head>`` (``<!doctype>``, ``<head>``, ``<title>``, ``<style>``)
on the *initial* HTTP GET, not just the ``dj-root`` fragment.

Root cause (verified symptom-up against the real ``djust new`` scaffold):
``get_template()`` collected the template search directories with a hardcoded
backend-name check that recognized ONLY the stock
``django.template.backends.django.DjangoTemplates`` backend. The scaffold (and
any project) that configures djust's own backend
(``djust.template.backend.DjustTemplateBackend``) with ``APP_DIRS=True`` got
its app-template directories DROPPED, so the Rust
``resolve_template_inheritance`` raised ``Template not found`` — which was
swallowed by a broad ``except Exception`` that logged only at DEBUG and set
``self._full_template = None``. ``render_full_template`` then fell through to
its ``else`` (``return self.render(request)``) → the bare ``dj-root`` fragment
with no shell/head.

The captured swallowed exception was::

    RuntimeError: Template error: Template not found: demo/index.html
    Searched in:
      - /private/tmp/.../demo/templates

The fix recognizes the djust backend(s) in the APP_DIRS dir-collection
(``utils._APP_DIRS_TEMPLATE_BACKENDS``), shares the one
``get_template_dirs()`` helper between ``get_template()`` and
``render_full_template`` step 2 (#1646 parallel-path cure), and narrows /
de-silences the ``except`` so a resolution failure logs at WARNING instead of
silently degrading to fragment-only.

These tests drive the REAL initial-GET path
(``LiveView.as_view()`` → ``get`` → ``render_full_template``) with djust's
OWN template backend configured — the exact configuration the bug reproduces
under — so the gate-off (unfixed code) fails fragment-only.
"""

from __future__ import annotations

import logging
import os
import shutil
from importlib import import_module

import pytest
from django.apps import apps
from django.conf import settings
from django.template import loader
from django.test import override_settings

from djust.live_view import LiveView
from djust.utils import clear_template_dirs_cache


# ---------------------------------------------------------------------------
# Scaffold-shaped templates: Django ``{# comments #}``, ``{% djust_client_config %}``,
# ``{% block title %}`` + ``{% load %}`` inside ``<head>``, ``{% csrf_token %}``,
# ``{% for %}/{% empty %}/{% endfor %}`` and nested ``{% if %}`` in the body —
# the exact mix the ``djust new`` scaffold emits and the bug reproduced against.
# ---------------------------------------------------------------------------

_BASE_HTML = """\
{% load live_tags %}
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {# djust client bootstrap comment — exercises the Rust comment path #}
    {% djust_client_config %}
    <title>{% block title %}Scaffold Demo{% endblock %}</title>
    <style>
        body { background: #0a0a14; }
    </style>
    {% load static %}
</head>
<body class="text-gray-200">
    <nav class="navbar"><span class="brand">Scaffold Demo</span></nav>
    <main>
        {% block content %}{% endblock %}
    </main>
</body>
</html>
"""

_INDEX_HTML = """\
{% extends "demo1801/base.html" %}

{% block content %}
{% csrf_token %}
<div dj-root dj-view="tests.integration.test_extends_head_initial_get_1801.ExtendsHeadView">
    <h1 class="page-marker">Items: {{ total_count }}</h1>
    <div class="space-y-2">
    {% for item in items %}
        <span class="{% if item.done %}done{% else %}todo{% endif %}">{{ item.name }}</span>
    {% empty %}
        <span class="empty">No items</span>
    {% endfor %}
    </div>
</div>
{% endblock %}
"""


class ExtendsHeadView(LiveView):
    """A LiveView whose template ``{% extends %}`` a base with a full ``<head>``."""

    template_name = "demo1801/index.html"

    def mount(self, request, **kwargs):
        self.total_count = 2
        self.items = [
            {"id": 1, "name": "Alpha", "done": False},
            {"id": 2, "name": "Beta", "done": True},
        ]


# ---------------------------------------------------------------------------
# Fixture: write the two templates into a REGISTERED app's templates dir so
# APP_DIRS discovery finds them, and switch settings.TEMPLATES to djust's OWN
# backend (the configuration the bug reproduces under). Clears both Django's
# engine cache AND djust's template-dirs lru_cache so the swap takes effect.
# ---------------------------------------------------------------------------

_DJUST_BACKEND = "djust.template.backend.DjustTemplateBackend"


@pytest.fixture
def _djust_backend_app_templates():
    # Pick a registered app that has a templates/ dir (APP_DIRS will find it).
    app_config = apps.get_app_config("demo_app")
    app_templates = os.path.join(app_config.path, "templates")
    dest_dir = os.path.join(app_templates, "demo1801")
    os.makedirs(dest_dir, exist_ok=True)
    base_path = os.path.join(dest_dir, "base.html")
    index_path = os.path.join(dest_dir, "index.html")
    with open(base_path, "w") as f:
        f.write(_BASE_HTML)
    with open(index_path, "w") as f:
        f.write(_INDEX_HTML)

    # Switch to djust's OWN backend with APP_DIRS=True — the exact config the
    # scaffold ships and the bug reproduces under. Use override_settings as a
    # context manager so TEMPLATES is restored deterministically; clear djust's
    # process-global get_template_dirs() lru_cache BOTH after entering AND after
    # exiting the override so neither this test's dirs nor a stale pre-test
    # value leaks into other tests (the cache doesn't observe settings changes).
    override = override_settings(
        TEMPLATES=[
            {
                "BACKEND": _DJUST_BACKEND,
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.request",
                    ],
                },
            }
        ]
    )
    override.enable()
    loader.engines._engines = {}
    clear_template_dirs_cache()
    try:
        yield dest_dir
    finally:
        shutil.rmtree(dest_dir, ignore_errors=True)
        override.disable()
        loader.engines._engines = {}
        clear_template_dirs_cache()


def _attach_session(request):
    engine = import_module(settings.SESSION_ENGINE)
    request.session = engine.SessionStore()
    request.session.save()


@pytest.mark.django_db
def test_extends_get_includes_base_head(rf, _djust_backend_app_templates):
    """The initial HTTP GET of an ``{% extends %}`` LiveView (under djust's own
    template backend, APP_DIRS=True) returns the FULL document — ``<!doctype>``,
    ``<head>``, ``<title>`` and the base ``<style>`` — not just the ``dj-root``
    fragment (#1801)."""
    request = rf.get("/")
    _attach_session(request)

    response = ExtendsHeadView.as_view()(request)
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "<!DOCTYPE html>" in body or "<!doctype html>" in body.lower(), (
        "Initial GET of an {% extends %} LiveView must include the base "
        "template's <!doctype> — it was served as a dj-root fragment only (#1801)."
    )
    assert "<head>" in body, "Initial GET must include the base template's <head> (#1801)."
    assert "<title>Scaffold Demo</title>" in body, (
        "Initial GET must include the base template's <title> (#1801)."
    )
    assert "background: #0a0a14" in body, (
        "Initial GET must include the base template's <style> (#1801)."
    )
    # …and the dj-root content must still be spliced into the shell.
    assert "page-marker" in body, "The dj-root content must be spliced into the shell."
    assert "Items: 2" in body, "Mounted state must render into the dj-root."
    assert "Alpha" in body and "Beta" in body, "Looped items must render."


@pytest.mark.django_db
def test_full_template_is_populated_for_extends(rf, _djust_backend_app_templates):
    """After ``get_template()`` runs for an ``{% extends %}`` view (djust
    backend, APP_DIRS=True), the resolved full document (with ``<head>``) must
    be stored on ``self._full_template`` — NOT ``None`` (the silent-catch
    symptom of #1801)."""
    view = ExtendsHeadView()
    view.mount(rf.get("/"))
    view.get_template()

    assert view._full_template is not None, (
        "get_template() must populate _full_template with the resolved full "
        "document for an {% extends %} view; None means the in-flow resolution "
        "silently degraded to fragment-only (#1801)."
    )
    assert "<head>" in view._full_template
    assert "<!DOCTYPE html>" in view._full_template


@pytest.mark.django_db
def test_app_template_dirs_collected_for_djust_backend():
    """``get_template_dirs()`` must collect app-template directories when the
    configured backend is djust's OWN backend with APP_DIRS=True — not only the
    stock Django backend. This is the root-cause unit pin for #1801; it gates
    off cleanly (the unfixed hardcoded backend-name check returns no app dirs).
    """
    from djust.utils import get_template_dirs

    override = override_settings(
        TEMPLATES=[
            {
                "BACKEND": _DJUST_BACKEND,
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {"context_processors": []},
            }
        ]
    )
    override.enable()
    clear_template_dirs_cache()
    try:
        dirs = get_template_dirs()
    finally:
        override.disable()
        clear_template_dirs_cache()

    app_templates = os.path.join(apps.get_app_config("demo_app").path, "templates")
    assert app_templates in dirs, (
        "get_template_dirs() must include the app templates dir for the djust "
        "backend with APP_DIRS=True; the hardcoded DjangoTemplates-only check "
        "dropped them, which is the #1801 root cause."
    )


@pytest.mark.django_db
def test_resolution_failure_is_logged_not_silent(rf, _djust_backend_app_templates, caplog):
    """When the template-inheritance resolution path genuinely fails, the
    fallback must LOG at WARNING (not swallow at DEBUG) — so a render-path
    error can never again degrade to fragment-only without a trace (#1801).
    We force a failure by monkeypatching the Rust resolver to raise.
    """
    import djust._rust as _rust
    import djust.mixins.template as template_mod

    view = ExtendsHeadView()
    view.mount(rf.get("/"))

    orig = _rust.resolve_template_inheritance

    def _boom(*args, **kwargs):
        raise RuntimeError("forced resolution failure for #1801 test")

    _rust.resolve_template_inheritance = _boom
    try:
        with caplog.at_level(logging.WARNING, logger=template_mod.logger.name):
            view.get_template()
    finally:
        _rust.resolve_template_inheritance = orig

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "resolution failed" in r.getMessage().lower()
        or "forced resolution failure for #1801 test" in r.getMessage()
        for r in warnings
    ), (
        "A failure in the template-inheritance resolution path must be logged "
        "at WARNING/ERROR (no silent swallow) so the fragment-only degradation "
        "is never invisible again (#1801). WARNING records: "
        f"{[r.getMessage() for r in warnings]}"
    )
