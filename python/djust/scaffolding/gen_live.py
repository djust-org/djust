"""
Generator logic for ``manage.py djust_gen_live``.

Generates a complete CRUD LiveView scaffold for a Django model, including:
- views.py (LiveView with event handlers)
- urls.py (using ``live_session()`` routing)
- HTML template (with dj-* directives)
- tests.py (basic test scaffold)
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import gen_live_templates as T

logger = logging.getLogger(__name__)

# Supported field types
VALID_FIELD_TYPES = frozenset(
    {
        "string",
        "text",
        "integer",
        "float",
        "decimal",
        "boolean",
        "date",
        "datetime",
        "email",
        "url",
        "slug",
        "fk",
    }
)

# Text-like types that are searchable
TEXT_TYPES = frozenset({"string", "text", "email", "url", "slug"})

# Maximum number of fields to display in list rows
MAX_LIST_FIELDS = 4


def parse_field_defs(field_defs: List[str]) -> List[Dict[str, Any]]:
    """
    Parse CLI field specifications into structured field dicts.

    Each field spec is ``name:type`` or ``name:fk:ModelName``.

    Returns:
        List of dicts with keys: name, type, label, related_model (for FK).

    Raises:
        ValueError: On invalid field definitions.
    """
    fields = []
    seen_names = set()

    for spec in field_defs:
        parts = spec.split(":")
        if len(parts) < 2:
            raise ValueError("Field '%s' must be in name:type format (e.g. title:string)." % spec)

        name = parts[0]
        field_type = parts[1].lower()

        # Validate field name
        if not name:
            raise ValueError("Field name cannot be empty in '%s'." % spec)
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
            raise ValueError(
                "'%s' is not a valid Python identifier. "
                "Field names must start with a letter or underscore." % name
            )
        if name in seen_names:
            raise ValueError("Duplicate field name: '%s'." % name)
        seen_names.add(name)

        # Handle FK fields
        related_model = None
        if field_type == "fk":
            if len(parts) < 3 or not parts[2]:
                raise ValueError(
                    "FK field '%s' requires a model name: %s:fk:ModelName." % (name, name)
                )
            related_model = parts[2]

        # Validate field type
        if field_type not in VALID_FIELD_TYPES:
            raise ValueError(
                "Unknown field type '%s' for field '%s'. "
                "Supported types: %s." % (field_type, name, ", ".join(sorted(VALID_FIELD_TYPES)))
            )

        label = name.replace("_", " ").title()

        fields.append(
            {
                "name": name,
                "type": field_type,
                "label": label,
                "related_model": related_model,
            }
        )

    return fields


def validate_model_name(model_name: str) -> None:
    """
    Validate that model_name is PascalCase.

    Raises:
        ValueError: If model_name is invalid.
    """
    if not model_name:
        raise ValueError("Model name cannot be empty.")
    if not re.match(r"^[A-Z][a-zA-Z0-9]*$", model_name):
        raise ValueError(
            "'%s' is not a valid model name. "
            "Must be PascalCase (e.g. BlogPost, Post2)." % model_name
        )


def get_search_filter(fields: List[Dict[str, Any]]) -> str:
    """
    Generate the search filter code using Q objects for OR logic.

    Bug #6 fix: Uses ``Q`` objects with ``|`` instead of chained ``.filter()``.

    Returns:
        String of Python code for the search filter block.
    """
    text_fields = [f for f in fields if f["type"] in TEXT_TYPES]
    if not text_fields:
        return "            pass  # No text fields to search\n"
    conditions = " | ".join("Q(%s__icontains=self.search_query)" % f["name"] for f in text_fields)
    return "            qs = qs.filter(%s)\n" % conditions


def build_create_body(fields: List[Dict[str, Any]], model_name: str) -> str:
    """
    Generate the create handler body.

    Bug #3 fix: Uses ``model_name`` parameter directly, not from field dict.
    Bug #5 fix: FK type check uses ``f["type"] == "fk"``.

    Args:
        fields: Parsed field definitions.
        model_name: The model class name (e.g. "Post").

    Returns:
        String of Python code for the create body.
    """
    if not fields:
        return "        %s.objects.create()\n" % model_name

    # Find first text field for validation guard
    text_fields = [f for f in fields if f["type"] in TEXT_TYPES]

    lines = []
    # Guard: require at least one text field to be non-empty
    if text_fields:
        first = text_fields[0]["name"]
        lines.append("        if %s and %s.strip():" % (first, first))
        indent = "            "
    else:
        indent = "        "

    # Build create kwargs
    create_kwargs = []
    for f in fields:
        if f["type"] == "fk":
            create_kwargs.append("%s_id=%s_id" % (f["name"], f["name"]))
        elif f["type"] in TEXT_TYPES:
            create_kwargs.append("%s=%s.strip()" % (f["name"], f["name"]))
        elif f["type"] == "boolean":
            create_kwargs.append("%s=bool(%s)" % (f["name"], f["name"]))
        elif f["type"] in ("integer",):
            create_kwargs.append("%s=int(%s) if %s else 0" % (f["name"], f["name"], f["name"]))
        elif f["type"] in ("float", "decimal"):
            create_kwargs.append("%s=float(%s) if %s else 0" % (f["name"], f["name"], f["name"]))
        else:
            create_kwargs.append("%s=%s" % (f["name"], f["name"]))

    lines.append("%s%s.objects.create(" % (indent, model_name))
    for kwarg in create_kwargs:
        lines.append("%s    %s," % (indent, kwarg))
    lines.append("%s)" % indent)

    return "\n".join(lines) + "\n"


def build_update_body(fields: List[Dict[str, Any]], model_name: str) -> str:
    """
    Generate the update handler body.

    Bug #3 fix: Uses ``model_name`` parameter directly.
    Bug #5 fix: FK type check uses ``f["type"] == "fk"``.

    Args:
        fields: Parsed field definitions.
        model_name: The model class name.

    Returns:
        String of Python code for the update body.
    """
    lines = []
    lines.append("        try:")
    lines.append("            obj = %s.objects.get(pk=item_id)" % model_name)

    for f in fields:
        if f["type"] == "fk":
            lines.append("            obj.%s_id = %s_id" % (f["name"], f["name"]))
        elif f["type"] in TEXT_TYPES:
            lines.append(
                "            obj.%s = %s.strip() if %s else obj.%s"
                % (f["name"], f["name"], f["name"], f["name"])
            )
        elif f["type"] == "boolean":
            lines.append("            obj.%s = bool(%s)" % (f["name"], f["name"]))
        elif f["type"] in ("integer",):
            lines.append(
                "            obj.%s = int(%s) if %s else obj.%s"
                % (f["name"], f["name"], f["name"], f["name"])
            )
        elif f["type"] in ("float", "decimal"):
            lines.append(
                "            obj.%s = float(%s) if %s else obj.%s"
                % (f["name"], f["name"], f["name"], f["name"])
            )
        else:
            lines.append(
                "            obj.%s = %s if %s else obj.%s"
                % (f["name"], f["name"], f["name"], f["name"])
            )

    lines.append("            obj.save()")
    lines.append("            self.selected = obj")
    lines.append("        except %s.DoesNotExist:" % model_name)
    lines.append("            pass")

    return "\n".join(lines) + "\n"


def _build_create_params(fields: List[Dict[str, Any]]) -> str:
    """Build the parameter list for the create handler."""
    params = []
    for f in fields:
        if f["type"] == "fk":
            params.append("%s_id: int = 0" % f["name"])
        elif f["type"] == "boolean":
            params.append('%s: str = ""' % f["name"])
        elif f["type"] in ("integer",):
            params.append('%s: str = ""' % f["name"])
        elif f["type"] in ("float", "decimal"):
            params.append('%s: str = ""' % f["name"])
        else:
            params.append('%s: str = ""' % f["name"])
    if params:
        return ", ".join(params) + ", "
    return ""


def _build_update_params(fields: List[Dict[str, Any]]) -> str:
    """Build the parameter list for the update handler."""
    params = []
    for f in fields:
        if f["type"] == "fk":
            params.append("%s_id: int = 0" % f["name"])
        elif f["type"] == "boolean":
            params.append('%s: str = ""' % f["name"])
        elif f["type"] in ("integer",):
            params.append('%s: str = ""' % f["name"])
        elif f["type"] in ("float", "decimal"):
            params.append('%s: str = ""' % f["name"])
        else:
            params.append('%s: str = ""' % f["name"])
    if params:
        return ", ".join(params) + ", "
    return ""


def _build_form_fields_html(fields: List[Dict[str, Any]]) -> str:
    """Build the HTML form fields for the create form."""
    html = ""
    for f in fields:
        input_tpl = T.FORM_INPUT_MAP.get(f["type"], T.FORM_INPUT_MAP["string"])
        html += input_tpl % {"name": f["name"], "label": f["label"]}
    return html


def _build_show_panel_fields(fields: List[Dict[str, Any]]) -> str:
    """Build the HTML fields for the show/edit panel."""
    html = ""
    for f in fields:
        show_tpl = T.SHOW_FIELD_MAP.get(f["type"], T.SHOW_FIELD_MAP["string"])
        html += show_tpl % {"name": f["name"], "label": f["label"]}
    return html


def _build_list_item_fields(fields: List[Dict[str, Any]]) -> str:
    """Build the HTML for list row fields (max MAX_LIST_FIELDS)."""
    html = ""
    for f in fields[:MAX_LIST_FIELDS]:
        if f["type"] == "boolean":
            tpl = T.LIST_FIELD_DISPLAY["boolean"]
        else:
            tpl = T.LIST_FIELD_DISPLAY["default"]
        html += tpl % {"name": f["name"]}
    return html


def _needs_q_import(fields: List[Dict[str, Any]]) -> bool:
    """Check if the generated views.py needs ``from django.db.models import Q``."""
    return any(f["type"] in TEXT_TYPES for f in fields)


def generate_liveview(
    app_name: str,
    model_name: str,
    fields: List[Dict[str, Any]],
    base_dir: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
) -> Optional[List[str]]:
    """
    Generate a complete LiveView CRUD scaffold for a model.

    Args:
        app_name: Django app name (directory must exist under base_dir).
        model_name: PascalCase model name.
        fields: Parsed field definitions from ``parse_field_defs()``.
        base_dir: Base directory containing the app. Defaults to cwd.
        options: Dict with keys: dry_run, force, no_tests, api.

    Returns:
        In dry-run mode, returns list of file paths that would be created.
        Otherwise returns None.

    Raises:
        ValueError: On invalid model name.
        FileNotFoundError: If app directory does not exist.
        FileExistsError: If files exist and --force is not set.
    """
    if options is None:
        options = {}
    if base_dir is None:
        base_dir = os.getcwd()

    # Validate
    validate_model_name(model_name)

    app_dir = os.path.join(base_dir, app_name)
    if not os.path.isdir(app_dir):
        raise FileNotFoundError(
            "App directory '%s' does not exist. "
            "Create the Django app first with 'python manage.py startapp %s'." % (app_dir, app_name)
        )

    # Build naming conventions
    model_slug = _to_slug(model_name)
    view_class = "%sListView" % model_name
    url_name = "%s_list" % model_slug
    model_display_singular = _to_display(model_name)
    model_display_plural = model_display_singular + "s"
    app_display = app_name.replace("_", " ").title()

    # Determine which files to generate
    is_api = options.get("api", False)
    no_tests = options.get("no_tests", False)
    dry_run = options.get("dry_run", False)
    force = options.get("force", False)

    files_to_write = {}

    # views.py
    q_import = ""
    if _needs_q_import(fields):
        q_import = "from django.db.models import Q\n\n"

    view_ctx = {
        "app_name": app_name,
        "model_name": model_name,
        "model_slug": model_slug,
        "view_class": view_class,
        "q_import": q_import,
        "search_filter": get_search_filter(fields),
        "create_params": _build_create_params(fields),
        "update_params": _build_update_params(fields),
        "create_body": build_create_body(fields, model_name),
        "update_body": build_update_body(fields, model_name),
    }

    if is_api:
        views_content = T.VIEWS_PY_API_TEMPLATE % view_ctx
    else:
        views_content = T.VIEWS_PY_TEMPLATE % view_ctx

    files_to_write[os.path.join(app_dir, "views.py")] = views_content

    # urls.py
    urls_ctx = {
        "app_name": app_name,
        "model_name": model_name,
        "model_slug": model_slug,
        "view_class": view_class,
        "url_name": url_name,
    }
    files_to_write[os.path.join(app_dir, "urls.py")] = T.URLS_PY_TEMPLATE % urls_ctx

    # HTML template (not for API mode)
    if not is_api:
        tpl_dir = os.path.join(app_dir, "templates", app_name)
        tpl_path = os.path.join(tpl_dir, "%s_list.html" % model_slug)
        tpl_ctx = {
            "app_name": app_name,
            "app_display": app_display,
            "view_class": view_class,
            "model_display_plural": model_display_plural,
            "model_display_plural_lower": model_display_plural.lower(),
            "model_display_singular": model_display_singular,
            "model_display_singular_lower": model_display_singular.lower(),
            "form_fields_html": _build_form_fields_html(fields),
            "show_panel_fields": _build_show_panel_fields(fields),
            "list_item_fields": _build_list_item_fields(fields),
        }
        files_to_write[tpl_path] = T.LIST_TEMPLATE % tpl_ctx

    # tests.py
    if not no_tests:
        test_ctx = {
            "app_name": app_name,
            "model_name": model_name,
            "view_class": view_class,
        }
        files_to_write[os.path.join(app_dir, "tests.py")] = T.TESTS_PY_TEMPLATE % test_ctx

    # Dry run — return file list without writing
    if dry_run:
        return sorted(files_to_write.keys())

    # Check for existing files (unless --force)
    if not force:
        existing = [p for p in files_to_write if os.path.exists(p)]
        if existing:
            raise FileExistsError(
                "Files already exist (use --force to overwrite): %s" % ", ".join(existing)
            )

    # Write files
    for filepath, content in files_to_write.items():
        dirpath = os.path.dirname(filepath)
        os.makedirs(dirpath, exist_ok=True)
        Path(filepath).write_text(content, encoding="utf-8")
        logger.info("Created %s", filepath)

    return None


def _to_slug(model_name: str) -> str:
    """Convert PascalCase to snake_case slug (e.g. BlogPost -> blog_post)."""
    # Insert underscore before uppercase letters (except first)
    slug = re.sub(r"(?<!^)(?=[A-Z])", "_", model_name).lower()
    return slug


def _to_display(model_name: str) -> str:
    """Convert PascalCase to display name (e.g. BlogPost -> Blog Post)."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", model_name)
