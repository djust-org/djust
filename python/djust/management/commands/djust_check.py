"""
Management command for running djust framework checks with pretty output.

Usage:
    python manage.py djust_check                  # all checks
    python manage.py djust_check --category security
    python manage.py djust_check --json           # CI-friendly JSON output
    python manage.py djust_check --format json    # enhanced JSON with fix_hints
    python manage.py djust_check --fix            # auto-fix safe issues
"""

import json
import logging
import os
import re

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

# Check IDs that are safe for auto-fix
_SAFE_FIX_IDS = frozenset({"djust.V004", "djust.T001", "djust.T004"})


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


def _has_fix_hint(check):
    """Return True if check has a non-empty fix_hint attribute."""
    return bool(getattr(check, "fix_hint", ""))


def _is_fixable(check):
    """Return True if the check can be safely auto-fixed."""
    return check.id in _SAFE_FIX_IDS and _has_fix_hint(check)


# ---------------------------------------------------------------------------
# Auto-fix implementations
# ---------------------------------------------------------------------------


def _fix_v004_add_event_handler(check):
    """Add @event_handler() decorator to a method missing it.

    Returns a description of what was fixed, or None if fix failed.
    """
    file_path = getattr(check, "file_path", "")
    line_number = getattr(check, "line_number", None)
    if not file_path or not line_number or not os.path.isfile(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return None

    # line_number is 1-indexed
    idx = line_number - 1
    if idx < 0 or idx >= len(lines):
        return None

    target_line = lines[idx]
    # Verify it looks like a def line
    stripped = target_line.lstrip()
    if not stripped.startswith("def "):
        return None

    # Calculate indentation
    indent = target_line[: len(target_line) - len(stripped)]

    # Check if @event_handler is already on the line above
    if idx > 0 and "event_handler" in lines[idx - 1]:
        return None

    # Insert @event_handler() decorator
    decorator_line = indent + "@event_handler()\n"
    lines.insert(idx, decorator_line)

    # Ensure 'from djust.decorators import event_handler' is present
    content = "".join(lines)
    if "from djust.decorators import event_handler" not in content:
        # Find the right place to insert (after last import, or at top)
        import_line = "from djust.decorators import event_handler\n"
        last_import_idx = -1
        for i, line in enumerate(lines):
            if line.strip().startswith("from ") or line.strip().startswith("import "):
                last_import_idx = i
        # Insert after last import, or at top if no imports found
        if last_import_idx >= 0:
            lines.insert(last_import_idx + 1, import_line)
        else:
            lines.insert(0, import_line)

    try:
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
    except OSError:
        return None

    return "Added @event_handler() decorator at %s:%d" % (
        os.path.relpath(file_path),
        line_number,
    )


def _fix_t001_replace_deprecated_attr(check):
    """Replace deprecated @click with dj-click in templates.

    Returns a description of what was fixed, or None if fix failed.
    """
    file_path = getattr(check, "file_path", "")
    if not file_path or not os.path.isfile(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return None

    # Extract old_attr from the message (e.g., "@click")
    msg = str(check.msg)
    match = re.search(r"deprecated '(@\w+)'", msg)
    if not match:
        return None

    old_attr = match.group(1)
    new_attr = old_attr.replace("@", "dj-")

    new_content = content.replace(old_attr + "=", new_attr + "=")
    if new_content == content:
        return None

    try:
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write(new_content)
    except OSError:
        return None

    return "Replaced '%s=' with '%s=' in %s" % (
        old_attr,
        new_attr,
        os.path.relpath(file_path),
    )


def _fix_t004_document_to_window(check):
    """Replace document.addEventListener with window.addEventListener for djust events.

    Returns a description of what was fixed, or None if fix failed.
    """
    file_path = getattr(check, "file_path", "")
    line_number = getattr(check, "line_number", None)
    if not file_path or not line_number or not os.path.isfile(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return None

    idx = line_number - 1
    if idx < 0 or idx >= len(lines):
        return None

    line = lines[idx]
    if "document" not in line or "addEventListener" not in line:
        return None

    # Only replace document.addEventListener for djust: events on this line
    new_line = re.sub(
        r"document\s*\.\s*addEventListener\s*\(\s*(['\"])djust:",
        r"window.addEventListener(\1djust:",
        line,
    )
    if new_line == line:
        return None

    lines[idx] = new_line

    try:
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
    except OSError:
        return None

    return "Replaced document.addEventListener with window.addEventListener at %s:%d" % (
        os.path.relpath(file_path),
        line_number,
    )


_FIX_HANDLERS = {
    "djust.V004": _fix_v004_add_event_handler,
    "djust.T001": _fix_t001_replace_deprecated_attr,
    "djust.T004": _fix_t004_document_to_window,
}


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
        parser.add_argument(
            "--format",
            choices=["text", "json"],
            default="text",
            dest="output_format",
            help="Output format: 'text' (default) or 'json' (enhanced with fix_hints)",
        )
        parser.add_argument(
            "--fix",
            action="store_true",
            dest="auto_fix",
            help="Automatically apply safe fixes",
        )

    def handle(self, *args, **options):
        category = options.get("category")
        json_output = options.get("json_output", False)
        output_format = options.get("output_format", "text")
        auto_fix = options.get("auto_fix", False)

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

        # Auto-fix mode
        if auto_fix:
            self._apply_fixes(all_checks)
            return

        # Output format selection
        if json_output:
            # Legacy --json flag: original format without fix_hints
            self._output_json(all_checks)
        elif output_format == "json":
            # New --format json: enhanced format with fix_hints
            self._output_json_enhanced(all_checks)
        else:
            self._output_pretty(all_checks, category)

    def _output_json(self, checks):
        """Output checks as JSON for CI pipelines (legacy format)."""
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

    def _output_json_enhanced(self, checks):
        """Output checks as enhanced JSON with fix_hints, file paths, and line numbers."""
        results = []
        fixable_count = 0
        for check in checks:
            label, _ = _severity_label(check)
            fix_hint = getattr(check, "fix_hint", "") or ""
            file_path = getattr(check, "file_path", "") or ""
            line_number = getattr(check, "line_number", None)
            is_fixable = _is_fixable(check)
            if is_fixable:
                fixable_count += 1

            entry = {
                "id": check.id,
                "severity": label.lower(),
                "category": _category_for_check(check.id),
                "message": str(check.msg),
                "hint": check.hint or "",
                "fix_hint": fix_hint,
                "fixable": is_fixable,
            }
            if file_path:
                entry["file_path"] = file_path
            if line_number is not None:
                entry["line_number"] = line_number

            results.append(entry)

        summary = {
            "total": len(results),
            "errors": sum(1 for r in results if r["severity"] == "error"),
            "warnings": sum(1 for r in results if r["severity"] == "warning"),
            "info": sum(1 for r in results if r["severity"] == "info"),
            "fixable": fixable_count,
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

    def _apply_fixes(self, checks):
        """Apply safe auto-fixes and report results."""
        fixable = [c for c in checks if _is_fixable(c)]
        unsafe = [c for c in checks if not _is_fixable(c) and _has_fix_hint(c)]

        if not fixable and not unsafe:
            self.stdout.write(self.style.SUCCESS("No fixable issues found."))
            return

        # Apply safe fixes
        fixed = []
        failed = []
        for check in fixable:
            handler = _FIX_HANDLERS.get(check.id)
            if handler:
                result = handler(check)
                if result:
                    fixed.append(result)
                else:
                    failed.append(check)
            else:
                failed.append(check)

        # Report fixed issues
        if fixed:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("Fixed %d issue(s):" % len(fixed)))
            for desc in fixed:
                self.stdout.write(self.style.SUCCESS("  + %s" % desc))

        if failed:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("Failed to fix %d issue(s):" % len(failed)))
            for check in failed:
                self.stdout.write(self.style.WARNING("  - %s: %s" % (check.id, check.msg)))

        # Report unsafe issues that need manual attention
        if unsafe:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("%d issue(s) require manual fixes:" % len(unsafe)))
            for check in unsafe:
                fix_hint = getattr(check, "fix_hint", "")
                self.stdout.write(self.style.WARNING("  - %s: %s" % (check.id, check.msg)))
                if fix_hint:
                    self.stdout.write("    FIX: %s" % fix_hint)

        # Summary
        self.stdout.write("")
        total_checks = len(checks)
        self.stdout.write(
            "  Total: %d issues, %d fixed, %d need manual attention"
            % (total_checks, len(fixed), len(unsafe) + len(failed))
        )
