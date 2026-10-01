"""#3307 -- a TEMPLATES entry is recognised by the class it names, not by one spelling.

``ContextMixin._get_context_processors`` compared ``BACKEND`` against a literal,
so ``djust.template.backend.DjustTemplateBackend`` (the canonical path, and the
one ``djust new`` historically emitted) silently got no context processors while
``djust.template_backend.DjustTemplateBackend`` (the shim) did.
"""

import pytest
from django.test import override_settings

from djust import LiveView
from djust.mixins.context import ContextMixin
from djust.template.backend import DjustTemplateBackend
from djust.utils import is_app_dirs_template_backend

PROCESSOR = "python.tests.test_template_backend_spelling_3307.marker_processor"
CANONICAL = "djust.template.backend.DjustTemplateBackend"
SHIM = "djust.template_backend.DjustTemplateBackend"
DJANGO = "django.template.backends.django.DjangoTemplates"
SUBCLASS = __name__ + ".MyDjustBackend"


class MyDjustBackend(DjustTemplateBackend):
    """A project-level subclass resolves to the same class family."""


def marker_processor(request):
    return {"marker_3307": "present"}


def _templates(backend):
    return [
        {
            "BACKEND": backend,
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {"context_processors": [PROCESSOR]},
        }
    ]


@pytest.mark.parametrize("backend", [CANONICAL, SHIM, SUBCLASS, DJANGO])
def test_context_processors_found_for_every_spelling(backend):
    with override_settings(TEMPLATES=_templates(backend)):
        assert ContextMixin._get_context_processors(LiveView()) == [PROCESSOR]


def test_canonical_and_shim_resolve_to_one_class():
    from django.utils.module_loading import import_string

    assert import_string(CANONICAL) is import_string(SHIM)


def test_unrelated_and_unimportable_backends_are_ignored():
    entries = [
        {"BACKEND": "no.such.Backend", "OPTIONS": {"context_processors": ["x.y"]}},
        {"BACKEND": 42, "OPTIONS": {"context_processors": ["x.y"]}},
        {"BACKEND": "os.path.join", "OPTIONS": {"context_processors": ["x.y"]}},
    ]
    with override_settings(TEMPLATES=entries):
        assert ContextMixin._get_context_processors(LiveView()) == []


def test_first_recognised_entry_with_processors_wins_regardless_of_spelling():
    entries = [
        {"BACKEND": CANONICAL, "OPTIONS": {}},
        {"BACKEND": DJANGO, "OPTIONS": {"context_processors": [PROCESSOR]}},
    ]
    with override_settings(TEMPLATES=entries):
        assert ContextMixin._get_context_processors(LiveView()) == [PROCESSOR]


@pytest.mark.parametrize("backend", [CANONICAL, SHIM, SUBCLASS, DJANGO])
def test_app_dirs_gate_recognises_every_spelling(backend):
    assert is_app_dirs_template_backend(backend)


@pytest.mark.parametrize("backend", ["no.such.Backend", "os.path.join", 42, None])
def test_app_dirs_gate_rejects_others(backend):
    assert not is_app_dirs_template_backend(backend)
