"""
Django management command: python manage.py djust_setup_css

Auto-configures CSS framework compilation (Tailwind, Bootstrap, etc.)
"""

import os
import subprocess
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings


class Command(BaseCommand):
    help = "Set up CSS framework compilation (Tailwind, Bootstrap, etc.)"

    def add_arguments(self, parser):
        parser.add_argument(
            "framework",
            nargs="?",
            default="tailwind",
            choices=["tailwind", "bootstrap", "none"],
            help="CSS framework to set up (default: tailwind)",
        )
        parser.add_argument(
            "--watch",
            action="store_true",
            help="Run in watch mode (auto-rebuild on changes)",
        )
        parser.add_argument(
            "--minify",
            action="store_true",
            help="Minify output CSS (recommended for production)",
        )

    def handle(self, *args, **options):
        framework = options["framework"]
        watch = options["watch"]
        minify = options["minify"]

        self.stdout.write(self.style.SUCCESS(f"\n🎨 Setting up {framework} CSS...\n"))

        if framework == "tailwind":
            self._setup_tailwind(watch=watch, minify=minify)
        elif framework == "bootstrap":
            self._setup_bootstrap(watch=watch, minify=minify)
        elif framework == "none":
            self.stdout.write("✓ No CSS framework configured")
        else:
            raise CommandError(f"Unsupported framework: {framework}")

    def _setup_tailwind(self, watch=False, minify=False):
        """Set up Tailwind CSS compilation."""
        # Create static/css directory
        static_dirs = getattr(settings, "STATICFILES_DIRS", [])
        if not static_dirs:
            raise CommandError(
                "STATICFILES_DIRS not configured in settings.py. "
                "Add: STATICFILES_DIRS = [BASE_DIR / 'static']"
            )

        static_dir = static_dirs[0]
        css_dir = os.path.join(static_dir, "css")
        os.makedirs(css_dir, exist_ok=True)

        # Create input.css if it doesn't exist
        input_css = os.path.join(css_dir, "input.css")
        if not os.path.exists(input_css):
            self._create_tailwind_input_css(input_css)
            self.stdout.write(self.style.SUCCESS(f"✓ Created {input_css}"))
        else:
            self.stdout.write(f"• Using existing {input_css}")

        # Create tailwind.config.js if it doesn't exist
        if not os.path.exists("tailwind.config.js"):
            self._create_tailwind_config()
            self.stdout.write(self.style.SUCCESS("✓ Created tailwind.config.js"))
        else:
            self.stdout.write("• Using existing tailwind.config.js")

        # Detect template directories
        template_dirs = self._get_template_dirs()
        self.stdout.write(
            self.style.SUCCESS(
                f"✓ Detected templates in: {', '.join(str(d) for d in template_dirs)}"
            )
        )

        # Build CSS
        output_css = os.path.join(css_dir, "output.css")
        self._build_tailwind_css(input_css, output_css, watch=watch, minify=minify)

    def _create_tailwind_input_css(self, path):
        """Create Tailwind v4 input.css."""
        # Detect template directories for @source directives
        template_dirs = self._get_template_dirs()
        source_directives = "\n".join(
            f'@source "../../{os.path.relpath(d)}";' for d in template_dirs[:3]
        )

        content = f"""@import "tailwindcss";

{source_directives}

/* Custom styles for your project */
/* Add your custom CSS here */
"""
        with open(path, "w") as f:
            f.write(content)

    def _create_tailwind_config(self):
        """Create tailwind.config.js for Tailwind v3."""
        template_dirs = self._get_template_dirs()
        content_patterns = ",\n    ".join(
            f"'./{os.path.relpath(d)}/**/*.html'" for d in template_dirs[:3]
        )

        content = f"""/** @type {{import('tailwindcss').Config}} */
module.exports = {{
  content: [
    {content_patterns},
  ],
  theme: {{
    extend: {{}},
  }},
  plugins: [],
}}
"""
        with open("tailwind.config.js", "w") as f:
            f.write(content)

    def _build_tailwind_css(self, input_css, output_css, watch=False, minify=False):
        """Run Tailwind CSS build."""
        # Check if tailwindcss CLI is available
        tailwind_cmd = self._get_tailwind_command()

        if not tailwind_cmd:
            self.stdout.write(
                self.style.WARNING(
                    "\n⚠️  Tailwind CLI not found. Install it:\n"
                    "  npm install -D tailwindcss\n"
                    "  or download: https://github.com/tailwindlabs/tailwindcss/releases\n"
                )
            )
            return

        # Build command
        cmd = [tailwind_cmd, "-i", input_css, "-o", output_css]
        if watch:
            cmd.append("--watch")
        if minify:
            cmd.append("--minify")

        self.stdout.write(f"\n🔨 Running: {' '.join(cmd)}\n")

        try:
            if watch:
                # Run in foreground for watch mode
                subprocess.run(cmd, check=True)
            else:
                # Run and capture output
                result = subprocess.run(cmd, check=True, capture_output=True, text=True)
                self.stdout.write(result.stdout)
                if result.stderr:
                    self.stdout.write(self.style.WARNING(result.stderr))

                # Show file size
                if os.path.exists(output_css):
                    size = os.path.getsize(output_css)
                    size_kb = size / 1024
                    self.stdout.write(
                        self.style.SUCCESS(f"\n✓ Tailwind CSS compiled: {size_kb:.1f}KB\n")
                    )
                    self.stdout.write(
                        "\nAdd to your base template:\n"
                        '  <link rel="stylesheet" href="{% static \'css/output.css\' %}">\n'
                    )
                    if not minify:
                        self.stdout.write(
                            self.style.WARNING("\n💡 Tip: Use --minify for production builds\n")
                        )
        except subprocess.CalledProcessError as e:
            raise CommandError(f"Tailwind CSS build failed: {e}")

    def _get_tailwind_command(self):
        """Find tailwindcss CLI command."""
        # Check npx
        try:
            subprocess.run(
                ["npx", "tailwindcss", "--help"],
                check=True,
                capture_output=True,
            )
            return "npx tailwindcss"
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Check node_modules
        if os.path.exists("node_modules/.bin/tailwindcss"):
            return "./node_modules/.bin/tailwindcss"

        # Check for standalone binary
        standalone_names = [
            "tailwindcss-macos-arm64",
            "tailwindcss-macos-x64",
            "tailwindcss-linux-x64",
            "tailwindcss-windows-x64.exe",
            "tailwindcss",
        ]
        for name in standalone_names:
            if os.path.exists(name):
                return f"./{name}"

        # Check global install
        try:
            subprocess.run(
                ["tailwindcss", "--help"],
                check=True,
                capture_output=True,
            )
            return "tailwindcss"
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        return None

    def _setup_bootstrap(self, watch=False, minify=False):
        """Set up Bootstrap + Sass compilation."""
        self.stdout.write(
            self.style.WARNING(
                "Bootstrap setup not yet implemented. "
                "Manually install: npm install bootstrap sass"
            )
        )

    def _get_template_dirs(self):
        """Get all template directories from settings."""
        dirs = []
        for backend in settings.TEMPLATES:
            for d in backend.get("DIRS", []):
                if os.path.isdir(d):
                    dirs.append(d)
            # Also check APP_DIRS templates
            if backend.get("APP_DIRS"):
                from django.apps import apps

                for config in apps.get_app_configs():
                    path = config.path
                    if "site-packages" not in path:
                        tpl_dir = os.path.join(path, "templates")
                        if os.path.isdir(tpl_dir):
                            dirs.append(tpl_dir)
        return dirs
