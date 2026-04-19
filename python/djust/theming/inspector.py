"""
Theme Inspector - Developer tool for debugging djust-theming.

Provides runtime inspection of theme tokens, CSS variables,
and visual debugging capabilities for theme development.
"""

from typing import Dict, List, Any
import json
from django.http import JsonResponse
from django.template.response import TemplateResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

from .presets import get_preset, THEME_PRESETS
from .theme_packs import get_design_system, get_all_design_systems
from .design_system_css import generate_design_system_css


class ThemeInspector:
    """Runtime theme inspection and debugging utilities."""

    def __init__(self):
        self.design_systems = get_all_design_systems()
        self.color_presets = THEME_PRESETS

    def get_theme_info(
        self, design_system_name: str = "minimalistist", color_preset_name: str = "default"
    ) -> Dict[str, Any]:
        """Get comprehensive information about a theme combination."""

        design_system = get_design_system(design_system_name)
        color_preset = get_preset(color_preset_name)

        if not design_system:
            raise ValueError(f"Design system '{design_system_name}' not found")

        # Extract design system details
        design_info = {
            "name": design_system.name,
            "display_name": design_system.display_name,
            "description": design_system.description,
            "category": design_system.category,
            "typography": {
                "name": design_system.typography.name,
                "heading_font": design_system.typography.heading_font,
                "body_font": design_system.typography.body_font,
                "base_size": design_system.typography.base_size,
                "heading_scale": design_system.typography.heading_scale,
                "line_height": design_system.typography.line_height,
                "heading_weight": design_system.typography.heading_weight,
                "body_weight": design_system.typography.body_weight,
                "letter_spacing": design_system.typography.letter_spacing,
            },
            "layout": {
                "name": design_system.layout.name,
                "space_unit": design_system.layout.space_unit,
                "space_scale": design_system.layout.space_scale,
                "border_radius_sm": design_system.layout.border_radius_sm,
                "border_radius_md": design_system.layout.border_radius_md,
                "border_radius_lg": design_system.layout.border_radius_lg,
                "button_shape": design_system.layout.button_shape,
                "card_shape": design_system.layout.card_shape,
                "input_shape": design_system.layout.input_shape,
                "container_width": design_system.layout.container_width,
                "grid_gap": design_system.layout.grid_gap,
                "section_spacing": design_system.layout.section_spacing,
            },
            "surface": {
                "name": design_system.surface.name,
                "shadow_sm": design_system.surface.shadow_sm,
                "shadow_md": design_system.surface.shadow_md,
                "shadow_lg": design_system.surface.shadow_lg,
                "border_width": design_system.surface.border_width,
                "border_style": design_system.surface.border_style,
                "surface_treatment": design_system.surface.surface_treatment,
                "backdrop_blur": design_system.surface.backdrop_blur,
                "noise_opacity": design_system.surface.noise_opacity,
            },
            "icons": {
                "name": design_system.icons.name,
                "style": design_system.icons.style,
                "weight": design_system.icons.weight,
                "size_scale": design_system.icons.size_scale,
                "stroke_width": design_system.icons.stroke_width,
                "corner_rounding": design_system.icons.corner_rounding,
            },
            "animation": {
                "name": design_system.animation.name,
                "entrance_effect": design_system.animation.entrance_effect,
                "exit_effect": design_system.animation.exit_effect,
                "hover_effect": design_system.animation.hover_effect,
                "hover_scale": design_system.animation.hover_scale,
                "hover_translate_y": design_system.animation.hover_translate_y,
                "click_effect": design_system.animation.click_effect,
                "loading_style": design_system.animation.loading_style,
                "transition_style": design_system.animation.transition_style,
                "duration_fast": design_system.animation.duration_fast,
                "duration_normal": design_system.animation.duration_normal,
                "duration_slow": design_system.animation.duration_slow,
                "easing": design_system.animation.easing,
            },
            "interaction": {
                "name": design_system.interaction.name,
                "button_hover": design_system.interaction.button_hover,
                "link_hover": design_system.interaction.link_hover,
                "card_hover": design_system.interaction.card_hover,
                "focus_style": design_system.interaction.focus_style,
                "focus_ring_width": design_system.interaction.focus_ring_width,
            },
        }

        # Extract color preset details
        def color_to_dict(color_scale):
            return {
                "hue": color_scale.h,
                "saturation": color_scale.s,
                "lightness": color_scale.lightness,
                "hsl": color_scale.to_hsl(),
                "hsl_func": color_scale.to_hsl_func(),
            }

        color_info = {
            "name": color_preset.name,
            "display_name": color_preset.display_name,
            "light": {
                "background": color_to_dict(color_preset.light.background),
                "foreground": color_to_dict(color_preset.light.foreground),
                "card": color_to_dict(color_preset.light.card),
                "card_foreground": color_to_dict(color_preset.light.card_foreground),
                "popover": color_to_dict(color_preset.light.popover),
                "popover_foreground": color_to_dict(color_preset.light.popover_foreground),
                "primary": color_to_dict(color_preset.light.primary),
                "primary_foreground": color_to_dict(color_preset.light.primary_foreground),
                "secondary": color_to_dict(color_preset.light.secondary),
                "secondary_foreground": color_to_dict(color_preset.light.secondary_foreground),
                "muted": color_to_dict(color_preset.light.muted),
                "muted_foreground": color_to_dict(color_preset.light.muted_foreground),
                "accent": color_to_dict(color_preset.light.accent),
                "accent_foreground": color_to_dict(color_preset.light.accent_foreground),
                "destructive": color_to_dict(color_preset.light.destructive),
                "destructive_foreground": color_to_dict(color_preset.light.destructive_foreground),
                "success": color_to_dict(color_preset.light.success),
                "success_foreground": color_to_dict(color_preset.light.success_foreground),
                "warning": color_to_dict(color_preset.light.warning),
                "warning_foreground": color_to_dict(color_preset.light.warning_foreground),
                "border": color_to_dict(color_preset.light.border),
                "input": color_to_dict(color_preset.light.input),
                "ring": color_to_dict(color_preset.light.ring),
                "radius": color_preset.radius,
            },
            "dark": {
                "background": color_to_dict(color_preset.dark.background),
                "foreground": color_to_dict(color_preset.dark.foreground),
                "card": color_to_dict(color_preset.dark.card),
                "card_foreground": color_to_dict(color_preset.dark.card_foreground),
                "popover": color_to_dict(color_preset.dark.popover),
                "popover_foreground": color_to_dict(color_preset.dark.popover_foreground),
                "primary": color_to_dict(color_preset.dark.primary),
                "primary_foreground": color_to_dict(color_preset.dark.primary_foreground),
                "secondary": color_to_dict(color_preset.dark.secondary),
                "secondary_foreground": color_to_dict(color_preset.dark.secondary_foreground),
                "muted": color_to_dict(color_preset.dark.muted),
                "muted_foreground": color_to_dict(color_preset.dark.muted_foreground),
                "accent": color_to_dict(color_preset.dark.accent),
                "accent_foreground": color_to_dict(color_preset.dark.accent_foreground),
                "destructive": color_to_dict(color_preset.dark.destructive),
                "destructive_foreground": color_to_dict(color_preset.dark.destructive_foreground),
                "success": color_to_dict(color_preset.dark.success),
                "success_foreground": color_to_dict(color_preset.dark.success_foreground),
                "warning": color_to_dict(color_preset.dark.warning),
                "warning_foreground": color_to_dict(color_preset.dark.warning_foreground),
                "border": color_to_dict(color_preset.dark.border),
                "input": color_to_dict(color_preset.dark.input),
                "ring": color_to_dict(color_preset.dark.ring),
                "radius": color_preset.radius,
            },
        }

        # Generate current CSS
        current_css = generate_design_system_css(design_system_name, color_preset_name)

        return {
            "combination": f"{design_system_name}-{color_preset_name}",
            "design_system": design_info,
            "color_preset": color_info,
            "generated_css": current_css,
            "css_size": len(current_css),
            "available_combinations": self.get_available_combinations(),
        }

    def get_available_combinations(self) -> List[Dict[str, str]]:
        """Get all available design system + color preset combinations."""
        combinations = []

        for design_name in self.design_systems.keys():
            for preset_name in self.color_presets.keys():
                combinations.append(
                    {
                        "id": f"{design_name}-{preset_name}",
                        "design_system": design_name,
                        "color_preset": preset_name,
                        "display_name": f"{self.design_systems[design_name].display_name} + {self.color_presets[preset_name].display_name}",
                    }
                )

        return combinations

    def compare_themes(self, theme1: str, theme2: str) -> Dict[str, Any]:
        """Compare two theme combinations."""

        # Parse theme names
        design1, color1 = theme1.split("-", 1)
        design2, color2 = theme2.split("-", 1)

        theme1_info = self.get_theme_info(design1, color1)
        theme2_info = self.get_theme_info(design2, color2)

        # Find differences
        differences = {
            "design_system_differences": {},
            "color_differences": {},
            "css_size_difference": theme2_info["css_size"] - theme1_info["css_size"],
        }

        # Compare design systems
        if design1 != design2:
            differences["design_system_differences"] = {
                "theme1": theme1_info["design_system"],
                "theme2": theme2_info["design_system"],
            }

        # Compare colors
        if color1 != color2:
            differences["color_differences"] = {
                "theme1": theme1_info["color_preset"],
                "theme2": theme2_info["color_preset"],
            }

        return {
            "theme1": theme1,
            "theme2": theme2,
            "differences": differences,
            "theme1_info": theme1_info,
            "theme2_info": theme2_info,
        }


# Django views for the inspector
def theme_inspector_view(request):
    """Theme inspector interface."""

    inspector = ThemeInspector()
    current_design = request.GET.get("design", "minimalist")
    current_color = request.GET.get("color", "default")

    theme_info = inspector.get_theme_info(current_design, current_color)

    return TemplateResponse(
        request,
        "theme_demo/inspector.html",
        {
            "title": "Theme Inspector",
            "theme_info": theme_info,
            "current_design": current_design,
            "current_color": current_color,
            "design_systems": list(inspector.design_systems.keys()),
            "color_presets": list(inspector.color_presets.keys()),
        },
    )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def theme_inspector_api(request):
    """API endpoint for theme inspection.

    CSRF exempt: This is a read-only/stateless API used by the theme inspector
    tool. POST requests only accept theme configuration (design system + color
    preset names) and return generated CSS — no state-changing operations.
    """

    inspector = ThemeInspector()

    if request.method == "GET":
        # Get theme info
        design = request.GET.get("design", "minimalist")
        color = request.GET.get("color", "default")

        try:
            theme_info = inspector.get_theme_info(design, color)
            return JsonResponse(theme_info)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)

    elif request.method == "POST":
        # Compare themes or other operations
        try:
            data = json.loads(request.body)
            action = data.get("action")

            if action == "compare":
                theme1 = data.get("theme1")
                theme2 = data.get("theme2")
                comparison = inspector.compare_themes(theme1, theme2)
                return JsonResponse(comparison)

            elif action == "combinations":
                combinations = inspector.get_available_combinations()
                return JsonResponse({"combinations": combinations})

            else:
                return JsonResponse({"error": "Unknown action"}, status=400)

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


def theme_css_api(request):
    """API endpoint to get CSS for a theme combination."""

    design = request.GET.get("design", "minimalist")
    color = request.GET.get("color", "default")

    try:
        css = generate_design_system_css(design, color)

        return JsonResponse({"combination": f"{design}-{color}", "css": css, "size": len(css)})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)
