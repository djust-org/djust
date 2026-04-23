"""Regression test for bug #930 — FormArrayNode dropped inner template content.

Before the fix: `{% form_array %}...{% endform_array %}` parsed the block body
into a nodelist via ``parser.parse(("endform_array",))`` but ``FormArrayNode.render``
never rendered that nodelist — users' inner template markup silently disappeared.

After the fix: when the block body is non-empty, FormArrayNode renders the
nodelist once per row with ``row``, ``row_index``, and ``forloop`` pushed onto
the template context; when the block body is empty/whitespace-only, the node
falls back to its original single-input-per-row default output.

These tests exercise ``FormArrayNode`` directly (bypassing the template
library-loader), since ``djust.components`` is not registered in the test
settings' ``INSTALLED_APPS``. They rely on the node's kwargs dict accepting
bare Python values (strings, variables) — the same shape ``_parse_kv_args``
produces at parse time.
"""

import tests.conftest  # noqa: F401  -- configure Django settings

from django import template as dj_template
from django.template import Context
from django.template.base import NodeList, TextNode, VariableNode, FilterExpression, Parser


def _make_parser() -> Parser:
    return Parser([])


def _fexpr(raw: str) -> FilterExpression:
    return FilterExpression(raw, _make_parser())


def _FormArrayNode():
    from djust.components.templatetags.djust_components import FormArrayNode

    return FormArrayNode


def test_form_array_renders_inner_content_per_row():
    """Bug #930: inner block markup must be rendered once per row."""
    FormArrayNode = _FormArrayNode()

    nodelist = NodeList(
        [
            TextNode("<span class='label'>"),
            VariableNode(_fexpr("row.label")),
            TextNode("</span><input name='items["),
            VariableNode(_fexpr("row_index")),
            TextNode("][value]' value='"),
            VariableNode(_fexpr("row.value")),
            TextNode("'>"),
        ]
    )

    # kwargs can be bare python values — _resolve() passes them through.
    node = FormArrayNode(
        nodelist,
        {"name": "items", "rows": dj_template.Variable("rows")},
    )

    rows = [
        {"label": "First", "value": "one"},
        {"label": "Second", "value": "two"},
    ]
    html = node.render(Context({"rows": rows}))

    assert "First" in html
    assert "Second" in html
    assert "items[0][value]" in html
    assert "items[1][value]" in html
    assert "value='one'" in html
    assert "value='two'" in html


def test_form_array_empty_block_falls_back_to_default_inputs():
    """Empty-nodelist form_array keeps the default row inputs."""
    FormArrayNode = _FormArrayNode()

    node = FormArrayNode(
        NodeList([]),
        {"name": "items", "rows": dj_template.Variable("rows")},
    )
    rows = [{"value": "alpha"}, {"value": "beta"}]
    html = node.render(Context({"rows": rows}))

    assert 'name="items[0]"' in html
    assert 'name="items[1]"' in html
    assert 'value="alpha"' in html
    assert 'value="beta"' in html
    assert "dj-form-array" in html


def test_form_array_whitespace_only_block_falls_back_to_default_inputs():
    """Whitespace-only inner content should also hit the default-render branch."""
    FormArrayNode = _FormArrayNode()

    node = FormArrayNode(
        NodeList([TextNode("   \n  ")]),
        {"name": "items", "rows": dj_template.Variable("rows")},
    )
    rows = [{"value": "x"}]
    html = node.render(Context({"rows": rows}))

    assert 'name="items[0]"' in html
    assert 'value="x"' in html


def test_form_array_forloop_counter_in_inner_block():
    """`forloop.counter`, `forloop.first`, `forloop.last` work inside the block.

    ``forloop`` is pushed onto the context as a dict, which Django's variable
    resolver handles via dict-lookup (``{{ forloop.counter }}`` -> ``forloop["counter"]``).
    """
    FormArrayNode = _FormArrayNode()

    nodelist = NodeList(
        [
            TextNode("[c="),
            VariableNode(_fexpr("forloop.counter")),
            TextNode(" first="),
            VariableNode(_fexpr("forloop.first")),
            TextNode(" last="),
            VariableNode(_fexpr("forloop.last")),
            TextNode(" idx="),
            VariableNode(_fexpr("row_index")),
            TextNode(" v="),
            VariableNode(_fexpr("row.v")),
            TextNode("]"),
        ]
    )
    node = FormArrayNode(
        nodelist,
        {"name": "items", "rows": dj_template.Variable("rows")},
    )

    rows = [{"v": "a"}, {"v": "b"}, {"v": "c"}]
    html = node.render(Context({"rows": rows}))

    assert "[c=1 first=True last=False idx=0 v=a]" in html
    assert "[c=2 first=False last=False idx=1 v=b]" in html
    assert "[c=3 first=False last=True idx=2 v=c]" in html
