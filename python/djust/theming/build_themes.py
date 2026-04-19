"""
Build-time theme generation for djust-theming.

Generates static CSS files for all theme combinations at build time,
eliminating runtime CSS generation overhead for production deployments.
"""

import logging
import os
import json
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

from .design_system_css import generate_design_system_css
from .presets import THEME_PRESETS
from .theme_packs import get_all_design_systems


class BuildTimeGenerator:
    """Generate static theme files at build time."""

    def __init__(
        self,
        output_dir: str = "static/themes",
        minify: bool = True,
        include_source_maps: bool = False,
        generate_manifest: bool = True,
    ):
        """
        Initialize build-time generator.

        Args:
            output_dir: Directory to write generated CSS files
            minify: Whether to minify generated CSS
            include_source_maps: Generate CSS source maps for debugging
            generate_manifest: Create a manifest.json with theme metadata
        """
        self.output_dir = Path(output_dir)
        self.minify = minify
        self.include_source_maps = include_source_maps
        self.generate_manifest = generate_manifest

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _minify_css(self, css: str) -> str:
        """Basic CSS minification."""
        if not self.minify:
            return css

        # Remove comments
        lines = []
        in_comment = False
        i = 0
        while i < len(css):
            if not in_comment and i < len(css) - 1 and css[i : i + 2] == "/*":
                in_comment = True
                i += 2
            elif in_comment and i < len(css) - 1 and css[i : i + 2] == "*/":
                in_comment = False
                i += 2
            elif not in_comment:
                lines.append(css[i])
                i += 1
            else:
                i += 1

        css = "".join(lines)

        # Remove extra whitespace
        css = " ".join(css.split())

        # Remove spaces around certain characters
        replacements = [
            (" {", "{"),
            ("{ ", "{"),
            (" }", "}"),
            ("} ", "}"),
            (" :", ":"),
            (": ", ":"),
            (" ;", ";"),
            ("; ", ";"),
            (", ", ","),
            (" ,", ","),
        ]

        for old, new in replacements:
            css = css.replace(old, new)

        return css

    def _generate_source_map(self, css: str, filename: str) -> str:
        """Generate a basic source map for debugging."""
        if not self.include_source_maps:
            return ""

        # Very basic source map - just maps to original line numbers
        source_map = {
            "version": 3,
            "file": filename,
            "sources": [f"{filename}.source"],
            "names": [],
            "mappings": "",
            "sourcesContent": [css],
        }

        return json.dumps(source_map)

    def generate_individual_themes(self) -> List[Tuple[str, str]]:
        """
        Generate individual CSS files for each theme combination.

        Returns:
            List of (filename, file_path) tuples for generated files
        """
        generated_files = []
        design_systems = get_all_design_systems()

        logger.info("Generating %d theme combinations...", len(design_systems) * len(THEME_PRESETS))

        for design_name in design_systems.keys():
            for preset_name in THEME_PRESETS.keys():
                # Generate CSS for this combination
                css = generate_design_system_css(
                    design_system_name=design_name,
                    color_preset_name=preset_name,
                    include_base_styles=True,
                    include_utilities=True,
                )

                # Minify if requested
                if self.minify:
                    css = self._minify_css(css)

                # Generate filename
                filename = f"{design_name}-{preset_name}"
                if self.minify:
                    filename += ".min"
                filename += ".css"

                # Write CSS file
                css_path = self.output_dir / filename
                with open(css_path, "w") as f:
                    f.write(css)

                # Generate source map if requested
                if self.include_source_maps:
                    source_map_content = self._generate_source_map(css, filename)
                    source_map_path = self.output_dir / f"{filename}.map"
                    with open(source_map_path, "w") as f:
                        f.write(source_map_content)

                    # Add source map reference to CSS
                    with open(css_path, "a") as f:
                        f.write(f"\n/*# sourceMappingURL={filename}.map */")

                generated_files.append((filename, str(css_path)))
                logger.info("  Generated %s", filename)

        return generated_files

    def generate_combined_bundle(self) -> str:
        """
        Generate a single CSS bundle with all themes using CSS layers.

        Returns:
            Path to the generated bundle file
        """
        logger.info("Generating combined theme bundle...")

        design_systems = get_all_design_systems()
        css_parts = []

        # CSS Layers declaration
        layer_names = []
        for design_name in design_systems.keys():
            for preset_name in THEME_PRESETS.keys():
                layer_names.append(f"theme-{design_name}-{preset_name}")

        css_parts.append(f"@layer {', '.join(layer_names)};")
        css_parts.append("")

        # Generate each theme in its own layer
        for design_name in design_systems.keys():
            for preset_name in THEME_PRESETS.keys():
                layer_name = f"theme-{design_name}-{preset_name}"

                css = generate_design_system_css(
                    design_system_name=design_name,
                    color_preset_name=preset_name,
                    include_base_styles=False,  # Only include base styles once
                    include_utilities=False,  # Only include utilities once
                )

                # Wrap in layer and data attribute selector
                layer_css = f"""
@layer {layer_name} {{
  [data-theme="{design_name}-{preset_name}"] {{
{css}
  }}
}}
"""
                css_parts.append(layer_css)

        # Add base styles and utilities once at the end
        base_css = generate_design_system_css(
            design_system_name="minimal",
            color_preset_name="default",
            include_base_styles=True,
            include_utilities=True,
        )

        # Extract just the base styles and utilities (not the theme variables)
        base_lines = base_css.split("\n")
        in_base_section = False
        base_parts = []

        for line in base_lines:
            if "/* Design System Styles */" in line:
                in_base_section = True
            if in_base_section:
                base_parts.append(line)

        if base_parts:
            css_parts.append("")
            css_parts.append("/* Base Styles and Utilities */")
            css_parts.extend(base_parts)

        # Combine all CSS
        combined_css = "\n".join(css_parts)

        # Minify if requested
        if self.minify:
            combined_css = self._minify_css(combined_css)

        # Write bundle file
        bundle_filename = "djust-theming-bundle"
        if self.minify:
            bundle_filename += ".min"
        bundle_filename += ".css"

        bundle_path = self.output_dir / bundle_filename
        with open(bundle_path, "w") as f:
            f.write(combined_css)

        logger.info("  Generated combined bundle: %s", bundle_filename)
        return str(bundle_path)

    def generate_manifest(self, generated_files: List[Tuple[str, str]]) -> str:
        """
        Generate a manifest.json with metadata about generated themes.

        Args:
            generated_files: List of (filename, filepath) tuples

        Returns:
            Path to generated manifest file
        """
        if not self.generate_manifest:
            return ""

        logger.info("Generating theme manifest...")

        design_systems = get_all_design_systems()

        # Build manifest data
        manifest = {
            "generator": "djust-theming",
            "version": "1.0.0",
            "generated_at": datetime.now().isoformat(),
            "build_options": {"minify": self.minify, "source_maps": self.include_source_maps},
            "design_systems": {
                name: {
                    "display_name": system.display_name,
                    "description": system.description,
                    "category": system.category,
                }
                for name, system in design_systems.items()
            },
            "color_presets": {
                name: {
                    "display_name": preset.display_name,
                    "description": getattr(
                        preset, "description", f"{preset.display_name} color preset"
                    ),
                }
                for name, preset in THEME_PRESETS.items()
            },
            "themes": {},
            "files": {},
        }

        # Add individual theme metadata
        for design_name in design_systems.keys():
            for preset_name in THEME_PRESETS.keys():
                theme_key = f"{design_name}-{preset_name}"
                manifest["themes"][theme_key] = {
                    "design_system": design_name,
                    "color_preset": preset_name,
                    "display_name": f"{design_systems[design_name].display_name} + {THEME_PRESETS[preset_name].display_name}",
                    "css_selector": f'[data-theme="{theme_key}"]',
                }

        # Add file metadata
        for filename, filepath in generated_files:
            file_stats = os.stat(filepath)
            manifest["files"][filename] = {
                "path": filepath,
                "size": file_stats.st_size,
                "modified": datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
            }

        # Write manifest
        manifest_path = self.output_dir / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        logger.info("  Generated manifest: manifest.json")
        return str(manifest_path)

    def generate_theme_switcher_js(self) -> str:
        """Generate JavaScript utility for switching themes at runtime."""
        js_content = """
/**
 * djust-theming Runtime Theme Switcher
 *
 * Provides utilities for switching between pre-built themes.
 */

class DjustThemeSwitcher {
    constructor() {
        this.currentTheme = this.detectCurrentTheme();
        this.callbacks = [];
    }

    /**
     * Detect the currently active theme from DOM
     */
    detectCurrentTheme() {
        const root = document.documentElement;
        const themeAttr = root.getAttribute('data-theme');
        if (themeAttr) return themeAttr;

        // Fallback: check for theme classes
        for (const className of root.classList) {
            if (className.startsWith('theme-')) {
                return className.replace('theme-', '');
            }
        }

        return 'minimal-default'; // Default fallback
    }

    /**
     * Switch to a specific theme combination
     */
    setTheme(designSystem, colorPreset) {
        const themeName = `${designSystem}-${colorPreset}`;
        return this.setThemeByName(themeName);
    }

    /**
     * Switch to a theme by full name (e.g., "brutalist-ocean")
     */
    setThemeByName(themeName) {
        const root = document.documentElement;

        // Remove existing theme attributes/classes
        root.removeAttribute('data-theme');
        for (const className of [...root.classList]) {
            if (className.startsWith('theme-')) {
                root.classList.remove(className);
            }
        }

        // Apply new theme
        root.setAttribute('data-theme', themeName);
        root.classList.add(`theme-${themeName}`);

        // Update state
        const oldTheme = this.currentTheme;
        this.currentTheme = themeName;

        // Trigger callbacks
        this.callbacks.forEach(callback => {
            try {
                callback(themeName, oldTheme);
            } catch (error) {
                console.error('Theme change callback error:', error);
            }
        });

        // Save preference
        try {
            localStorage.setItem('djust-theme', themeName);
        } catch (error) {
            // localStorage might be disabled
        }

        return themeName;
    }

    /**
     * Get the current active theme
     */
    getCurrentTheme() {
        return this.currentTheme;
    }

    /**
     * Register a callback for theme changes
     */
    onChange(callback) {
        this.callbacks.push(callback);
        return () => {
            const index = this.callbacks.indexOf(callback);
            if (index > -1) {
                this.callbacks.splice(index, 1);
            }
        };
    }

    /**
     * Load theme from localStorage
     */
    loadSavedTheme() {
        try {
            const saved = localStorage.getItem('djust-theme');
            if (saved) {
                this.setThemeByName(saved);
            }
        } catch (error) {
            // localStorage might be disabled
        }
    }

    /**
     * Get available themes from manifest
     */
    async getAvailableThemes() {
        try {
            const response = await fetch('/static/themes/manifest.json');
            const manifest = await response.json();
            return manifest.themes;
        } catch (error) {
            console.error('Failed to load theme manifest:', error);
            return {};
        }
    }

    /**
     * Preload a theme's CSS file
     */
    preloadTheme(themeName) {
        return new Promise((resolve, reject) => {
            const link = document.createElement('link');
            link.rel = 'preload';
            link.as = 'style';
            link.href = `/static/themes/${themeName}.min.css`;
            link.onload = resolve;
            link.onerror = reject;
            document.head.appendChild(link);
        });
    }
}

// Global instance
window.djustTheme = new DjustThemeSwitcher();

// Auto-load saved theme on DOMContentLoaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.djustTheme.loadSavedTheme();
    });
} else {
    window.djustTheme.loadSavedTheme();
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DjustThemeSwitcher;
}
"""

        js_filename = "djust-theme-switcher"
        if self.minify:
            js_filename += ".min"
        js_filename += ".js"

        js_path = self.output_dir / js_filename
        with open(js_path, "w") as f:
            f.write(js_content)

        logger.info("  Generated theme switcher: %s", js_filename)
        return str(js_path)

    def build_all(self) -> Dict[str, str]:
        """
        Generate all build artifacts.

        Returns:
            Dictionary of artifact types to file paths
        """
        logger.info("Starting djust-theming build process...")
        logger.info("Output directory: %s", self.output_dir)

        artifacts = {}

        # Generate individual theme files
        generated_files = self.generate_individual_themes()
        artifacts["individual_themes"] = [path for _, path in generated_files]

        # Generate combined bundle
        bundle_path = self.generate_combined_bundle()
        artifacts["bundle"] = bundle_path

        # Generate manifest
        manifest_path = self.generate_manifest(generated_files)
        if manifest_path:
            artifacts["manifest"] = manifest_path

        # Generate theme switcher JavaScript
        js_path = self.generate_theme_switcher_js()
        artifacts["theme_switcher_js"] = js_path

        logger.info(
            "Build complete! Generated %d themes + bundle + utilities", len(generated_files)
        )
        return artifacts


def build_themes(
    output_dir: str = "static/themes",
    minify: bool = True,
    source_maps: bool = False,
    manifest: bool = True,
) -> Dict[str, str]:
    """
    Convenience function to build all themes.

    Args:
        output_dir: Output directory for generated files
        minify: Whether to minify CSS
        source_maps: Generate source maps for debugging
        manifest: Generate manifest.json

    Returns:
        Dictionary of generated artifacts
    """
    generator = BuildTimeGenerator(
        output_dir=output_dir,
        minify=minify,
        include_source_maps=source_maps,
        generate_manifest=manifest,
    )

    return generator.build_all()


if __name__ == "__main__":
    # CLI usage
    import sys

    output_dir = sys.argv[1] if len(sys.argv) > 1 else "static/themes"
    minify = "--no-minify" not in sys.argv
    source_maps = "--source-maps" in sys.argv
    manifest = "--no-manifest" not in sys.argv

    build_themes(output_dir=output_dir, minify=minify, source_maps=source_maps, manifest=manifest)
