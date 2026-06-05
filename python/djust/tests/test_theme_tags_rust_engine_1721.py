"""Regression tests for #1721 — documented {% theme_X %} tags must work in
the Rust template engine.

The theming guide (docs.djust.org/theming) documents the theme switcher as
the template tag ``{% theme_panel %}`` (with ``{% load theme_tags %}``).
Before the fix, rendering that tag through the Rust template engine — the
engine djust uses for LiveView templates — raised::

    RuntimeError: Template error: Unsupported template tag '{% theme_panel %}'.
    Register a handler via djust._rust.register_tag_handler(), or use
    Django's template engine instead.

The ``{{ theme_panel }}`` context-string form (#1435) rendered fine, so docs
and engine disagreed (cf. #1452 on ``{{ theme_head }}`` vs ``{% theme_head %}``).

Fix (option A): register the documented theme tags as Rust tag handlers so
the ``{% theme_X %}`` form works in the Rust engine. The handler delegates to
the same ``theme_tags.py`` ``@register.simple_tag`` body the context-string
form uses, so the two forms produce equivalent output for default args, and
the customization-with-args form (``{% theme_panel show_packs=False %}``)
works too.

These tests render through ``djust._rust.render_template`` — the faithful
Rust-engine entry point that the reporter's 500 came from (it is the path
that raises the "Unsupported template tag" error at
``crates/djust_templates/src/renderer.rs``).
"""

from __future__ import annotations

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "djust.theming",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        STATIC_URL="/static/",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "DIRS": [],
                "OPTIONS": {"context_processors": []},
            }
        ],
    )
    django.setup()

import pytest

from djust._rust import has_tag_handler, render_template

pytestmark = pytest.mark.theming


# The five user-facing documented tags from #1721 / companion #1722.
DOCUMENTED_TAGS = [
    "theme_panel",
    "theme_head",
    "theme_switcher",
    "theme_mode_toggle",
    "theme_preset_selector",
]


def _render_via_rust(source: str) -> str:
    """Render a template fragment through the Rust template engine.

    This is the same engine path that renders LiveView templates and that
    raised the reporter's 500 — it goes through the Rust parser/renderer
    which either dispatches to a registered tag handler or raises the
    "Unsupported template tag" error.
    """
    return render_template(source, {})


def _render_tag_body(tag_name: str, **kwargs) -> str:
    """Render the corresponding ``theme_tags.py`` simple_tag body directly.

    This is exactly what the ``{{ theme_X }}`` context-string form (#1435)
    calls via the context processor: ``fn({"request": request})``. With no
    request the manager falls back to defaults (``get_theme_manager(None)``).
    """
    from djust.theming.templatetags import theme_tags

    fn = getattr(theme_tags, tag_name)
    return str(fn({"request": None}, **kwargs))


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tag_name", DOCUMENTED_TAGS)
def test_theme_tag_is_registered_with_rust_engine(tag_name):
    """Each documented theme tag has a Rust tag handler registered.

    ``DjustThemingConfig.ready()`` runs at ``django.setup()``; the handlers
    must be registered by then.
    """
    assert has_tag_handler(tag_name), (
        f"{{% {tag_name} %}} has no Rust tag handler — the documented tag "
        f"form is unusable in the Rust engine (#1721)."
    )


# ---------------------------------------------------------------------------
# Rust-engine rendering no longer raises (the reporter's 500)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tag_name", DOCUMENTED_TAGS)
def test_theme_tag_renders_via_rust_engine_without_error(tag_name):
    """Rendering ``{% theme_X %}`` through the Rust engine must not raise.

    Pre-fix this raised ``RuntimeError: Unsupported template tag
    '{% theme_X %}'`` — the reporter's 500.
    """
    out = _render_via_rust("{% " + tag_name + " %}")
    assert isinstance(out, str)
    # Sanity: the panel/switcher/head tags emit non-trivial HTML.
    if tag_name in {"theme_panel", "theme_head", "theme_switcher"}:
        assert out.strip(), f"{{% {tag_name} %}} rendered empty via the Rust engine"


def test_theme_panel_load_then_tag_renders_via_rust_engine():
    """The exact documented snippet from the issue renders without error.

    ``{% load theme_tags %}{% theme_panel %}`` is what the theming guide
    documents. The Rust engine ignores ``{% load %}`` (tags are globally
    registered), so the panel tag must dispatch to its handler.
    """
    out = _render_via_rust("{% load theme_tags %}{% theme_panel %}")
    assert isinstance(out, str)
    assert out.strip()


# ---------------------------------------------------------------------------
# Output parity with the {{ ... }} context-string form (default args)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tag_name", DOCUMENTED_TAGS)
def test_rust_tag_output_matches_context_string_form(tag_name):
    """``{% theme_X %}`` (Rust engine) == the tag body the ``{{ theme_X }}``
    context-string form (#1435) calls, for default args.

    Both paths run the same ``theme_tags.py`` simple_tag body, so the
    output must be equivalent (docs ↔ engine agree).
    """
    rust_output = _render_via_rust("{% " + tag_name + " %}")
    context_string_output = _render_tag_body(tag_name)
    assert rust_output == context_string_output, (
        f"{{% {tag_name} %}} (Rust engine) diverged from the "
        f"{{{{ {tag_name} }}}} context-string form — docs/engine disagree."
    )


# ---------------------------------------------------------------------------
# Customization-with-args form
# ---------------------------------------------------------------------------


def test_theme_panel_respects_kwargs_via_rust_engine():
    """``{% theme_panel show_packs=False %}`` must honor the kwarg.

    The customization-with-args form is the documented reason to use the
    tag form over the ``{{ }}`` form. The Rust-engine output with
    ``show_packs=False`` must equal the tag body called with
    ``show_packs=False`` and must differ from the default render.
    """
    default_output = _render_via_rust("{% theme_panel %}")
    no_packs_rust = _render_via_rust("{% theme_panel show_packs=False %}")
    no_packs_body = _render_tag_body("theme_panel", show_packs=False)

    assert no_packs_rust == no_packs_body, (
        "{% theme_panel show_packs=False %} (Rust engine) diverged from the "
        "tag body called with show_packs=False — kwargs are not flowing "
        "through the handler."
    )
    assert no_packs_rust != default_output, (
        "{% theme_panel show_packs=False %} produced the same output as the "
        "default render — the show_packs kwarg was ignored."
    )
