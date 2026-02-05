"""
PWA manifest generation for djust applications.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from django.http import JsonResponse, HttpRequest
from django.conf import settings

logger = logging.getLogger(__name__)


class PWAManifestGenerator:
    """
    Generates PWA manifest files for djust applications.

    Provides customizable app metadata, icons, and display options
    based on Django settings and view configuration.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """Get default PWA configuration from Django settings."""
        try:
            djust_config = getattr(settings, "DJUST_CONFIG", {})
            return {
                "name": djust_config.get("PWA_NAME", "djust App"),
                "short_name": djust_config.get("PWA_SHORT_NAME", "djust"),
                "description": djust_config.get(
                    "PWA_DESCRIPTION", "Progressive Web App built with djust"
                ),
                "start_url": djust_config.get("PWA_START_URL", "/"),
                "scope": djust_config.get("PWA_SCOPE", "/"),
                "display": djust_config.get("PWA_DISPLAY", "standalone"),
                "orientation": djust_config.get("PWA_ORIENTATION", "any"),
                "theme_color": djust_config.get("PWA_THEME_COLOR", "#000000"),
                "background_color": djust_config.get("PWA_BACKGROUND_COLOR", "#ffffff"),
                "lang": djust_config.get("PWA_LANG", "en"),
                "dir": djust_config.get("PWA_DIR", "ltr"),
                "icons": djust_config.get("PWA_ICONS", []),
                "categories": djust_config.get("PWA_CATEGORIES", ["productivity"]),
                "shortcuts": djust_config.get("PWA_SHORTCUTS", []),
                "screenshots": djust_config.get("PWA_SCREENSHOTS", []),
            }
        except Exception as e:
            logger.error("Error loading PWA config: %s", e, exc_info=True)
            return self._get_minimal_config()

    def _get_minimal_config(self) -> Dict[str, Any]:
        """Get minimal PWA configuration."""
        return {
            "name": "djust App",
            "short_name": "djust",
            "description": "Progressive Web App built with djust",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "theme_color": "#000000",
            "background_color": "#ffffff",
            "lang": "en",
            "dir": "ltr",
            "icons": [],
            "categories": ["productivity"],
        }

    def generate_manifest(self) -> Dict[str, Any]:
        """
        Generate PWA manifest dictionary.

        Returns:
            PWA manifest as dictionary
        """
        manifest = {
            "name": self.config["name"],
            "short_name": self.config["short_name"],
            "description": self.config["description"],
            "start_url": self.config["start_url"],
            "scope": self.config["scope"],
            "display": self.config["display"],
            "orientation": self.config["orientation"],
            "theme_color": self.config["theme_color"],
            "background_color": self.config["background_color"],
            "lang": self.config["lang"],
            "dir": self.config["dir"],
            "categories": self.config["categories"],
        }

        # Add icons if configured
        if self.config.get("icons"):
            manifest["icons"] = self._process_icons(self.config["icons"])
        else:
            # Generate default icons
            manifest["icons"] = self._generate_default_icons()

        # Add shortcuts if configured
        if self.config.get("shortcuts"):
            manifest["shortcuts"] = self.config["shortcuts"]

        # Add screenshots if configured
        if self.config.get("screenshots"):
            manifest["screenshots"] = self.config["screenshots"]

        # Add optional fields
        if self.config.get("prefer_related_applications"):
            manifest["prefer_related_applications"] = self.config["prefer_related_applications"]

        if self.config.get("related_applications"):
            manifest["related_applications"] = self.config["related_applications"]

        return manifest

    def _process_icons(self, icons: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process and validate icon configurations."""
        processed_icons = []

        for icon in icons:
            if not isinstance(icon, dict):
                continue

            processed_icon = {}

            # Required fields
            if "src" in icon:
                processed_icon["src"] = icon["src"]
            else:
                continue  # Skip icons without src

            if "sizes" in icon:
                processed_icon["sizes"] = icon["sizes"]

            if "type" in icon:
                processed_icon["type"] = icon["type"]

            # Optional fields
            if "purpose" in icon:
                processed_icon["purpose"] = icon["purpose"]

            processed_icons.append(processed_icon)

        return processed_icons

    def _generate_default_icons(self) -> List[Dict[str, Any]]:
        """Generate default icon set if none configured."""
        # This would generate placeholder icons or use defaults
        return [
            {
                "src": "/static/icons/icon-192x192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": "/static/icons/icon-512x512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
        ]

    def generate_json(self) -> str:
        """
        Generate PWA manifest as JSON string.

        Returns:
            JSON string representation of manifest
        """
        manifest = self.generate_manifest()
        return json.dumps(manifest, indent=2)

    def update_config(self, updates: Dict[str, Any]) -> None:
        """
        Update manifest configuration.

        Args:
            updates: Dictionary of config updates
        """
        self.config.update(updates)


def manifest_view(request: HttpRequest) -> JsonResponse:
    """
    Django view that serves PWA manifest.

    Args:
        request: Django HTTP request

    Returns:
        JsonResponse with PWA manifest
    """
    try:
        generator = PWAManifestGenerator()
        manifest = generator.generate_manifest()

        response = JsonResponse(manifest)
        response["Content-Type"] = "application/manifest+json"
        response["Cache-Control"] = "max-age=86400"  # Cache for 24 hours

        return response
    except Exception as e:
        logger.error("Error generating manifest: %s", e, exc_info=True)

        # Return minimal manifest
        minimal_manifest = {
            "name": "djust App",
            "short_name": "djust",
            "start_url": "/",
            "display": "standalone",
            "theme_color": "#000000",
            "background_color": "#ffffff",
            "icons": [],
        }

        response = JsonResponse(minimal_manifest)
        response["Content-Type"] = "application/manifest+json"
        return response


def generate_manifest_link_tag(manifest_url: str = "/manifest.json") -> str:
    """
    Generate HTML link tag for PWA manifest.

    Args:
        manifest_url: URL to manifest file

    Returns:
        HTML link tag string
    """
    return f'<link rel="manifest" href="{manifest_url}">'


def generate_theme_color_meta(theme_color: Optional[str] = None) -> str:
    """
    Generate theme-color meta tag.

    Args:
        theme_color: Theme color (defaults to config value)

    Returns:
        HTML meta tag string
    """
    if not theme_color:
        try:
            djust_config = getattr(settings, "DJUST_CONFIG", {})
            theme_color = djust_config.get("PWA_THEME_COLOR", "#000000")
        except Exception:
            theme_color = "#000000"

    return f'<meta name="theme-color" content="{theme_color}">'


def generate_apple_touch_icon_tags(icons: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """
    Generate Apple touch icon meta tags.

    Args:
        icons: List of icon configurations

    Returns:
        List of HTML meta tag strings
    """
    if not icons:
        # Use default icons
        icons = [{"src": "/static/icons/apple-touch-icon.png", "sizes": "180x180"}]

    tags = []

    for icon in icons:
        if "sizes" in icon:
            tags.append(
                f'<link rel="apple-touch-icon" sizes="{icon["sizes"]}" href="{icon["src"]}">'
            )
        else:
            tags.append(f'<link rel="apple-touch-icon" href="{icon["src"]}">')

    return tags
