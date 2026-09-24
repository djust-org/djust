"""Component markup and CSS that must hold for v1.2.1-12.

- #3008: ``CodeBlock``'s Copy button copies its own code (``dj-copy``).
- #2996: button/badge labels follow the theme's paired foreground; empty
  rating stars reach 3:1 against the page.
- #2993: Alert/Progress/Avatar say they ship no CSS, and that stays true.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from djust.components.components.code_block import CodeBlock

STATIC = Path(__file__).resolve().parents[1] / "static" / "djust_components"
CLASSES_CSS = (STATIC / "components-classes.css").read_text()
COMPONENTS_CSS = (STATIC / "components.css").read_text()
DJUST_ROOT = Path(__file__).resolve().parents[2]


def _rule(css: str, selector: str) -> str:
    """The declaration block of the rule whose selector is exactly ``selector``."""
    match = re.search(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.M)
    assert match, f"no rule for {selector!r}"
    return match.group(1)


# ---------------------------------------------------------------------------
# #3008 — CodeBlock copy button
# ---------------------------------------------------------------------------


class TestCodeBlockCopy3008:
    def test_button_copies_its_own_code_element(self):
        html = CodeBlock(code="print('hi')", language="python").render()
        button = re.search(r"<button[^>]*code-block-copy[^>]*>", html).group(0)
        target = re.search(r'dj-copy="#([^"]+)"', button).group(1)
        # The id sits on the <code> that holds the source, so the copied text
        # is the code's textContent — the raw source, not the header labels.
        assert re.search(rf'<code class="language-python" id="{re.escape(target)}">', html)
        assert 'type="button"' in button
        assert 'aria-label="Copy code"' in button
        assert 'dj-copy-feedback="Copied!"' in button

    def test_two_blocks_never_copy_each_others_code(self):
        a = CodeBlock(code="a = 1", language="python").render()
        b = CodeBlock(code="b = 2", language="python").render()
        target_a = re.search(r'dj-copy="#([^"]+)"', a).group(1)
        target_b = re.search(r'dj-copy="#([^"]+)"', b).group(1)
        assert target_a != target_b

    def test_the_target_is_stable_across_renders_of_one_instance(self):
        block = CodeBlock(code="x", language="python")
        assert block.render() == block.render()

    def test_an_explicit_id_is_used_and_escaped(self):
        html = CodeBlock(code="x", id='snip"1').render()
        assert 'dj-copy="#snip&quot;1"' in html
        assert 'id="snip&quot;1"' in html

    def test_classes_are_unchanged(self):
        """Apps style against these; the fix adds attributes only."""
        html = CodeBlock(code="x", filename="a.py", language="python").render()
        for cls in ("code-block", "code-block-header", "code-block-copy", "code-block-pre"):
            assert f'class="{cls}"' in html or f'class="{cls} ' in html


# ---------------------------------------------------------------------------
# #2996 — label fallbacks and empty-star contrast
# ---------------------------------------------------------------------------


class TestButtonLabelFollowsTheTheme2996:
    @pytest.mark.parametrize(
        "selector,override,token",
        [
            (".dj-btn", "--dj-btn-primary-fg", "--primary-foreground"),
            (".dj-btn-danger", "--dj-btn-danger-fg", "--destructive-foreground"),
            (".dj-btn-success", "--dj-btn-success-fg", "--success-foreground"),
            (".dj-notification-badge", "--dj-notification-badge-fg", "--destructive-foreground"),
        ],
    )
    def test_override_first_then_paired_foreground_then_white(self, selector, override, token):
        block = _rule(CLASSES_CSS, selector)
        color = re.search(r"(?<![\w-])color:\s*([^;]+);", block).group(1).strip()
        # The app's override still wins; without one the label is the preset's
        # own paired foreground; with no theme loaded it is still white.
        assert color == f"var({override}, hsl(var({token}, 0 0% 100%)))"

    def test_popover_badge_uses_the_destructive_pair(self):
        block = _rule(CLASSES_CSS, ".dj-notif-popover__badge")
        assert "color: hsl(var(--destructive-foreground, 0 0% 100%))" in block

    def test_no_literal_white_label_left_on_a_themed_primary_danger_or_success_fill(self):
        for selector in (".dj-btn", ".dj-btn-danger", ".dj-btn-success"):
            assert not re.search(
                r"color:\s*[^;]*\b(white|#fff)\s*\)?;", _rule(CLASSES_CSS, selector)
            )


def _blend_contrast(fg, bg, alpha: float) -> float:
    from djust.theming.colors import hsl_to_rgb

    def lum(rgb):
        out = []
        for c in rgb:
            c = c / 255
            out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]

    f = hsl_to_rgb(fg.h, fg.s, fg.lightness)
    b = hsl_to_rgb(bg.h, bg.s, bg.lightness)
    mixed = tuple(alpha * x + (1 - alpha) * y for x, y in zip(f, b))
    hi, lo = sorted((lum(mixed), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class TestEmptyStarContrast2996:
    def _alpha(self, selector: str) -> float:
        block = _rule(COMPONENTS_CSS, selector)
        color = re.search(r"color:\s*hsl\(var\(--muted-foreground\)(?:\s*/\s*([\d.]+))?\)", block)
        assert color, f"{selector} no longer derives from --muted-foreground"
        return float(color.group(1) or 1)

    @pytest.mark.parametrize(
        "selector", [".rating-star-empty", "button.rating-star:hover ~ .rating-star"]
    )
    def test_empty_star_reaches_3_to_1_on_every_non_exempt_preset(self, selector):
        """WCAG 1.4.11: a non-text indicator needs 3:1. Checked on the page
        background and on a card, for every preset whose muted-foreground /
        background pair is not a documented A11Y exemption (those presets fail
        the 4.5:1 text gate already; recolouring them is #2885)."""
        from djust.theming.a11y_exemptions import A11Y_EXEMPTIONS
        from djust.theming.presets import THEME_PRESETS

        alpha = self._alpha(selector)
        failures = []
        for name, preset in THEME_PRESETS.items():
            for mode in ("light", "dark"):
                if (name, mode, "muted_foreground", "background") in A11Y_EXEMPTIONS:
                    continue
                tokens = getattr(preset, mode)
                for surface in ("background", "card"):
                    ratio = _blend_contrast(
                        tokens.muted_foreground, getattr(tokens, surface), alpha
                    )
                    if ratio < 3.0:
                        failures.append((name, mode, surface, round(ratio, 2)))
        assert not failures, failures

    def test_the_old_value_would_have_failed(self):
        """The gate-off check: at the old ``/ 0.3`` the same test fails."""
        from djust.theming.presets import THEME_PRESETS

        tokens = THEME_PRESETS["default"].light
        assert _blend_contrast(tokens.muted_foreground, tokens.background, 0.3) < 3.0


# ---------------------------------------------------------------------------
# #2993 — Alert / Progress / Avatar ship no CSS, and say so
# ---------------------------------------------------------------------------


def _all_shipped_css() -> str:
    return "\n".join(
        p.read_text(errors="ignore")
        for p in DJUST_ROOT.rglob("*.css")
        if "node_modules" not in p.parts
    )


class TestUnstyledComponentsAreDocumented2993:
    def test_the_list_is_still_true(self):
        """If a shipped stylesheet gains a rule for one of these classes, the
        component is no longer unstyled: drop it from UNSTYLED_PYTHON_CLASSES
        and its docstring (the 1.3 part of #2993)."""
        from djust.theming.gallery.component_registry import UNSTYLED_PYTHON_CLASSES

        css = _all_shipped_css()
        for _name, (_cls, root) in UNSTYLED_PYTHON_CLASSES.items():
            # `.dj-avatar-group` / `.dj-progress-circle` are other components.
            hits = re.findall(rf"\.{root}(?![\w-]*(?:group|circle))[\w-]*", css)
            assert not hits, f"{root}: shipped CSS now styles {sorted(set(hits))}"

    @pytest.mark.parametrize(
        "module,cls,tag",
        [
            ("alert", "Alert", "theme_alert"),
            ("progress", "Progress", "theme_progress"),
            ("avatar", "Avatar", "theme_avatar"),
        ],
    )
    def test_docstring_says_unstyled_and_points_to_the_tag(self, module, cls, tag):
        import importlib

        klass = getattr(importlib.import_module(f"djust.components.components.{module}"), cls)
        doc = klass.__doc__ or ""
        assert "No stylesheet djust ships has a rule" in doc
        assert f"{{% {tag} %}}" in doc
        # The first line is the catalogue card's description, and the card
        # shows the styled tag — it must not call the component unstyled.
        assert "unstyled" not in doc.strip().splitlines()[0].lower()
        assert "Style-agnostic" not in doc


@pytest.mark.django_db
class TestCatalogueSaysSo2993:
    @pytest.fixture(autouse=True)
    def _urlconf(self):
        from django.test import override_settings

        from djust.tests.test_catalogue_ux import _SETTINGS

        with override_settings(**_SETTINGS):
            yield

    @pytest.mark.parametrize(
        "name,cls", [("alert", "Alert"), ("progress", "Progress"), ("avatar", "Avatar")]
    )
    def test_the_detail_page_names_the_unstyled_class(self, client, name, cls):
        body = client.get(f"/theme/components/{name}/").content.decode()
        assert f"from djust.components import {cls}</code>) renders the same component" in body
        assert "<strong>unstyled</strong>" in body

    def test_a_styled_component_has_no_such_note(self, client):
        body = client.get("/theme/components/badge/").content.decode()
        assert "<strong>unstyled</strong>" not in body
