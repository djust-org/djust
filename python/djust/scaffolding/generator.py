"""
Project generator for ``djust new``.

Generates a complete, runnable djust project directory from templates,
with optional feature flags for auth, database models, presence, and streaming.
"""

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import templates as T

logger = logging.getLogger(__name__)

# Django field type mapping for --from-schema
_SCHEMA_FIELD_MAP = {
    "string": "models.CharField(max_length=%(max_length)s)",
    "text": "models.TextField(%(blank)s)",
    "integer": "models.IntegerField(%(default)s)",
    "float": "models.FloatField(%(default)s)",
    "decimal": "models.DecimalField(max_digits=%(max_digits)s, decimal_places=%(decimal_places)s)",
    "boolean": "models.BooleanField(default=%(default)s)",
    "date": "models.DateField(%(null)s)",
    "datetime": "models.DateTimeField(%(null)s)",
    "email": "models.EmailField(%(blank)s)",
    "url": "models.URLField(%(blank)s)",
    "slug": "models.SlugField(unique=True)",
    "foreignkey": "models.ForeignKey(%(related_model)s, on_delete=models.CASCADE)",
}


def generate_project(
    app_name: str,
    target_dir: Optional[str] = None,
    with_auth: bool = False,
    with_db: bool = False,
    with_presence: bool = False,
    with_streaming: bool = False,
    from_schema: Optional[str] = None,
    auto_setup: bool = True,
) -> Path:
    """
    Generate a complete djust project.

    Args:
        app_name: Python-valid name for the project (e.g., ``myapp``).
        target_dir: Parent directory to create project in. Defaults to cwd.
        with_auth: Include login/logout views and auth configuration.
        with_db: Include Django models, admin, and database-backed views.
        with_presence: Include PresenceMixin for online user tracking.
        with_streaming: Include StreamingMixin for live feed updates.
        from_schema: Path to a JSON schema file describing models.
        auto_setup: Run venv creation, install, and migrate after generation.

    Returns:
        Path to the generated project directory.

    Raises:
        ValueError: If app_name is invalid or directory already exists.
    """
    # Validate app_name
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", app_name):
        raise ValueError(
            "'%s' is not a valid Python identifier. Use letters, numbers, and underscores."
            % app_name
        )

    # Determine target
    if target_dir is None:
        target_dir = os.getcwd()
    project_dir = Path(target_dir) / app_name

    if project_dir.exists():
        raise ValueError("Directory '%s' already exists." % project_dir)

    # If --from-schema, load and validate it. Also implies --with-db.
    schema = None
    if from_schema:
        schema = _load_schema(from_schema)
        with_db = True

    # Build context
    ctx = _build_context(
        app_name,
        with_auth=with_auth,
        with_db=with_db,
        with_presence=with_presence,
        with_streaming=with_streaming,
        schema=schema,
    )

    # Create project structure
    _create_project_files(project_dir, app_name, ctx)

    # Auto-setup
    if auto_setup:
        _run_auto_setup(project_dir)

    return project_dir


def _build_context(
    app_name: str,
    with_auth: bool = False,
    with_db: bool = False,
    with_presence: bool = False,
    with_streaming: bool = False,
    schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the template rendering context from feature flags."""
    display_name = app_name.replace("_", " ").replace("-", " ").title()
    app_class = app_name.replace("_", " ").title().replace(" ", "")
    view_class = app_class + "View"

    # Determine view bases
    bases = ["LiveView"]
    extra_imports = []
    extra_mount = ""
    extra_context = ""
    extra_methods = ""
    extra_data = ""

    if with_presence:
        bases.insert(0, "PresenceMixin")
        extra_imports.append("from djust.presence import PresenceMixin")
        extra_context += '            "presence_list": self.presence_list,\n'
        extra_context += '            "presence_count": len(self.presence_list),\n'

    if with_streaming:
        bases.insert(0, "StreamingMixin")
        extra_imports.append("from djust.streaming import StreamingMixin")
        extra_mount += "        self.stream('feed', [])\n"
        extra_methods += """
    @event_handler()
    def send_message(self, message: str = "", **kwargs):
        \"\"\"Push a message to the live feed stream.\"\"\"
        if message.strip():
            import datetime
            ts = datetime.datetime.now().strftime("%%H:%%M:%%S")
            self.stream_insert("feed", "[%%s] %%s" %% (ts, message.strip()))
"""

    view_bases = ", ".join(bases)
    extra_imports_str = "\n".join(extra_imports) + ("\n" if extra_imports else "")

    # Extra settings
    extra_settings = ""
    if with_auth:
        extra_settings += T.AUTH_SETTINGS_EXTRA

    # Extra urls
    extra_urls = ""
    if with_auth:
        extra_urls = T.AUTH_URLS_EXTRA % {"app_name": app_name}

    # Nav extras
    nav_extra = ""
    if with_auth:
        nav_extra = T.AUTH_NAV_EXTRA

    # Template extras
    template_extra = ""
    if with_presence:
        template_extra += T.PRESENCE_TEMPLATE_EXTRA
    if with_streaming:
        template_extra += T.STREAMING_TEMPLATE_EXTRA

    ctx = {
        "app_name": app_name,
        "app_class": app_class,
        "display_name": display_name,
        "view_class": view_class,
        "view_bases": view_bases,
        "secret_key": secrets.token_urlsafe(50),
        "extra_imports": extra_imports_str,
        "extra_mount": extra_mount,
        "extra_context": extra_context,
        "extra_methods": extra_methods,
        "extra_data": extra_data,
        "extra_settings": extra_settings,
        "extra_urls": extra_urls,
        "nav_extra": nav_extra,
        "template_extra": template_extra,
        "with_auth": with_auth,
        "with_db": with_db,
        "with_presence": with_presence,
        "with_streaming": with_streaming,
        "schema": schema,
    }
    return ctx


def _create_project_files(project_dir: Path, app_name: str, ctx: Dict[str, Any]) -> None:
    """Write all project files to disk."""
    pkg_dir = project_dir / app_name
    tpl_dir = pkg_dir / "templates" / app_name

    # Create directories
    project_dir.mkdir(parents=True)
    pkg_dir.mkdir()
    tpl_dir.mkdir(parents=True)
    (project_dir / "templates").mkdir(exist_ok=True)

    # manage.py
    _write(project_dir / "manage.py", T.MANAGE_PY % ctx)
    os.chmod(project_dir / "manage.py", 0o755)  # nosec B103 — manage.py must be executable

    # App package __init__.py
    _write(pkg_dir / "__init__.py", T.INIT_PY)

    # apps.py
    _write(pkg_dir / "apps.py", T.APPS_PY % ctx)

    # settings.py
    _write(pkg_dir / "settings.py", T.SETTINGS_PY % ctx)

    # asgi.py
    _write(pkg_dir / "asgi.py", T.ASGI_PY % ctx)

    # wsgi.py
    _write(pkg_dir / "wsgi.py", T.WSGI_PY % ctx)

    # urls.py
    _write(pkg_dir / "urls.py", T.URLS_PY % ctx)

    # Makefile
    _write(project_dir / "Makefile", T.MAKEFILE % ctx)

    # requirements.txt
    _write(project_dir / "requirements.txt", T.REQUIREMENTS_TXT)

    # .gitignore
    _write(project_dir / ".gitignore", T.GITIGNORE)

    # base.html
    _write(tpl_dir / "base.html", T.BASE_HTML % ctx)

    # Decide views/templates based on --from-schema vs feature flags
    schema = ctx.get("schema")
    if schema:
        _create_schema_files(project_dir, pkg_dir, tpl_dir, app_name, ctx, schema)
    else:
        # Standard views.py
        if ctx["with_db"]:
            _create_db_views(pkg_dir, tpl_dir, app_name, ctx)
        else:
            # In-memory views
            _write(pkg_dir / "views.py", T.VIEWS_PY_BASE % ctx)
            _write(tpl_dir / "index.html", T.INDEX_HTML % ctx)

    # Feature: --with-db (models + admin)
    if ctx["with_db"] and not schema:
        _write(pkg_dir / "models.py", T.MODELS_PY % ctx)
        _write(pkg_dir / "admin.py", T.ADMIN_PY % ctx)

    # Feature: --with-auth (login template)
    if ctx["with_auth"]:
        _write(tpl_dir / "login.html", T.LOGIN_HTML % ctx)


def _create_db_views(pkg_dir: Path, tpl_dir: Path, app_name: str, ctx: Dict[str, Any]) -> None:
    """Create views.py with database-backed CRUD instead of in-memory lists."""
    # Build a DB-backed version of views.py by composing the base template
    # with overridden methods
    db_ctx = dict(ctx)
    db_ctx["extra_imports"] = ctx["extra_imports"] + T.DB_VIEWS_EXTRA_IMPORTS

    # For DB-backed views, we construct a custom views.py
    view_bases = ctx["view_bases"]
    content = '"""LiveView for %s."""\n\n' % app_name
    content += "from djust import LiveView\n"
    content += "from djust.decorators import event_handler\n"
    if ctx["extra_imports"].strip():
        for line in ctx["extra_imports"].strip().split("\n"):
            content += line + "\n"
    content += "from .models import Item\n"
    content += "\n\n"
    content += "class %s(%s):\n" % (ctx["view_class"], view_bases)
    content += '    template_name = "%s/index.html"\n' % app_name
    content += "\n"
    content += "    def mount(self, request, **kwargs):\n"
    content += '        self.search_query = ""\n'
    if ctx["extra_mount"]:
        content += ctx["extra_mount"]
    content += "\n"
    content += T.DB_VIEWS_COMPUTE_OVERRIDE
    content += "\n"
    content += "    @event_handler()\n"
    content += '    def search(self, value: str = "", **kwargs):\n'
    content += '        """Filter items by search query."""\n'
    content += "        self.search_query = value\n"
    content += "        self._compute()\n"
    content += "\n"
    content += T.DB_VIEWS_ADD_ITEM_OVERRIDE
    content += "\n"
    content += T.DB_VIEWS_TOGGLE_OVERRIDE
    content += "\n"
    content += T.DB_VIEWS_DELETE_OVERRIDE
    content += "\n"
    content += "    def get_context_data(self, **kwargs):\n"
    content += "        self._compute()\n"
    content += "        return {\n"
    content += '            "items": self.items,\n'
    content += '            "search_query": self.search_query,\n'
    content += '            "total_count": self.total_count,\n'
    content += '            "done_count": self.done_count,\n'
    if ctx["extra_context"]:
        content += ctx["extra_context"]
    content += "        }\n"
    if ctx["extra_methods"]:
        content += ctx["extra_methods"]

    _write(pkg_dir / "views.py", content)
    _write(tpl_dir / "index.html", T.INDEX_HTML % ctx)


def _create_schema_files(
    project_dir: Path,
    pkg_dir: Path,
    tpl_dir: Path,
    app_name: str,
    ctx: Dict[str, Any],
    schema: Dict[str, Any],
) -> None:
    """Generate models, views, templates, and admin from a JSON schema."""
    models = schema.get("models", [])
    if not models:
        raise ValueError("Schema must have at least one model in 'models' array.")

    display_name = ctx["display_name"]

    # --- models.py ---
    model_classes = ""
    admin_imports = []
    admin_registrations = []
    view_classes_code = []
    urls_imports = []
    urls_patterns = []
    model_imports = []

    for model_def in models:
        model_name = model_def["name"]
        fields = model_def.get("fields", [])
        model_imports.append(model_name)

        # Build field definitions
        field_lines = []
        search_fields = []
        list_display_fields = []
        str_field = None
        first_char_field = None

        for field in fields:
            fname = field["name"]
            ftype = field.get("type", "string").lower()
            field_def = _schema_field_to_django(fname, ftype, field)
            field_lines.append("    %s = %s" % (fname, field_def))

            # Track fields for admin and str
            if ftype in ("string", "text", "email", "url"):
                search_fields.append(fname)
                if first_char_field is None:
                    first_char_field = fname
            if len(list_display_fields) < 5:
                list_display_fields.append(fname)

        if not str_field:
            str_field = "self.%s" % first_char_field if first_char_field else "str(self.pk)"

        field_definitions = "\n".join(field_lines) if field_lines else "    pass"

        model_classes += T.SCHEMA_MODEL_CLASS % {
            "model_name": model_name,
            "field_definitions": field_definitions,
            "str_field": str_field,
        }

        # Admin registration
        admin_imports.append("from .models import %s" % model_name)
        admin_reg = "@admin.register(%s)\n" % model_name
        admin_reg += "class %sAdmin(admin.ModelAdmin):\n" % model_name
        admin_reg += "    list_display = %s\n" % repr(list_display_fields or ["__str__"])
        if search_fields:
            admin_reg += "    search_fields = %s\n" % repr(search_fields[:3])
        admin_registrations.append(admin_reg)

        # View class
        model_display = model_name.replace("_", " ") + "s"
        model_display_singular = model_name.replace("_", " ")
        view_cls_name = model_name + "ListView"
        template_name = "%s_list.html" % model_name.lower()

        urls_imports.append(view_cls_name)
        urls_patterns.append(
            '    path("%s/", %s.as_view(), name="%s_list"),'
            % (model_name.lower() + "s", view_cls_name, model_name.lower())
        )

        # Build view mount, compute, handlers
        mount_body = '        self.search_query = ""\n'
        compute_body = "        qs = %s.objects.all()\n" % model_name
        compute_body += "        if self.search_query:\n"
        if search_fields:
            filter_kwarg = "%s__icontains" % search_fields[0]
            compute_body += "            qs = qs.filter(%s=self.search_query)\n" % filter_kwarg
        else:
            compute_body += "            pass  # No text fields to search\n"
        compute_body += "        self.items = [\n"
        compute_body += '            {"id": obj.pk, %s}\n' % ", ".join(
            '"%s": obj.%s' % (f["name"], f["name"]) for f in fields
        )
        compute_body += "            for obj in qs\n"
        compute_body += "        ]\n"

        context_body = '            "items": self.items,\n'
        context_body += '            "search_query": self.search_query,\n'

        # Handlers: search, create, delete
        handler_methods = "    @event_handler()\n"
        handler_methods += '    def search(self, value: str = "", **kwargs):\n'
        handler_methods += '        """Search %s."""\n' % model_display.lower()
        handler_methods += "        self.search_query = value\n"
        handler_methods += "        self._compute()\n\n"

        # Create handler
        create_params = ", ".join(
            "%s: str = ''" % f["name"]
            for f in fields
            if f.get("type", "string").lower() in ("string", "text", "email", "url")
        )
        if not create_params:
            create_params = ""
        handler_methods += "    @event_handler()\n"
        handler_methods += "    def create(self, %s, **kwargs):\n" % create_params
        handler_methods += '        """Create a new %s."""\n' % model_display_singular.lower()
        # Build create kwargs
        create_fields = [
            f
            for f in fields
            if f.get("type", "string").lower() in ("string", "text", "email", "url")
        ]
        if create_fields:
            first_field = create_fields[0]["name"]
            handler_methods += "        if %s and %s.strip():\n" % (first_field, first_field)
            handler_methods += "            %s.objects.create(\n" % model_name
            for cf in create_fields:
                handler_methods += "                %s=%s.strip(),\n" % (cf["name"], cf["name"])
            handler_methods += "            )\n"
        else:
            handler_methods += "        %s.objects.create()\n" % model_name
        handler_methods += "        self._compute()\n\n"

        # Delete handler
        handler_methods += "    @event_handler()\n"
        handler_methods += "    def delete(self, item_id: int = 0, **kwargs):\n"
        handler_methods += '        """Delete a %s."""\n' % model_display_singular.lower()
        handler_methods += "        %s.objects.filter(pk=item_id).delete()\n" % model_name
        handler_methods += "        self._compute()\n\n"

        view_classes_code.append(
            T.SCHEMA_VIEW_CLASS
            % {
                "view_class": view_cls_name,
                "app_name": app_name,
                "template_name": template_name,
                "mount_body": mount_body,
                "compute_body": compute_body,
                "handler_methods": handler_methods,
                "context_body": context_body,
            }
        )

        # Template for this model
        form_fields_html = ""
        for f in fields:
            ftype = f.get("type", "string").lower()
            if ftype in ("string", "text", "email", "url"):
                input_type = "email" if ftype == "email" else "url" if ftype == "url" else "text"
                form_fields_html += "        <div>\n"
                form_fields_html += '            <input type="%s" name="%s" placeholder="%s"\n' % (
                    input_type,
                    f["name"],
                    f["name"].replace("_", " ").title(),
                )
                form_fields_html += '                   class="w-full px-3 py-2 rounded-lg bg-surface-800 border border-white/10 text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500">\n'
                form_fields_html += "        </div>\n"

        list_item_fields = ""
        for f in fields[:4]:  # Show first 4 fields
            list_item_fields += (
                '                <span class="text-gray-200">{{ item.%s }}</span>\n' % f["name"]
            )

        _write(
            tpl_dir / template_name,
            T.SCHEMA_LIST_HTML
            % {
                "app_name": app_name,
                "display_name": display_name,
                "view_class": view_cls_name,
                "model_display": model_display,
                "model_display_lower": model_display.lower(),
                "model_display_singular": model_display_singular,
                "model_display_singular_lower": model_display_singular.lower(),
                "form_fields_html": form_fields_html,
                "list_item_fields": list_item_fields,
            },
        )

    # Write models.py
    _write(
        pkg_dir / "models.py",
        T.SCHEMA_MODELS_PY
        % {
            "app_name": app_name,
            "model_classes": model_classes,
        },
    )

    # Write admin.py
    _write(
        pkg_dir / "admin.py",
        T.SCHEMA_ADMIN_PY
        % {
            "admin_imports": "\n".join(admin_imports),
            "admin_registrations": "\n\n".join(admin_registrations),
        },
    )

    # Write views.py
    schema_model_imports = "\n".join("from .models import %s" % m for m in model_imports)
    _write(
        pkg_dir / "views.py",
        T.SCHEMA_VIEWS_PY
        % {
            "app_name": app_name,
            "schema_model_imports": schema_model_imports,
            "schema_view_classes": "\n\n".join(view_classes_code),
        },
    )

    # Update urls.py for schema — overwrite with multi-view routing
    first_view = urls_imports[0] if urls_imports else ctx["view_class"]
    urls_content = '"""URL configuration for %s project."""\n\n' % app_name
    urls_content += "from django.contrib import admin\n"
    urls_content += "from django.urls import path\n\n"
    urls_content += "from .views import %s\n\n" % ", ".join(urls_imports)
    urls_content += "urlpatterns = [\n"
    urls_content += '    path("admin/", admin.site.urls),\n'
    urls_content += '    path("", %s.as_view(), name="index"),\n' % first_view
    for pat in urls_patterns:
        urls_content += pat + "\n"
    urls_content += "]\n"
    _write(pkg_dir / "urls.py", urls_content)

    # Update LIVEVIEW_ALLOWED_MODULES (already handled by settings template)


def _schema_field_to_django(field_name: str, field_type: str, field_def: Dict[str, Any]) -> str:
    """Convert a schema field definition to a Django model field string."""
    ftype = field_type.lower()

    if ftype == "string":
        max_length = field_def.get("max_length", 200)
        blank = ", blank=True" if field_def.get("blank") else ""
        return "models.CharField(max_length=%d%s)" % (max_length, blank)
    elif ftype == "text":
        blank = "blank=True" if field_def.get("blank", True) else ""
        return "models.TextField(%s)" % blank
    elif ftype == "integer":
        default = field_def.get("default")
        default_str = ", default=%s" % repr(default) if default is not None else ""
        return "models.IntegerField(%s)" % default_str.lstrip(", ")
    elif ftype == "float":
        default = field_def.get("default")
        default_str = "default=%s" % repr(default) if default is not None else ""
        return "models.FloatField(%s)" % default_str
    elif ftype == "decimal":
        max_digits = field_def.get("max_digits", 10)
        decimal_places = field_def.get("decimal_places", 2)
        return "models.DecimalField(max_digits=%d, decimal_places=%d)" % (
            max_digits,
            decimal_places,
        )
    elif ftype == "boolean":
        default = field_def.get("default", False)
        return "models.BooleanField(default=%s)" % repr(default)
    elif ftype == "date":
        null = "null=True, blank=True" if field_def.get("null", False) else ""
        return "models.DateField(%s)" % null
    elif ftype == "datetime":
        null = "null=True, blank=True" if field_def.get("null", False) else ""
        return "models.DateTimeField(%s)" % null
    elif ftype == "email":
        blank = "blank=True" if field_def.get("blank") else ""
        return "models.EmailField(%s)" % blank
    elif ftype == "url":
        blank = "blank=True" if field_def.get("blank") else ""
        return "models.URLField(%s)" % blank
    elif ftype == "slug":
        return "models.SlugField(unique=True)"
    elif ftype == "foreignkey":
        related = field_def.get("related_model", "self")
        return 'models.ForeignKey("%s", on_delete=models.CASCADE)' % related
    else:
        # Default to CharField
        return "models.CharField(max_length=200)"


def _load_schema(schema_path: str) -> Dict[str, Any]:
    """Load and validate a JSON schema file."""
    path = Path(schema_path)
    if not path.exists():
        raise ValueError("Schema file not found: %s" % schema_path)

    with open(path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    # Basic validation
    if not isinstance(schema, dict):
        raise ValueError("Schema must be a JSON object with a 'models' array.")
    if "models" not in schema:
        raise ValueError("Schema must contain a 'models' key with an array of model definitions.")
    for model in schema["models"]:
        if "name" not in model:
            raise ValueError("Each model must have a 'name' key.")
        if not re.match(r"^[A-Z][a-zA-Z0-9]*$", model["name"]):
            raise ValueError(
                "Model name '%s' must be CamelCase (start with uppercase)." % model["name"]
            )

    return schema


def _run_auto_setup(project_dir: Path) -> None:
    """Create venv, install deps, and run migrate in the generated project."""
    # Check for uv
    uv_available = shutil.which("uv") is not None

    print("\n--- Setting up project ---\n")

    # Create virtualenv
    if uv_available:
        print("Creating virtualenv (uv)...")
        _run_cmd(["uv", "venv"], cwd=project_dir)
    else:
        print("Creating virtualenv (python -m venv)...")
        _run_cmd([sys.executable, "-m", "venv", ".venv"], cwd=project_dir)

    # Install dependencies
    if uv_available:
        print("Installing dependencies (uv)...")
        _run_cmd(["uv", "pip", "install", "-r", "requirements.txt"], cwd=project_dir)
    else:
        venv_pip = str(project_dir / ".venv" / "bin" / "pip")
        print("Installing dependencies (pip)...")
        _run_cmd([venv_pip, "install", "-r", "requirements.txt"], cwd=project_dir)

    # Run migrate
    venv_python = str(project_dir / ".venv" / "bin" / "python")
    print("Running migrations...")
    _run_cmd([venv_python, "manage.py", "migrate", "--run-syncdb"], cwd=project_dir)

    print("\nDone!")


def _run_cmd(cmd: List[str], cwd: Path) -> None:
    """Run a subprocess command, printing output on failure."""
    try:
        subprocess.run(
            cmd,
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        logger.warning(
            "Command failed: %s\nstdout: %s\nstderr: %s",
            " ".join(cmd),
            e.stdout,
            e.stderr,
        )
        print("  Warning: '%s' failed (non-fatal)" % " ".join(cmd[:3]))
    except FileNotFoundError:
        print("  Warning: '%s' not found (skipping)" % cmd[0])


def _write(filepath: Path, content: str) -> None:
    """Write content to a file, creating parent dirs if needed."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")
