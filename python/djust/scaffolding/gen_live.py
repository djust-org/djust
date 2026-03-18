"""
Generator logic for ``djust_gen_live`` scaffolding command.

Parses CLI arguments, validates inputs, and generates LiveView files
from model field definitions.

Usage:
    from djust.scaffolding.gen_live import generate_liveview
    generate_liveview("blog", "Post", [("title", "string", {})], {"force": False})
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import gen_live_templates as T

logger = logging.getLogger(__name__)

# Valid field types
VALID_FIELD_TYPES = (
    "string",
    "text",
    "integer",
    "float",
    "boolean",
    "date",
    "datetime",
    "email",
    "url",
    "slug",
    "decimal",
)

VALID_FIELD_TYPES_WITH_FK = VALID_FIELD_TYPES + ("fk",)


# Django field type to scaffolding type mapping
DJANGO_FIELD_TYPE_MAP = {
    "CharField": "string",
    "TextField": "text",
    "IntegerField": "integer",
    "BigIntegerField": "integer",
    "SmallIntegerField": "integer",
    "FloatField": "float",
    "BooleanField": "boolean",
    "DateField": "date",
    "DateTimeField": "datetime",
    "EmailField": "email",
    "URLField": "url",
    "SlugField": "slug",
    "DecimalField": "decimal",
    "ForeignKey": "fk",
}


class GenerationError(ValueError):
    """Raised when file generation fails."""


def parse_field_defs(field_defs: List[str]) -> List[Dict[str, str]]:
    """
    Parse a list of field definition strings.

    Args:
        field_defs: List of strings like ``["title:string", "body:text", "published:boolean"]``.

    Returns:
        List of dicts with keys: ``name``, ``type``, ``label``, ``model_name``.

    Raises:
        ValueError: If field syntax is invalid or type is unknown.
    """
    fields = []
    seen_names = set()

    for defn in field_defs:
        if not defn or ":" not in defn:
            raise ValueError(
                "Invalid field definition '%s'. Expected format: ``name:type`` "
                "(e.g., ``title:string``, ``body:text``)." % defn
            )

        # FK fields: name:fk:ModelName — split carefully
        parts = defn.split(":")
        if len(parts) >= 3 and parts[-2].lower() == "fk":
            # name:fk:ModelName format
            name = parts[0].strip()
            type_str = "fk"
            related_model = parts[-1].strip()
        else:
            # Regular field: name:type
            name, type_str = defn.rsplit(":", 1)
            name = name.strip()
            type_str = type_str.strip().lower()
            related_model = None

        if not name:
            raise ValueError("Field name cannot be empty in '%s'." % defn)

        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
            raise ValueError(
                "'%s' is not a valid Python/Django field name. "
                "Use letters, numbers, and underscores, starting with a letter or underscore."
                % name
            )

        if name in seen_names:
            raise ValueError("Duplicate field name: '%s'." % name)
        seen_names.add(name)

        # Validate FK fields
        if type_str == "fk":
            if not related_model:
                raise ValueError(
                    "Foreign key requires model name: ``%s`` is invalid. "
                    "Use ``fk:ModelName`` (e.g., ``fk:User``)." % defn
                )
            field_type = "fk"
        elif type_str not in VALID_FIELD_TYPES:
            raise ValueError(
                "Unknown field type '%s'. Valid types are: %s. "
                "Or use ``fk:ModelName`` for foreign keys."
                % (type_str, ", ".join(VALID_FIELD_TYPES))
            )
        else:
            field_type = type_str

        label = name.replace("_", " ").title()

        fields.append(
            {
                "name": name,
                "type": field_type,
                "label": label,
                "related_model": related_model,
                "model_name": None,  # Set by caller
            }
        )

    return fields


def introspect_model(model_class_name: str, app_name: str) -> List[Dict[str, str]]:
    """
    Introspect a Django model and return field definitions.

    Args:
        model_class_name: The model class name (e.g., ``Post``).
        app_name: The Django app name (used to import the model).

    Returns:
        List of field dicts with keys: ``name``, ``type``, ``label``, ``model_name``.

    Raises:
        ValueError: If the model cannot be found or has unsupported field types.
    """
    try:
        from django.apps import apps

        model = apps.get_model(app_name, model_class_name)
    except LookupError:
        raise ValueError(
            "Model '%s' not found in app '%s'. "
            "Make sure the app is in INSTALLED_APPS and you have run migrations."
            % (model_class_name, app_name)
        )

    fields = []
    seen_names = set()

    for field in model._meta.get_fields():
        # Skip non-field attributes (e.g., pk, manager)
        if not hasattr(field, "attname"):
            continue

        name = field.attname
        if name in seen_names:
            continue
        seen_names.add(name)

        # Get the field type
        field_type = type(field).__name__
        scaffold_type = DJANGO_FIELD_TYPE_MAP.get(field_type)
        related_model = None

        if field_type == "ForeignKey":
            if scaffold_type == "fk":
                related_model = field.related_model.__name__
        elif scaffold_type is None:
            logger.warning(
                "Unsupported field type '%s' for field '%s', mapping to string",
                field_type,
                name,
            )
            scaffold_type = "string"

        label = name.replace("_", " ").title()

        field_dict = {
            "name": name,
            "type": scaffold_type,
            "label": label,
            "related_model": related_model,
            "model_name": model_class_name,
        }
        fields.append(field_dict)

    if not fields:
        raise ValueError(
            "Model '%s' has no fields to scaffold. "
            "Make sure it has at least one database field." % model_class_name
        )

    return fields


def validate_model_name(model_name: str) -> None:
    """
    Validate a model name is a valid Python identifier in PascalCase.

    Args:
        model_name: The model name to validate.

    Raises:
        ValueError: If the name is not a valid PascalCase identifier.
    """
    if not model_name:
        raise ValueError("Model name cannot be empty.")

    if not re.match(r"^[A-Z][a-zA-Z0-9]*$", model_name):
        raise ValueError(
            "'%s' is not a valid model name. Model names must be PascalCase "
            "(start with an uppercase letter, e.g., ``Post``, ``BlogPost``)." % model_name
        )


def build_template_context(
    app_name: str,
    model_name: str,
    fields: List[Dict[str, str]],
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build the template rendering context for all generated files.

    Args:
        app_name: Django app name (e.g., ``blog``).
        model_name: Model class name in PascalCase (e.g., ``Post``).
        fields: List of field dicts from ``parse_field_defs()``.
        options: Generation options (e.g., ``api``, ``no_tests``, ``force``).

    Returns:
        Dict suitable for ``%``-format substitution into templates.
    """
    if options is None:
        options = {}

    # Set model_name on each field
    for f in fields:
        f["model_name"] = model_name

    model_slug = model_name.lower()
    model_display = model_name.replace("_", " ").title() + "s"
    model_display_singular = model_name.replace("_", " ")
    model_display_lower = model_display.lower()
    model_display_singular_lower = model_display_singular.lower()

    display_name = app_name.replace("_", " ").replace("-", " ").title()

    view_class = model_name + "ListView"
    url_prefix = model_slug + "/"

    # Build search filter
    search_filter = T.get_search_filter(fields)

    # Build create params and body
    create_params = T.build_create_params(fields)
    create_body = T.build_create_body(fields)
    create_body = create_body % {"model_name": model_name}

    # Build update params and body
    update_params = T.build_update_params(fields)
    update_body = T.build_update_body(fields)

    # Build context data
    context_data = T.build_context_data(fields)

    # Build computed props
    computed_props = T.build_computed_props(fields)

    # Build list item fields
    list_item_fields = T.build_list_item_fields(fields)

    # Build show panel
    show_item_fields = T.build_show_item_fields(fields)

    # Build form fields
    form_fields_create = T.build_form_fields_create(fields)
    form_fields_edit = T.build_form_fields_edit(fields)

    # Model import
    model_import = T.build_model_import(model_name, fields)

    ctx = {
        "app_name": app_name,
        "model_name": model_name,
        "model_slug": model_slug,
        "model_display": model_display,
        "model_display_lower": model_display_lower,
        "model_display_singular": model_display_singular,
        "model_display_singular_lower": model_display_singular_lower,
        "display_name": display_name,
        "view_class": view_class,
        "url_prefix": url_prefix,
        "url_name": model_slug + "_list",
        "fields": fields,
        "search_filter": search_filter,
        "create_params": create_params,
        "create_body": create_body,
        "update_params": update_params,
        "update_body": update_body,
        "context_data": context_data,
        "computed_props": computed_props,
        "list_item_fields": list_item_fields,
        "show_item_fields": show_item_fields,
        "form_fields_create": form_fields_create,
        "form_fields_edit": form_fields_edit,
        "model_import": model_import,
        "options": options,
    }

    return ctx


def generate_liveview(
    app_name: str,
    model_name: str,
    fields: List[Dict[str, str]],
    options: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Generate LiveView files for a Django model.

    Creates the following files in the app directory:
        - ``views.py`` — LiveView class (merged if exists)
        - ``urls.py`` — URL patterns (merged if exists)
        - ``templates/<app_name>/<model_slug>_list.html`` — List view
        - ``tests/test_<model_slug>_crud.py`` — Smoke tests (unless ``no_tests``)

    Args:
        app_name: Django app name (e.g., ``blog``).
        model_name: Model class name in PascalCase (e.g., ``Post``).
        fields: List of field dicts from ``parse_field_defs()``.
        options: Generation options:
            - ``force``: Overwrite existing files without error.
            - ``no_tests``: Skip generating test file.
            - ``dry_run``: Print what would be generated without writing.
            - ``api``: Generate JSON API views (no templates).

    Raises:
        GenerationError: If target files exist and ``force`` is not set.
    """
    if options is None:
        options = {}

    try:
        validate_model_name(model_name)
    except ValueError as e:
        raise GenerationError(str(e))

    ctx = build_template_context(app_name, model_name, fields, options)

    # Determine app directory
    app_dir = Path(app_name)
    if not app_dir.exists() and not app_dir.is_dir():
        # Try relative to current file
        pass

    templates_dir = app_dir / "templates" / app_name
    tests_dir = app_dir / "tests"

    # Check for existing files (unless --force)
    force = options.get("force", False)
    dry_run = options.get("dry_run", False)

    model_slug = ctx["model_slug"]
    api_mode = options.get("api", False)
    files_to_check = [
        (app_dir / "views.py", "views.py"),
        (app_dir / "urls.py", "urls.py"),
    ]
    if not api_mode:
        files_to_check.append((templates_dir / (model_slug + "_list.html"), "list template"))

    if not options.get("no_tests"):
        files_to_check.append((tests_dir / ("test_" + model_slug + "_crud.py"), "test file"))

    existing = [path for path, label in files_to_check if path.exists()]

    if existing and not force:
        file_list = ", ".join(str(p) for p in existing)
        raise GenerationError(
            "Files already exist: %s. Use --force to overwrite.\n"
            "Existing files: %s" % (file_list, file_list)
        )

    if dry_run:
        logger.info("[DRY RUN] Would generate LiveView for %s in %s/", model_name, app_name)
        logger.info("[DRY RUN]   views.py")
        logger.info("[DRY RUN]   urls.py")
        if not api_mode:
            logger.info("[DRY RUN]   templates/%s/%s_list.html", app_name, ctx["model_slug"])
        if not options.get("no_tests"):
            logger.info("[DRY RUN]   tests/test_%s_crud.py", ctx["model_slug"])
        return

    # Create directories
    if not api_mode:
        templates_dir.mkdir(parents=True, exist_ok=True)
    if not options.get("no_tests"):
        tests_dir.mkdir(parents=True, exist_ok=True)

    # Write views.py
    _merge_or_write(
        app_dir / "views.py",
        _build_views_content(ctx),
        force=force,
    )

    # Write urls.py
    _merge_or_write(
        app_dir / "urls.py",
        _build_urls_content(ctx),
        force=force,
    )

    # Write list template (skip in API mode)
    if not api_mode:
        _write(
            templates_dir / (model_slug + "_list.html"),
            _build_list_html_content(ctx),
        )

    # Write test file
    if not options.get("no_tests"):
        _write(
            tests_dir / ("test_" + model_slug + "_crud.py"),
            _build_test_content(ctx),
        )


def _build_views_content(ctx: Dict[str, Any]) -> str:
    """Build the views.py content."""
    if ctx.get("options", {}).get("api"):
        return T.VIEWS_API_TEMPLATE % ctx
    return T.VIEWS_PY_TEMPLATE % ctx


def _build_urls_content(ctx: Dict[str, Any]) -> str:
    """Build the urls.py content."""
    return T.URLS_PY_TEMPLATE % ctx


def _build_list_html_content(ctx: Dict[str, Any]) -> str:
    """Build the list HTML template content."""
    # Build show panel
    show_panel = T.SHOW_PANEL_TEMPLATE % ctx
    list_ctx = dict(ctx, show_panel=show_panel)
    return T.LIST_HTML_TEMPLATE % list_ctx


def _build_test_content(ctx: Dict[str, Any]) -> str:
    """Build the test file content."""
    return T.TEST_TEMPLATE % ctx


def _write(filepath: Path, content: str) -> None:
    """Write content to a file, creating parent dirs if needed."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")
    logger.info("Created %s", filepath)


def _merge_or_write(filepath: Path, content: str, force: bool = False) -> None:
    """
    Write a file, merging with existing content if appropriate.

    For views.py and urls.py, we merge by checking if the target class/pattern
    already exists before appending.
    """
    if filepath.exists() and not force:
        # Read existing and check for conflicts
        existing = filepath.read_text(encoding="utf-8")
        if "class " + content.split("class ")[1].split("(")[0] in existing:
            logger.warning("View class already exists in %s, skipping", filepath)
            return
        # Merge: append new content
        merged = existing.rstrip() + "\n\n" + content
        filepath.write_text(merged, encoding="utf-8")
        logger.info("Merged %s", filepath)
    else:
        _write(filepath, content)
