"""
Accessibility validation for djust-theming.

WCAG 2.1 compliance checking for color contrast, focus states,
and accessibility features across all theme combinations.
"""

from typing import Dict, List, Tuple
from dataclasses import dataclass

from .presets import ColorScale, get_preset, THEME_PRESETS
from .theme_packs import get_design_system, get_all_design_systems


@dataclass
class ContrastResult:
    """Result of a contrast ratio check."""

    ratio: float
    passes_aa: bool
    passes_aaa: bool
    level: str  # "AA", "AAA", or "FAIL"


@dataclass
class AccessibilityReport:
    """Complete accessibility report for a theme combination."""

    design_system: str
    color_preset: str
    overall_score: float  # 0-100
    contrast_results: Dict[str, ContrastResult]
    focus_visibility: bool
    motion_safety: bool
    color_independence: bool
    issues: List[str]
    recommendations: List[str]


class AccessibilityValidator:
    """WCAG 2.1 accessibility validation for theme combinations."""

    # WCAG contrast requirements
    AA_NORMAL = 4.5
    AA_LARGE = 3.0
    AAA_NORMAL = 7.0
    AAA_LARGE = 4.5

    def __init__(self):
        self.design_systems = get_all_design_systems()
        self.color_presets = THEME_PRESETS

    def hsl_to_rgb(self, color_scale: ColorScale) -> Tuple[float, float, float]:
        """Convert HSL ColorScale to RGB (0-1 range)."""
        from .colors import hsl_to_rgb as _hsl_to_rgb

        r, g, b = _hsl_to_rgb(color_scale.h, color_scale.s, color_scale.lightness)
        return (r / 255.0, g / 255.0, b / 255.0)

    def rgb_to_luminance(self, r: float, g: float, b: float) -> float:
        """Calculate relative luminance according to WCAG formula."""

        def gamma_correct(channel: float) -> float:
            if channel <= 0.03928:
                return channel / 12.92
            else:
                return ((channel + 0.055) / 1.055) ** 2.4

        r_linear = gamma_correct(r)
        g_linear = gamma_correct(g)
        b_linear = gamma_correct(b)

        return 0.2126 * r_linear + 0.7152 * g_linear + 0.0722 * b_linear

    def calculate_contrast_ratio(self, color1: ColorScale, color2: ColorScale) -> float:
        """Calculate WCAG contrast ratio between two colors."""
        rgb1 = self.hsl_to_rgb(color1)
        rgb2 = self.hsl_to_rgb(color2)

        lum1 = self.rgb_to_luminance(*rgb1)
        lum2 = self.rgb_to_luminance(*rgb2)

        # Ensure lighter color is numerator
        lighter = max(lum1, lum2)
        darker = min(lum1, lum2)

        return (lighter + 0.05) / (darker + 0.05)

    def evaluate_contrast(self, ratio: float, is_large_text: bool = False) -> ContrastResult:
        """Evaluate contrast ratio against WCAG standards."""
        aa_threshold = self.AA_LARGE if is_large_text else self.AA_NORMAL
        aaa_threshold = self.AAA_LARGE if is_large_text else self.AAA_NORMAL

        passes_aa = ratio >= aa_threshold
        passes_aaa = ratio >= aaa_threshold

        if passes_aaa:
            level = "AAA"
        elif passes_aa:
            level = "AA"
        else:
            level = "FAIL"

        return ContrastResult(ratio=ratio, passes_aa=passes_aa, passes_aaa=passes_aaa, level=level)

    def validate_theme_accessibility(
        self,
        design_system_name: str = "minimal",
        color_preset_name: str = "default",
        check_dark_mode: bool = True,
    ) -> AccessibilityReport:
        """Validate accessibility for a theme combination."""

        design_system = get_design_system(design_system_name)
        color_preset = get_preset(color_preset_name)

        if not design_system or not color_preset:
            raise ValueError(f"Invalid theme combination: {design_system_name}-{color_preset_name}")

        # Check both light and dark modes
        modes_to_check = ["light"]
        if check_dark_mode:
            modes_to_check.append("dark")

        contrast_results = {}
        issues = []
        recommendations = []

        for mode in modes_to_check:
            mode_tokens = getattr(color_preset, mode)
            mode_prefix = f"{mode}_" if len(modes_to_check) > 1 else ""

            # Critical contrast checks
            critical_pairs = [
                ("text_on_background", mode_tokens.foreground, mode_tokens.background),
                ("text_on_card", mode_tokens.card_foreground, mode_tokens.card),
                ("primary_text", mode_tokens.primary_foreground, mode_tokens.primary),
                ("secondary_text", mode_tokens.secondary_foreground, mode_tokens.secondary),
                ("muted_text", mode_tokens.muted_foreground, mode_tokens.muted),
                ("destructive_text", mode_tokens.destructive_foreground, mode_tokens.destructive),
                ("success_text", mode_tokens.success_foreground, mode_tokens.success),
                ("warning_text", mode_tokens.warning_foreground, mode_tokens.warning),
                ("border_contrast", mode_tokens.border, mode_tokens.background),
                ("input_contrast", mode_tokens.input, mode_tokens.background),
            ]

            for pair_name, fg_color, bg_color in critical_pairs:
                ratio = self.calculate_contrast_ratio(fg_color, bg_color)
                result = self.evaluate_contrast(ratio, is_large_text=False)

                contrast_results[f"{mode_prefix}{pair_name}"] = result

                # Track issues
                if not result.passes_aa:
                    issues.append(
                        f"{mode.title()} mode: {pair_name} contrast {ratio:.2f} fails WCAG AA (needs {self.AA_NORMAL})"
                    )
                elif not result.passes_aaa:
                    recommendations.append(
                        f"{mode.title()} mode: {pair_name} could be improved to meet AAA standard ({ratio:.2f} current, {self.AAA_NORMAL} needed)"
                    )

        # Check design system accessibility features
        focus_visibility = self._check_focus_visibility(design_system)
        motion_safety = self._check_motion_safety(design_system)
        color_independence = self._check_color_independence(design_system)

        if not focus_visibility:
            issues.append("Focus states may not be sufficiently visible")

        if not motion_safety:
            recommendations.append("Consider adding motion reduction support")

        if not color_independence:
            issues.append("Design may rely too heavily on color for meaning")

        # Calculate overall score
        total_contrasts = len(contrast_results)
        passing_contrasts = sum(1 for r in contrast_results.values() if r.passes_aa)
        contrast_score = (passing_contrasts / total_contrasts) * 70 if total_contrasts > 0 else 0

        feature_score = (
            (20 if focus_visibility else 0)
            + (10 if motion_safety else 0)
            + (10 if color_independence else 0)
        )

        overall_score = min(100, contrast_score + feature_score - len(issues) * 5)

        return AccessibilityReport(
            design_system=design_system_name,
            color_preset=color_preset_name,
            overall_score=max(0, overall_score),
            contrast_results=contrast_results,
            focus_visibility=focus_visibility,
            motion_safety=motion_safety,
            color_independence=color_independence,
            issues=issues,
            recommendations=recommendations,
        )

    def _check_focus_visibility(self, design_system) -> bool:
        """Check if focus states are sufficiently visible."""
        # Check if focus ring is defined and visible
        focus_ring_width = getattr(design_system.interaction, "focus_ring_width", "0px")

        try:
            # Extract numeric value (assuming px units)
            width_value = float(focus_ring_width.replace("px", "").strip())
            return width_value >= 2.0  # Minimum 2px for visibility
        except (ValueError, AttributeError):
            return False

    def _check_motion_safety(self, design_system) -> bool:
        """Check if animations respect motion preferences."""
        # Check if durations are reasonable and not excessive
        try:
            fast_duration = design_system.animation.duration_fast
            normal_duration = design_system.animation.duration_normal

            # Extract numeric values (assuming ms units)
            fast_ms = float(fast_duration.replace("ms", "").strip())
            normal_ms = float(normal_duration.replace("ms", "").strip())

            # Reasonable durations for accessibility
            return fast_ms <= 200 and normal_ms <= 500

        except (ValueError, AttributeError):
            return False

    def _check_color_independence(self, design_system) -> bool:
        """Check if design doesn't rely solely on color for meaning."""
        # This is a heuristic - in real implementation would check for:
        # - Icons accompanying color states
        # - Text labels for status
        # - Patterns/textures for differentiation

        # For now, check if there are sufficient non-color visual cues
        has_shadows = design_system.surface.shadow_md != "none"
        has_borders = design_system.surface.border_width != "0px"
        has_varied_typography = (
            design_system.typography.heading_weight != design_system.typography.body_weight
        )

        return has_shadows or has_borders or has_varied_typography

    def validate_all_combinations(self) -> Dict[str, AccessibilityReport]:
        """Validate accessibility for all theme combinations."""
        results = {}

        for design_name in self.design_systems.keys():
            for preset_name in self.color_presets.keys():
                combination_key = f"{design_name}-{preset_name}"
                try:
                    report = self.validate_theme_accessibility(design_name, preset_name)
                    results[combination_key] = report
                except Exception as e:
                    # Create error report
                    results[combination_key] = AccessibilityReport(
                        design_system=design_name,
                        color_preset=preset_name,
                        overall_score=0,
                        contrast_results={},
                        focus_visibility=False,
                        motion_safety=False,
                        color_independence=False,
                        issues=[f"Validation failed: {str(e)}"],
                        recommendations=[],
                    )

        return results

    def generate_accessibility_report_html(self, reports: Dict[str, AccessibilityReport]) -> str:
        """Generate HTML accessibility report."""

        # Calculate summary stats
        total_themes = len(reports)
        passing_themes = sum(1 for r in reports.values() if r.overall_score >= 70)
        avg_score = (
            sum(r.overall_score for r in reports.values()) / total_themes if total_themes > 0 else 0
        )

        # CSS is kept separate from the formatted HTML template so literal
        # CSS braces are NOT interpreted as str.format placeholders
        # (CodeQL: py/str-format/missing-named-argument).
        _css_styles = """
        body { font-family: system-ui, sans-serif; margin: 2rem; }
        .summary { background: #f5f5f5; padding: 1rem; border-radius: 8px; margin-bottom: 2rem; }
        .theme-report { border: 1px solid #ddd; margin-bottom: 1rem; border-radius: 8px; }
        .theme-header { background: #f8f8f8; padding: 1rem; font-weight: bold; }
        .theme-content { padding: 1rem; }
        .score { font-size: 1.5em; font-weight: bold; }
        .score.excellent { color: #22c55e; }
        .score.good { color: #3b82f6; }
        .score.fair { color: #f59e0b; }
        .score.poor { color: #ef4444; }
        .contrast-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1rem; }
        .contrast-item { background: #f9f9f9; padding: 0.5rem; border-radius: 4px; }
        .contrast-pass { background: #dcfce7; }
        .contrast-fail { background: #fee2e2; }
        .issues { background: #fef3c7; padding: 1rem; border-radius: 4px; margin: 0.5rem 0; }
        .recommendations { background: #dbeafe; padding: 1rem; border-radius: 4px; margin: 0.5rem 0; }
"""

        _html_template = """
<!DOCTYPE html>
<html>
<head>
    <title>djust-theming Accessibility Report</title>
    <style>{styles}</style>
</head>
<body>
    <h1>djust-theming Accessibility Report</h1>

    <div class="summary">
        <h2>Summary</h2>
        <p><strong>Total Themes:</strong> {total_themes}</p>
        <p><strong>Passing Themes (≥70%):</strong> {passing_themes}</p>
        <p><strong>Average Score:</strong> {avg_score:.1f}%</p>
        <p><strong>Pass Rate:</strong> {pass_rate:.1f}%</p>
    </div>
"""

        html_parts = [
            _html_template.format(
                styles=_css_styles,
                total_themes=total_themes,
                passing_themes=passing_themes,
                avg_score=avg_score,
                pass_rate=(passing_themes / total_themes * 100) if total_themes > 0 else 0,
            )
        ]

        # Add individual theme reports
        for theme_key, report in sorted(reports.items()):
            score_class = (
                "excellent"
                if report.overall_score >= 90
                else "good"
                if report.overall_score >= 75
                else "fair"
                if report.overall_score >= 60
                else "poor"
            )

            html_parts.append(f"""
    <div class="theme-report">
        <div class="theme-header">
            <span>{theme_key}</span>
            <span class="score {score_class}" style="float: right;">{report.overall_score:.1f}%</span>
        </div>
        <div class="theme-content">
""")

            # Contrast results
            if report.contrast_results:
                html_parts.append('<h4>Contrast Results</h4><div class="contrast-grid">')
                for contrast_name, result in report.contrast_results.items():
                    css_class = "contrast-pass" if result.passes_aa else "contrast-fail"
                    html_parts.append(f"""
                    <div class="contrast-item {css_class}">
                        <strong>{contrast_name}:</strong> {result.ratio:.2f} ({result.level})
                    </div>
""")
                html_parts.append("</div>")

            # Issues
            if report.issues:
                html_parts.append('<div class="issues"><h4>Issues</h4><ul>')
                for issue in report.issues:
                    html_parts.append(f"<li>{issue}</li>")
                html_parts.append("</ul></div>")

            # Recommendations
            if report.recommendations:
                html_parts.append('<div class="recommendations"><h4>Recommendations</h4><ul>')
                for rec in report.recommendations:
                    html_parts.append(f"<li>{rec}</li>")
                html_parts.append("</ul></div>")

            html_parts.append("</div></div>")

        html_parts.append("</body></html>")

        return "".join(html_parts)


def validate_accessibility(
    design_system_name: str = "minimal", color_preset_name: str = "default"
) -> AccessibilityReport:
    """Convenience function to validate a single theme combination."""
    validator = AccessibilityValidator()
    return validator.validate_theme_accessibility(design_system_name, color_preset_name)


def validate_all_accessibility() -> Dict[str, AccessibilityReport]:
    """Convenience function to validate all theme combinations."""
    validator = AccessibilityValidator()
    return validator.validate_all_combinations()


if __name__ == "__main__":
    # CLI usage
    import logging
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _logger = logging.getLogger(__name__)

    if len(sys.argv) == 3:
        # Validate single combination
        design = sys.argv[1]
        color = sys.argv[2]
        report = validate_accessibility(design, color)

        _logger.info("Accessibility Report: %s-%s", design, color)
        _logger.info("Overall Score: %.1f%%", report.overall_score)
        _logger.info("Issues: %d", len(report.issues))
        _logger.info("Recommendations: %d", len(report.recommendations))

        for issue in report.issues:
            _logger.info("  %s", issue)

        for rec in report.recommendations:
            _logger.info("  %s", rec)

    else:
        # Validate all combinations
        _logger.info("Validating all theme combinations...")
        reports = validate_all_accessibility()

        # Generate HTML report
        validator = AccessibilityValidator()
        html_report = validator.generate_accessibility_report_html(reports)

        with open("accessibility_report.html", "w") as f:
            f.write(html_report)

        _logger.info("Generated accessibility_report.html with %d theme reports", len(reports))
