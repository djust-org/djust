"""
Management command for running djust framework checks with pretty output.

Usage:
    python manage.py djust_check                  # all checks
    python manage.py djust_check --category security
    python manage.py djust_check --json           # CI-friendly JSON output
"""

import json
import logging

from django.core.checks import Error, Warning, run_checks
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

# Map check IDs to categories
_CATEGORY_PREFIXES = {
    "config": ("C0", "C00"),
    "liveview": ("V0", "V00"),
    "security": ("S0", "S00"),
    "templates": ("T0", "T00"),
    "quality": ("Q0", "Q00"),
}

CATEGORIES = list(_CATEGORY_PREFIXES.keys())


def _check_id_suffix(check_id):
    """Extract the suffix after 'djust.' from a check ID."""
    if check_id and check_id.startswith("djust."):
        return check_id[len("djust.") :]
    return check_id or ""


def _category_for_check(check_id):
    """Return the category name for a check ID, or 'other'."""
    suffix = _check_id_suffix(check_id)
    for category, prefixes in _CATEGORY_PREFIXES.items():
        if any(suffix.startswith(p) for p in prefixes):
            return category
    return "other"


def _severity_label(check):
    """Return (label, style_method_name) for a check."""
    if isinstance(check, Error) or check.level >= 40:
        return "ERROR", "ERROR"
    if isinstance(check, Warning) or check.level >= 30:
        return "WARNING", "WARNING"
    return "INFO", "HTTP_INFO"


class Command(BaseCommand):
    help = "Run djust framework checks with pretty output"

    def add_arguments(self, parser):
        parser.add_argument(
            "--category",
            choices=CATEGORIES,
            help="Only run checks for a specific category: %s" % ", ".join(CATEGORIES),
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Output results as JSON (CI-friendly)",
        )

    def handle(self, *args, **options):
        category = options.get("category")
        json_output = options.get("json_output", False)

        # Ensure checks module is imported so @register decorators fire
        try:
            import djust.checks  # noqa: F401
        except ImportError:
            pass  # checks module not installed — skip @register decorators

        # Run all checks tagged with "djust"
        all_checks = run_checks(tags=["djust"])

        # Filter by category if requested
        if category:
            all_checks = [c for c in all_checks if _category_for_check(c.id) == category]

        if json_output:
            self._output_json(all_checks)
        else:
            self._output_pretty(all_checks, category)

    def _output_json(self, checks):
        """Output checks as JSON for CI pipelines."""
        results = []
        for check in checks:
            label, _ = _severity_label(check)
            results.append(
                {
                    "id": check.id,
                    "severity": label.lower(),
                    "category": _category_for_check(check.id),
                    "message": str(check.msg),
                    "hint": check.hint or "",
                }
            )

        summary = {
            "total": len(results),
            "errors": sum(1 for r in results if r["severity"] == "error"),
            "warnings": sum(1 for r in results if r["severity"] == "warning"),
            "info": sum(1 for r in results if r["severity"] == "info"),
        }

        output = {"checks": results, "summary": summary}
        self.stdout.write(json.dumps(output, indent=2))

    def _output_pretty(self, checks, category):
        """Output checks with colour-coded severity."""
        if not checks:
            scope = " (%s)" % category if category else ""
            self.stdout.write(self.style.SUCCESS("All djust checks passed%s!" % scope))
            return

        # Group by category
        by_category = {}
        for check in checks:
            cat = _category_for_check(check.id)
            by_category.setdefault(cat, []).append(check)

        # Print header
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("djust check results"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))

        error_count = 0
        warning_count = 0
        info_count = 0

        for cat in CATEGORIES + ["other"]:
            cat_checks = by_category.get(cat)
            if not cat_checks:
                continue

            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_LABEL("  [%s]" % cat.upper()))

            for check in cat_checks:
                label, style_name = _severity_label(check)

                if label == "ERROR":
                    error_count += 1
                    styled = self.style.ERROR("  %s %s: %s" % (label, check.id, check.msg))
                elif label == "WARNING":
                    warning_count += 1
                    styled = self.style.WARNING("  %s %s: %s" % (label, check.id, check.msg))
                else:
                    info_count += 1
                    styled = self.style.HTTP_INFO("  %s %s: %s" % (label, check.id, check.msg))

                self.stdout.write(styled)
                if check.hint:
                    self.stdout.write("    HINT: %s" % check.hint)

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("-" * 60))
        parts = []
        if error_count:
            parts.append(self.style.ERROR("%d error(s)" % error_count))
        if warning_count:
            parts.append(self.style.WARNING("%d warning(s)" % warning_count))
        if info_count:
            parts.append(self.style.HTTP_INFO("%d info" % info_count))
        self.stdout.write("  Summary: %s" % ", ".join(parts))
        self.stdout.write("")
