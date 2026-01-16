"""
Configuration system for djust

Provides centralized configuration for:
- CSS framework (Bootstrap 5, Tailwind CSS, None)
- Field rendering options
- Component defaults
- Template preferences
"""

from typing import Dict, Any


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
    _defaults = {
        # LiveView transport mode
        "use_websocket": True,  # Set to False to use HTTP polling instead of WebSocket
        # Debug settings
        "debug_vdom": False,  # Enable detailed VDOM patching debug logs
        "debug_components": False,  # Enable component lifecycle debug logs
        "debug_panel_max_history": 50,  # Maximum number of events/patches to keep in debug panel history
        # Hot Reload (Development)
        "hot_reload": True,  # Enable hot reload in development (requires DEBUG=True)
        "hot_reload_watch_dirs": None,  # Directories to watch (None = auto-detect BASE_DIR)
        "hot_reload_exclude_dirs": None,  # Directories to exclude (None = use defaults)
        # JIT Serialization (Phase 5)
        "jit_serialization": True,  # Enable/disable JIT auto-serialization
        "jit_debug": False,  # Debug logging for JIT serialization
        "jit_cache_backend": "filesystem",  # 'filesystem' or 'redis'
        "jit_cache_dir": "__pycache__/djust_serializers",  # Filesystem cache directory
        "jit_redis_url": "redis://localhost:6379/0",  # Redis URL for production
        "serialization_max_depth": 3,  # Max depth for nested model serialization (e.g., lease.tenant.user = 3 levels)
        # CSS Framework
        "css_framework": "bootstrap5",  # Options: 'bootstrap5', 'tailwind', None
        # Bootstrap 5 classes
        "bootstrap5": {
            "field_class": "form-control",
            "field_class_invalid": "form-control is-invalid",
            "error_class": "invalid-feedback",
            "error_class_block": "invalid-feedback d-block",
            "label_class": "form-label",
            "checkbox_class": "form-check-input",
            "checkbox_label_class": "form-check-label",
            "checkbox_wrapper_class": "form-check",
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
        # @loading attribute configuration (Phase 5)
        "loading_grouping_classes": [
            "d-flex",  # Bootstrap flex container
            "btn-group",  # Bootstrap button group
            "input-group",  # Bootstrap input group
            "form-group",  # Bootstrap form group
            "btn-toolbar",  # Bootstrap button toolbar
        ],
    }

    def __init__(self):
        self._config = self._defaults.copy()
        self._load_from_settings()

    def _load_from_settings(self):
        """Load configuration from Django settings if available"""
        try:
            from django.conf import settings

            if hasattr(settings, "LIVEVIEW_CONFIG"):
                self._config.update(settings.LIVEVIEW_CONFIG)
        except ImportError:
            pass

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
        value = self._config

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default

        return value if value is not None else default

    def set(self, key: str, value: Any):
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

        return self.get(f"{framework}.{class_type}", "")

    def reset(self):
        """Reset configuration to defaults"""
        self._config = self._defaults.copy()
        self._load_from_settings()

    def update(self, config_dict: Dict[str, Any]):
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
