"""Tests for the Iter 1 dj-if boundary markers exposed through the Python API.

Foundation 1 of 3 toward issue #1358 (re-open of #256 Option A — keyed
VDOM diff for conditional subtrees). At template-render time, every
`{% if %}` block whose body contains element nodes gets wrapped in HTML
comment boundary markers:

    <!--dj-if id="if-N"-->...rendered body...<!--/dj-if-->

Browsers ignore HTML comments — zero observable behaviour for users.
The markers are framework-internal metadata for the upcoming Iter 3
(Rust VDOM differ) which uses them as keyed boundaries when
conditionals flip.

These tests use `render_template_with_dirs` (which preserves the
markers, since djust uses the same path internally for VDOM
diffing). The public `render_template` STRIPS the markers so the
contract that standalone rendering yields clean HTML is preserved.
"""

import pytest

from djust._rust import render_template, render_template_with_dirs


def render_raw(source: str, ctx: dict) -> str:
    """Render template via `render_template_with_dirs` (no marker stripping).

    djust uses this path internally for VDOM rendering — the markers
    are needed there. The public `render_template` strips them.
    """
    return render_template_with_dirs(source, ctx, [])


# ---------------------------------------------------------------------------
# Element-bearing if blocks emit markers (visible via raw rendering)
# ---------------------------------------------------------------------------


class TestElementBearingIfMarkers:
    def test_simple_element_if_true(self):
        result = render_raw("{% if show %}<div>foo</div>{% endif %}", {"show": True})
        assert result == '<!--dj-if id="if-0"--><div>foo</div><!--/dj-if-->'

    def test_simple_element_if_false_no_else(self):
        result = render_raw("{% if show %}<div>foo</div>{% endif %}", {"show": False})
        assert result == '<!--dj-if id="if-0"--><!--/dj-if-->'

    def test_element_if_else_branch(self):
        tmpl = "{% if show %}<div>A</div>{% else %}<span>B</span>{% endif %}"
        assert render_raw(tmpl, {"show": True}) == '<!--dj-if id="if-0"--><div>A</div><!--/dj-if-->'
        assert (
            render_raw(tmpl, {"show": False}) == '<!--dj-if id="if-0"--><span>B</span><!--/dj-if-->'
        )


# ---------------------------------------------------------------------------
# Pure-text conditionals — no markers (legacy `<!--dj-if-->` preserved)
# ---------------------------------------------------------------------------


class TestPureTextSkip:
    def test_pure_text_if_true_no_markers(self):
        result = render_raw("{% if show %}foo{% endif %}", {"show": True})
        assert result == "foo"
        assert "dj-if id=" not in result

    def test_pure_text_if_false_keeps_legacy_placeholder(self):
        # Issue #295 / DJE-053 behaviour preserved.
        result = render_raw("{% if show %}foo{% endif %}", {"show": False})
        assert result == "<!--dj-if-->"
        assert "dj-if id=" not in result

    def test_pure_text_if_else_no_markers(self):
        tmpl = "{% if show %}yes{% else %}no{% endif %}"
        assert render_raw(tmpl, {"show": True}) == "yes"
        assert render_raw(tmpl, {"show": False}) == "no"


# ---------------------------------------------------------------------------
# Public `render_template` strips ALL markers (clean output contract)
# ---------------------------------------------------------------------------


class TestPublicRenderTemplateStrips:
    def test_strips_boundary_markers_from_element_if(self):
        # The internal raw rendering shows markers; the public
        # `render_template` strips them. This preserves the existing
        # contract that public rendering returns clean HTML.
        result = render_template(
            "{% if show %}<div>foo</div>{% endif %}",
            {"show": True},
        )
        assert result == "<div>foo</div>"
        assert "dj-if" not in result

    def test_strips_boundary_markers_when_false(self):
        result = render_template(
            "{% if show %}<div>foo</div>{% endif %}",
            {"show": False},
        )
        assert result == ""
        assert "dj-if" not in result

    def test_strips_legacy_placeholder(self):
        # Pure-text-conditional placeholder (issue #295) also stripped.
        result = render_template("{% if show %}foo{% endif %}", {"show": False})
        assert result == ""

    def test_strips_markers_in_complex_template(self):
        result = render_template(
            "<section>"
            "{% if a %}<div>A</div>{% endif %}"
            "{% if b %}<span>B</span>{% endif %}"
            "</section>",
            {"a": True, "b": True},
        )
        assert result == "<section><div>A</div><span>B</span></section>"
        assert "dj-if" not in result


# ---------------------------------------------------------------------------
# ID stability across renders (the contract Iter 3's differ inherits)
# ---------------------------------------------------------------------------


class TestIdStability:
    def test_same_template_same_ids_across_renders(self):
        source = "{% if show %}<div>foo</div>{% endif %}"
        r1 = render_raw(source, {"show": True})
        r2 = render_raw(source, {"show": True})
        assert r1 == r2
        assert 'id="if-0"' in r1

    def test_ids_unchanged_after_context_mutation(self):
        # Snapshot-after-capture discipline (Action #1039): re-rendering
        # with different state must NOT shift IDs.
        source = "{% if show %}<div>foo</div>{% endif %}"
        r_true = render_raw(source, {"show": True})
        r_false = render_raw(source, {"show": False})
        assert 'id="if-0"' in r_true
        assert 'id="if-0"' in r_false


# ---------------------------------------------------------------------------
# Sequential and nested ifs get distinct IDs in document order
# ---------------------------------------------------------------------------


class TestIdAssignment:
    def test_sequential_ifs_distinct_ids(self):
        result = render_raw(
            "{% if a %}<div>X</div>{% endif %}{% if b %}<div>Y</div>{% endif %}",
            {"a": True, "b": True},
        )
        assert 'id="if-0"' in result
        assert 'id="if-1"' in result
        # Order: outer if-0 wraps X, then if-1 wraps Y.
        assert result.index('id="if-0"') < result.index('id="if-1"')

    def test_nested_if_distinct_ids(self):
        result = render_raw(
            "{% if a %}<div>{% if b %}<span>x</span>{% endif %}</div>{% endif %}",
            {"a": True, "b": True},
        )
        # Outer (if-0) seen first; inner (if-1) is nested.
        assert (
            result
            == '<!--dj-if id="if-0"--><div><!--dj-if id="if-1"--><span>x</span><!--/dj-if--></div><!--/dj-if-->'
        )

    def test_for_if_iteration_uses_same_id(self):
        # The {% if %} inside the {% for %} body has marker_id assigned
        # ONCE at parse time — every loop iteration emits the same id.
        result = render_raw(
            "{% for i in items %}{% if i.show %}<div>{{ i.name }}</div>{% endif %}{% endfor %}",
            {
                "items": [
                    {"show": True, "name": "a"},
                    {"show": True, "name": "b"},
                ]
            },
        )
        # Both iterations use id="if-0".
        assert result.count('id="if-0"') == 2
        assert "<div>a</div>" in result
        assert "<div>b</div>" in result


# ---------------------------------------------------------------------------
# HTML attribute context — markers SKIPPED (issue #380 preserved)
# ---------------------------------------------------------------------------


class TestAttributeContext:
    @pytest.mark.parametrize("active", [True, False])
    def test_no_markers_in_class_attribute(self, active):
        # {% if %} inside an HTML attribute value must not emit any
        # comment markers. Browsers don't honour comments inside
        # attribute strings; emitting them would shift VDOM path
        # indices for sibling elements (issue #380).
        result = render_raw(
            r'<a class="nav-link {% if active %}active{% endif %}">link</a>',
            {"active": active},
        )
        assert "dj-if" not in result, (
            f"no dj-if comments allowed inside attribute (active={active}): {result}"
        )


# ---------------------------------------------------------------------------
# Sibling position stability — the property Iter 3's differ uses
# ---------------------------------------------------------------------------


class TestSiblingStability:
    @pytest.mark.parametrize("show", [True, False])
    def test_marker_anchors_same_position(self, show):
        # When the condition flips, the marker pair anchors a stable
        # position relative to siblings. Iter 3's differ uses this.
        result = render_raw(
            "<div>{% if show %}<span>A</span>{% endif %}<i>B</i></div>",
            {"show": show},
        )
        # In both cases the if's pair appears before <i>B</i>.
        if_pair_end = result.index("<!--/dj-if-->")
        i_b_start = result.index("<i>B</i>")
        assert if_pair_end < i_b_start
