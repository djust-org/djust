"""
Django management command for djust-theming Tailwind integration.

Usage:
    python manage.py djust_theme tailwind-config [--preset blue] [--output tailwind.config.js]
    python manage.py djust_theme export-colors [--preset blue] [--format json]
    python manage.py djust_theme list-presets
    python manage.py djust_theme generate-examples
"""

from django.core.management.base import BaseCommand, CommandError
from djust.theming.registry import get_registry
from djust.theming.tailwind import (
    generate_tailwind_config,
    export_preset_as_tailwind_colors,
    generate_tailwind_apply_examples,
)
from djust.theming.shadcn import (
    import_shadcn_theme_from_file,
    export_shadcn_theme_to_file,
)
import json


class Command(BaseCommand):
    help = "djust-theming utilities for Tailwind CSS integration"

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to run")

        # tailwind-config subcommand
        tailwind_parser = subparsers.add_parser(
            "tailwind-config", help="Generate tailwind.config.js with theme CSS variables"
        )
        tailwind_parser.add_argument(
            "--preset", type=str, default="default", help="Preset name to use (default: default)"
        )
        tailwind_parser.add_argument(
            "--output",
            type=str,
            default="tailwind.config.js",
            help="Output file path (default: tailwind.config.js)",
        )
        tailwind_parser.add_argument(
            "--extend",
            action="store_true",
            default=True,
            help="Extend Tailwind default colors (default: true)",
        )
        tailwind_parser.add_argument(
            "--no-extend",
            action="store_false",
            dest="extend",
            help="Replace Tailwind default colors instead of extending",
        )
        tailwind_parser.add_argument(
            "--all-presets",
            action="store_true",
            help="Include all presets as additional color scales",
        )

        # export-colors subcommand
        export_parser = subparsers.add_parser(
            "export-colors", help="Export preset colors in various formats"
        )
        export_parser.add_argument(
            "--preset", type=str, default="default", help="Preset name to export (default: default)"
        )
        export_parser.add_argument(
            "--format",
            type=str,
            choices=["json", "python"],
            default="json",
            help="Output format (default: json)",
        )
        export_parser.add_argument("--output", type=str, help="Output file path (default: stdout)")

        # list-presets subcommand
        subparsers.add_parser("list-presets", help="List all available theme presets")

        # generate-examples subcommand
        examples_parser = subparsers.add_parser(
            "generate-examples", help="Generate CSS examples showing @apply usage"
        )
        examples_parser.add_argument(
            "--output",
            type=str,
            default="theme-examples.css",
            help="Output file path (default: theme-examples.css)",
        )

        # shadcn-import subcommand
        import_parser = subparsers.add_parser(
            "shadcn-import", help="Import a shadcn/ui theme JSON file"
        )
        import_parser.add_argument("input_file", type=str, help="Path to shadcn theme JSON file")
        import_parser.add_argument(
            "--register", action="store_true", help="Register the imported theme in THEME_PRESETS"
        )

        # shadcn-export subcommand
        shadcn_export_parser = subparsers.add_parser(
            "shadcn-export", help="Export a preset to shadcn/ui theme JSON format"
        )
        shadcn_export_parser.add_argument(
            "--preset", type=str, default="default", help="Preset name to export (default: default)"
        )
        shadcn_export_parser.add_argument(
            "--output", type=str, required=True, help="Output JSON file path"
        )

        # init subcommand
        init_parser = subparsers.add_parser("init", help="Initialize djust-theming in your project")
        init_parser.add_argument(
            "--preset", type=str, default="default", help="Initial preset to use (default: default)"
        )
        init_parser.add_argument(
            "--with-tailwind", action="store_true", help="Also generate Tailwind config"
        )
        init_parser.add_argument(
            "--with-examples", action="store_true", help="Generate example templates"
        )

        # create-theme subcommand
        create_parser = subparsers.add_parser(
            "create-theme", help="Scaffold a new user theme directory with theme.toml"
        )
        create_parser.add_argument(
            "theme_name",
            type=str,
            help="Theme directory name (lowercase letters, digits, hyphens only)",
        )
        create_parser.add_argument(
            "--base",
            type=str,
            default=None,
            help="Base theme to extend (another theme directory name)",
        )
        create_parser.add_argument(
            "--preset",
            type=str,
            default="default",
            help="Color preset from THEME_PRESETS (default: default)",
        )
        create_parser.add_argument(
            "--design-system",
            type=str,
            default="material",
            help="Design system from DESIGN_SYSTEMS (default: material)",
        )
        create_parser.add_argument(
            "--dir",
            type=str,
            default=None,
            help="Override themes directory (default: from config or BASE_DIR/themes/)",
        )
        create_parser.add_argument(
            "--force", action="store_true", help="Overwrite existing theme directory"
        )

        # validate-theme subcommand
        validate_parser = subparsers.add_parser(
            "validate-theme", help="Validate a theme manifest and its referenced files"
        )
        validate_parser.add_argument("theme_name", nargs="?", help="Theme name to validate")
        validate_parser.add_argument(
            "--all",
            action="store_true",
            dest="validate_all",
            help="Validate all themes in the themes directory",
        )
        validate_parser.add_argument(
            "--dir", type=str, dest="validate_dir", help="Override themes directory"
        )

        # create-package subcommand
        pkg_parser = subparsers.add_parser(
            "create-package", help="Generate a pip-installable theme package scaffold"
        )
        pkg_parser.add_argument(
            "package_name", type=str, help="Package name (lowercase letters, digits, hyphens only)"
        )
        pkg_parser.add_argument("--author", type=str, default="", help="Package author name")
        pkg_parser.add_argument(
            "--preset",
            type=str,
            default="default",
            help="Color preset from THEME_PRESETS (default: default)",
        )
        pkg_parser.add_argument(
            "--design-system",
            type=str,
            default="material",
            help="Design system from DESIGN_SYSTEMS (default: material)",
        )
        pkg_parser.add_argument(
            "--dir",
            type=str,
            default=None,
            dest="pkg_dir",
            help="Output directory (default: current working directory)",
        )
        pkg_parser.add_argument(
            "--force", action="store_true", help="Overwrite existing package directory"
        )

        # check-compat subcommand
        compat_parser = subparsers.add_parser(
            "check-compat", help="Check theme overrides against component contracts"
        )
        compat_parser.add_argument("compat_theme_name", nargs="?", help="Theme name to check")
        compat_parser.add_argument(
            "--all",
            action="store_true",
            dest="check_all",
            help="Check all themes in the themes directory",
        )
        compat_parser.add_argument("--dir", type=str, dest="dir", help="Override themes directory")

        # marketplace-info subcommand
        mp_parser = subparsers.add_parser(
            "marketplace-info", help="Show marketplace metadata and component coverage for a theme"
        )
        mp_parser.add_argument("mp_theme_name", type=str, help="Theme name to inspect")
        mp_parser.add_argument("--dir", type=str, dest="dir", help="Override themes directory")

    def handle(self, *args, **options):
        subcommand = options.get("subcommand")

        if not subcommand:
            self.print_help("manage.py", "djust_theme")
            return

        if subcommand == "tailwind-config":
            self.handle_tailwind_config(options)
        elif subcommand == "export-colors":
            self.handle_export_colors(options)
        elif subcommand == "list-presets":
            self.handle_list_presets()
        elif subcommand == "generate-examples":
            self.handle_generate_examples(options)
        elif subcommand == "shadcn-import":
            self.handle_shadcn_import(options)
        elif subcommand == "shadcn-export":
            self.handle_shadcn_export(options)
        elif subcommand == "init":
            self.handle_init(options)
        elif subcommand == "create-theme":
            self.handle_create_theme(options)
        elif subcommand == "validate-theme":
            self.handle_validate_theme(options)
        elif subcommand == "create-package":
            self.handle_create_package(options)
        elif subcommand == "check-compat":
            self.handle_check_compat(options)
        elif subcommand == "marketplace-info":
            self.handle_marketplace_info(options)
        else:
            raise CommandError(f"Unknown subcommand: {subcommand}")

    def handle_tailwind_config(self, options):
        """Generate tailwind.config.js file."""
        preset = options["preset"]
        output = options["output"]
        extend = options["extend"]
        all_presets = options["all_presets"]

        registry = get_registry()
        if not registry.has_preset(preset):
            raise CommandError(
                f"Unknown preset: {preset}. "
                f"Available: {', '.join(sorted(registry.list_presets().keys()))}"
            )

        self.stdout.write(f"Generating Tailwind config for preset '{preset}'...")

        try:
            config_content = generate_tailwind_config(
                preset_name=preset,
                extend_colors=extend,
                include_all_presets=all_presets,
            )

            with open(output, "w") as f:
                f.write(config_content)

            self.stdout.write(self.style.SUCCESS(f"✓ Generated {output}"))

            self.stdout.write("\nNext steps:")
            self.stdout.write("  1. Install Tailwind CSS if you haven't:")
            self.stdout.write("     npm install -D tailwindcss")
            self.stdout.write("")
            self.stdout.write("  2. Add theme CSS to your base template:")
            self.stdout.write("     {{ theme_head }}")
            self.stdout.write("")
            self.stdout.write("  3. Use theme colors in your templates:")
            self.stdout.write(
                '     <button class="bg-primary text-primary-foreground">Click me</button>'
            )
            self.stdout.write("")
            self.stdout.write("  4. Or use @apply in your CSS:")
            self.stdout.write("     python manage.py djust_theme generate-examples")

        except Exception as e:
            raise CommandError(f"Failed to generate config: {e}")

    def handle_export_colors(self, options):
        """Export preset colors."""
        preset = options["preset"]
        format_type = options["format"]
        output = options.get("output")

        registry = get_registry()
        if not registry.has_preset(preset):
            raise CommandError(
                f"Unknown preset: {preset}. "
                f"Available: {', '.join(sorted(registry.list_presets().keys()))}"
            )

        try:
            colors = export_preset_as_tailwind_colors(preset)

            if format_type == "json":
                content = json.dumps(colors, indent=2)
            elif format_type == "python":
                content = f"# Colors for preset: {preset}\n\n"
                content += "COLORS = {\n"
                for key, value in colors.items():
                    content += f"    '{key}': '{value}',\n"
                content += "}\n"
            else:
                raise CommandError(f"Unknown format: {format_type}")

            if output:
                with open(output, "w") as f:
                    f.write(content)
                self.stdout.write(self.style.SUCCESS(f"✓ Exported colors to {output}"))
            else:
                self.stdout.write(content)

        except Exception as e:
            raise CommandError(f"Failed to export colors: {e}")

    def handle_list_presets(self):
        """List all available presets."""
        self.stdout.write(self.style.SUCCESS("Available theme presets:\n"))

        presets = get_registry().list_presets()
        for name, preset in presets.items():
            self.stdout.write(f"  • {name:12} - {preset.display_name}")
            if hasattr(preset, "description"):
                self.stdout.write(f"               {preset.description}")

        self.stdout.write(f"\nTotal: {len(presets)} presets")

    def handle_generate_examples(self, options):
        """Generate @apply examples."""
        output = options["output"]

        try:
            examples = generate_tailwind_apply_examples()

            with open(output, "w") as f:
                f.write(examples)

            self.stdout.write(self.style.SUCCESS(f"✓ Generated {output}"))
            self.stdout.write("\nThis file shows how to use @apply with theme colors.")
            self.stdout.write("You can copy these examples into your own CSS files.")

        except Exception as e:
            raise CommandError(f"Failed to generate examples: {e}")

    def handle_shadcn_import(self, options):
        """Import a shadcn theme from JSON file."""
        input_file = options["input_file"]
        register = options["register"]

        try:
            preset = import_shadcn_theme_from_file(input_file)

            self.stdout.write(self.style.SUCCESS(f"✓ Imported theme: {preset.name}"))
            self.stdout.write(f"  Display name: {preset.display_name}")

            if register:
                get_registry().register_preset(preset.name, preset)
                self.stdout.write(
                    self.style.SUCCESS(f"\n✓ Registered '{preset.name}' in theme registry")
                )
                self.stdout.write(
                    "\nTo make this permanent, add the theme to djust_theming/presets.py"
                )
            else:
                self.stdout.write("\nTo register this theme, re-run with --register flag")

        except FileNotFoundError:
            raise CommandError(f"File not found: {input_file}")
        except json.JSONDecodeError as e:
            raise CommandError(f"Invalid JSON: {e}")
        except Exception as e:
            raise CommandError(f"Failed to import theme: {e}")

    def handle_shadcn_export(self, options):
        """Export a preset to shadcn theme JSON format."""
        preset = options["preset"]
        output = options["output"]

        registry = get_registry()
        if not registry.has_preset(preset):
            raise CommandError(
                f"Unknown preset: {preset}. "
                f"Available: {', '.join(sorted(registry.list_presets().keys()))}"
            )

        try:
            export_shadcn_theme_to_file(preset, output)

            self.stdout.write(self.style.SUCCESS(f"✓ Exported {preset} to {output}"))
            self.stdout.write(
                "\nThis file can be imported into shadcn/ui-based projects "
                "or uploaded to themes.shadcn.com"
            )

        except Exception as e:
            raise CommandError(f"Failed to export theme: {e}")

    def handle_init(self, options):
        """Initialize djust-theming in the project."""
        preset = options["preset"]
        with_tailwind = options["with_tailwind"]
        with_examples = options["with_examples"]

        registry = get_registry()
        if not registry.has_preset(preset):
            raise CommandError(
                f"Unknown preset: {preset}. "
                f"Available: {', '.join(sorted(registry.list_presets().keys()))}"
            )

        self.stdout.write(self.style.SUCCESS("\n🎨 Initializing djust-theming...\n"))

        # Check if djust.theming is in INSTALLED_APPS
        try:
            import django.conf

            if "djust.theming" not in django.conf.settings.INSTALLED_APPS:
                self.stdout.write(
                    self.style.WARNING("⚠  djust.theming not found in INSTALLED_APPS")
                )
                self.stdout.write(
                    "\nPlease add to settings.py:\n"
                    "INSTALLED_APPS = [\n"
                    "    ...\n"
                    "    'djust.theming',\n"
                    "]\n"
                )
        except Exception:
            pass

        # Generate tailwind config if requested
        if with_tailwind:
            try:
                config_content = generate_tailwind_config(preset_name=preset)
                with open("tailwind.config.js", "w") as f:
                    f.write(config_content)
                self.stdout.write(self.style.SUCCESS("✓ Generated tailwind.config.js"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✗ Failed to generate Tailwind config: {e}"))

        # Generate example templates if requested
        if with_examples:
            import os

            os.makedirs("templates/examples", exist_ok=True)

            example_template = """{% load theme_tags theme_components %}
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>djust-theming Example</title>
    {% theme_head %}
    {% if tailwind %}
    <link href="https://cdn.tailwindcss.com" rel="stylesheet">
    {% endif %}
</head>
<body class="bg-background text-foreground min-h-screen p-8">
    <div class="max-w-4xl mx-auto">
        <div class="flex items-center justify-between mb-8">
            <h1 class="text-3xl font-bold">djust-theming Example</h1>
            {% theme_switcher %}
        </div>

        {% theme_card title="Welcome" %}
            <p class="mb-4">This is an example using djust-theming components.</p>
            {% theme_button "Click me" variant="primary" %}
            {% theme_button "Secondary" variant="secondary" %}
        {% end_theme_card %}

        <div class="mt-4">
            {% theme_alert "This is a success message!" variant="success" dismissible=True %}
        </div>

        <div class="mt-4 flex gap-2">
            {% theme_badge "New" variant="success" %}
            {% theme_badge "Beta" variant="secondary" %}
            {% theme_badge "Popular" variant="default" %}
        </div>
    </div>
</body>
</html>
"""
            with open("templates/examples/theme_example.html", "w") as f:
                f.write(example_template)

            self.stdout.write(
                self.style.SUCCESS("✓ Generated templates/examples/theme_example.html")
            )

        # Print next steps
        self.stdout.write(self.style.SUCCESS("\n✓ Initialization complete!\n"))
        self.stdout.write("Next steps:\n")
        self.stdout.write("  1. Add djust.theming to INSTALLED_APPS (if not already)")
        self.stdout.write("  2. Add theme context processor to settings.py:")
        self.stdout.write("     'djust.theming.context_processors.theme_context'")
        self.stdout.write("  3. Use {{ theme_head }} in your base template")
        self.stdout.write("  4. Use {% load theme_components %} to access components\n")

        if with_examples:
            self.stdout.write(
                "  5. Check templates/examples/theme_example.html for usage examples\n"
            )

        self.stdout.write(f"\n📚 Preset: {preset}")
        self.stdout.write("🎨 Theme switcher: { theme_switcher }")
        self.stdout.write("🌓 Mode toggle: { theme_mode_toggle }\n")

    def handle_create_theme(self, options):
        """Scaffold a new user theme directory."""
        from pathlib import Path

        from django.conf import settings as django_settings

        from djust.theming.manifest import ThemeManifest
        from djust.theming.manager import get_theme_config

        registry = get_registry()
        theme_name = options["theme_name"]
        base = options.get("base")
        preset = options["preset"]
        design_system = options["design_system"]
        force = options.get("force", False)

        # Validate theme name
        import re

        if not re.match(r"^[a-z0-9][a-z0-9-]*$", theme_name):
            raise CommandError(
                f"Invalid theme name '{theme_name}': must contain only "
                f"lowercase letters, digits, and hyphens (pattern: [a-z0-9-])."
            )

        # Validate preset
        if not registry.has_preset(preset):
            raise CommandError(
                f"Unknown preset '{preset}'. "
                f"Available: {', '.join(sorted(registry.list_presets().keys()))}"
            )

        # Validate design system
        if not registry.has_theme(design_system):
            raise CommandError(
                f"Unknown design system '{design_system}'. "
                f"Available: {', '.join(sorted(registry.list_themes().keys()))}"
            )

        # Resolve themes directory
        dir_override = options.get("dir")
        if dir_override:
            themes_dir = Path(dir_override)
        else:
            config = get_theme_config()
            themes_dir_rel = config.get("themes_dir", "themes/")
            base_dir = getattr(django_settings, "BASE_DIR", Path.cwd())
            themes_dir = Path(base_dir) / themes_dir_rel

        theme_dir = themes_dir / theme_name

        # Check for existing theme
        if theme_dir.exists() and not force:
            raise CommandError(
                f"Theme directory already exists: {theme_dir}\nUse --force to overwrite."
            )

        # Build manifest
        manifest = ThemeManifest(
            name=theme_name,
            version="0.1.0",
            description=f"Custom theme: {theme_name}",
            base=base,
            preset=preset,
            design_system=design_system,
        )

        # Create directory structure
        theme_dir.mkdir(parents=True, exist_ok=True)

        subdirs = [
            "components",
            "layouts",
            "pages",
            "static/css",
            "static/fonts",
        ]
        for subdir in subdirs:
            d = theme_dir / subdir
            d.mkdir(parents=True, exist_ok=True)
            gitkeep = d / ".gitkeep"
            gitkeep.touch()

        # Write theme.toml
        toml_path = theme_dir / "theme.toml"
        toml_path.write_text(manifest.to_toml())

        # Write tokens.css template
        tokens_css = theme_dir / "tokens.css"
        tokens_css.write_text(
            f"/* Theme: {theme_name}\n"
            f" * Preset: {preset} | Design System: {design_system}\n"
            f" *\n"
            f" * Override CSS custom properties here.\n"
            f" * These are applied AFTER the preset tokens.\n"
            f" *\n"
            f" * Example:\n"
            f" *   :root {{\n"
            f" *     --primary: 220 90% 56%;\n"
            f" *     --radius: 0.75rem;\n"
            f" *   }}\n"
            f" */\n"
        )

        self.stdout.write(self.style.SUCCESS(f"\nCreated theme '{theme_name}' at {theme_dir}\n"))
        self.stdout.write(
            f"  theme.toml     — manifest (preset: {preset}, design system: {design_system})"
        )
        self.stdout.write("  tokens.css     — CSS custom property overrides")
        self.stdout.write("  components/    — component template overrides")
        self.stdout.write("  layouts/       — layout template overrides")
        self.stdout.write("  pages/         — page template overrides")
        self.stdout.write("  static/css/    — additional stylesheets")
        self.stdout.write("  static/fonts/  — custom web fonts\n")

    def handle_validate_theme(self, options):
        """Validate a theme manifest and its referenced files."""
        from pathlib import Path

        from django.conf import settings as django_settings

        from djust.theming.manager import get_theme_config
        from djust.theming.presets import ThemeTokens

        registry = get_registry()
        validate_all = options.get("validate_all", False)
        validate_dir = options.get("validate_dir")
        theme_name = options.get("theme_name")

        # Resolve themes directory
        if validate_dir:
            themes_dir = Path(validate_dir)
        else:
            config = get_theme_config()
            themes_dir_rel = config.get("themes_dir", "themes/")
            base_dir = getattr(django_settings, "BASE_DIR", Path.cwd())
            themes_dir = Path(base_dir) / themes_dir_rel

        if validate_all:
            # Validate all themes in the directory
            if not themes_dir.is_dir():
                raise CommandError(f"Themes directory not found: {themes_dir}")
            found = False
            for child in sorted(themes_dir.iterdir()):
                if child.is_dir() and (child / "theme.toml").exists():
                    found = True
                    self._validate_single_theme(child, registry, ThemeTokens)
            if not found:
                self.stdout.write(self.style.WARNING("No themes found in: " + str(themes_dir)))
            return

        if not theme_name:
            raise CommandError("Provide a theme name or use --all to validate all themes.")

        theme_dir = themes_dir / theme_name
        if not theme_dir.is_dir():
            raise CommandError(f"Theme directory not found: {theme_dir}")

        toml_path = theme_dir / "theme.toml"
        if not toml_path.is_file():
            raise CommandError(f"theme.toml not found in: {theme_dir}")

        self._validate_single_theme(theme_dir, registry, ThemeTokens)

    def _validate_single_theme(self, theme_dir, registry, ThemeTokens):
        """Run all validation checks on a single theme directory."""
        from djust.theming.manifest import ThemeManifest

        toml_path = theme_dir / "theme.toml"
        theme_name = theme_dir.name
        errors = []
        warnings = []

        self.stdout.write(f"\nValidating theme: {theme_name}")
        self.stdout.write("-" * 40)

        # 1. Parse TOML
        try:
            manifest = ThemeManifest.from_toml(toml_path)
        except (ValueError, FileNotFoundError) as e:
            raise CommandError(f"Failed to parse theme.toml for '{theme_name}': {e}")

        # 2. Run manifest.validate() (name, preset, design system)
        manifest_errors = manifest.validate()
        for err in manifest_errors:
            errors.append(err)

        # 3. Validate static file references
        for css_file in manifest.css:
            css_path = theme_dir / css_file
            if not css_path.is_file():
                warnings.append(f"Static CSS file not found: {css_file}")

        for font_file in manifest.fonts:
            font_path = theme_dir / font_file
            if not font_path.is_file():
                warnings.append(f"Static font file not found: {font_file}")

        # 4. Validate token override keys against ThemeTokens fields
        valid_token_names = set(ThemeTokens.__dataclass_fields__.keys())
        for key in manifest.overrides:
            if key not in valid_token_names:
                warnings.append(
                    f"Unknown override key '{key}' — not a ThemeTokens field. "
                    f"Valid keys include: {', '.join(sorted(list(valid_token_names)[:10]))}..."
                )

        # Print results
        if errors:
            for err in errors:
                self.stdout.write(self.style.ERROR(f"  ERROR: {err}"))
        if warnings:
            for warn in warnings:
                self.stdout.write(self.style.WARNING(f"  WARNING: {warn}"))

        if not errors and not warnings:
            self.stdout.write(self.style.SUCCESS("  PASS: All checks passed."))
        elif not errors:
            self.stdout.write(
                self.style.SUCCESS(f"  PASS: Valid (with {len(warnings)} warning(s)).")
            )

    def handle_create_package(self, options):
        """Generate a pip-installable theme package scaffold."""
        import re
        from pathlib import Path

        from djust.theming.manifest import ThemeManifest

        registry = get_registry()
        name = options["package_name"]
        author = options.get("author", "")
        preset = options["preset"]
        design_system = options["design_system"]
        force = options.get("force", False)
        output_dir = options.get("pkg_dir")

        # Validate package name
        if not re.match(r"^[a-z0-9][a-z0-9-]*$", name):
            raise CommandError(
                f"Invalid package name '{name}': must contain only "
                f"lowercase letters, digits, and hyphens (pattern: [a-z0-9-])."
            )

        # Validate preset
        if not registry.has_preset(preset):
            raise CommandError(
                f"Unknown preset '{preset}'. "
                f"Available: {', '.join(sorted(registry.list_presets().keys()))}"
            )

        # Validate design system
        if not registry.has_theme(design_system):
            raise CommandError(
                f"Unknown design system '{design_system}'. "
                f"Available: {', '.join(sorted(registry.list_themes().keys()))}"
            )

        # Resolve output directory
        if output_dir:
            base_dir = Path(output_dir)
        else:
            base_dir = Path.cwd()

        # Naming conventions
        py_name = name.replace("-", "_")
        dist_name = f"djust-theme-{name}"
        py_pkg_name = f"djust_theme_{py_name}"
        pkg_root = base_dir / dist_name

        # Check for existing package
        if pkg_root.exists() and not force:
            raise CommandError(
                f"Package directory already exists: {pkg_root}\nUse --force to overwrite."
            )

        # --- Create directory structure ---
        pkg_root.mkdir(parents=True, exist_ok=True)
        py_pkg_dir = pkg_root / py_pkg_name
        py_pkg_dir.mkdir(parents=True, exist_ok=True)

        # Template directories
        templates_dir = py_pkg_dir / "templates" / "djust_theming" / "themes" / name / "components"
        templates_dir.mkdir(parents=True, exist_ok=True)
        (templates_dir / ".gitkeep").touch()

        # Static directories
        for subdir in ["css", "fonts"]:
            d = py_pkg_dir / "static" / py_pkg_name / subdir
            d.mkdir(parents=True, exist_ok=True)
            (d / ".gitkeep").touch()

        # --- Write __init__.py ---
        (py_pkg_dir / "__init__.py").write_text("")

        # --- Write theme.toml ---
        manifest = ThemeManifest(
            name=name,
            version="0.1.0",
            description=f"Theme package: {name}",
            author=author,
            preset=preset,
            design_system=design_system,
        )
        (py_pkg_dir / "theme.toml").write_text(manifest.to_toml())

        # --- Write tokens.css ---
        (py_pkg_dir / "tokens.css").write_text(
            f"/* Theme: {name}\n"
            f" * Preset: {preset} | Design System: {design_system}\n"
            f" *\n"
            f" * Override CSS custom properties here.\n"
            f" * These are applied AFTER the preset tokens.\n"
            f" *\n"
            f" * Example:\n"
            f" *   :root {{\n"
            f" *     --primary: 220 90% 56%;\n"
            f" *     --radius: 0.75rem;\n"
            f" *   }}\n"
            f" */\n"
        )

        # --- Write pyproject.toml ---
        author_line = ""
        if author:
            author_line = f'authors = [{{name = "{author}"}}]'
        else:
            author_line = 'authors = [{name = "Theme Author"}]'

        pyproject_content = f"""[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{dist_name}"
version = "0.1.0"
description = "djust-theming theme package: {name}"
{author_line}
requires-python = ">=3.10"
license = {{text = "MIT"}}

dependencies = [
    "djust-theming>=0.3.0",
]

[tool.setuptools.packages.find]
include = ["{py_pkg_name}*"]

[tool.setuptools.package-data]
{py_pkg_name} = [
    "theme.toml",
    "tokens.css",
    "templates/**/*.html",
    "static/**/*",
]
"""
        (pkg_root / "pyproject.toml").write_text(pyproject_content)

        # --- Write README.md ---
        readme_content = f"""# {dist_name}

A theme package for [djust-theming](https://djust.org/theming).

## Installation

```bash
pip install {dist_name}
```

## Usage

Add the package to your Django `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    ...
    "djust.theming",
    "{py_pkg_name}",
]
```

Then configure it in your djust-theming settings:

```python
LIVEVIEW_CONFIG = {{
    "theme": {{
        "packages": ["{dist_name}"],
    }}
}}
```
"""
        (pkg_root / "README.md").write_text(readme_content)

        # --- Write LICENSE ---
        license_content = """MIT License

Copyright (c) 2024

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
        (pkg_root / "LICENSE").write_text(license_content)

        # --- Print summary ---
        self.stdout.write(
            self.style.SUCCESS(f"\nCreated theme package '{dist_name}' at {pkg_root}\n")
        )
        self.stdout.write("  pyproject.toml           -- build metadata")
        self.stdout.write("  README.md                -- installation instructions")
        self.stdout.write("  LICENSE                  -- MIT license")
        self.stdout.write(f"  {py_pkg_name}/")
        self.stdout.write("    __init__.py            -- package marker")
        self.stdout.write(f"    theme.toml             -- theme manifest (preset: {preset})")
        self.stdout.write("    tokens.css             -- CSS custom property overrides")
        self.stdout.write("    templates/             -- component template overrides")
        self.stdout.write("    static/                -- CSS and font assets")
        self.stdout.write("\nNext steps:")
        self.stdout.write("  1. Edit tokens.css to customize your theme")
        self.stdout.write("  2. Add component templates to templates/")
        self.stdout.write(f"  3. Build: pip install -e {pkg_root}")
        self.stdout.write("  4. Publish: python -m build && twine upload dist/*\n")

    def handle_check_compat(self, options):
        """Check theme overrides against component contracts."""
        from pathlib import Path

        from django.conf import settings as django_settings

        from djust.theming.manager import get_theme_config

        check_all = options.get("check_all", False)
        theme_name = options.get("compat_theme_name")
        dir_override = options.get("dir")

        # Resolve themes directory
        if dir_override:
            themes_dir = Path(dir_override)
        else:
            config = get_theme_config()
            themes_dir_rel = config.get("themes_dir", "themes/")
            base_dir = getattr(django_settings, "BASE_DIR", Path.cwd())
            themes_dir = Path(base_dir) / themes_dir_rel

        if check_all:
            if not themes_dir.is_dir():
                raise CommandError(f"Themes directory not found: {themes_dir}")
            found = False
            for child in sorted(themes_dir.iterdir()):
                if child.is_dir() and (child / "theme.toml").exists():
                    found = True
                    self._check_compat_single(child)
            if not found:
                self.stdout.write(self.style.WARNING("No themes found in: " + str(themes_dir)))
            return

        if not theme_name:
            raise CommandError("Provide a theme name or use --all to check all themes.")

        theme_dir = themes_dir / theme_name
        if not theme_dir.is_dir():
            raise CommandError(f"Theme directory not found: {theme_dir}")

        self._check_compat_single(theme_dir)

    def _check_compat_single(self, theme_dir):
        """Run compatibility check on a single theme directory."""
        from djust.theming.compat import check_theme_compat

        theme_name = theme_dir.name
        self.stdout.write(f"\nChecking compatibility: {theme_name}")
        self.stdout.write("-" * 40)

        issues = check_theme_compat(theme_dir)

        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        infos = [i for i in issues if i.severity == "info"]

        for issue in errors:
            self.stdout.write(self.style.ERROR(f"  ERROR: {issue.message}"))
        for issue in warnings:
            self.stdout.write(self.style.WARNING(f"  WARNING: {issue.message}"))
        for issue in infos:
            self.stdout.write(f"  INFO: {issue.message}")

        if not issues:
            self.stdout.write(self.style.SUCCESS("  PASS: All contract checks passed."))
        elif not errors:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  PASS: Compatible (with {len(warnings)} warning(s), {len(infos)} info(s))."
                )
            )

    def handle_marketplace_info(self, options):
        """Show marketplace metadata and component coverage for a theme."""
        from pathlib import Path

        from django.conf import settings as django_settings

        from djust.theming.gallery.storybook import get_component_coverage
        from djust.theming.manager import get_theme_config
        from djust.theming.manifest import ThemeManifest

        theme_name = options.get("mp_theme_name")
        dir_override = options.get("dir")

        # Resolve themes directory
        if dir_override:
            themes_dir = Path(dir_override)
        else:
            config = get_theme_config()
            themes_dir_rel = config.get("themes_dir", "themes/")
            base_dir = getattr(django_settings, "BASE_DIR", Path.cwd())
            themes_dir = Path(base_dir) / themes_dir_rel

        theme_dir = themes_dir / theme_name
        if not theme_dir.is_dir():
            raise CommandError(f"Theme directory not found: {theme_dir}")

        # Load manifest
        toml_path = theme_dir / "theme.toml"
        if not toml_path.is_file():
            raise CommandError(f"No theme.toml found in: {theme_dir}")

        manifest = ThemeManifest.from_toml(toml_path)

        # Component coverage
        coverage = get_component_coverage(theme_name, themes_dir)

        # Output
        self.stdout.write(f"\nTheme: {manifest.name} v{manifest.version}")
        if manifest.description:
            self.stdout.write(f"Description: {manifest.description}")
        if manifest.author:
            self.stdout.write(f"Author: {manifest.author}")
        self.stdout.write("")

        # Marketplace metadata
        if manifest.tags or manifest.compatibility_range or manifest.preview_url:
            self.stdout.write("Marketplace metadata:")
            if manifest.tags:
                self.stdout.write(f"  Tags: {', '.join(manifest.tags)}")
            if manifest.compatibility_range:
                self.stdout.write(f"  Compatibility: {manifest.compatibility_range}")
            if manifest.preview_url:
                self.stdout.write(f"  Preview URL: {manifest.preview_url}")
            if manifest.screenshots:
                self.stdout.write(f"  Screenshots: {', '.join(manifest.screenshots)}")
            self.stdout.write("")

        # Coverage report
        total = len(coverage["overridden"]) + len(coverage["inherited"])
        self.stdout.write(
            f"Component Coverage: {coverage['coverage_pct']}% "
            f"({len(coverage['overridden'])}/{total})"
        )
        self.stdout.write("")

        if coverage["overridden"]:
            self.stdout.write("Overridden components:")
            for name in coverage["overridden"]:
                self.stdout.write(self.style.SUCCESS(f"  + {name}"))

        if coverage["inherited"]:
            self.stdout.write("Inherited (default) components:")
            for name in coverage["inherited"]:
                self.stdout.write(f"  - {name}")
