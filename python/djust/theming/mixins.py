"""
ThemeMixin for djust LiveViews.

Provides theme-aware context, event handlers, and reactive theme switching.
Works with both Django templates (via context processors) and LiveView
(via instance attributes and push_event).
"""

try:
    from djust.decorators import event_handler

    DJUST_AVAILABLE = True
except ImportError:
    DJUST_AVAILABLE = False

    def event_handler(*args, **kwargs):
        """Dummy decorator when djust is not available."""

        def decorator(func):
            return func

        if args and callable(args[0]):
            return args[0]
        return decorator


from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from .manager import ThemeManager, generate_css_for_state, get_css_prefix, get_theme_manager
from .presets import THEME_PRESETS


class ThemeMixin:
    """
    Mixin to add theme support to LiveViews.

    Provides:
    - Theme manager initialization in mount()
    - Event handlers for theme switching
    - Template context with theme state (as instance attributes)
    - Reactive theme switching via push_event (no page reload)

    Usage:
        class MyView(ThemeMixin, LiveView):
            template_name = 'my_template.html'

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                # Your mount code here

        In template, use these instance variables:
            {{ theme_head }}      - CSS + anti-FOUC script + JS
            {{ theme_switcher }}  - Theme mode and preset controls
            {{ theme_css }}       - Just the CSS variables
            {{ theme_preset }}    - Current preset name
            {{ theme_mode }}      - Current mode (light/dark/system)

    For reactive theme changes (no page reload):
        - Use dj-click="set_theme_mode" with data-dj-mode="dark"
        - Use dj-click="set_theme_preset" with data-dj-preset="blue"
        - The mixin will push CSS updates to the client via push_event
    """

    # Internal state (prefixed to avoid serialization)
    _theme_manager: ThemeManager = None
    _theme_state = None

    def mount(self, request, **kwargs):
        """Initialize theme manager and add theme context as instance attributes."""
        # Call parent mount if it exists
        if hasattr(super(), "mount"):
            super().mount(request, **kwargs)

        # Initialize theme manager (underscore prefix to avoid serialization)
        self._theme_manager = get_theme_manager(request)
        self._theme_state = self._theme_manager.get_state()

        # Add theme context as instance attributes
        # These get picked up by get_context_data() in LiveView
        self._setup_theme_context()

    def _setup_theme_context(self):
        """Set up theme context variables as instance attributes.

        Renders ``theme_head`` via the shared ``theme_head.html`` template and
        ``theme_switcher`` via ``theme_switcher.html`` (with ``liveview=True``
        for ``dj-click``/``dj-change`` event bindings).
        """
        if not self._theme_manager or not self._theme_state:
            return

        state = self._theme_state
        presets = self._theme_manager.get_available_presets()

        # Generate CSS using the central convenience function
        prefix = get_css_prefix()
        css = generate_css_for_state(state, css_prefix=prefix)

        # Build the CSS block (link or inline style)
        from django.urls import reverse, NoReverseMatch

        try:
            url = reverse("djust_theming:theme_css")
            cache_buster = f"t={state.theme}&p={state.preset}&m={state.mode}"
            if state.pack:
                cache_buster += f"&pk={state.pack}"
            css_block = f'<link rel="stylesheet" href="{url}?{cache_buster}" data-djust-theme id="djust-theme-css">'
        except NoReverseMatch:
            css_block = f'<style id="djust-theme-css" data-djust-theme>{css}</style>'

        # Render theme_head via shared template
        self.theme_head = mark_safe(
            render_to_string(
                "djust_theming/theme_head.html",
                {
                    "loading_class": False,
                    "css_block": css_block,
                    "include_js": True,
                },
            )
        )

        # Set theme_css - just the CSS (for cases where you want more control)
        self.theme_css = css_block

        # Raw CSS content (for push_event updates)
        self._theme_css_raw = css

        # Set theme_switcher - render via shared template with liveview events
        self.theme_switcher = mark_safe(
            render_to_string(
                "djust_theming/theme_switcher.html",
                {
                    "theme_mode": state.mode,
                    "presets": presets,
                    "show_mode_toggle": True,
                    "show_presets": True,
                    "show_labels": False,
                    "liveview": True,
                    "button_class": "",
                    "dropdown_class": "",
                },
            )
        )

        # Individual state values (useful for conditional logic in templates)
        self.theme_preset = state.preset
        self.theme_mode = state.mode
        self.theme_resolved_mode = state.resolved_mode
        self.theme_presets = presets

    def _push_theme_update(self, mode: str = None, preset: str = None, css: str = None):
        """
        Push theme update to client via push_event.

        This allows reactive theme changes without full page reload.
        The client JS will update the CSS and DOM attributes.

        The event name 'theme_update' will be dispatched by djust as
        'djust:push_event' with detail { event: 'theme_update', payload: {...} }
        """
        if hasattr(self, "push_event"):
            payload = {}
            if mode is not None:
                payload["mode"] = mode
            if preset is not None:
                payload["preset"] = preset
            if css is not None:
                payload["css"] = css

            self.push_event("theme_update", payload)
            # Theme changes are purely client-side (CSS variables) — skip the
            # server render to avoid a full HTML update that mangles the page
            # when theme state is outside <div data-djust-root> (DJE-053).
            self._skip_render = True

    # Event handlers - only define if djust is available
    if DJUST_AVAILABLE:

        @event_handler()
        def set_theme_mode(self, mode: str = "", value: str = "", **kwargs):
            """
            Set theme mode to light, dark, or system.

            Usage in template:
                <button dj-click="set_theme_mode" data-dj-mode="dark">Dark Mode</button>
            """
            # Support both data-dj-mode (dj-click) and value (dj-change)
            mode = mode or value or "system"
            if mode not in ("light", "dark", "system"):
                return

            success = self._theme_manager.set_mode(mode)
            if success:
                self._theme_state = self._theme_manager.get_state()
                self._setup_theme_context()  # Refresh context

                # Push update to client (mode only - CSS doesn't change)
                self._push_theme_update(mode=mode)

        @event_handler()
        def set_theme_preset(self, preset: str = "", value: str = "", **kwargs):
            """
            Set theme preset.

            Usage in template (button):
                <button dj-click="set_theme_preset" data-dj-preset="blue">Blue</button>

            Usage in template (select):
                <select dj-change="set_theme_preset">
                    <option value="default">Default</option>
                    <option value="blue">Blue</option>
                </select>
            """
            # Support both data-dj-preset (dj-click) and value (dj-change)
            preset = preset or value or "default"
            if preset not in THEME_PRESETS:
                return

            success = self._theme_manager.set_preset(preset)
            if success:
                self._theme_state = self._theme_manager.get_state()
                self._setup_theme_context()  # Refresh context with new CSS

                # Push update to client with new CSS
                self._push_theme_update(preset=preset, css=self._theme_css_raw)

        @event_handler()
        def toggle_theme_mode(self, **kwargs):
            """
            Toggle between light and dark mode.

            Usage in template:
                <button dj-click="toggle_theme_mode">Toggle Theme</button>
            """
            new_mode = self._theme_manager.toggle_mode()
            self._theme_state = self._theme_manager.get_state()
            self._setup_theme_context()

            # Push update to client
            self._push_theme_update(mode=new_mode)

        @event_handler()
        def cycle_theme_preset(self, **kwargs):
            """
            Cycle through available theme presets.

            Usage in template:
                <button dj-click="cycle_theme_preset">Next Theme</button>
            """
            current = self._theme_state.preset
            presets = list(THEME_PRESETS.keys())
            current_index = presets.index(current) if current in presets else 0
            next_index = (current_index + 1) % len(presets)
            next_preset = presets[next_index]

            self._theme_manager.set_preset(next_preset)
            self._theme_state = self._theme_manager.get_state()
            self._setup_theme_context()

            # Push update to client with new CSS
            self._push_theme_update(preset=next_preset, css=self._theme_css_raw)
