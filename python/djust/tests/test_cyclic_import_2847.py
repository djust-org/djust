"""#2847 — CodeQL ``py/cyclic-import``: ``template/backend.py`` <-> ``template_libraries.py``.

Both modules already imported each other lazily — inside a method, not at
module scope — specifically so the cycle never bit at runtime; CodeQL's
``py/cyclic-import`` still flags it because it builds the import graph from
every ``import`` statement regardless of where it sits. The fix replaces
``template_libraries._template_backend()``'s ``isinstance(engine,
DjustTemplateBackend)`` fallback with a duck-typed marker attribute, which
removes the only import ``template_libraries.py`` made of ``djust.template``
— breaking the cycle rather than merely deferring it.

Two things are pinned: the import graph itself (a regression on the fix
reintroducing an import in either direction), and the behaviour the removed
``isinstance`` check existed for (the fallback still finds a configured djust
backend among ``django.template.engines`` when no backend is active).
"""

from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys

import pytest
from django.template import engines


class TestNoImportCycleBetweenTheTwoModules:
    """The static import graph, not just the runtime behaviour (#2847)."""

    def test_template_libraries_does_not_import_djust_template(self) -> None:
        """The only prior edge back from ``template_libraries.py`` — an
        ``isinstance`` check needing ``DjustTemplateBackend`` — is gone.
        Importing this module alone must not pull in ``djust.template`` or
        ``djust.template.backend`` as a module-scope side effect.

        Run in a SUBPROCESS, not by popping ``sys.modules`` in-process:
        re-importing ``djust.template_libraries`` here would build a second
        module object with its own fresh ``_installed_cache`` / registry
        globals, discarding whatever state another test in this process
        warmed and depends on (the #1646 class, this time as the polluter
        rather than the victim) — a fresh interpreter is also the more
        faithful claim: "importing this module from scratch never imports
        ``djust.template``", not "re-importing it here doesn't".
        """
        script = (
            "import sys;"
            "import djust.template_libraries;"
            "print('djust.template' in sys.modules);"
            "print('djust.template.backend' in sys.modules)"
        )
        env = dict(os.environ, DJANGO_SETTINGS_MODULE="demo_project.settings")
        env.pop("PYTEST_CURRENT_TEST", None)
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(pathlib.Path(__file__).resolve().parents[3] / "examples" / "demo_project"),
        )
        assert proc.returncode == 0, proc.stderr
        lines = proc.stdout.strip().splitlines()
        assert lines == ["False", "False"], (proc.stdout, proc.stderr)

    def test_the_source_names_no_cyclic_import(self) -> None:
        """Grep pin (#2727): the specific import CodeQL cited must not come
        back. A regex over the source is cheaper than re-deriving the whole
        import graph and is exactly the line the alert names.
        """
        import re

        from djust import template_libraries

        source = importlib.import_module(template_libraries.__name__).__file__
        assert source is not None
        text = open(source).read()
        assert not re.search(r"from\s+\.template\.backend\s+import\s+DjustTemplateBackend", text)
        assert not re.search(r"from\s+djust\.template\.backend\s+import", text)

    def test_the_marker_attribute_exists_on_the_backend(self) -> None:
        from djust.template.backend import DjustTemplateBackend

        assert DjustTemplateBackend._is_djust_template_backend is True


class TestTemplateBackendFallbackStillFindsAConfiguredEngine:
    """The behaviour the removed ``isinstance`` check protected."""

    @pytest.fixture(autouse=True)
    def _clean_registry_state(self, settings):
        from djust.template_libraries import _active_backend, _registry_backends

        assert _active_backend() is None, "test must run with no ACTIVE backend registered"

        original_templates = settings.TEMPLATES
        original_engines_cache = dict(engines.__dict__)
        yield
        settings.TEMPLATES = original_templates
        engines.__dict__.clear()
        engines.__dict__.update(original_engines_cache)
        _registry_backends.clear()

    def test_a_configured_djust_backend_is_found_by_the_marker(self, settings) -> None:
        settings.TEMPLATES = [
            {
                "BACKEND": "djust.template.backend.DjustTemplateBackend",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {},
            }
        ]
        engines._engines = {}
        engines.__dict__.pop("templates", None)

        from djust.template.backend import DjustTemplateBackend
        from djust.template_libraries import _template_backend

        found = _template_backend()

        assert isinstance(found, DjustTemplateBackend)
        assert getattr(found, "_is_djust_template_backend", False) is True

    def test_no_djust_backend_configured_falls_back_to_the_loader(self, settings) -> None:
        settings.TEMPLATES = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {},
            }
        ]
        engines._engines = {}
        engines.__dict__.pop("templates", None)

        from djust.template_libraries import _LoaderBackend, _template_backend

        assert isinstance(_template_backend(), _LoaderBackend)
