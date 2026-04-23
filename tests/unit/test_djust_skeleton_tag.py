"""Unit tests for the {% djust_skeleton %} template tag (v0.6.0).

Covers the 10 cases from the FLIP+Skeleton implementation plan:

1. Defaults — <div> with class "djust-skeleton djust-skeleton-line",
   non-empty style.
2. Shape variants — circle has border-radius:50%; rect does not.
3. Count=3 emits 3 line blocks.
4. Count ignored for circle/rect (always one block).
5. XSS in width: every value HTML-escaped via build_tag() — no raw
   "<script>" leaks through.
6. Invalid shape falls back to "line".
7. Count clamped to 100 (and to >=1 at the low end).
8. class_ appended after the default classes.
9. Shimmer <style> emitted exactly once per render (invoke 3x, assert
   style count == 1).
10. Tag loads via {% load live_tags %} without TemplateSyntaxError.
"""

from django.template import Context, Template


def _render(tmpl_source, context=None):
    full = "{% load live_tags %}" + tmpl_source
    return Template(full).render(Context(context or {}))


class TestDjustSkeletonDefaults:
    def test_defaults_emit_div_with_default_classes_and_style(self):
        out = _render("{% djust_skeleton %}")
        # A <div> element is emitted.
        assert "<div " in out
        # Default classes are present.
        assert "djust-skeleton" in out
        assert "djust-skeleton-line" in out
        # Non-empty style (width + height defaults).
        assert "style=" in out
        assert "width:100%" in out
        assert "height:1em" in out

    def test_tag_loads_without_template_syntax_error(self):
        # If the @register.simple_tag decoration is wrong, {% load %}
        # itself won't raise — but invoking the tag will. This is a
        # smoke test that the tag is registered under the expected name.
        out = _render("{% djust_skeleton %}")
        assert out  # Non-empty render.


class TestDjustSkeletonShapes:
    def test_circle_has_border_radius_50pct(self):
        out = _render('{% djust_skeleton shape="circle" %}')
        assert "djust-skeleton-circle" in out
        assert "border-radius:50%" in out
        # Circle defaults: 40px by 40px.
        assert "width:40px" in out
        assert "height:40px" in out

    def test_rect_has_no_border_radius_override(self):
        out = _render('{% djust_skeleton shape="rect" %}')
        assert "djust-skeleton-rect" in out
        # Rect uses the default 4px corner from the CSS class — not an
        # inline 50% override on the block itself. The stylesheet's
        # ``.djust-skeleton-circle{border-radius:50%}`` rule is fine;
        # what must NOT happen is the rect's INLINE style pulling in
        # border-radius:50%.
        assert 'style="width:100%;height:120px"' in out
        assert "border-radius:50%" not in out.split("<div")[1]
        # Rect defaults: 100% width, 120px height.
        assert "width:100%" in out
        assert "height:120px" in out

    def test_invalid_shape_falls_back_to_line(self):
        out = _render('{% djust_skeleton shape="triangle" %}')
        # Fallback class is djust-skeleton-line.
        assert "djust-skeleton-line" in out
        assert "djust-skeleton-triangle" not in out


class TestDjustSkeletonCount:
    def test_count_3_emits_3_line_blocks(self):
        out = _render("{% djust_skeleton count=3 %}")
        # 3 distinct <div class="djust-skeleton ..."> blocks.
        assert out.count('class="djust-skeleton djust-skeleton-line"') == 3

    def test_count_ignored_for_circle(self):
        # Count is line-only; circle/rect render exactly one block.
        out = _render('{% djust_skeleton shape="circle" count=5 %}')
        assert out.count('class="djust-skeleton djust-skeleton-circle"') == 1

    def test_count_ignored_for_rect(self):
        out = _render('{% djust_skeleton shape="rect" count=5 %}')
        assert out.count('class="djust-skeleton djust-skeleton-rect"') == 1

    def test_count_clamped_to_100(self):
        out = _render("{% djust_skeleton count=5000 %}")
        # Clamped to 100.
        assert out.count('class="djust-skeleton djust-skeleton-line"') == 100

    def test_count_clamped_to_at_least_one(self):
        out = _render("{% djust_skeleton count=0 %}")
        assert out.count('class="djust-skeleton djust-skeleton-line"') == 1

    def test_negative_count_clamped_to_one(self):
        out = _render("{% djust_skeleton count=-5 %}")
        assert out.count('class="djust-skeleton djust-skeleton-line"') == 1


class TestDjustSkeletonXSS:
    def test_xss_in_width_is_rejected(self):
        # A malicious width value must not reach the output as a literal
        # "<script>". Defense-in-depth: ``build_tag`` HTML-escapes the
        # attribute value, AND the CSS-length whitelist rejects anything
        # that isn't a pure length — so the malicious string falls back
        # to the shape default and never appears at all.
        out = _render(
            "{% djust_skeleton width=w %}",
            {"w": '"><script>alert(1)</script>'},
        )
        assert "<script>alert(1)" not in out
        # The raw payload string (in any form) must not appear.
        assert "script" not in out
        # The width must fall back to the shape default.
        assert "width:100%" in out

    def test_xss_in_height_is_rejected(self):
        out = _render(
            "{% djust_skeleton height=h %}",
            {"h": '"><img src=x onerror=alert(1)>'},
        )
        # Raw ``<img>`` must not appear — neither in literal nor
        # HTML-escaped form — because the whitelist rejects the value
        # entirely. Verifies defense-in-depth: even if the escape layer
        # regressed, the whitelist would still refuse this value.
        assert "<img src=x" not in out
        assert "onerror" not in out
        # Height must fall back to the shape default.
        assert "height:1em" in out

    def test_xss_in_class_is_escaped(self):
        out = _render(
            "{% djust_skeleton class_=c %}",
            {"c": '"><script>alert(1)</script>'},
        )
        assert "<script>alert(1)" not in out

    def test_css_injection_in_width_via_semicolon_is_rejected(self):
        # Values like ``100%;background:red`` pass HTML escaping (no
        # attribute breakout) but would compose freely into the inline
        # ``style=`` string. The CSS-length whitelist must reject them
        # and fall back to the shape default.
        out = _render(
            "{% djust_skeleton width=w %}",
            {"w": "100%;background:red"},
        )
        assert "background:red" not in out
        # Width must fall back to the shape default for ``line``.
        assert "width:100%;height:1em" in out

    def test_css_injection_in_height_via_semicolon_is_rejected(self):
        out = _render(
            "{% djust_skeleton height=h %}",
            {"h": "1em;background:url(//evil)"},
        )
        assert "background:url" not in out
        assert "height:1em" in out

    def test_valid_width_units_pass_the_whitelist(self):
        # px, em, rem, %, vh, vw, ch, and unitless numbers all allowed.
        for value in ("120px", "2em", "1.5rem", "50%", "10vh", "20vw", "4ch", "100"):
            out = _render(
                "{% djust_skeleton width=w %}",
                {"w": value},
            )
            assert f"width:{value}" in out, f"value={value!r} did not survive whitelist"


class TestDjustSkeletonClass:
    def test_class_appended_to_default_classes(self):
        out = _render('{% djust_skeleton class_="my-extra" %}')
        assert "djust-skeleton" in out
        assert "djust-skeleton-line" in out
        assert "my-extra" in out


class TestDjustSkeletonStyleDedupe:
    def test_shimmer_style_emitted_once_per_render(self):
        # Three invocations in one render → exactly one <style> block.
        out = _render("{% djust_skeleton %}{% djust_skeleton %}{% djust_skeleton %}")
        # Exactly one @keyframes djust-skeleton-shimmer block.
        assert out.count("@keyframes djust-skeleton-shimmer") == 1
        # Exactly one <style> tag from this tag.
        assert out.count("<style") == 1

    def test_shimmer_style_includes_reduced_motion_query(self):
        out = _render("{% djust_skeleton %}")
        assert "prefers-reduced-motion" in out

    def test_two_renders_each_emit_their_own_style(self):
        # Each Template.render() gets a fresh render_context — the
        # dedupe state does NOT leak across renders.
        out1 = _render("{% djust_skeleton %}")
        out2 = _render("{% djust_skeleton %}")
        assert out1.count("@keyframes djust-skeleton-shimmer") == 1
        assert out2.count("@keyframes djust-skeleton-shimmer") == 1
