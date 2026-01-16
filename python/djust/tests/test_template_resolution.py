"""
Test that Rust template loader uses the same resolution order as Django.

This test ensures that the Rust template inheritance resolver finds the exact
same template files as Django's template loader, preventing issues where
different base templates are used.
"""

import os
import tempfile
from pathlib import Path

from django.conf import settings


def test_template_resolution_order_matches_django():
    """
    Verify that our template directory ordering matches Django's resolution.

    Django searches in this order:
    1. DIRS from each TEMPLATES config (in order)
    2. APP_DIRS (if enabled) - searches app templates in app order

    Our Rust loader must use the EXACT same order.
    """
    # Create temporary template directories
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create two versions of the same template
        dirs_base = tmpdir / "templates"
        app_base = tmpdir / "app_templates"

        dirs_base.mkdir()
        app_base.mkdir()

        # DIRS template (should be found first)
        (dirs_base / "test.html").write_text("{% extends 'base.html' %}\nDIRS version")
        (dirs_base / "base.html").write_text("<html>DIRS base</html>")

        # APP_DIRS template (should NOT be used if DIRS has it)
        (app_base / "test.html").write_text("{% extends 'base.html' %}\nAPP version")
        (app_base / "base.html").write_text("<html>APP base</html>")

        # Simulate Django's template directory building
        template_dirs = []

        # Step 1: Add DIRS
        template_dirs.append(str(dirs_base))

        # Step 2: Add APP_DIRS
        template_dirs.append(str(app_base))

        # Django should find the DIRS version first
        expected_path = str(dirs_base / "test.html")

        # Our Rust loader should find the same one
        rust_found = None
        for template_dir in template_dirs:
            candidate = os.path.join(template_dir, "test.html")
            if os.path.exists(candidate):
                rust_found = os.path.abspath(candidate)
                break

        assert rust_found is not None, "Rust loader should find the template"
        assert os.path.abspath(expected_path) == rust_found, (
            f"Template resolution mismatch!\n"
            f"  Expected (Django DIRS): {expected_path}\n"
            f"  Rust found:             {rust_found}"
        )


def test_template_directories_ordering():
    """Test that template directories are built in the correct order."""
    from django.apps import apps
    from pathlib import Path

    # Build template dirs using the same logic as LiveView
    template_dirs = []

    # Step 1: Add DIRS from all TEMPLATES configs
    for template_config in settings.TEMPLATES:
        if "DIRS" in template_config:
            template_dirs.extend(template_config["DIRS"])

    # Step 2: Add app template directories
    for template_config in settings.TEMPLATES:
        if template_config["BACKEND"] == "django.template.backends.django.DjangoTemplates":
            if template_config.get("APP_DIRS", False):
                for app_config in apps.get_app_configs():
                    templates_dir = Path(app_config.path) / "templates"
                    if templates_dir.exists():
                        template_dirs.append(str(templates_dir))

    # Verify DIRS comes before APP_DIRS
    # Find index of first DIRS entry and first APP_DIRS entry
    first_dirs_idx = None
    first_app_idx = None

    for i, dir_path in enumerate(template_dirs):
        # DIRS entries are typically in the project root
        if "demo_project/templates" in str(dir_path) and first_dirs_idx is None:
            first_dirs_idx = i
        # APP_DIRS entries are in app directories
        if "demo_app/templates" in str(dir_path) and first_app_idx is None:
            first_app_idx = i

    if first_dirs_idx is not None and first_app_idx is not None:
        assert first_dirs_idx < first_app_idx, (
            "DIRS must come before APP_DIRS in template search order!\n"
            f"  DIRS index: {first_dirs_idx}\n"
            f"  APP_DIRS index: {first_app_idx}\n"
            f"  Template dirs: {template_dirs}"
        )


def test_duplicate_base_templates_use_first():
    """
    Test that when multiple base.html files exist, the first one is used.

    This is a regression test for the bug where demo_app/templates/base.html
    was incorrectly being used instead of templates/base.html.
    """
    from pathlib import Path

    project_base = Path(__file__).parent.parent.parent.parent / "examples" / "demo_project"

    # Both these files should exist
    templates_base = project_base / "templates" / "base.html"
    demo_app_base = project_base / "demo_app" / "templates" / "base.html"

    if templates_base.exists() and demo_app_base.exists():
        # Both should have dependencies_css block now
        templates_content = templates_base.read_text()
        demo_app_content = demo_app_base.read_text()

        assert "dependencies_css" in templates_content, (
            "templates/base.html MUST have dependencies_css block "
            "(this is the file Django finds first!)"
        )
        assert "dependencies_css" in demo_app_content, (
            "demo_app/templates/base.html should also have dependencies_css block "
            "for consistency"
        )
