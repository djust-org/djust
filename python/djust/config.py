"""
Configuration system for djust

Provides centralized configuration for:
- CSS framework (Bootstrap 5, Tailwind CSS, None)
- Field rendering options
- Component defaults
- Template preferences
- Serialization behavior (strict mode, depth limits)
"""

import logging
from typing import Any, ClassVar, Dict

logger = logging.getLogger(__name__)


class LiveViewConfig:
    """
    Central configuration for djust framework behavior.

    Usage:
        # In settings.py
        LIVEVIEW_CONFIG = {
            'css_framework': 'bootstrap5',
            'field_class': 'form-control',
            'error_class': 'invalid-feedback',
        }

        # Or programmatically
        from djust.config import config
        config.set('css_framework', 'tailwind')
    """

    # Default configuration
    _defaults: ClassVar[Dict[str, Any]] = {
        # Rate limiting for WebSocket events (token bucket)
        "rate_limit": {
            "rate": 100,
            "burst": 20,
            "max_warnings": 3,
            "max_connections_per_ip": 10,
            "reconnect_cooldown": 5,
            # Dedicated higher-ceiling bucket for binary upload frames (#F17).
            # Legitimate uploads are high-volume (a 10 MB file is ~157 64 KB
            # chunks); this is sized to let a full single-file upload land as a
            # burst, while a sustained flood still depletes the bucket and trips
            # the abuse-disconnect.
            "upload_rate": 200,
            "upload_burst": 400,
        },
        # Maximum incoming WebSocket message size in bytes (0 = no limit)
        "max_message_size": 65536,  # 64KB
        # Event security mode: "open", "warn", or "strict"
        # "open"   - no decorator check (legacy behavior)
        # "warn"   - allow unmarked methods but log deprecation warning
        # "strict" - only @event_handler decorated methods
        "event_security": "strict",
        # LiveView transport mode
        "use_websocket": True,  # Set to False to use HTTP polling instead of WebSocket
        # WebSocket per-message compression advisory flag (v0.6.0).
        # permessage-deflate negotiation actually happens in the ASGI
        # server — Uvicorn/Daphne both support it by default. This flag
        # is the declarative "we want compression" signal that
        # djust_audit / debug-panel introspection surface, and that
        # application code can branch on (e.g. to skip a manual
        # JSON.stringify optimization that only helps without the
        # wire-level compress). Costs ~64 KB of zlib context per open
        # connection — disable on extreme-connection-density
        # deployments (100k+/worker). Override via
        # settings.DJUST_WS_COMPRESSION.
        "websocket_compression": True,
        # Debug settings
        "debug_vdom": False,  # Enable detailed VDOM patching debug logs
        "debug_components": False,  # Enable component lifecycle debug logs
        "debug_panel_max_history": 50,  # Maximum number of events/patches to keep in debug panel history
        "debug_auto_open_on_error": False,  # Auto-open debug panel on first error/warning (DEBUG mode only)
        # Colocated JS hook namespacing (Phoenix 1.1 parity).
        # "lax"    - bare hook name, no prefix (default, compat)
        # "strict" - prefix with <view-module>.<view-qualname> so two views
        #            can both define `Chart` without colliding.
        # Per-tag opt-out: {% colocated_hook "X" global %} always emits bare name.
        # This is also readable from settings.DJUST_CONFIG["hook_namespacing"]
        # via the template tag (kept here for discoverability / system-check tooling).
        "hook_namespacing": "lax",
        # Automatic SPA navigation (#1734, ADR-021 Stage 2). When True,
        # {% djust_client_config %} emits a <meta name="djust-auto-navigate">
        # flag and the client installs ONE delegated click listener that
        # SPA-navigates plain <a href> links whose path resolves in the
        # (auth-filtered, #1758) route map — Turbo-Drive-style, no djust
        # attributes needed. Default OFF: opt in only after reading the
        # opt-out matrix (modifier/middle-click, target/download, external,
        # hash-only, data-no-navigate). Non-LiveView links full-reload as usual.
        "auto_navigate": False,
        # Re-check auth on every WS event (#1777, threat model T3, defense-in-depth).
        # Auth normally runs only at mount; with this OFF (default) an
        # authenticated user who logs out / loses a permission mid-session keeps
        # dispatching events on the open socket until they reconnect (the
        # connect-time scope user is cached). When True, handle_event re-resolves
        # the user from the session (channels.auth.get_user) and re-runs the
        # view's login_required/permission_required check, closing 4403 on
        # failure. Default OFF: it costs one session read per event — opt in for
        # high-security apps that want mid-session deauth enforced on the live path.
        "reauth_on_event": False,
        # Hot Reload (Development)
        "hot_reload": True,  # Enable hot reload in development (requires DEBUG=True)
        "hot_reload_watch_dirs": None,  # Directories to watch (None = auto-detect BASE_DIR)
        "hot_reload_exclude_dirs": None,  # Directories to exclude (None = use defaults)
        # Auto-call enable_hot_reload() from DjustConfig.ready() in DEBUG.
        # Set to False if you orchestrate the file watcher externally (e.g.
        # watchfiles wrapping uvicorn) and want full manual control.
        "hot_reload_auto_enable": True,
        # Hot View Replacement (v0.6.1) — state-preserving Python code
        # reload in dev. Gated on DEBUG=True AND hot_reload=True.
        "hvr_enabled": True,
        # Time-travel debugging (v0.6.1) — dev-only. Master switch for
        # the global default (views still opt in via
        # ``LiveView.time_travel_enabled = True``). ``time_travel_max_events``
        # caps per-view ring buffer size (memory-bound).
        "time_travel_enabled": False,
        "time_travel_max_events": 100,
        # JIT Serialization (Phase 5)
        "jit_serialization": True,  # Enable/disable JIT auto-serialization
        "jit_debug": False,  # Debug logging for JIT serialization
        "jit_cache_backend": "filesystem",  # 'filesystem' or 'redis'
        "jit_cache_dir": "__pycache__/djust_serializers",  # Filesystem cache directory
        "jit_redis_url": "redis://localhost:6379/0",  # Redis URL for production
        "serialization_max_depth": 3,  # Max depth for nested model serialization (e.g., lease.tenant.user = 3 levels)
        # Serialization behavior (issue #292)
        # When False (default): non-serializable values are converted via str() fallback with a warning log
        # When True: non-serializable values raise TypeError with actionable error message
        # Always emits warning logs before fallback, even in non-strict mode
        "strict_serialization": False,  # Raise TypeError for non-serializable values instead of str() fallback
        # Per-item loop render cache (#1967). When True, the Rust renderer
        # caches each loop item's rendered fragment by a content hash and reuses
        # it across render_with_diff() calls, turning a pure reorder of a large
        # keyed list from O(n) re-renders into O(changed). Default OFF
        # (split-foundation #1122) — a hot-path change that must soak. Only
        # applies to position-INDEPENDENT loop bodies; bodies using
        # {% if %}/{% cycle %}/nested loops/forloop are auto-excluded (correct
        # by construction). Safe to enable for list/table-heavy views.
        "loop_render_cache_enabled": False,
        # Django-parity template auto-call (ADR-024). When True (default),
        # the Rust engine's sidecar getattr walk invokes callables exactly
        # like Django's Variable._resolve_lookup ({{ user.get_full_name }},
        # {{ workspace.memberships.count }}), honoring
        # do_not_call_in_templates and refusing alters_data. False restores
        # the pre-ADR plain-getattr behavior — a kill-switch only, not a
        # feature toggle (candidate for removal at 2.0).
        "template_auto_call": True,
        # #1987: TYPE-based serialization floor (defense-in-depth over the
        # name/method floor). A list of Django field CLASS names (matched
        # anywhere in a field's MRO) to always exclude from client-bound
        # serialization — e.g. ["BinaryField", "MyEncryptedField"]. BinaryField
        # and best-effort encrypted-field types are excluded unconditionally;
        # this adds project-specific types. FileField/ImageField are never
        # excluded (they serialize a URL).
        "sensitive_field_types": [],
        # CSS Framework
        "css_framework": "bootstrap5",  # Options: 'bootstrap4', 'bootstrap5', 'tailwind', None
        # Bootstrap 4 classes (NYC Core Framework, gov sites, legacy projects)
        "bootstrap4": {
            "field_class": "form-control",
            "field_class_invalid": "form-control is-invalid",
            "select_class": "custom-select",
            "error_class": "invalid-feedback",
            "error_class_block": "invalid-feedback d-block",
            "help_text_class": "form-text text-muted",
            "label_class": "",
            "checkbox_class": "custom-control-input",
            "checkbox_label_class": "custom-control-label",
            "checkbox_wrapper_class": "custom-control custom-checkbox",
            "radio_class": "custom-control-input",
            "radio_label_class": "custom-control-label",
            "radio_wrapper_class": "custom-control custom-radio",
            "field_wrapper_class": "form-group",
            "button_primary_class": "btn btn-primary",
            "button_secondary_class": "btn btn-secondary",
        },
        # Bootstrap 5 classes
        "bootstrap5": {
            "field_class": "form-control",
            "field_class_invalid": "form-control is-invalid",
            "select_class": "form-select",
            "error_class": "invalid-feedback",
            "error_class_block": "invalid-feedback d-block",
            "help_text_class": "form-text",
            "label_class": "form-label",
            "checkbox_class": "form-check-input",
            "checkbox_label_class": "form-check-label",
            "checkbox_wrapper_class": "form-check",
            "radio_class": "form-check-input",
            "radio_label_class": "form-check-label",
            "radio_wrapper_class": "form-check",
            "field_wrapper_class": "mb-3",
            "button_primary_class": "btn btn-primary",
            "button_secondary_class": "btn btn-secondary",
        },
        # Tailwind CSS classes
        "tailwind": {
            "field_class": "block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm",
            "field_class_invalid": "block w-full rounded-md border-red-300 pr-10 text-red-900 placeholder-red-300 focus:border-red-500 focus:outline-none focus:ring-red-500 sm:text-sm",
            "error_class": "mt-2 text-sm text-red-600",
            "error_class_block": "mt-2 text-sm text-red-600",
            "label_class": "block text-sm font-medium text-gray-700",
            "checkbox_class": "h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500",
            "checkbox_label_class": "ml-2 block text-sm text-gray-900",
            "checkbox_wrapper_class": "flex items-center",
            "field_wrapper_class": "mb-4",
            "button_primary_class": "inline-flex justify-center rounded-md border border-transparent bg-indigo-600 py-2 px-4 text-sm font-medium text-white shadow-sm hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2",
            "button_secondary_class": "inline-flex justify-center rounded-md border border-gray-300 bg-white py-2 px-4 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2",
        },
        # Plain HTML (no framework)
        "plain": {
            "field_class": "",
            "field_class_invalid": "error",
            "error_class": "error-message",
            "error_class_block": "error-message",
            "label_class": "",
            "checkbox_class": "",
            "checkbox_label_class": "",
            "checkbox_wrapper_class": "",
            "field_wrapper_class": "",
            "button_primary_class": "button primary",
            "button_secondary_class": "button secondary",
        },
        # Field rendering options
        "render_labels": True,
        "render_help_text": True,
        "render_errors": True,
        "auto_validate_on_change": True,
        # Component defaults
        "component_wrapper_class": "",
        "component_loading_class": "loading",
        # Service worker (v0.5.0 P3) — opt-in instant shell + reconnection bridge.
        # These values are informational for tooling / system checks; the
        # actual runtime knobs live in the client-side registration call
        # (``djust.registerServiceWorker({...})``) and in the SW itself.
        "service_worker": {
            "main_selector": "main",  # element whose innerHTML is the "main" swap target
            "shell_cache_name": "djust-shell-v1",  # Cache API bucket key for the cached shell
            "reconnect_buffer_cap": 50,  # max buffered WS messages per connection id
            # VDOM patch cache (v0.6.0) — per-URL HTML snapshots served
            # instantly on popstate, then reconciled against the live
            # WebSocket mount reply. Top-level setting aliases are
            # ``DJUST_VDOM_CACHE_ENABLED`` / ``DJUST_VDOM_CACHE_TTL_SECONDS``
            # / ``DJUST_VDOM_CACHE_MAX_ENTRIES``.
            "vdom_cache_enabled": True,
            "vdom_cache_ttl_seconds": 1800,  # 30 minutes
            "vdom_cache_max_entries": 50,
            # State snapshot (v0.6.0) — opt-in per-view via
            # ``LiveView.enable_state_snapshot = True``. Master switch
            # also toggled via ``DJUST_STATE_SNAPSHOT_ENABLED``.
            "state_snapshot_enabled": True,
        },
        # @loading attribute configuration (Phase 5)
        "loading_grouping_classes": [
            "d-flex",  # Bootstrap flex container
            "btn-group",  # Bootstrap button group
            "input-group",  # Bootstrap input group
            "form-group",  # Bootstrap form group
            "btn-toolbar",  # Bootstrap button toolbar
        ],
    }

    def __init__(self) -> None:
        self._config: Dict[str, Any] = self._defaults.copy()
        self._load_from_settings()

    def _load_from_settings(self) -> None:
        """Load configuration from Django settings if available"""
        try:
            from django.conf import settings

            live_cfg = getattr(settings, "LIVEVIEW_CONFIG", None) or {}
            if live_cfg:
                self._config.update(live_cfg)
            # #1993: also honor LiveView runtime keys set in the similarly-named
            # ``DJUST_CONFIG`` dict as a fallback. The two dicts are defined in
            # the same module and easy to confuse — ``DJUST_CONFIG`` already
            # backs tenancy/presence/state-backend/suppress_checks — so a
            # ``max_message_size`` / ``rate_limit`` / ``event_security`` set
            # there was a SILENT no-op (this method only read ``LIVEVIEW_CONFIG``).
            # Adopt only keys that are genuine LiveView config keys (present in
            # the defaults) so unrelated tenancy/presence keys aren't pulled in;
            # ``LIVEVIEW_CONFIG`` WINS on a collision (it is the documented home),
            # and each adopted key logs a debug breadcrumb naming where it came
            # from, surfacing the ambiguity rather than resolving it silently.
            djust_cfg = getattr(settings, "DJUST_CONFIG", None)
            if isinstance(djust_cfg, dict):
                for key, value in djust_cfg.items():
                    if key in self._defaults and key not in live_cfg:
                        self._config[key] = value
                        logger.debug(
                            "djust: applied LiveView config key %r from "
                            "DJUST_CONFIG (its documented home is LIVEVIEW_CONFIG)",
                            key,
                        )
            # Top-level flat ``DJUST_*`` settings — convenience aliases for a few
            # specific nested keys, for discoverability of operator-facing
            # toggles. (The nested ``DJUST_CONFIG`` *dict* is handled just above,
            # #1993 — these are the separate flat scalars.)
            if hasattr(settings, "DJUST_WS_COMPRESSION"):
                self._config["websocket_compression"] = bool(settings.DJUST_WS_COMPRESSION)
            # Service-worker advanced features (v0.6.0) — top-level aliases
            # for the ``service_worker`` nested dict. Modifying the nested
            # dict directly also works; these aliases exist for operator
            # discoverability and to match the
            # ``DJUST_{VDOM_CACHE,STATE_SNAPSHOT}_*`` naming seen in the
            # v0.6.0 release notes.
            sw_cfg = self._config.setdefault("service_worker", {})
            if hasattr(settings, "DJUST_VDOM_CACHE_ENABLED"):
                sw_cfg["vdom_cache_enabled"] = bool(settings.DJUST_VDOM_CACHE_ENABLED)
            if hasattr(settings, "DJUST_VDOM_CACHE_TTL_SECONDS"):
                sw_cfg["vdom_cache_ttl_seconds"] = int(settings.DJUST_VDOM_CACHE_TTL_SECONDS)
            if hasattr(settings, "DJUST_VDOM_CACHE_MAX_ENTRIES"):
                sw_cfg["vdom_cache_max_entries"] = int(settings.DJUST_VDOM_CACHE_MAX_ENTRIES)
            if hasattr(settings, "DJUST_STATE_SNAPSHOT_ENABLED"):
                sw_cfg["state_snapshot_enabled"] = bool(settings.DJUST_STATE_SNAPSHOT_ENABLED)
        except ImportError:
            # Django not installed
            pass
        except Exception:
            # Settings not configured yet (e.g., ImproperlyConfigured during import)
            # We catch Exception here because the ImproperlyConfigured import might
            # itself fail if Django is partially installed
            pass

        # --- Validate config values ---
        self._validate_config()

        # Bridge debug_vdom to Rust VDOM tracing so developers only need one setting
        if self._config.get("debug_vdom", False):
            import os

            os.environ.setdefault("DJUST_VDOM_TRACE", "1")

    def _validate_config(self) -> None:
        """Validate security-critical config values on startup."""
        valid_modes = ("open", "warn", "strict")
        mode = self._config.get("event_security")
        if mode not in valid_modes:
            logger.warning(
                "Invalid event_security mode %r (must be one of %s). Falling back to 'strict'.",
                mode,
                valid_modes,
            )
            self._config["event_security"] = "strict"

        # Validate rate_limit values
        defaults = self._defaults["rate_limit"]
        rl = self._config.get("rate_limit", {})
        if isinstance(rl, dict):
            for key in (
                "rate",
                "burst",
                "max_warnings",
                "max_connections_per_ip",
                "reconnect_cooldown",
                "upload_rate",
                "upload_burst",
            ):
                val = rl.get(key)
                if val is not None and (not isinstance(val, (int, float)) or val <= 0):
                    logger.warning(
                        "Invalid rate_limit.%s=%r (must be > 0). Using default %s.",
                        key,
                        val,
                        defaults[key],
                    )
                    rl[key] = defaults[key]

        # Warn about risky non-DEBUG settings
        try:
            from django.conf import settings as django_settings

            debug = getattr(django_settings, "DEBUG", True)
        except Exception:
            debug = True

        if not debug:
            if self._config.get("max_message_size") == 0:
                logger.warning(
                    "max_message_size is 0 (no limit) with DEBUG=False. "
                    "Consider setting a message size limit in production."
                )
            if self._config.get("event_security") == "open":
                logger.warning(
                    "event_security is 'open' with DEBUG=False. "
                    "Consider using 'warn' or 'strict' in production."
                )

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value.

        Args:
            key: Configuration key (supports dot notation for nested values)
            default: Default value if key not found

        Returns:
            Configuration value or default

        Example:
            config.get('css_framework')  # 'bootstrap5'
            config.get('bootstrap5.field_class')  # 'form-control'
        """
        # Support dot notation for nested keys
        keys = key.split(".")
        value: Any = self._config

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default

        return value if value is not None else default

    def set(self, key: str, value: Any) -> None:
        """
        Set a configuration value.

        Args:
            key: Configuration key (supports dot notation for nested values)
            value: Value to set

        Example:
            config.set('css_framework', 'tailwind')
            config.set('bootstrap5.field_class', 'custom-control')
        """
        keys = key.split(".")

        # Navigate to the nested dict
        target = self._config
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]

        # Set the value
        target[keys[-1]] = value

    def get_framework_class(self, class_type: str) -> str:
        """
        Get a CSS class for the current framework.

        Args:
            class_type: Type of class (e.g., 'field_class', 'error_class')

        Returns:
            CSS class string for the current framework

        Example:
            config.get_framework_class('field_class')  # 'form-control' (Bootstrap)
        """
        framework = self.get("css_framework", "bootstrap5")

        # Handle None or missing framework
        if framework is None:
            framework = "plain"

        result = self.get(f"{framework}.{class_type}", "")
        return str(result) if result is not None else ""

    def reset(self) -> None:
        """Reset configuration to defaults"""
        self._config = self._defaults.copy()
        self._load_from_settings()

    def update(self, config_dict: Dict[str, Any]) -> None:
        """
        Update multiple configuration values at once.

        Args:
            config_dict: Dictionary of configuration values

        Example:
            config.update({
                'css_framework': 'tailwind',
                'render_labels': False,
            })
        """
        self._config.update(config_dict)

    def as_dict(self) -> Dict[str, Any]:
        """Get the entire configuration as a dictionary"""
        return self._config.copy()


# Global configuration instance
config = LiveViewConfig()


def get_config() -> LiveViewConfig:
    """Get the global configuration instance"""
    return config


def get_djust_config() -> Dict[str, Any]:
    """
    Get the DJUST_CONFIG dictionary from Django settings.

    This provides centralized access to the ``DJUST_CONFIG`` dict used by
    state backends, presence backends, tenants, PWA, and other subsystems.
    Falls back to an empty dict when Django settings are unavailable.

    Returns:
        Dictionary of DJUST_CONFIG settings
    """
    try:
        from django.conf import settings

        result: Dict[str, Any] = getattr(settings, "DJUST_CONFIG", {})
        return result
    except Exception:
        return {}
