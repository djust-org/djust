"""The alert ``info`` variant and ``role`` parameter (#3280).

Every case renders through the real ``{% theme_alert %}`` tag in the Django
engine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.template import engines

from djust.theming.a11y_exemptions import CONTRAST_PAIRS

pytestmark = pytest.mark.theming

COMPONENTS_CSS = (
    Path(__file__).resolve().parents[1] / "theming/static/djust_theming/css/components.css"
)


def render(source: str, **ctx) -> str:
    template = engines["django"].from_string("{% load theme_components %}" + source)
    return template.render(ctx)


# ---------------------------------------------------------------------------
# #3280 — alert info variant and role
# ---------------------------------------------------------------------------


class TestAlertInfoAndRole:
    def test_info_variant_renders_its_class(self):
        html = render('{% theme_alert "As of 2026-01-01" variant="info" %}')
        assert "alert-info" in html

    def test_the_info_variant_has_css_on_the_info_token(self):
        css = COMPONENTS_CSS.read_text(encoding="utf-8")
        rule = css.split(".alert-info {")[1].split("}")[0]
        assert "var(--info)" in rule
        assert "var(--info) / 0.1" in rule

    @pytest.mark.parametrize("variant", ["destructive", "warning"])
    def test_urgent_variants_default_to_role_alert(self, variant):
        html = render('{% theme_alert "x" variant=variant %}', variant=variant)
        assert 'role="alert"' in html

    @pytest.mark.parametrize("variant", ["info", "success", "default"])
    def test_other_variants_default_to_role_status(self, variant):
        html = render('{% theme_alert "x" variant=variant %}', variant=variant)
        assert 'role="status"' in html
        assert 'role="alert"' not in html

    def test_role_can_be_overridden_both_ways(self):
        assert 'role="alert"' in render('{% theme_alert "x" variant="success" role="alert" %}')
        assert 'role="status"' in render(
            '{% theme_alert "x" variant="destructive" role="status" %}'
        )

    def test_an_unknown_role_is_refused(self):
        with pytest.raises(ValueError, match="role"):
            render('{% theme_alert "x" role="button" %}')

    def test_info_is_in_the_contrast_matrix(self):
        assert ("info", "info_tint", 4.5, "info text on its alert tint") in CONTRAST_PAIRS

    def test_the_legal_preset_passes_it_in_both_modes(self):
        from djust.theming._types import _contrast
        from djust.theming.presets import THEME_PRESETS

        for mode in ("light", "dark"):
            tokens = getattr(THEME_PRESETS["legal"], mode)
            assert _contrast(tokens.info, tokens.info_tint) >= 4.5, mode
            assert _contrast(tokens.info_foreground, tokens.info) >= 4.5, mode
