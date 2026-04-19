"""
Build sample context data for the theme gallery view.

Each component gets a section with display name, description, and a list of
example dicts containing the kwargs that will be passed to its template tag.
"""

from dataclasses import fields as dataclass_fields

from djust.theming.contracts import COMPONENT_CONTRACTS
from djust.theming.presets import ColorScale, ThemePreset, ThemeTokens, list_presets
from djust.theming.theme_packs import DESIGN_SYSTEMS, DesignSystem


def _button_examples():
    examples = []
    # Variant examples (all at default size)
    for variant in ("primary", "secondary", "destructive", "ghost", "link"):
        examples.append(
            {
                "variant": variant,
                "size": "md",
                "text": variant.title(),
            }
        )
    # Size examples (all at primary variant)
    for size in ("sm", "md", "lg"):
        examples.append(
            {
                "variant": "primary",
                "size": size,
                "text": f"Button ({size})",
            }
        )
    return examples


def _card_examples():
    return [
        {"title": "Card Title", "content": "Card body content goes here.", "footer": "Card footer"},
        {"title": "No Footer", "content": "A card without a footer."},
        {"content": "A card with no title or footer."},
    ]


def _alert_examples():
    examples = []
    for variant in ("default", "success", "warning", "destructive"):
        examples.append(
            {
                "variant": variant,
                "message": f"This is a {variant} alert message.",
                "title": variant.title(),
            }
        )
    # Dismissible
    examples.append(
        {
            "variant": "default",
            "message": "This alert can be dismissed.",
            "dismissible": True,
        }
    )
    return examples


def _badge_examples():
    examples = []
    for variant in ("default", "secondary", "success", "warning", "destructive"):
        examples.append(
            {
                "variant": variant,
                "text": variant.title(),
            }
        )
    return examples


def _input_examples():
    return [
        {
            "name": "gallery_text",
            "label": "Text Input",
            "placeholder": "Enter text...",
            "type": "text",
        },
        {
            "name": "gallery_email",
            "label": "Email",
            "placeholder": "you@example.com",
            "type": "email",
        },
        {
            "name": "gallery_password",
            "label": "Password",
            "placeholder": "Enter password",
            "type": "password",
        },
    ]


def _modal_examples():
    examples = []
    for size in ("sm", "md", "lg"):
        examples.append(
            {
                "id": f"gallery-modal-{size}",
                "title": f"Modal ({size})",
                "size": size,
            }
        )
    return examples


def _dropdown_examples():
    return [
        {"id": "gallery-dropdown-left", "label": "Dropdown (left)", "align": "left"},
        {"id": "gallery-dropdown-right", "label": "Dropdown (right)", "align": "right"},
    ]


def _tabs_examples():
    return [
        {
            "id": "gallery-tabs",
            "tabs": [
                {"label": "Tab 1", "content": "Content for tab 1."},
                {"label": "Tab 2", "content": "Content for tab 2."},
                {"label": "Tab 3", "content": "Content for tab 3."},
            ],
            "active": 0,
        },
    ]


def _table_examples():
    headers = ["Name", "Email", "Role"]
    rows = [
        ["Alice", "alice@example.com", "Admin"],
        ["Bob", "bob@example.com", "Editor"],
        ["Charlie", "charlie@example.com", "Viewer"],
    ]
    examples = []
    for variant in ("default", "striped", "hover"):
        examples.append(
            {
                "variant": variant,
                "headers": headers,
                "rows": rows,
                "caption": f"Table ({variant})",
            }
        )
    return examples


def _pagination_examples():
    return [
        {"current_page": 3, "total_pages": 10, "url_pattern": "?page={}"},
    ]


def _select_examples():
    options = [
        {"value": "opt1", "label": "Option 1"},
        {"value": "opt2", "label": "Option 2"},
        {"value": "opt3", "label": "Option 3"},
    ]
    return [
        {
            "name": "gallery_select",
            "label": "Select Field",
            "options": options,
            "placeholder": "Choose...",
        },
    ]


def _textarea_examples():
    return [
        {
            "name": "gallery_textarea",
            "label": "Textarea",
            "placeholder": "Write something...",
            "rows": 4,
        },
    ]


def _checkbox_examples():
    return [
        {
            "name": "gallery_checkbox",
            "label": "Accept terms",
            "description": "You agree to our terms of service.",
        },
        {"name": "gallery_checkbox_plain", "label": "Subscribe to newsletter"},
    ]


def _radio_examples():
    options = [
        {"value": "sm", "label": "Small"},
        {"value": "md", "label": "Medium"},
        {"value": "lg", "label": "Large"},
    ]
    return [
        {"name": "gallery_radio", "label": "Choose a size", "options": options, "selected": "md"},
    ]


def _breadcrumb_examples():
    items = [
        {"label": "Home", "url": "/"},
        {"label": "Products", "url": "/products/"},
        {"label": "Current Page", "url": ""},
    ]
    return [
        {"items": items, "separator": "/"},
        {"items": items, "separator": ">"},
    ]


def _avatar_examples():
    examples = []
    for size in ("sm", "md", "lg"):
        examples.append(
            {
                "name": "Jane Doe",
                "size": size,
                "alt": "Jane Doe",
            }
        )
    # With src
    examples.append(
        {
            "src": "https://via.placeholder.com/64",
            "alt": "Placeholder avatar",
            "size": "md",
        }
    )
    return examples


def _toast_examples():
    examples = []
    for variant in ("success", "warning", "error", "info"):
        examples.append(
            {
                "variant": variant,
                "message": f"This is a {variant} toast notification.",
            }
        )
    return examples


def _progress_examples():
    examples = []
    for value in (25, 50, 75, 100):
        examples.append(
            {
                "value": value,
                "max": 100,
                "label": f"Progress {value}%",
            }
        )
    # Indeterminate
    examples.append(
        {
            "value": None,
            "max": 100,
            "label": "Loading...",
        }
    )
    return examples


def _skeleton_examples():
    return [
        {"variant": "text", "width": "200px", "height": "1rem"},
        {"variant": "circle", "width": "3rem", "height": "3rem"},
        {"variant": "rect", "width": "100%", "height": "6rem"},
    ]


def _tooltip_examples():
    examples = []
    for position in ("top", "bottom", "left", "right"):
        examples.append(
            {
                "text": f"Tooltip ({position})",
                "position": position,
                "slot_content": f"<button>Hover ({position})</button>",
            }
        )
    return examples


def _nav_item_examples():
    return [
        {"label": "Home", "url": "/", "active": True},
        {"label": "About", "url": "/about/", "active": False},
        {"label": "Inbox", "url": "/inbox/", "badge": "5"},
    ]


def _nav_group_examples():
    items = [
        {"label": "Users", "url": "/admin/users/"},
        {"label": "Settings", "url": "/admin/settings/"},
    ]
    return [
        {"label": "Admin", "items": items, "expanded": True},
        {"label": "Collapsed Group", "items": items, "expanded": False},
    ]


def _nav_examples():
    items = [
        {"label": "Home", "url": "/"},
        {"label": "Docs", "url": "/docs/"},
        {"label": "Gallery", "url": "/gallery/"},
    ]
    return [
        {"brand": "MyApp", "items": items},
    ]


def _sidebar_nav_examples():
    sections = [
        {
            "title": "Main",
            "items": [
                {"label": "Dashboard", "url": "/dash/"},
                {"label": "Analytics", "url": "/analytics/"},
            ],
        },
        {
            "title": "Settings",
            "items": [
                {"label": "Profile", "url": "/profile/"},
                {"label": "Billing", "url": "/billing/"},
            ],
        },
    ]
    return [
        {"sections": sections},
    ]


# Maps component name -> example builder function
_EXAMPLE_BUILDERS = {
    "button": _button_examples,
    "card": _card_examples,
    "alert": _alert_examples,
    "badge": _badge_examples,
    "input": _input_examples,
    "modal": _modal_examples,
    "dropdown": _dropdown_examples,
    "tabs": _tabs_examples,
    "table": _table_examples,
    "pagination": _pagination_examples,
    "select": _select_examples,
    "textarea": _textarea_examples,
    "checkbox": _checkbox_examples,
    "radio": _radio_examples,
    "breadcrumb": _breadcrumb_examples,
    "avatar": _avatar_examples,
    "toast": _toast_examples,
    "progress": _progress_examples,
    "skeleton": _skeleton_examples,
    "tooltip": _tooltip_examples,
    "nav_item": _nav_item_examples,
    "nav_group": _nav_group_examples,
    "nav": _nav_examples,
    "sidebar_nav": _sidebar_nav_examples,
}


def build_gallery_context(preset_name: str = "default") -> dict:
    """Build the full gallery context with sections for all components.

    Returns:
        dict with keys:
            sections: list of dicts, each with name, display_name, examples
            presets: list of available presets (name, display_name, description)
            current_preset: the active preset name
    """
    sections = []
    for name in COMPONENT_CONTRACTS:
        builder = _EXAMPLE_BUILDERS.get(name)
        examples = builder() if builder else []
        sections.append(
            {
                "name": name,
                "display_name": name.replace("_", " ").title(),
                "examples": examples,
            }
        )

    return {
        "sections": sections,
        "presets": list_presets(),
        "current_preset": preset_name,
    }


def serialize_tokens(tokens: ThemeTokens) -> dict:
    """Convert ThemeTokens to a JSON-friendly dict.

    Returns:
        Dict mapping field names to ``{"h": int, "s": int, "l": int}``.
    """
    result = {}
    for f in dataclass_fields(tokens):
        value = getattr(tokens, f.name)
        if isinstance(value, ColorScale):
            result[f.name] = {"h": value.h, "s": value.s, "l": value.lightness}
    return result


def serialize_preset(preset: ThemePreset) -> dict:
    """Serialize a ThemePreset to a JSON-friendly structure.

    Returns:
        Dict with ``light``, ``dark``, ``radius``, and ``default_mode`` keys.
    """
    return {
        "light": serialize_tokens(preset.light),
        "dark": serialize_tokens(preset.dark),
        "radius": float(preset.radius),
        "default_mode": preset.default_mode,
    }


def serialize_all_presets() -> dict:
    """Serialize every registered preset to a dict keyed by preset name."""
    from djust.theming.presets import THEME_PRESETS

    return {name: serialize_preset(preset) for name, preset in THEME_PRESETS.items()}


def serialize_design_system(ds: DesignSystem) -> dict:
    """Serialize a DesignSystem to a JSON-friendly structure."""
    return {
        "name": ds.name,
        "display_name": ds.display_name,
        "description": ds.description,
        "category": ds.category,
        "typography": {
            "heading_font": ds.typography.heading_font,
            "body_font": ds.typography.body_font,
            "base_size": ds.typography.base_size,
            "heading_scale": ds.typography.heading_scale,
            "line_height": ds.typography.line_height,
            "heading_weight": ds.typography.heading_weight,
            "body_weight": ds.typography.body_weight,
            "letter_spacing": ds.typography.letter_spacing,
        },
        "layout": {
            "space_unit": ds.layout.space_unit,
            "space_scale": ds.layout.space_scale,
            "border_radius_sm": ds.layout.border_radius_sm,
            "border_radius_md": ds.layout.border_radius_md,
            "border_radius_lg": ds.layout.border_radius_lg,
            "button_shape": ds.layout.button_shape,
            "card_shape": ds.layout.card_shape,
            "input_shape": ds.layout.input_shape,
            "container_width": ds.layout.container_width,
            "grid_gap": ds.layout.grid_gap,
            "section_spacing": ds.layout.section_spacing,
        },
        "surface": {
            "shadow_sm": ds.surface.shadow_sm,
            "shadow_md": ds.surface.shadow_md,
            "shadow_lg": ds.surface.shadow_lg,
            "border_width": ds.surface.border_width,
            "border_style": ds.surface.border_style,
            "surface_treatment": ds.surface.surface_treatment,
            "backdrop_blur": ds.surface.backdrop_blur,
            "noise_opacity": ds.surface.noise_opacity,
        },
        "animation": {
            "entrance_effect": ds.animation.entrance_effect,
            "exit_effect": ds.animation.exit_effect,
            "hover_effect": ds.animation.hover_effect,
            "hover_scale": ds.animation.hover_scale,
            "hover_translate_y": ds.animation.hover_translate_y,
            "click_effect": ds.animation.click_effect,
            "loading_style": ds.animation.loading_style,
            "transition_style": ds.animation.transition_style,
            "duration_fast": ds.animation.duration_fast,
            "duration_normal": ds.animation.duration_normal,
            "duration_slow": ds.animation.duration_slow,
            "easing": ds.animation.easing,
        },
        "interaction": {
            "button_hover": ds.interaction.button_hover,
            "link_hover": ds.interaction.link_hover,
            "card_hover": ds.interaction.card_hover,
            "focus_style": ds.interaction.focus_style,
            "focus_ring_width": ds.interaction.focus_ring_width,
        },
    }


def serialize_all_design_systems() -> dict:
    """Serialize every registered design system to a dict keyed by name."""
    return {name: serialize_design_system(ds) for name, ds in DESIGN_SYSTEMS.items()}
