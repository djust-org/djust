"""
Theme state management for djust.

Manages theme preset and mode preferences, with session persistence.
"""

import re
from dataclasses import dataclass
from typing import Literal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest

from .presets import ThemePreset, get_preset

# #1169(b) — cookie_namespace is interpolated directly into cookie names. RFC
# 6265 forbids whitespace, control chars, '=' and ';' inside cookie names;
# non-ASCII is implementation-defined and unsafe in practice. Restrict to
# ASCII alphanumerics + underscore + hyphen (a strict subset of RFC 6265
# token chars) so misconfiguration fails loudly at config-load instead of
# silently emitting malformed Set-Cookie headers that browsers reject or
# split into multiple cookies.
COOKIE_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

ThemeMode = Literal["light", "dark", "system"]

# Languages that use right-to-left script direction.
RTL_LANGUAGES = frozenset(
    {
        "ar",  # Arabic
        "he",  # Hebrew
        "fa",  # Farsi / Persian
        "ur",  # Urdu
        "ps",  # Pashto
        "sd",  # Sindhi
        "ckb",  # Central Kurdish (Sorani)
        "yi",  # Yiddish
        "dv",  # Divehi / Maldivian
        "ku",  # Kurdish
        "ug",  # Uyghur
    }
)

# Default configuration
DEFAULT_CONFIG = {
    "theme": "material",  # Design system theme
    "preset": "default",  # Color preset
    "default_mode": "system",
    "persist_in_session": True,
    "session_key": "djust_theme",
    "enable_dark_mode": True,
    "css_prefix": "",  # Namespace prefix for component CSS classes (e.g. "dj-")
    "use_css_layers": True,  # Wrap generated CSS in @layer declarations
    "css_layer_order": "base, tokens, components, djust-components, theme",  # Layer priority order
    "critical_css": True,  # Split CSS into critical (inlined) and deferred (async-loaded)
    "themes_dir": "themes/",  # User theme directory, relative to BASE_DIR
    "direction": "auto",  # Text direction: "ltr", "rtl", or "auto" (detect from LANGUAGE_CODE)
}


def _validate_cookie_namespace(value):
    """Validate ``LIVEVIEW_CONFIG['theme']['cookie_namespace']`` (#1169(b)).

    The value is interpolated directly into cookie names; characters
    illegal in cookie names (whitespace, ``=``, ``;``, non-ASCII)
    silently produce malformed Set-Cookie headers. Validate at
    config-load so the failure mode is a loud
    :class:`~django.core.exceptions.ImproperlyConfigured` at startup
    instead of broken cookies in production.

    ``None`` and empty string are accepted as "no namespace configured".
    """
    if value is None or value == "":
        return value
    if not isinstance(value, str) or not COOKIE_NAMESPACE_RE.match(value):
        raise ImproperlyConfigured(
            f"LIVEVIEW_CONFIG['theme']['cookie_namespace']={value!r} contains "
            f"characters illegal in cookie names. Use only ASCII letters, "
            f"digits, underscores, and hyphens."
        )
    return value


def get_theme_config() -> dict:
    """Get theme configuration from Django settings.

    Raises:
        ImproperlyConfigured: if ``cookie_namespace`` contains characters
            illegal in cookie names (#1169(b)).
    """
    liveview_config = getattr(settings, "LIVEVIEW_CONFIG", {})
    theme_config = liveview_config.get("theme", {})
    merged = {**DEFAULT_CONFIG, **theme_config}
    # Validate cookie_namespace at config-load (#1169(b)).
    _validate_cookie_namespace(merged.get("cookie_namespace"))
    return merged


@dataclass
class ThemeState:
    """Current theme state."""

    theme: str  # Design system theme (material, ios, fluent, etc.)
    preset: str  # Color preset
    mode: ThemeMode
    resolved_mode: str  # 'light' or 'dark' (system resolved to actual)
    pack: str = None  # Theme pack name (optional, overrides theme + preset)
    layout: str = ""  # Layout template (sidebar, topbar, dashboard, centered, etc.)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "theme": self.theme,
            "preset": self.preset,
            "mode": self.mode,
            "resolved_mode": self.resolved_mode,
            "pack": self.pack,
        }


def get_css_prefix() -> str:
    """Get the configured CSS namespace prefix."""
    return get_theme_config().get("css_prefix", "")


def get_direction() -> str:
    """Resolve the text direction for the current configuration.

    Returns ``"ltr"`` or ``"rtl"``.  When the config value is ``"auto"``
    (the default), the direction is inferred from Django's
    ``settings.LANGUAGE_CODE`` by checking the primary language subtag
    against :data:`RTL_LANGUAGES`.
    """
    config = get_theme_config()
    direction = config.get("direction", "auto")

    if direction in ("ltr", "rtl"):
        return direction

    # "auto" -- detect from LANGUAGE_CODE
    lang_code = getattr(settings, "LANGUAGE_CODE", "en")
    # Extract primary language subtag (e.g. "ar-sa" -> "ar")
    primary = lang_code.split("-")[0].lower()
    return "rtl" if primary in RTL_LANGUAGES else "ltr"


def generate_css_for_state(state: "ThemeState", css_prefix: str = "") -> str:
    """
    Generate CSS for a given theme state, handling pack-vs-theme selection.

    Central function that consolidates the pack-or-theme CSS generation logic
    previously duplicated across theme_tags, views, context_processors, and mixins.

    Args:
        state: Current ThemeState (from ThemeManager.get_state())
        css_prefix: Namespace prefix for component CSS classes (e.g. "dj-")

    Returns:
        Generated CSS string
    """
    if state.pack:
        try:
            from .pack_css_generator import generate_pack_css

            return generate_pack_css(pack_name=state.pack)
        except ValueError:
            # Fall back to theme generator if pack not found
            pass

    from .theme_css_generator import generate_theme_css

    return generate_theme_css(
        theme_name=state.theme,
        color_preset=state.preset,
        css_prefix=css_prefix,
    )


def generate_critical_css_for_state(state: "ThemeState", css_prefix: str = "") -> str:
    """
    Generate critical CSS for a given theme state (for inline delivery).

    Critical CSS contains only tokens, custom properties, and layer declarations
    needed for first paint. This is the complement of
    ``generate_deferred_css_for_state()``.

    Args:
        state: Current ThemeState (from ThemeManager.get_state())
        css_prefix: Namespace prefix for component CSS classes (e.g. "dj-")

    Returns:
        Critical CSS string suitable for inlining in a <style> tag.
    """
    if state.pack:
        try:
            from .pack_css_generator import ThemePackCSSGenerator

            gen = ThemePackCSSGenerator(pack_name=state.pack)
            critical = gen.theme_generator.generate_critical_css()
            # Include design system tokens (form, layout, typography vars)
            # in critical CSS so they're available for first paint.
            ds_vars = gen._generate_design_system_vars()
            # Framework CSS (theme vars → Bootstrap/Tailwind selectors) is
            # emitted separately by {% theme_framework_overrides %} so it
            # loads AFTER the framework's static CSS file in the cascade.
            return critical + "\n" + ds_vars
        except ValueError:
            pass

    from .theme_css_generator import CompleteThemeCSSGenerator

    gen = CompleteThemeCSSGenerator(state.theme, state.preset, css_prefix=css_prefix)
    return gen.generate_critical_css()


def generate_deferred_css_for_state(state: "ThemeState", css_prefix: str = "") -> str:
    """
    Generate deferred CSS for a given theme state (for async loading).

    Deferred CSS contains base styles, utilities, typography classes, and
    component styles. This is the complement of
    ``generate_critical_css_for_state()``.

    Args:
        state: Current ThemeState (from ThemeManager.get_state())
        css_prefix: Namespace prefix for component CSS classes (e.g. "dj-")

    Returns:
        Deferred CSS string suitable for serving from a <link> tag.
    """
    if state.pack:
        try:
            from .pack_css_generator import ThemePackCSSGenerator

            gen = ThemePackCSSGenerator(pack_name=state.pack)
            return gen.theme_generator.generate_deferred_css()
        except ValueError:
            pass

    from .theme_css_generator import CompleteThemeCSSGenerator

    gen = CompleteThemeCSSGenerator(state.theme, state.preset, css_prefix=css_prefix)
    return gen.generate_deferred_css()


def get_theme_manager(request: HttpRequest | None = None) -> "ThemeManager":
    """
    Get or create a cached ThemeManager for the given request.

    Caches the instance on ``request._djust_theme_manager`` so that
    multiple template tags / context processors within the same
    request reuse a single ThemeManager (same pattern Django uses
    for ``request.user``).
    """
    if request is not None:
        manager = getattr(request, "_djust_theme_manager", None)
        if manager is not None:
            return manager
        manager = ThemeManager(request=request)
        request._djust_theme_manager = manager
        return manager
    # No request — cannot cache, return fresh instance
    return ThemeManager(request=None)


class ThemeManager:
    """
    Manages theme state for a session.

    Handles preset selection, mode switching, and session persistence.
    """

    VALID_MODES = ("light", "dark", "system")

    def __init__(self, request: HttpRequest | None = None):
        """
        Initialize theme manager.

        Args:
            request: Django HTTP request (for session access)
        """
        self.request = request
        self.config = get_theme_config()
        self._session_key = self.config["session_key"]

    @property
    def session(self):
        """Get session if available."""
        if self.request and hasattr(self.request, "session"):
            return self.request.session
        return None

    def _get_session_data(self) -> dict:
        """Get theme data from session."""
        if not self.session:
            return {}
        return self.session.get(self._session_key, {})

    def _set_session_data(self, data: dict) -> None:
        """Save theme data to session."""
        if self.session and self.config["persist_in_session"]:
            self.session[self._session_key] = data

    def get_state(self) -> ThemeState:
        """
        Get current theme state.

        Returns:
            ThemeState with current theme, preset and mode
        """
        from .registry import get_registry
        import logging

        logger = logging.getLogger(__name__)

        registry = get_registry()
        session_data = self._get_session_data()

        # Check cookies for theme, preset, and pack (set by JavaScript).
        #
        # #1013 — sites WITHOUT a user-facing theme switcher can disable cookie
        # reads via ``LIVEVIEW_CONFIG['theme']['enable_client_override']: False``
        # to prevent cross-project cookie bleed on localhost (every djust site
        # answering on localhost shares a cookie jar — `djust_theme_pack` set
        # by project A pins the palette for project B). Default ``True`` for
        # back-compat: sites with a user-facing switcher keep working.
        #
        # #1158 — sites WITH a user-facing switcher (so cookies must stay on)
        # can opt into a per-project cookie namespace via
        # ``LIVEVIEW_CONFIG['theme']['cookie_namespace']: '<ns>'``. When set,
        # the four theming cookies become ``<ns>_djust_theme``,
        # ``<ns>_djust_theme_preset``, ``<ns>_djust_theme_pack``,
        # ``<ns>_djust_theme_layout``. Read-side falls back to the unprefixed
        # name for one-time migration; write-side (theme.js) writes only the
        # namespaced name when the prefix is set.
        theme = None
        preset = None
        pack = None
        layout = ""
        enable_client_override = bool(self.config.get("enable_client_override", True))
        if self.request and enable_client_override:
            cookies = self.request.COOKIES
            ns = (self.config.get("cookie_namespace") or "").strip()
            prefix = f"{ns}_" if ns else ""

            # Read namespaced first; fall back to unprefixed (migration window).
            #
            # #1169(a) — distinguish "namespaced cookie not set" from
            # "namespaced cookie set to empty string". An empty string is a
            # deliberate value (e.g. user cleared a layout); falling through
            # to the legacy unprefixed cookie in that case re-introduces
            # cross-project bleed via the same path #1158 closed.
            def _read(name: str, default: str = "") -> str:
                if prefix:
                    namespaced = cookies.get(f"{prefix}{name}")
                    if namespaced is not None:
                        return namespaced
                    return cookies.get(name, default)
                return cookies.get(name, default)

            # _read returns "" when the cookie is set to empty; preserve that
            # rather than coercing to None via `or None` (which would re-trigger
            # the bleed-through path the namespaced read just closed).
            raw_theme = _read("djust_theme")
            raw_preset = _read("djust_theme_preset")
            raw_pack = _read("djust_theme_pack")
            theme = raw_theme if raw_theme else None
            preset = raw_preset if raw_preset else None
            pack = raw_pack if raw_pack else None
            layout = _read("djust_theme_layout", "")
            logger.debug(
                "Cookies (ns=%r): theme=%s, preset=%s, pack=%s, layout=%s",
                ns,
                theme,
                preset,
                pack,
                layout,
            )
        elif self.request:
            logger.debug("Cookies skipped — LIVEVIEW_CONFIG.theme.enable_client_override=False")

        # Fall back to session, then config default
        if not theme:
            theme = session_data.get("theme", self.config["theme"])
        if not preset:
            preset = session_data.get("preset", self.config["preset"])
        if not pack:
            pack = session_data.get("pack", self.config.get("pack"))

        mode = session_data.get("mode", self.config["default_mode"])

        logger.debug(
            "Resolved before validation: theme=%s, preset=%s, pack=%s, mode=%s",
            theme,
            preset,
            pack,
            mode,
        )

        # If pack is set, override theme and preset from pack
        if pack:
            from .theme_packs import get_theme_pack

            theme_pack = get_theme_pack(pack)
            if theme_pack:
                theme = theme_pack.design_theme
                preset = theme_pack.color_preset

        # Validate theme
        if not registry.has_theme(theme):
            theme = "material"

        # Validate preset
        if not registry.has_preset(preset):
            preset = "default"

        # Validate mode
        if mode not in self.VALID_MODES:
            mode = "system"

        # Resolve system mode (default to light for server-side)
        resolved_mode = mode if mode != "system" else "light"

        return ThemeState(
            theme=theme,
            preset=preset,
            mode=mode,
            resolved_mode=resolved_mode,
            pack=pack,
            layout=layout or session_data.get("layout", ""),
        )

    def set_theme(self, theme_name: str) -> bool:
        """
        Set design system theme.

        Args:
            theme_name: Name of theme to use (material, ios, fluent, etc.)

        Returns:
            True if theme was valid and set
        """
        from .registry import get_registry

        if not get_registry().has_theme(theme_name):
            return False

        session_data = self._get_session_data()
        session_data["theme"] = theme_name
        self._set_session_data(session_data)
        return True

    def set_preset(self, preset_name: str) -> bool:
        """
        Set color preset.

        Args:
            preset_name: Name of color preset to use

        Returns:
            True if preset was valid and set
        """
        from .registry import get_registry

        if not get_registry().has_preset(preset_name):
            return False

        session_data = self._get_session_data()
        session_data["preset"] = preset_name
        self._set_session_data(session_data)
        return True

    def set_mode(self, mode: str) -> bool:
        """
        Set theme mode.

        Args:
            mode: 'light', 'dark', or 'system'

        Returns:
            True if mode was valid and set
        """
        if mode not in self.VALID_MODES:
            return False

        if mode == "dark" and not self.config["enable_dark_mode"]:
            return False

        session_data = self._get_session_data()
        session_data["mode"] = mode
        self._set_session_data(session_data)
        return True

    def toggle_mode(self) -> str:
        """
        Toggle between light and dark mode.

        If currently in system mode, switches to opposite of system preference.

        Returns:
            New mode ('light' or 'dark')
        """
        state = self.get_state()

        # Toggle based on resolved mode
        new_mode = "light" if state.resolved_mode == "dark" else "dark"
        self.set_mode(new_mode)
        return new_mode

    def get_preset(self) -> ThemePreset:
        """Get current theme preset object."""
        state = self.get_state()
        return get_preset(state.preset)

    def get_available_presets(self) -> list[dict]:
        """Get list of available preset metadata."""
        from .registry import get_registry

        return [
            {
                "name": preset.name,
                "display_name": preset.display_name,
                "description": preset.description,
                "is_active": preset.name == self.get_state().preset,
                "primary_hsl": preset.dark.primary.to_hsl(),
                "primary_hsl_light": preset.light.primary.to_hsl(),
            }
            for preset in get_registry().list_presets().values()
        ]

    def get_context(self) -> dict:
        """
        Get template context for theme rendering.

        Returns:
            Dict with theme state and presets for templates
        """
        state = self.get_state()
        return {
            "theme_preset": state.preset,
            "theme_mode": state.mode,
            "theme_resolved_mode": state.resolved_mode,
            "theme_presets": self.get_available_presets(),
            "dark_mode_enabled": self.config["enable_dark_mode"],
        }
