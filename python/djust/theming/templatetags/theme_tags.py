"""
Template tags for djust_theming.

Usage:
    {% load theme_tags %}

    <!-- In <head> -->
    {% theme_head %}

    <!-- Theme switcher component -->
    {% theme_switcher %}

    <!-- Simple mode toggle -->
    {% theme_mode_toggle %}

    <!-- Preset selector -->
    {% theme_preset_selector layout="dropdown" %}
"""

from django import template
from django.template.loader import render_to_string
from django.urls import reverse, NoReverseMatch
from django.utils.html import format_html
from django.utils.http import urlencode
from django.utils.safestring import mark_safe

from ..components import PresetSelector, ThemeModeButton, ThemeSwitcher, ThemeSwitcherConfig
from ..component_css_generator import generate_component_css
from ..manager import (
    generate_critical_css_for_state,
    generate_css_for_state,
    get_css_prefix,
    get_direction,
    get_theme_config,
    get_theme_manager,
)
from ..template_resolver import resolve_theme_template

register = template.Library()


@register.simple_tag(takes_context=True)
def theme_head(context, include_js: bool = True, link_css: bool = False):
    """
    Render theme CSS and anti-FOUC script in the <head>.

    Usage:
        {% theme_head %}
        {% theme_head include_js=False %}
        {% theme_head link_css=True %}

    Renders via the shared ``djust_theming/theme_head.html`` template:

    - Anti-flash script (runs before page render to set correct theme)
    - Theme CSS (either inline <style> or <link> tag)
    - Component CSS (``components.css`` via <link> tag)
    - Optionally, the theme.js script tag

    The component CSS file contains styles for all template-tag components
    (alert, badge, button, card, input, theme-switcher). It is loaded once
    regardless of how many components are rendered on the page.
    """
    request = context.get("request")

    # Get current theme state
    manager = get_theme_manager(request)
    state = manager.get_state()
    config = get_theme_config()
    critical_css_enabled = config.get("critical_css", True)

    css_block = ""
    deferred_css_block = ""

    # Get css_prefix — needed for both CSS generation and component CSS
    css_prefix = get_css_prefix()

    if critical_css_enabled and not link_css:
        # Critical CSS split: inline critical, async-load deferred
        critical_css = generate_critical_css_for_state(state, css_prefix=css_prefix)
        css_block = f"<style data-djust-theme-critical>{critical_css}</style>"

        # Build deferred CSS URL
        try:
            deferred_url = reverse("djust_theming:deferred_theme_css")
            query_params = {"t": state.theme, "p": state.preset, "m": state.mode}
            if state.pack:
                query_params["pk"] = state.pack
            deferred_href = f"{deferred_url}?{urlencode(query_params)}"
            deferred_css_block = (
                f'<link rel="preload" href="{deferred_href}" as="style" '
                f"onload=\"this.onload=null;this.rel='stylesheet'\" data-djust-theme-deferred>"
                f'\n<noscript><link rel="stylesheet" href="{deferred_href}"></noscript>'
            )
        except NoReverseMatch:
            # Cannot resolve deferred URL — fall back to inlining everything
            css = generate_css_for_state(state, css_prefix=css_prefix)
            css_block = f"<style data-djust-theme>{css}</style>"
            deferred_css_block = ""
    elif link_css:
        try:
            url = reverse("djust_theming:theme_css")
            # Add cache buster based on state
            query_params = {"t": state.theme, "p": state.preset, "m": state.mode}
            if state.pack:
                query_params["pk"] = state.pack

            css_block = (
                f'<link rel="stylesheet" href="{url}?{urlencode(query_params)}" data-djust-theme>'
            )
        except NoReverseMatch:
            # Fallback to inline if URL not configured
            pass

    if not css_block:
        # Generate CSS inline (legacy behavior or fallback)
        css = generate_css_for_state(state, css_prefix=css_prefix)
        css_block = f"<style data-djust-theme>{css}</style>"

    # Component CSS: inline when prefix is set, static link otherwise
    component_css_block = ""
    include_component_link = True

    if css_prefix:
        # Generate prefixed component CSS inline
        component_css = generate_component_css(css_prefix)
        component_css_block = f"<style data-djust-components>{component_css}</style>"
        include_component_link = False

    # Resolve text direction
    direction = get_direction()

    # Render via shared template
    html = render_to_string(
        "djust_theming/theme_head.html",
        {
            "loading_class": True,
            "css_block": css_block,
            "deferred_css_block": deferred_css_block,
            "component_css_block": component_css_block,
            "include_component_link": include_component_link,
            "include_js": include_js,
            "direction": direction,
        },
    )
    return mark_safe(html)


@register.simple_tag(takes_context=True)
def theme_css(context):
    """
    Render only the theme CSS (no scripts).

    Useful when you want more control over script placement.

    Usage:
        {% theme_css %}
    """
    request = context.get("request")
    manager = get_theme_manager(request)
    state = manager.get_state()

    css = generate_css_for_state(state, css_prefix=get_css_prefix())

    return format_html("<style data-djust-theme>{}</style>", mark_safe(css))


@register.simple_tag(takes_context=True)
def theme_framework_overrides(context):
    """
    Render theme-aware CSS overrides for the active CSS framework.

    Maps djust theme variables (--primary, --border, --ring, etc.) onto the
    framework's form, button, badge, and alert selectors. Place this tag
    AFTER your framework's CSS file so the theme-based rules take precedence.

    Usage:
        <link rel="stylesheet" href="bootstrap4.css">
        {% theme_framework_overrides %}
        <link rel="stylesheet" href="base.css">
    """
    request = context.get("request")
    manager = get_theme_manager(request)
    state = manager.get_state()

    if not state.pack:
        return ""

    try:
        from ..pack_css_generator import ThemePackCSSGenerator

        gen = ThemePackCSSGenerator(pack_name=state.pack)
        fw_css = gen._generate_framework_css()
        if fw_css:
            return format_html(
                "<style data-djust-framework-overrides>{}</style>", mark_safe(fw_css)
            )
    except (ValueError, ImportError):
        # Pack CSS generator is optional (older installs / missing pack); emit nothing.
        pass

    return ""


@register.simple_tag(takes_context=True)
def theme_switcher(
    context,
    show_presets: bool = True,
    show_mode_toggle: bool = True,
    show_labels: bool = True,
    dropdown_position: str = "bottom-end",
    button_class: str = "",
    dropdown_class: str = "",
):
    """
    Render the full theme switcher component.

    Usage:
        {% theme_switcher %}
        {% theme_switcher show_presets=False %}
        {% theme_switcher show_labels=False button_class="btn btn-sm" %}
    """
    request = context.get("request")
    manager = get_theme_manager(request)

    config = ThemeSwitcherConfig(
        show_presets=show_presets,
        show_mode_toggle=show_mode_toggle,
        show_labels=show_labels,
        dropdown_position=dropdown_position,
        button_class=button_class,
        dropdown_class=dropdown_class,
    )

    switcher = ThemeSwitcher(theme_manager=manager, config=config)
    tmpl = resolve_theme_template(request, "theme_switcher")
    html = tmpl.render(switcher.get_context())
    return mark_safe(html)


@register.simple_tag(takes_context=True)
def theme_mode_toggle(context, button_class: str = "", show_label: bool = False):
    """
    Render a simple theme mode toggle button.

    Usage:
        {% theme_mode_toggle %}
        {% theme_mode_toggle button_class="btn btn-outline-secondary" %}
        {% theme_mode_toggle show_label=True %}
    """
    request = context.get("request")
    manager = get_theme_manager(request)

    button = ThemeModeButton(
        theme_manager=manager,
        button_class=button_class,
        show_label=show_label,
    )
    return mark_safe(button.render())


@register.simple_tag(takes_context=True)
def theme_preset_selector(
    context,
    layout: str = "dropdown",
    show_descriptions: bool = True,
    dropdown_class: str = "",
):
    """
    Render theme preset selector.

    Usage:
        {% theme_preset_selector %}
        {% theme_preset_selector layout="grid" %}
        {% theme_preset_selector layout="list" show_descriptions=True %}
    """
    request = context.get("request")
    manager = get_theme_manager(request)

    selector = PresetSelector(
        theme_manager=manager,
        show_descriptions=show_descriptions,
        layout=layout,
        dropdown_class=dropdown_class,
    )
    return mark_safe(selector.render())


@register.simple_tag(takes_context=True)
def theme_panel(
    context,
    show_mode: bool = True,
    show_packs: bool = True,
    show_presets: bool = True,
    show_design: bool = True,
    show_layout: bool = True,
):
    """
    Render a combined theme settings panel in a single dropdown.

    Includes mode toggle, theme pack selector, color preset, and design
    system — all in one compact dropdown behind a gear icon.

    Usage:
        {% theme_panel %}
        {% theme_panel show_packs=False %}
        {% theme_panel show_design=False %}
    """
    from ..theme_packs import get_all_design_systems, get_all_theme_packs

    request = context.get("request")
    manager = get_theme_manager(request)
    state = manager.get_state()
    presets = manager.get_available_presets()

    # Build design system list with display names
    designs = [
        {"name": name, "display_name": name.replace("_", " ").title()}
        for name in sorted(get_all_design_systems().keys())
    ]

    # Build theme pack list
    packs = [
        {"name": name, "display_name": pack.display_name, "description": pack.description}
        for name, pack in sorted(get_all_theme_packs().items())
    ]

    # Build layout list
    layouts = [
        {"name": "", "display_name": "Base"},
        {"name": "sidebar", "display_name": "Sidebar"},
        {"name": "topbar", "display_name": "Top Bar"},
        {"name": "sidebar-topbar", "display_name": "Sidebar + Top Bar"},
        {"name": "dashboard", "display_name": "Dashboard Grid"},
        {"name": "centered", "display_name": "Centered"},
    ]

    tmpl = resolve_theme_template(request, "components/theme_panel")
    return mark_safe(
        tmpl.render(
            {
                "show_mode": show_mode,
                "show_packs": show_packs,
                "show_presets": show_presets,
                "show_design": show_design,
                "show_layout": show_layout,
                "presets": presets,
                "designs": designs,
                "packs": packs,
                "layouts": layouts,
                "current_pack": state.pack or "",
                "current_design": getattr(state, "theme", "") or "ios",
                "current_layout": getattr(state, "layout", "") or "",
                "theme_mode": state.mode,
            }
        )
    )


@register.simple_tag(takes_context=True)
def theme_preset(context):
    """
    Get current theme preset name.

    Usage:
        <body class="theme-{% theme_preset %}">
    """
    request = context.get("request")
    manager = get_theme_manager(request)
    return manager.get_state().preset


@register.simple_tag(takes_context=True)
def theme_mode(context):
    """
    Get current theme mode setting.

    Returns 'light', 'dark', or 'system'.

    Usage:
        <body data-theme-setting="{% theme_mode %}">
    """
    request = context.get("request")
    manager = get_theme_manager(request)
    return manager.get_state().mode


@register.simple_tag(takes_context=True)
def theme_resolved_mode(context):
    """
    Get resolved theme mode (always 'light' or 'dark').

    Usage:
        <body class="{% theme_resolved_mode %}">
    """
    request = context.get("request")
    manager = get_theme_manager(request)
    return manager.get_state().resolved_mode
