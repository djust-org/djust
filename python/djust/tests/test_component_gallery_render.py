"""The component gallery must render and style its components.

Two independent defects made the gallery useless, and neither raised anything
a caller could see:

1. Every example snippet was compiled with ``django.template.base.Template``,
   which resolves through ``Engine.get_default()`` and so requires a
   ``DjangoTemplates`` entry in ``TEMPLATES``. A project scaffolded by
   ``djust new`` configures only ``DjustTemplateBackend``, so **every**
   component rendered as a red "Render error" placeholder — 245 of them.

2. The theme integration imported ``djust_theming.*``, which is not a Python
   package (``djust_theming`` is only the static namespace). Every import
   raised ``ModuleNotFoundError`` into a bare ``except``, so the gallery
   injected no theme CSS at all and its preset dropdown listed one entry.

Both are the "silent fallback" shape: the page rendered, returned 200, and
looked plausibly like a gallery that simply had nothing to show.
"""

import re

import pytest

from djust.components.gallery.views import (
    _gallery_template_backend,
    _get_theme_css,
    _get_theme_options,
)


# ---------------------------------------------------------------------------
# 1. Snippets compile through the project's own backend
# ---------------------------------------------------------------------------


def test_gallery_template_backend_resolves():
    """A backend must be found even when the project has no DjangoTemplates.

    This is the exact configuration `djust new` scaffolds.
    """
    backend = _gallery_template_backend()

    assert backend is not None
    assert hasattr(backend, "from_string")


def test_gallery_backend_compiles_a_component_snippet():
    """The resolved backend must actually compile a `{% load %}`-ed snippet."""
    template = _gallery_template_backend().from_string(
        "{% load djust_components %}{% dj_button 'Save' %}"
    )

    rendered = template.render({})

    assert "Save" in rendered, rendered[:200]


def test_backend_is_not_resolved_by_guessing_the_alias():
    """The alias comes from the handler, not a hardcoded 'django'.

    Django derives the alias from the backend path when NAME is absent, so a
    lone DjustTemplateBackend registers as `template_backend`. Indexing
    `engines["django"]` raises KeyError and lands back at "Render error".
    """
    from django.template import engines

    aliases = list(getattr(engines, "templates", {}).keys())

    assert aliases, "no template backends configured at all"
    # Whatever the alias is, the helper must return the engine it names.
    backend = _gallery_template_backend()
    assert backend in [engines[a] for a in aliases]


# ---------------------------------------------------------------------------
# 2. The theme integration is live, not silently dead
# ---------------------------------------------------------------------------


def test_theme_css_is_generated():
    """`djust_theming` is not a module; the import must resolve.

    An empty string here means the gallery renders without design tokens,
    which is what made every component unstyled.
    """
    css = _get_theme_css(preset="dracula", design_system="material", mode="dark")

    assert css, "no theme CSS generated — the gallery would render unstyled"
    assert "--background" in css


def test_theme_options_are_enumerated():
    """A one-entry preset list is the signature of the swallowed import."""
    presets, systems = _get_theme_options()

    assert len(presets) > 1, f"expected many presets, got {presets}"
    assert "default" in presets
    assert systems, "no design systems enumerated"


def test_theming_modules_are_importable_where_the_gallery_expects_them():
    """Pin the package path the gallery imports from.

    The static namespace is `djust_theming/`; the Python package is
    `djust.theming`. Conflating them is what broke this, and the failure is
    invisible because of the try/except.
    """
    import importlib

    for module in (
        "djust.theming.manager",
        "djust.theming.presets",
        "djust.theming.theme_packs",
    ):
        assert importlib.import_module(module) is not None

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("djust_theming")


# ---------------------------------------------------------------------------
# 3. End to end — no component renders as the error placeholder
# ---------------------------------------------------------------------------


def test_form_category_renders_without_placeholder_errors(rf):
    """Render a full category and assert no variant fell back to the error box."""
    from djust.components.gallery.views import gallery_category_view

    request = rf.get("/components/form/")
    response = gallery_category_view(request, "form")

    assert response.status_code == 200
    body = response.content.decode()

    failures = re.findall(r"variant-label\">([^<]+)<.*?Render error", body, re.DOTALL)
    assert not failures, f"components fell back to 'Render error': {failures[:5]}"
