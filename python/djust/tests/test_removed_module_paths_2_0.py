"""The 1.x module shims and announced removals (#3500, #4025) stay removed, and
a TEMPLATES path that named one fails startup with its replacement."""

import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from djust._removed_settings import reject_removed_settings

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
            reject_removed_settings()
    message = str(exc.value)
    assert "TEMPLATES[1]['BACKEND']" in message
    assert repr(CANONICAL) in message
    assert "removed in djust 2.0" in message


@pytest.mark.parametrize("backend", [CANONICAL, "no.such.Backend", 42, None])
def test_other_backends_are_left_to_django(backend):
    with override_settings(TEMPLATES=[{"BACKEND": backend}, "not-a-dict"]):
        reject_removed_settings()


# --- #4025: the eight component mixins and social_auth_providers --------------

REMOVED_MIXINS = [
    "accordion",
    "tabs",
    "modal",
    "collapsible",
    "sheet",
    "dropdown",
    "tooltip",
    "carousel",
]
REMOVED_PROCESSOR = "djust.auth.social.social_auth_providers"


@pytest.mark.parametrize("name", REMOVED_MIXINS)
def test_the_component_mixins_are_gone(name):
    import djust.components
    import djust.components.mixins

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"djust.components.mixins.{name}")
    for symbol in (f"{name.capitalize()}Mixin", f"{name.capitalize()}State"):
        for module in (djust.components, djust.components.mixins):
            assert not hasattr(module, symbol), (module.__name__, symbol)
            assert symbol not in module.__all__


@pytest.mark.parametrize("name", REMOVED_MIXINS)
def test_the_descriptor_replacements_remain(name):
    import djust.components
    from djust.components import descriptors

    assert hasattr(descriptors, name.capitalize())
    assert name.capitalize() in djust.components.__all__


def test_the_kept_mixins_are_still_exported():
    from djust.components.mixins import ComponentMixin, DataTableMixin, TypedState

    assert ComponentMixin and DataTableMixin and TypedState


def test_social_auth_providers_is_gone():
    import djust.auth

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("djust.auth.social")
    assert "social_auth_providers" not in djust.auth.__all__
    with pytest.raises(AttributeError):
        djust.auth.social_auth_providers


def test_the_removed_context_processor_names_its_replacement():
    templates = [
        {"BACKEND": CANONICAL, "OPTIONS": {"context_processors": ["a.b.c"]}},
        {
            "BACKEND": CANONICAL,
            "OPTIONS": {"context_processors": ["a.b.c", REMOVED_PROCESSOR]},
        },
    ]
    with override_settings(TEMPLATES=templates):
        with pytest.raises(ImproperlyConfigured) as exc:
            reject_removed_settings()
    message = str(exc.value)
    assert "TEMPLATES[1]['OPTIONS']['context_processors']" in message
    assert repr(REMOVED_PROCESSOR) in message
    assert "{% auth_providers auth %}" in message
    assert "removed in djust 2.0" in message


@pytest.mark.parametrize(
    "options",
    [
        None,
        "not-a-dict",
        {},
        {"context_processors": None},
        {"context_processors": "djust.auth.social.social_auth_providers"},
        {"context_processors": ["a.b.c", 42, None]},
        {"context_processors": ("myapp.social.social_auth_providers",)},
    ],
)
def test_other_context_processors_are_left_to_django(options):
    with override_settings(TEMPLATES=[{"BACKEND": CANONICAL, "OPTIONS": options}]):
        reject_removed_settings()
