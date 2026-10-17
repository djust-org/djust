"""The 1.x module shims removed in 2.0 (#3500) stay removed, and the TEMPLATES
path that named one fails startup with its replacement."""

import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from djust.apps import _reject_removed_template_backends

CANONICAL = "djust.template.backend.DjustTemplateBackend"
REMOVED = "djust.template_backend.DjustTemplateBackend"


@pytest.mark.parametrize(
    "module", ["djust.template_backend", "djust.state_backend", "djust._view_resolution"]
)
def test_the_shim_modules_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_the_removed_backend_path_names_its_replacement():
    templates = [
        {"BACKEND": "django.template.backends.django.DjangoTemplates"},
        {"BACKEND": REMOVED},
    ]
    with override_settings(TEMPLATES=templates):
        with pytest.raises(ImproperlyConfigured) as exc:
            _reject_removed_template_backends()
    message = str(exc.value)
    assert "TEMPLATES[1]['BACKEND']" in message
    assert repr(CANONICAL) in message


@pytest.mark.parametrize("backend", [CANONICAL, "no.such.Backend", 42, None])
def test_other_backends_are_left_to_django(backend):
    with override_settings(TEMPLATES=[{"BACKEND": backend}, "not-a-dict"]):
        _reject_removed_template_backends()
