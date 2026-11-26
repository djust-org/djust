"""Six layout tags are one implementation on both engines (#4331, #4002 slice 4).

``app_sidebar``, ``app_header``, ``app_content``, ``toolbar_separator``, ``toolbar_overflow`` and
``page_header_actions`` were drawn twice: by a native handler (the Rust engine) and by Python
markup. Their Django nodes now call the handler, so there is one renderer, as ``data_table`` has
since #3869. The Rust root-proof protocol of the ``app_*`` handlers is untouched
(``test_app_shell_rust_engine_3448.py``).
"""

from __future__ import annotations

import pytest
from django.utils.safestring import mark_safe

from djust.tests.test_component_dj_attr_parity_3669 import _django, _rust

HOSTILE = "<script>alert(\"x\")</script> 'q' & co"

CONTEXTS = {
    "typical": {"label": "Hello"},
    "hostile": {"label": HOSTILE},
    "missing": {},
    "none": {"label": None},
    "safe": {"label": mark_safe("<b>bold</b>")},
}

BLOCK_TAGS = ("app_sidebar", "app_header", "app_content", "toolbar_overflow", "page_header_actions")

BODIES = {
    "text": "{{ label }}",
    "empty": "",
    "whitespace": "  \n ",
    "nested": '<nav><a href="/x">{{ label }}</a><i>{{ label }}</i></nav>',
    "nested-same-tag": "{% app_content %}{{ label }}{% endapp_content %}",
}


@pytest.mark.parametrize("ctx", sorted(CONTEXTS))
@pytest.mark.parametrize("body", sorted(BODIES))
@pytest.mark.parametrize("tag", BLOCK_TAGS)
def test_block_tags_draw_the_same_bytes(tag: str, body: str, ctx: str) -> None:
    src = "{% " + tag + " %}" + BODIES[body] + "{% end" + tag + " %}"
    assert _django(src, CONTEXTS[ctx]) == _rust(src, CONTEXTS[ctx])


def test_toolbar_separator_draws_the_same_bytes() -> None:
    src = "{% toolbar_separator %}"
    assert _django(src) == _rust(src)
    assert 'role="separator"' in _django(src)


@pytest.mark.parametrize("ctx", sorted(CONTEXTS))
def test_the_tags_inside_their_parents_draw_the_same_bytes(ctx: str) -> None:
    for src in (
        "{% app_shell %}{% app_sidebar %}{{ label }}{% endapp_sidebar %}"
        "{% app_header %}H{% endapp_header %}{% app_content %}{{ label }}{% endapp_content %}"
        "{% endapp_shell %}",
        '{% toolbar id="t" %}{% toolbar_separator %}{% toolbar_overflow %}{{ label }}'
        "{% endtoolbar_overflow %}{% endtoolbar %}",
        '{% page_header title="T" %}{% page_header_actions %}A{{ label }}'
        "{% endpage_header_actions %}{% endpage_header %}",
    ):
        assert _django(src, CONTEXTS[ctx]) == _rust(src, CONTEXTS[ctx]), src


def test_hostile_content_is_escaped_by_the_variable_not_the_wrapper() -> None:
    out = _django("{% app_content %}{{ label }}{% endapp_content %}", CONTEXTS["hostile"])
    assert "<script>" not in out
    assert out.startswith('<main class="dj-app-shell__content">')


def test_the_django_nodes_call_the_handlers(monkeypatch) -> None:
    """Gate-off: put Python markup back in a node and this fails."""
    from djust.components import rust_handlers

    for tag, handler in (
        ("app_sidebar", "AppSidebarHandler"),
        ("app_header", "AppHeaderHandler"),
        ("app_content", "AppContentHandler"),
        ("toolbar_overflow", "ToolbarOverflowHandler"),
        ("page_header_actions", "PageHeaderActionsHandler"),
    ):
        monkeypatch.setattr(
            getattr(rust_handlers, handler), "render", lambda self, *a, _h=handler: "<" + _h + ">"
        )
        out = _django("{% " + tag + " %}x{% end" + tag + " %}")
        assert out == "<" + handler + ">", tag
    monkeypatch.setattr(
        rust_handlers.ToolbarSeparatorHandler, "render", lambda self, *a: "<sep-handler>"
    )
    assert _django("{% toolbar_separator %}") == "<sep-handler>"
