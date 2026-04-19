"""
Context processors for djust_theming.

Adds theme CSS and state to template context.
"""

from django.contrib.staticfiles.storage import staticfiles_storage
from django.utils.safestring import mark_safe

from .manager import generate_css_for_state, get_css_prefix, get_theme_manager


def theme_context(request):
    """
    Add theme CSS and state to template context.

    Injects theme CSS and state directly into the template context.

    Usage in templates:
        {{ theme_head }}
        {{ theme_switcher }}
    """
    manager = get_theme_manager(request)
    state = manager.get_state()

    # Generate CSS - use pack generator if pack is set, otherwise use theme generator
    css = generate_css_for_state(state, css_prefix=get_css_prefix())

    # Anti-FOUC script
    anti_fouc_script = """<script>
(function() {
    var storageKey = 'djust-theme-mode';
    var storedMode = localStorage.getItem(storageKey);
    var mode = storedMode || 'system';
    var resolvedMode = mode;
    if (mode === 'system') {
        resolvedMode = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    document.documentElement.setAttribute('data-theme', resolvedMode);
    document.documentElement.setAttribute('data-theme-mode', mode);
})();
</script>"""

    # Complete theme head HTML
    js_url = staticfiles_storage.url("djust_theming/js/theme.js")
    theme_head = f"""{anti_fouc_script}
<style data-djust-theme>{css}</style>
<script src="{js_url}" defer></script>"""

    # Theme switcher HTML
    theme_switcher = _render_theme_switcher(state, manager.get_available_presets())

    return {
        "theme_head": mark_safe(theme_head),
        "theme_switcher": mark_safe(theme_switcher),
        "theme_preset": state.preset,
        "theme_mode": state.mode,
        "theme_resolved_mode": state.resolved_mode,
        "theme_presets": manager.get_available_presets(),
    }


def _render_theme_switcher(state, presets):
    """Render the theme switcher HTML inline."""
    # Mode buttons
    mode_buttons = ""
    for mode, icon, label in [
        ("light", _sun_icon(), "Light"),
        ("dark", _moon_icon(), "Dark"),
        ("system", _monitor_icon(), "System"),
    ]:
        active = "active" if state.mode == mode else ""
        mode_buttons += f"""
        <button type="button"
                class="theme-mode-btn {active}"
                onclick="window.djustTheme && window.djustTheme.setMode('{mode}')"
                aria-label="{label} mode"
                title="{label} mode">
            {icon}
        </button>"""

    # Preset options
    preset_options = ""
    for preset in presets:
        selected = "selected" if preset["is_active"] else ""
        preset_options += (
            f'<option value="{preset["name"]}" {selected}>{preset["display_name"]}</option>'
        )

    return f"""<div class="theme-switcher">
    <div class="theme-mode-controls">
        {mode_buttons}
    </div>
    <select class="theme-preset-select"
            onchange="window.djustTheme && window.djustTheme.setPreset(this.value)"
            aria-label="Select theme">
        {preset_options}
    </select>
</div>
<style>
.theme-switcher {{
    display: flex;
    align-items: center;
    gap: 0.75rem;
}}
.theme-mode-controls {{
    display: flex;
    background-color: hsl(var(--muted));
    border-radius: var(--radius);
    padding: 0.25rem;
    gap: 0.125rem;
}}
.theme-mode-btn {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0.375rem 0.5rem;
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    border-radius: calc(var(--radius) - 0.125rem);
    cursor: pointer;
    transition: all 0.15s ease;
}}
.theme-mode-btn:hover {{
    color: hsl(var(--foreground));
    background-color: hsl(var(--background));
}}
.theme-mode-btn.active {{
    color: hsl(var(--foreground));
    background-color: hsl(var(--background));
    box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
}}
.theme-preset-select {{
    padding: 0.375rem 2rem 0.375rem 0.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background-color: hsl(var(--background));
    color: hsl(var(--foreground));
    color-scheme: light;
    font-size: 0.875rem;
    cursor: pointer;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23666' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 0.5rem center;
}}
[data-theme="dark"] .theme-preset-select {{
    color-scheme: dark;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23aaa' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
}}
.theme-preset-select:hover {{
    border-color: hsl(var(--ring));
}}
.theme-preset-select:focus {{
    outline: none;
    border-color: hsl(var(--ring));
    box-shadow: 0 0 0 2px hsl(var(--ring) / 0.2);
}}
</style>"""


def _sun_icon():
    return """<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>"""


def _moon_icon():
    return """<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>"""


def _monitor_icon():
    return """<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg>"""
