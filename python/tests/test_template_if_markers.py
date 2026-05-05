"""Tests for the Iter 1 dj-if boundary markers exposed through the Python API.

Foundation 1 of 3 toward issue #1358 (re-open of #256 Option A — keyed
VDOM diff for conditional subtrees). At template-render time, every
`{% if %}` block whose body contains element nodes gets wrapped in HTML
comment boundary markers:

    <!--dj-if id="if-<prefix>-N"-->...rendered body...<!--/dj-if-->

The `<prefix>` is an 8-hex-character source-derived hash, added in
Stage 11 of PR #1363 to prevent ID collisions between independently-
parsed templates (`{% extends %}` parents, `{% include %}` children).

Browsers ignore HTML comments — zero observable behaviour for users.
The markers are framework-internal metadata for the upcoming Iter 3
(Rust VDOM differ) which uses them as keyed boundaries when
conditionals flip.

These tests use `render_template_with_dirs` (which preserves the
markers, since djust uses the same path internally for VDOM
diffing). The public `render_template` STRIPS the markers so the
contract that standalone rendering yields clean HTML is preserved.
"""

import re

import pytest

from djust._rust import render_template, render_template_with_dirs


def render_raw(source: str, ctx: dict) -> str:
    """Render template via `render_template_with_dirs` (no marker stripping).

    djust uses this path internally for VDOM rendering — the markers
    are needed there. The public `render_template` strips them.
    """
    return render_template_with_dirs(source, ctx, [])


_PREFIXED_ID_RE = re.compile(r'id="if-[0-9a-f]{8}-(\d+)"')


def strip_prefix(rendered: str) -> str:
    """Replace `id="if-<prefix>-N"` with `id="if-N"` for legacy assertions.

    The per-template source-prefix is orthogonal to the per-template
    counter the legacy tests check.
    """
    return _PREFIXED_ID_RE.sub(r'id="if-\1"', rendered)


# ---------------------------------------------------------------------------
# Element-bearing if blocks emit markers (visible via raw rendering)
# ---------------------------------------------------------------------------


class TestElementBearingIfMarkers:
    def test_simple_element_if_true(self):
        result = render_raw("{% if show %}<div>foo</div>{% endif %}", {"show": True})
        assert strip_prefix(result) == '<!--dj-if id="if-0"--><div>foo</div><!--/dj-if-->'

    def test_simple_element_if_false_no_else(self):
        result = render_raw("{% if show %}<div>foo</div>{% endif %}", {"show": False})
        assert strip_prefix(result) == '<!--dj-if id="if-0"--><!--/dj-if-->'

    def test_element_if_else_branch(self):
        tmpl = "{% if show %}<div>A</div>{% else %}<span>B</span>{% endif %}"
        assert (
            strip_prefix(render_raw(tmpl, {"show": True}))
            == '<!--dj-if id="if-0"--><div>A</div><!--/dj-if-->'
        )
        assert (
            strip_prefix(render_raw(tmpl, {"show": False}))
            == '<!--dj-if id="if-0"--><span>B</span><!--/dj-if-->'
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
        # After Stage 11 fix on PR #1363, IDs are `if-<8hex>-N`. Strip
        # the per-source prefix to verify the legacy positional claim.
        assert 'id="if-0"' in strip_prefix(r1)

    def test_ids_unchanged_after_context_mutation(self):
        # Snapshot-after-capture discipline (Action #1039): re-rendering
        # with different state must NOT shift IDs.
        source = "{% if show %}<div>foo</div>{% endif %}"
        r_true = render_raw(source, {"show": True})
        r_false = render_raw(source, {"show": False})
        assert 'id="if-0"' in strip_prefix(r_true)
        assert 'id="if-0"' in strip_prefix(r_false)
        # Prefixed IDs in r_true and r_false must be byte-identical.
        ids_true = _PREFIXED_ID_RE.findall(r_true) + re.findall(
            r'id="(if-[0-9a-f]{8}-\d+)"', r_true
        )
        ids_false = _PREFIXED_ID_RE.findall(r_false) + re.findall(
            r'id="(if-[0-9a-f]{8}-\d+)"', r_false
        )
        assert ids_true == ids_false


# ---------------------------------------------------------------------------
# Sequential and nested ifs get distinct IDs in document order
# ---------------------------------------------------------------------------


class TestIdAssignment:
    def test_sequential_ifs_distinct_ids(self):
        result = render_raw(
            "{% if a %}<div>X</div>{% endif %}{% if b %}<div>Y</div>{% endif %}",
            {"a": True, "b": True},
        )
        # After Stage 11 prefix fix, IDs include a per-source hash.
        # Strip to compare the positional counter parts.
        stripped = strip_prefix(result)
        assert 'id="if-0"' in stripped
        assert 'id="if-1"' in stripped
        # Order: outer if-0 wraps X, then if-1 wraps Y.
        assert stripped.index('id="if-0"') < stripped.index('id="if-1"')

    def test_nested_if_distinct_ids(self):
        result = render_raw(
            "{% if a %}<div>{% if b %}<span>x</span>{% endif %}</div>{% endif %}",
            {"a": True, "b": True},
        )
        # Outer (if-0) seen first; inner (if-1) is nested.
        assert (
            strip_prefix(result)
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
        # Both iterations use the same id (the parser only saw ONE
        # Node::If). After prefix fix that's `if-<prefix>-0`, twice.
        stripped = strip_prefix(result)
        assert stripped.count('id="if-0"') == 2
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


# ---------------------------------------------------------------------------
# ID prefix uniqueness — Stage 11 MUST-FIX #1 on PR #1363
#
# Independently parsed templates must NOT share the `if-<prefix>-N` ID
# space. This is the property Iter 3's "differ keys off the id alone"
# contract relies on.
# ---------------------------------------------------------------------------


class TestIdPrefixUniqueness:
    def test_different_sources_have_different_prefixes(self):
        # Two different template sources, each with `{% if %}`. Their
        # ID prefixes must differ.
        r1 = render_raw("{% if a %}<div>1</div>{% endif %}", {"a": True})
        r2 = render_raw("{% if b %}<span>2</span>{% endif %}", {"b": True})

        ids1 = re.findall(r'id="(if-[0-9a-f]{8}-\d+)"', r1)
        ids2 = re.findall(r'id="(if-[0-9a-f]{8}-\d+)"', r2)
        assert ids1, "expected at least one id in r1"
        assert ids2, "expected at least one id in r2"

        # Extract the prefix portion (the 8 hex chars) from each id.
        def prefix_of(marker_id: str) -> str:
            return marker_id.split("-")[1]

        prefixes1 = {prefix_of(i) for i in ids1}
        prefixes2 = {prefix_of(i) for i in ids2}
        assert prefixes1.isdisjoint(prefixes2), (
            f"prefixes must differ across sources: {prefixes1} vs {prefixes2}"
        )

    def test_same_source_same_prefix(self):
        # Render the SAME source twice. IDs must be byte-identical.
        source = "{% if a %}<div>X</div>{% endif %}{% if b %}<span>Y</span>{% endif %}"
        r1 = render_raw(source, {"a": True, "b": True})
        r2 = render_raw(source, {"a": True, "b": True})
        ids1 = re.findall(r'id="(if-[0-9a-f]{8}-\d+)"', r1)
        ids2 = re.findall(r'id="(if-[0-9a-f]{8}-\d+)"', r2)
        assert ids1 == ids2

    def test_id_format_matches_contract(self):
        # The contract is `if-<8-hex>-<counter>`. Lock it in.
        result = render_raw("{% if a %}<div>X</div>{% endif %}", {"a": True})
        ids = re.findall(r'id="(if-[0-9a-f]{8}-\d+)"', result)
        assert len(ids) == 1
        # No more than 8 hex chars in the prefix.
        assert re.fullmatch(r"if-[0-9a-f]{8}-\d+", ids[0])


# ---------------------------------------------------------------------------
# csrf_token element-bearing classification — Stage 11 MUST-FIX #2
#
# `{% csrf_token %}` renders an `<input type="hidden">`. It must be
# classified as element-bearing so the dj-if marker pair wraps it,
# otherwise Iter 3's differ is blind to that case.
# ---------------------------------------------------------------------------


class TestCsrfTokenElementBearing:
    def test_csrf_token_inside_if_emits_markers(self):
        result = render_raw(
            "{% if show %}{% csrf_token %}{% endif %}",
            {"show": True, "csrf_token": "abc123"},
        )
        # Marker pair must wrap the csrf_token output.
        assert "dj-if id=" in result
        assert "<!--/dj-if-->" in result
        # csrf_token rendered as hidden input.
        assert '<input type="hidden" name="csrfmiddlewaretoken"' in result

    def test_variable_only_does_not_emit_markers(self):
        # Regression: pure-variable bodies must remain text-only.
        result = render_raw(
            "{% if show %}{{ name }}{% endif %}",
            {"show": True, "name": "hello"},
        )
        assert result == "hello"
        assert "dj-if" not in result

    def test_input_element_emits_markers(self):
        # Baseline regression: literal `<input>` inside if must emit
        # markers (the long-standing element-bearing path).
        result = render_raw(
            '{% if show %}<input type="hidden">{% endif %}',
            {"show": True},
        )
        assert "dj-if id=" in result
