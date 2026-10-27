"""
ThemeMixin for djust LiveViews.

Provides theme-aware context, event handlers, and reactive theme switching.
Works with both Django templates (via context processors) and LiveView
(via instance attributes and push_event).
"""

from typing import Any, Callable

try:
    from djust.decorators import event_handler

    DJUST_AVAILABLE = True
except ImportError:
    DJUST_AVAILABLE = False

    def event_handler(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        """Dummy decorator when djust is not available."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            return func

        if args and callable(args[0]):
            return args[0]
        return decorator


from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from ..utils import get_csp_nonce
from ._lazy_html import LazyThemeHTML, LazyThemeList
from .manager import ThemeManager, generate_css_for_state, get_css_prefix, get_theme_manager
from .presets import THEME_PRESETS

#: The four rendered values the mixin used to assign as view attributes (#3626).
#: Each is large (tens of KB) and was therefore stored in every snapshot of
#: the view's state. They are now computed when a template or a caller reads
#: them and are never assigned, so they are never part of the state.
_RENDERED_THEME_NAMES = ("theme_head", "theme_css", "theme_switcher", "theme_presets")


class _RenderedOnRead:
    """A read-only class attribute whose value is computed on each access.

    This is how ``view.theme_head`` (and ``theme_css`` / ``theme_switcher`` /
    ``theme_presets``) keeps working for Python callers although the mixin no
    longer assigns it. Three properties matter:

    * It has no ``__set__``, so it is a NON-data descriptor. An instance
      assignment (``self.theme_head = "..."`` in a subclass) shadows it, exactly
      as it would have shadowed the old attribute.
    * It is marked ``_djust_framework_derived``, the marker the framework reads
      to tell derived values from view data. ``get_context_data`` skips such a
      class attribute, and a context-processor value may be injected over it
      (the HTTP POST fallback does, see ``_view_supplies``). Without the marker
      the POST fallback would treat the name as supplied by the view and skip
      the injection.
    * It renders on every read and caches nothing: a cache on the instance would
      be a stored attribute again.
    """

    _djust_framework_derived = True

    def __init__(self, method: str) -> None:
        self._method = method

    def __get__(self, obj: Any, owner: Any = None) -> Any:
        if obj is None:
            return self
        return getattr(obj, self._method)()


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

        In template, use these variables:
            {{ theme_head }}      - CSS + anti-FOUC script + JS
            {{ theme_switcher }}  - Theme mode and preset controls
            {{ theme_css }}       - Just the CSS variables
            {{ theme_presets }}   - The selectable presets
            {{ theme_preset }}    - Current preset name
            {{ theme_mode }}      - Current mode (light/dark/system)

    The first four are rendered when a template reads them. They are not
    attributes of the view, so they are not part of its state: a page that
    prints only ``{% theme_head %}`` stores none of their ~80 KB (#3626).
    ``view.theme_head`` and the other three still work in Python: each is
    computed on every read and cached nowhere. Assigning one in a subclass
    (``self.theme_head = ...``) still takes precedence over the computed value,
    and also makes it state, as any assignment does.

    For reactive theme changes (no page reload):
        - Use dj-click="set_theme_mode" with data-dj-mode="dark"
        - Use dj-click="set_theme_preset" with data-dj-preset="blue"
        - The mixin will push CSS updates to the client via push_event
    """

    # Internal state (prefixed to avoid serialization)
    _theme_manager: ThemeManager | None = None
    _theme_state: Any = None
    # CSP nonce of the request that mounted the view ("" when none), so the
    # inline <script>/<style> in ``theme_head`` carry it like the tag does (#3284).
    _theme_csp_nonce: str = ""

    def mount(self, request: Any, **kwargs: Any) -> None:
        """Initialize theme manager and add theme context as instance attributes."""
        # Call parent mount if it exists. ThemeMixin is mixed into a LiveView
        # at runtime (the MRO provides ``mount``); ``object`` has none, so the
        # static checker can't see it — guarded by the hasattr check above.
        if hasattr(super(), "mount"):
            super().mount(request, **kwargs)  # type: ignore[misc]

        # Initialize theme manager (underscore prefix to avoid serialization)
        self._theme_manager = get_theme_manager(request)
        self._theme_state = self._theme_manager.get_state()
        self._theme_csp_nonce = get_csp_nonce(request)

        # Add theme context as instance attributes
        # These get picked up by get_context_data() in LiveView
        self._setup_theme_context()

    def _setup_theme_context(self) -> None:
        """Set up the small theme state values as instance attributes.

        Only ``theme_preset``, ``theme_mode`` and ``theme_resolved_mode`` are
        assigned: they are a few bytes each, and a template that branches on
        them needs a change to re-render. The four rendered values
        (``theme_head``, ``theme_css``, ``theme_switcher``, ``theme_presets``)
        are deliberately NOT assigned (#3626): each is tens of KB, and every
        public attribute is serialised into every stored snapshot. Templates
        reach them through :meth:`_apply_context_processors`, which supplies
        them lazily and out of the view's state, and Python reads them through
        the :class:`_RenderedOnRead` class attributes below.
        """
        if not self._theme_manager or not self._theme_state:
            return

        state = self._theme_state

        # A session written before #3626 holds the four rendered strings as
        # public attributes, and a restore sets them back on the view, where
        # they would shadow the lazy values with a stale copy and be saved
        # again. A mount that runs sheds them.
        for name in _RENDERED_THEME_NAMES:
            self.__dict__.pop(name, None)

        # Individual state values (useful for conditional logic in templates)
        self.theme_preset = state.preset
        self.theme_mode = state.mode
        self.theme_resolved_mode = state.resolved_mode

    # --- the rendered values: computed on read, never stored (#3626) ---------

    def _theme_manager_and_state(self) -> tuple[ThemeManager, Any]:
        """The manager and state to render from.

        ``mount()`` sets both. A view restored from a saved session did not run
        ``mount()`` (the manager is not serialisable), so fall back to the
        request, which resolves the same manager the tags use.
        """
        manager = self._theme_manager
        state = self._theme_state
        if manager is None or state is None:
            manager = get_theme_manager(getattr(self, "request", None))
            state = manager.get_state()
        return manager, state

    def _theme_head_context(self) -> dict[str, Any]:
        # Build the theme_head render context via the shared builder so the
        # ThemeMixin path cannot drift from the {% theme_head %} tag (#1531 —
        # the #1452 drift, repeated). loading_class=False: a LiveView mount is
        # a reactive render, not a cold page load, so no loading-class flash.
        from .templatetags.theme_tags import build_theme_head_context

        manager, _state = self._theme_manager_and_state()
        return build_theme_head_context(
            None,
            loading_class=False,
            manager=manager,
            nonce=self._theme_csp_nonce,
        )

    def _render_theme_head(self) -> Any:
        """``theme_head``: CSS + anti-FOUC script + JS, via the shared template."""
        return mark_safe(
            render_to_string("djust_theming/theme_head.html", self._theme_head_context())
        )

    def _render_theme_css(self) -> Any:
        """``theme_css``: just the CSS block, as in ``theme_head`` (#1531)."""
        return mark_safe(self._theme_head_context()["css_block"])

    def _render_theme_switcher(self) -> Any:
        """``theme_switcher``: the mode and preset controls."""
        manager, state = self._theme_manager_and_state()
        return mark_safe(
            render_to_string(
                "djust_theming/theme_switcher.html",
                {
                    "theme_mode": state.mode,
                    "presets": manager.get_available_presets(),
                    "show_mode_toggle": True,
                    "show_presets": True,
                    "show_labels": False,
                    "liveview": True,
                    "button_class": "",
                    "dropdown_class": "",
                },
            )
        )

    def _theme_presets_list(self) -> Any:
        """``theme_presets``: the presets the switcher offers."""
        manager, _state = self._theme_manager_and_state()
        return manager.get_available_presets()

    def _theme_css_raw_for_push(self) -> str:
        """The raw CSS ``push_event`` sends to the client on a preset change."""
        _manager, state = self._theme_manager_and_state()
        return generate_css_for_state(state, css_prefix=get_css_prefix())

    theme_head = _RenderedOnRead("_render_theme_head")
    theme_css = _RenderedOnRead("_render_theme_css")
    theme_switcher = _RenderedOnRead("_render_theme_switcher")
    theme_presets = _RenderedOnRead("_theme_presets_list")

    def _apply_context_processors(self, context: dict[str, Any], request: Any) -> dict[str, Any]:
        """Supply the four rendered theme values to the template, lazily.

        They used to be instance attributes, so they were in the view's state
        (#3626). They are now added here next to the context processors'
        output, which is where every request-scoped value comes from: the
        framework keeps those out of the change-detection fingerprint and, when
        they are not plain data (a :class:`LazyThemeHTML` is not), out of the
        stored Rust state too. A chunk renders on its first read and not at all
        on a page that never reads it.

        The mixin's values win over a ``theme_context`` processor's, as the old
        attributes did (view context beats a processor): they differ from it in
        what a LiveView needs (``loading_class=False`` in the head, and a
        switcher built for the view). A name the view's own context already
        holds is left alone, so an assignment in a subclass still wins.
        """
        own = set(context)
        result: dict[str, Any] = super()._apply_context_processors(context, request)  # type: ignore[misc]
        if self._theme_manager is None or self._theme_state is None:
            return result

        lazy = {
            "theme_head": LazyThemeHTML(self._render_theme_head, name="ThemeMixin.theme_head"),
            "theme_css": LazyThemeHTML(self._render_theme_css, name="ThemeMixin.theme_css"),
            "theme_switcher": LazyThemeHTML(
                self._render_theme_switcher, name="ThemeMixin.theme_switcher"
            ),
        }
        added = set()
        for name, chunk in lazy.items():
            if name not in own and name not in self.__dict__:
                result[name] = chunk
                added.add(name)
        # ``theme_presets`` is plain data, which djust copies into the stored
        # state even when it is request-scoped, so it rides as a lazy list too.
        # A processor that is installed supplies the same list eagerly; the
        # mixin's wins, as it did when it was an attribute.
        if "theme_presets" not in own and "theme_presets" not in self.__dict__:
            result["theme_presets"] = LazyThemeList(self._theme_presets_list)
            added.add("theme_presets")
        # Record them as framework-supplied, like the processor keys: that is
        # what keeps them out of the change-detection fingerprint and, for the
        # lazy chunks, out of the stored state.
        self._context_processor_keys = set(getattr(self, "_context_processor_keys", ())) | added
        return result

    def _push_theme_update(
        self, mode: str | None = None, preset: str | None = None, css: str | None = None
    ) -> None:
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
        def set_theme_mode(self, mode: str = "", value: str = "", **kwargs: Any) -> None:
            """
            Set theme mode to light, dark, or system.

            Usage in template:
                <button dj-click="set_theme_mode" data-dj-mode="dark">Dark Mode</button>
            """
            # Support both data-dj-mode (dj-click) and value (dj-change)
            mode = mode or value or "system"
            if mode not in ("light", "dark", "system"):
                return
            if self._theme_manager is None:
                return

            success = self._theme_manager.set_mode(mode)
            if success:
                self._theme_state = self._theme_manager.get_state()
                self._setup_theme_context()  # Refresh context

                # Push update to client (mode only - CSS doesn't change)
                self._push_theme_update(mode=mode)

        @event_handler()
        def set_theme_preset(self, preset: str = "", value: str = "", **kwargs: Any) -> None:
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
            if self._theme_manager is None:
                return

            success = self._theme_manager.set_preset(preset)
            if success:
                self._theme_state = self._theme_manager.get_state()
                self._setup_theme_context()  # Refresh context with new CSS

                # Push update to client with new CSS
                self._push_theme_update(preset=preset, css=self._theme_css_raw_for_push())

        @event_handler()
        def toggle_theme_mode(self, **kwargs: Any) -> None:
            """
            Toggle between light and dark mode.

            Usage in template:
                <button dj-click="toggle_theme_mode">Toggle Theme</button>
            """
            if self._theme_manager is None:
                return
            new_mode = self._theme_manager.toggle_mode()
            self._theme_state = self._theme_manager.get_state()
            self._setup_theme_context()

            # Push update to client
            self._push_theme_update(mode=new_mode)

        @event_handler()
        def cycle_theme_preset(self, **kwargs: Any) -> None:
            """
            Cycle through available theme presets.

            Usage in template:
                <button dj-click="cycle_theme_preset">Next Theme</button>
            """
            if self._theme_manager is None:
                return
            current = self._theme_state.preset
            presets = list(THEME_PRESETS.keys())
            current_index = presets.index(current) if current in presets else 0
            next_index = (current_index + 1) % len(presets)
            next_preset = presets[next_index]

            self._theme_manager.set_preset(next_preset)
            self._theme_state = self._theme_manager.get_state()
            self._setup_theme_context()

            # Push update to client with new CSS
            self._push_theme_update(preset=next_preset, css=self._theme_css_raw_for_push())
