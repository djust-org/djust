"""
Regression tests for #1551 — Multi-line `{# ... #}` comment handling
asymmetry between the Rust template renderer and Django classical.

## The bug

The Rust lexer at `crates/djust_templates/src/lexer.rs:289-305` treats
`{# ... #}` as opaque even across newlines — it consumes until `#}` without
caring what's between. Django classical's tokenizer uses a non-DOTALL regex
in `django.template.base.Lexer`, so multi-line `{# ... #}` is NOT recognized
as a comment. Any `{% ... %}` syntax inside a multi-line comment body is
parsed as a real tag, raising `TemplateSyntaxError`.

This is asymmetric: a template renders fine via the LiveView WS path (Rust)
but crashes via `client.get()` (Django classical), Django's debug error
renderer, or any view that uses `render()` directly. The mismatch is silent
— projects ship templates that work on the dev server and break in CI or on
error paths.

Follow-up to #1423 (which fixed the Rust side but didn't normalize behavior
with classical). Reporter: NYC Comptroller Claims project (private).

## Fix

`djust.template.loaders` provides a `FilesystemLoader` + `AppDirectoriesLoader`
subclass that preprocesses multi-line `{# ... #}` blocks out of the template
source before Django classical sees it. The preprocessing matches both
single-line and multi-line comments uniformly (Django ALSO strips
single-line comments — the only difference is multi-line — so preprocessing
all of them yields identical output).

Projects opt in by replacing their classical Django loader entries in
`TEMPLATES`:

    'OPTIONS': {
        'loaders': [
            'djust.template.loaders.FilesystemLoader',
            'djust.template.loaders.AppDirectoriesLoader',
        ],
    },
"""

from __future__ import annotations


import pytest
from django.template import engines
from django.template.exceptions import TemplateSyntaxError


# ---------------------------------------------------------------------------
# The bug — Django classical crashes on multi-line {# ... %} ... #}
# ---------------------------------------------------------------------------


_PROBLEMATIC_TEMPLATE = """\
{# d-none (not {% if %}) so the VDOM
   toggles a class attribute, not a subtree.
   See djust #1550. #}
<div class="foo {% if x %}d-none{% endif %}"></div>
"""

_SINGLELINE_OK_TEMPLATE = """\
{# d-none keeps the DOM stable #}
<div class="foo {% if x %}d-none{% endif %}"></div>
"""


def test_django_classical_crashes_on_multiline_comment_with_tag_syntax():
    """The bug: Django classical raises TemplateSyntaxError on multi-line
    {# ... %} ... #} comments containing partial tag syntax."""
    django_engine = engines["django"]
    with pytest.raises(TemplateSyntaxError, match="Unexpected end of expression"):
        django_engine.from_string(_PROBLEMATIC_TEMPLATE).render({"x": True})


def test_django_classical_single_line_comment_works():
    """Control: single-line {# ... #} comments are NOT broken in Django
    classical even when they contain template-tag-like syntax.

    Wait — actually they ARE broken when the body contains `{% %}`. Single-
    line comments work for plain prose; the `{% %}`-in-comment behavior is
    the cross-cutting Django bug. This control verifies the bug shape.
    """
    django_engine = engines["django"]
    # Plain single-line comment (no embedded tag syntax) — works.
    out = django_engine.from_string("{# just a comment #}\n<div>x</div>").render({})
    assert "<div>x</div>" in out
    assert "just a comment" not in out


# ---------------------------------------------------------------------------
# The fix — djust.template.loaders strip multi-line {# ... #} preprocessing
# ---------------------------------------------------------------------------


def test_strip_multiline_comments_removes_problematic_block():
    """The preprocessing helper strips multi-line {# ... #} comments."""
    from djust.template.loaders import strip_multiline_comments

    out = strip_multiline_comments(_PROBLEMATIC_TEMPLATE)
    assert "d-none (not" not in out
    assert "{% if %}" not in out  # the prose-tag-text is stripped
    # The actual template tags outside the comment remain.
    assert "{% if x %}d-none{% endif %}" in out
    assert '<div class="foo' in out


def test_strip_multiline_comments_handles_single_line():
    """The preprocessing also handles single-line comments — Django strips
    them anyway, so doing it ourselves is safe and uniform."""
    from djust.template.loaders import strip_multiline_comments

    src = "{# single-line #}<p>hello</p>"
    assert strip_multiline_comments(src) == "<p>hello</p>"


def test_strip_multiline_comments_preserves_template_tags():
    """The preprocessing MUST NOT strip {% ... %} or {{ ... }} — only
    {# ... #}."""
    from djust.template.loaders import strip_multiline_comments

    src = "{% if x %}{{ y }}{% endif %}{# comment #}"
    assert "{% if x %}{{ y }}{% endif %}" in strip_multiline_comments(src)


def test_strip_multiline_comments_handles_nested_braces():
    """A comment body can contain `{` and `}` chars (e.g. JSON examples).
    The matcher must terminate on `#}`, not on bare `}`."""
    from djust.template.loaders import strip_multiline_comments

    src = '{# example: {"key": "value"} #}<p>x</p>'
    assert strip_multiline_comments(src) == "<p>x</p>"


def test_strip_multiline_comments_multiple_comments():
    """Multiple non-overlapping comments are stripped independently."""
    from djust.template.loaders import strip_multiline_comments

    src = "{# A #}<p>x</p>{# B\nspans lines #}<p>y</p>"
    out = strip_multiline_comments(src)
    assert "A" not in out and "B" not in out
    assert "<p>x</p>" in out and "<p>y</p>" in out


def test_strip_multiline_comments_non_greedy():
    """The matcher must be non-greedy — `{# A #} text {# B #}` must produce
    ' text ', not '' (greedy would consume across both comments)."""
    from djust.template.loaders import strip_multiline_comments

    src = "{# A #} text {# B #}"
    assert strip_multiline_comments(src) == " text "


# ---------------------------------------------------------------------------
# End-to-end via the Loader subclass — Django can parse the template now
# ---------------------------------------------------------------------------


def test_loader_preprocesses_problematic_template(tmp_path, settings):
    """When the FilesystemLoader is used, a template that previously crashed
    Django classical now renders cleanly."""
    from django.template import Engine

    # Write the problematic template to a temp dir.
    (tmp_path / "test.html").write_text(_PROBLEMATIC_TEMPLATE)

    # Use the djust FilesystemLoader directly — no settings mutation.
    engine = Engine(
        dirs=[str(tmp_path)],
        loaders=[
            ("djust.template.loaders.FilesystemLoader", [str(tmp_path)]),
        ],
    )
    tpl = engine.get_template("test.html")
    out = tpl.render(
        engine.template_context_processors_func.__self__
        if False
        else __import__("django").template.Context({"x": True})
    )
    assert "d-none" in out
    assert '<div class="foo d-none">' in out


def test_loader_does_not_mutate_loaded_source_for_non_comment_content(tmp_path):
    """The Loader subclass only strips comments — actual template content
    (tags, text, variables) must pass through unchanged."""
    from django.template import Engine, Context

    src = "Hello {{ name }} — count: {% if n %}{{ n }}{% endif %}"
    (tmp_path / "no_comments.html").write_text(src)
    engine = Engine(
        dirs=[str(tmp_path)],
        loaders=[
            ("djust.template.loaders.FilesystemLoader", [str(tmp_path)]),
        ],
    )
    tpl = engine.get_template("no_comments.html")
    out = tpl.render(Context({"name": "world", "n": 3}))
    assert out == "Hello world — count: 3"
