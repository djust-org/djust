"""
Theme manifest parsing, validation, and serialization.

Handles theme.toml files that define user-created themes with references
to color presets and design systems.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# tomllib is stdlib in 3.11+; fall back to tomli for 3.10
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        raise ImportError(
            "Python 3.10 requires the 'tomli' package for TOML parsing. "
            "Install it with: pip install tomli"
        )

# Regex: only lowercase ASCII letters, digits, and hyphens
_VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Semantic versioning regex (e.g., 1.0.0, 1.2.3-beta.1, 1.0.0+build.42)
_VALID_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]+)?(\+[a-zA-Z0-9.-]+)?$")


@dataclass
class ThemeManifest:
    """Parsed and validated theme.toml manifest."""

    # [theme] section
    name: str
    version: str
    description: str = ""
    author: str = ""
    license: str = ""
    direction: Optional[str] = None  # "ltr", "rtl", or None (inherit from config)

    # [extends] section
    base: Optional[str] = None

    # [tokens] section
    preset: str = "default"
    design_system: str = "material"
    overrides: dict[str, str] = field(default_factory=dict)

    # [static] section
    css: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)

    # [marketplace] section (optional)
    screenshots: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    compatibility_range: str = ""
    preview_url: str = ""

    # Internal (not from TOML)
    path: Optional[Path] = None
    templates_dir: Optional[Path] = None

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @classmethod
    def from_toml(cls, toml_path: Path) -> ThemeManifest:
        """Parse and validate a theme.toml file.

        Args:
            toml_path: Path to the theme.toml file.

        Returns:
            A populated ThemeManifest instance.

        Raises:
            FileNotFoundError: If toml_path does not exist.
            ValueError: If the TOML is invalid or required fields are missing.
        """
        toml_path = Path(toml_path)
        if not toml_path.is_file():
            raise FileNotFoundError(f"Theme manifest not found: {toml_path}")

        try:
            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
        except Exception as exc:
            raise ValueError(f"Invalid TOML in {toml_path}: {exc}") from exc

        # --- [theme] section (required) ---
        theme_section = data.get("theme")
        if not theme_section or not isinstance(theme_section, dict):
            raise ValueError(f"Missing required [theme] section in {toml_path}")

        name = theme_section.get("name")
        if not name:
            raise ValueError(f"Missing required 'name' field in [theme] section of {toml_path}")

        version = theme_section.get("version")
        if not version:
            raise ValueError(f"Missing required 'version' field in [theme] section of {toml_path}")

        # --- [extends] section (optional) ---
        extends_section = data.get("extends", {})
        base = extends_section.get("base")

        # --- [tokens] section (optional) ---
        tokens_section = data.get("tokens", {})
        preset = tokens_section.get("preset", "default")
        design_system = tokens_section.get("design_system", "material")
        overrides = tokens_section.get("overrides", {})

        # --- [static] section (optional) ---
        static_section = data.get("static", {})
        css_files = static_section.get("css", [])
        font_files = static_section.get("fonts", [])

        # Direction (optional)
        direction = theme_section.get("direction")

        # --- [marketplace] section (optional) ---
        marketplace_section = data.get("marketplace", {})
        screenshots = list(marketplace_section.get("screenshots", []))
        tags = list(marketplace_section.get("tags", []))
        compatibility_range = marketplace_section.get("compatibility_range", "")
        preview_url = marketplace_section.get("preview_url", "")

        return cls(
            name=name,
            version=version,
            description=theme_section.get("description", ""),
            author=theme_section.get("author", ""),
            license=theme_section.get("license", ""),
            direction=direction,
            base=base,
            preset=preset,
            design_system=design_system,
            overrides=dict(overrides),
            css=list(css_files),
            fonts=list(font_files),
            screenshots=screenshots,
            tags=tags,
            compatibility_range=compatibility_range,
            preview_url=preview_url,
            path=toml_path.parent,
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of validation error messages. Empty list means valid."""
        from .registry import get_registry

        registry = get_registry()
        errors: list[str] = []

        # Name validation: only [a-z0-9-], must start with alphanumeric
        if not self.name or not _VALID_NAME_RE.match(self.name):
            errors.append(
                f"Invalid theme name '{self.name}': must contain only "
                f"lowercase letters, digits, and hyphens (pattern: [a-z0-9-])."
            )

        # Version validation
        if self.version and not _VALID_VERSION_RE.match(self.version):
            errors.append(
                f"Invalid version '{self.version}': must follow semantic versioning "
                f"(e.g., 1.0.0, 1.2.3-beta.1)."
            )

        # Preset validation
        if not registry.has_preset(self.preset):
            valid = ", ".join(sorted(registry.list_presets().keys()))
            errors.append(f"Unknown preset '{self.preset}'. Valid presets: {valid}")

        # Design system validation
        if not registry.has_theme(self.design_system):
            valid = ", ".join(sorted(registry.list_themes().keys()))
            errors.append(
                f"Unknown design_system '{self.design_system}'. Valid design systems: {valid}"
            )

        return errors

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _escape_toml_string(value: str) -> str:
        """Escape a string for use in a TOML double-quoted value."""
        return (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )

    def to_toml(self) -> str:
        """Serialize this manifest to a TOML string."""
        esc = self._escape_toml_string
        lines: list[str] = []

        # [theme]
        lines.append("[theme]")
        lines.append(f'name = "{esc(self.name)}"')
        lines.append(f'version = "{esc(self.version)}"')
        if self.description:
            lines.append(f'description = "{esc(self.description)}"')
        if self.author:
            lines.append(f'author = "{esc(self.author)}"')
        if self.license:
            lines.append(f'license = "{esc(self.license)}"')
        if self.direction:
            lines.append(f'direction = "{esc(self.direction)}"')

        # [extends]
        if self.base:
            lines.append("")
            lines.append("[extends]")
            lines.append(f'base = "{esc(self.base)}"')

        # [tokens] — always include so the manifest is self-documenting
        lines.append("")
        lines.append("[tokens]")
        lines.append(f'preset = "{esc(self.preset)}"')
        lines.append(f'design_system = "{esc(self.design_system)}"')

        if self.overrides:
            lines.append("")
            lines.append("[tokens.overrides]")
            for key, value in self.overrides.items():
                lines.append(f'{key} = "{esc(value)}"')

        # [static]
        if self.css or self.fonts:
            lines.append("")
            lines.append("[static]")
            if self.css:
                items = ", ".join(f'"{esc(f)}"' for f in self.css)
                lines.append(f"css = [{items}]")
            if self.fonts:
                items = ", ".join(f'"{esc(f)}"' for f in self.fonts)
                lines.append(f"fonts = [{items}]")

        # [marketplace]
        has_marketplace = (
            self.screenshots or self.tags or self.compatibility_range or self.preview_url
        )
        if has_marketplace:
            lines.append("")
            lines.append("[marketplace]")
            if self.screenshots:
                items = ", ".join(f'"{esc(s)}"' for s in self.screenshots)
                lines.append(f"screenshots = [{items}]")
            if self.tags:
                items = ", ".join(f'"{esc(t)}"' for t in self.tags)
                lines.append(f"tags = [{items}]")
            if self.compatibility_range:
                lines.append(f'compatibility_range = "{esc(self.compatibility_range)}"')
            if self.preview_url:
                lines.append(f'preview_url = "{esc(self.preview_url)}"')

        lines.append("")  # trailing newline
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def load_theme_manifests(themes_dir: Path) -> list[ThemeManifest]:
    """Discover and load all theme.toml files under *themes_dir*.

    Each immediate subdirectory of *themes_dir* is checked for a
    ``theme.toml`` file.  Directories without one are silently skipped.

    Args:
        themes_dir: Root directory containing theme subdirectories.

    Returns:
        List of parsed ThemeManifest instances (may be empty).
    """
    themes_dir = Path(themes_dir)
    if not themes_dir.is_dir():
        return []

    manifests: list[ThemeManifest] = []
    for child in sorted(themes_dir.iterdir()):
        if not child.is_dir():
            continue
        toml_path = child / "theme.toml"
        if toml_path.is_file():
            manifests.append(ThemeManifest.from_toml(toml_path))

    return manifests
