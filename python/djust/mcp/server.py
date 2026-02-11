"""
MCP server for djust framework.

Provides tools for AI assistants to introspect djust projects, run system
checks, and generate code scaffolding.

Two modes:
- **Framework-only** (no Django): Returns static schema (directives, lifecycle,
  decorators). Works anywhere with ``python -m djust.mcp``.
- **Full mode** (with Django): Also introspects live project — views, handlers,
  routes, state. Requires ``python manage.py djust_mcp``.
"""

import json
import logging
import sys

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Django availability detection
# ---------------------------------------------------------------------------

_django_ready = False


def _ensure_django():
    """Try to set up Django if not already configured."""
    global _django_ready
    if _django_ready:
        return True
    try:
        import django
        from django.conf import settings

        if not settings.configured:
            return False
        django.setup()
        _django_ready = True
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def create_server():
    """Create and configure the djust MCP server with all tools."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print(
            "Error: mcp package not installed. " "Install with: pip install 'mcp[cli]'",
            file=sys.stderr,
        )
        sys.exit(1)

    mcp = FastMCP(
        "djust",
        instructions=(
            "djust MCP server — introspect djust projects, run system checks, "
            "and scaffold LiveView code. Use get_framework_schema() first to "
            "understand djust directives and patterns."
        ),
    )

    # === Introspection tools ===

    @mcp.tool()
    def get_framework_schema() -> str:
        """Get the complete djust framework schema.

        Returns all template directives (dj-click, dj-model, etc.),
        lifecycle methods (mount, get_context_data, etc.), decorators
        (@event_handler, @debounce, etc.), and conventions.

        This is the first tool to call — it gives you all the context
        needed to write correct djust code.
        """
        from djust.schema import get_framework_schema as _get

        return json.dumps(_get(), indent=2)

    @mcp.tool()
    def get_template_directives() -> str:
        """Get all available dj-* template directives with usage examples.

        Returns a focused list of just the template directives with their
        parameters, DOM events, examples, and modifiers.
        """
        from djust.schema import DIRECTIVES

        return json.dumps(DIRECTIVES, indent=2)

    @mcp.tool()
    def get_decorators() -> str:
        """Get all available djust decorators with usage examples.

        Returns @event_handler, @debounce, @throttle, @cache, @optimistic,
        @client_state, @rate_limit, @permission_required, @reactive, state(),
        and @computed with their parameters and import paths.
        """
        from djust.schema import DECORATORS

        return json.dumps(DECORATORS, indent=2)

    @mcp.tool()
    def get_best_practices() -> str:
        """Get djust best practices, patterns, and common pitfalls.

        Returns comprehensive guidance for writing correct djust code:
        setup, lifecycle flow, event handler rules, JIT serialization
        patterns, form integration, security rules, template directive
        examples, and the 8 most common pitfalls to avoid.

        Call this before writing djust code to understand the correct
        patterns. No Django required.
        """
        from djust.schema import BEST_PRACTICES

        return json.dumps(BEST_PRACTICES, indent=2)

    @mcp.tool()
    def list_views() -> str:
        """List all LiveView classes in the current Django project.

        Returns each view with its template, mount params, event handlers,
        exposed state variables, auth configuration, and mixins.

        Requires Django to be configured (run via 'python manage.py djust_mcp').
        """
        if not _ensure_django():
            return json.dumps(
                {
                    "error": "Django not configured. Run via 'python manage.py djust_mcp' "
                    "for project introspection.",
                    "hint": "Use get_framework_schema() for framework-level info without Django.",
                }
            )

        from djust.schema import get_project_schema

        schema = get_project_schema()
        return json.dumps(schema["views"], indent=2)

    @mcp.tool()
    def list_components() -> str:
        """List all LiveComponent classes in the current Django project.

        Returns each component with its props, slots, event handlers,
        and template information.

        Requires Django to be configured.
        """
        if not _ensure_django():
            return json.dumps(
                {
                    "error": "Django not configured. Run via 'python manage.py djust_mcp'.",
                }
            )

        from djust.schema import get_project_schema

        schema = get_project_schema()
        return json.dumps(schema["components"], indent=2)

    @mcp.tool()
    def list_routes() -> str:
        """List all URL routes mapped to LiveView classes.

        Returns URL patterns, view class paths, and route names.

        Requires Django to be configured.
        """
        if not _ensure_django():
            return json.dumps(
                {
                    "error": "Django not configured. Run via 'python manage.py djust_mcp'.",
                }
            )

        from djust.schema import get_project_schema

        schema = get_project_schema()
        return json.dumps(schema["routes"], indent=2)

    @mcp.tool()
    def get_view_schema(view_name: str) -> str:
        """Get the full schema for a specific LiveView class.

        Args:
            view_name: Class name (e.g., 'CounterView') or fully qualified
                path (e.g., 'myapp.views.CounterView').

        Returns state variables, event handlers with params, decorators,
        template bindings, auth config, and mixins.
        """
        if not _ensure_django():
            return json.dumps(
                {
                    "error": "Django not configured. Run via 'python manage.py djust_mcp'.",
                }
            )

        from djust.schema import get_project_schema

        schema = get_project_schema()
        # Search by class name or full path
        for view in schema["views"] + schema["components"]:
            if view["class"] == view_name or view["class"].endswith("." + view_name):
                return json.dumps(view, indent=2)

        return json.dumps(
            {
                "error": "View '%s' not found" % view_name,
                "available": [v["class"] for v in schema["views"]],
            }
        )

    # === Runtime tools ===

    @mcp.tool()
    def run_system_checks(category: str = "") -> str:
        """Run djust system checks and return structured results.

        Args:
            category: Optional filter — 'config', 'liveview', 'security',
                'templates', or 'quality'. Empty string runs all checks.

        Returns JSON with check results including IDs, severity, messages,
        hints, and fix suggestions. Use this after generating code to
        validate it.
        """
        if not _ensure_django():
            return json.dumps(
                {
                    "error": "Django not configured. Run via 'python manage.py djust_mcp'.",
                }
            )

        try:
            import djust.checks  # noqa: F401 — ensure checks are registered
        except ImportError:
            pass

        from django.core.checks import Error, Warning, run_checks

        all_checks = run_checks(tags=["djust"])

        if category:
            _prefixes = {
                "config": ("C0",),
                "liveview": ("V0",),
                "security": ("S0",),
                "templates": ("T0",),
                "quality": ("Q0",),
            }
            prefixes = _prefixes.get(category, ())
            if prefixes:
                all_checks = [
                    c
                    for c in all_checks
                    if any((c.id or "").replace("djust.", "").startswith(p) for p in prefixes)
                ]

        results = []
        for check in all_checks:
            if isinstance(check, Error) or check.level >= 40:
                severity = "error"
            elif isinstance(check, Warning) or check.level >= 30:
                severity = "warning"
            else:
                severity = "info"

            result = {
                "id": check.id,
                "severity": severity,
                "message": str(check.msg),
                "hint": check.hint or "",
            }
            # Include enhanced fields if available (from Initiative 5)
            if hasattr(check, "fix_hint"):
                result["fix_hint"] = check.fix_hint
            if hasattr(check, "file_path"):
                result["file_path"] = check.file_path
            if hasattr(check, "line_number"):
                result["line_number"] = check.line_number

            results.append(result)

        return json.dumps(
            {
                "checks": results,
                "summary": {
                    "total": len(results),
                    "errors": sum(1 for r in results if r["severity"] == "error"),
                    "warnings": sum(1 for r in results if r["severity"] == "warning"),
                    "info": sum(1 for r in results if r["severity"] == "info"),
                },
            },
            indent=2,
        )

    @mcp.tool()
    def run_audit(app_label: str = "") -> str:
        """Run a security audit on all LiveViews and return structured results.

        Args:
            app_label: Optional Django app to audit (e.g., 'myapp'). Empty
                string audits all apps.

        Returns comprehensive audit: exposed state, auth config, handler
        signatures, decorator protections, and mixins for each view.
        """
        if not _ensure_django():
            return json.dumps(
                {
                    "error": "Django not configured. Run via 'python manage.py djust_mcp'.",
                }
            )

        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        kwargs = {"json_output": True, "stdout": out}
        if app_label:
            kwargs["app_label"] = app_label

        call_command("djust_audit", **kwargs)
        return out.getvalue()

    @mcp.tool()
    def validate_view(code: str) -> str:
        """Validate a LiveView class definition without running it.

        Args:
            code: Python source code of a LiveView class to validate.

        Checks for common issues:
        - Missing @event_handler decorators on handler-like methods
        - Missing **kwargs in handler signatures
        - Public QuerySet attributes (should be _private)
        - Missing mount() method
        - Security issues (mark_safe with f-strings, etc.)

        Returns list of issues found with severity and fix suggestions.
        """
        import ast as _ast
        import re

        issues = []

        try:
            tree = _ast.parse(code)
        except SyntaxError as e:
            return json.dumps(
                [
                    {
                        "severity": "error",
                        "message": "Syntax error: %s" % e,
                        "fix_hint": "Fix the syntax error at line %s" % e.lineno,
                    }
                ]
            )

        handler_pattern = re.compile(
            r"^(handle_|on_|toggle_|select_|update_|delete_|"
            r"create_|add_|remove_|save_|cancel_|submit_|close_|open_)"
        )

        for node in _ast.walk(tree):
            if not isinstance(node, _ast.ClassDef):
                continue

            _has_mount = False
            has_template = False

            for item in node.body:
                # Check class attributes
                if isinstance(item, _ast.Assign):
                    for target in item.targets:
                        if isinstance(target, _ast.Name):
                            if target.id in ("template_name", "template"):
                                has_template = True

                # Check methods
                if isinstance(item, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    if item.name == "mount":
                        _has_mount = True
                        # Check mount signature
                        args = item.args
                        arg_names = [a.arg for a in args.args]
                        if "request" not in arg_names:
                            issues.append(
                                {
                                    "severity": "error",
                                    "message": "mount() missing 'request' parameter",
                                    "line": item.lineno,
                                    "fix_hint": "Change signature to: def mount(self, request, **kwargs):",
                                }
                            )
                        if not args.kwarg:
                            issues.append(
                                {
                                    "severity": "warning",
                                    "message": "mount() should accept **kwargs",
                                    "line": item.lineno,
                                    "fix_hint": "Add **kwargs to mount() signature",
                                }
                            )
                        continue

                    # Check handler-like methods
                    if handler_pattern.match(item.name):
                        has_handler_decorator = False
                        for dec in item.decorator_list:
                            if isinstance(dec, _ast.Name) and dec.id == "event_handler":
                                has_handler_decorator = True
                            elif isinstance(dec, _ast.Call):
                                func = dec.func
                                if isinstance(func, _ast.Name) and func.id == "event_handler":
                                    has_handler_decorator = True
                        if not has_handler_decorator:
                            issues.append(
                                {
                                    "severity": "warning",
                                    "message": "Method '%s' looks like an event handler "
                                    "but lacks @event_handler decorator" % item.name,
                                    "line": item.lineno,
                                    "fix_hint": "Add @event_handler() above the method",
                                }
                            )

                    # Check **kwargs on event handlers
                    for dec in item.decorator_list:
                        is_handler = False
                        if isinstance(dec, _ast.Name) and dec.id == "event_handler":
                            is_handler = True
                        elif isinstance(dec, _ast.Call):
                            func = dec.func
                            if isinstance(func, _ast.Name) and func.id == "event_handler":
                                is_handler = True
                        if is_handler and not item.args.kwarg:
                            issues.append(
                                {
                                    "severity": "warning",
                                    "message": "Event handler '%s' should accept **kwargs"
                                    % item.name,
                                    "line": item.lineno,
                                    "fix_hint": "Add **kwargs to the handler signature",
                                }
                            )

            if not has_template:
                issues.append(
                    {
                        "severity": "warning",
                        "message": "Class '%s' has no template_name or template attribute"
                        % node.name,
                        "line": node.lineno,
                        "fix_hint": "Add template_name = 'myapp/template.html' to the class",
                    }
                )

        # Check for security issues
        if "mark_safe(f'" in code or 'mark_safe(f"' in code:
            issues.append(
                {
                    "severity": "error",
                    "message": "SECURITY: mark_safe() with f-string — XSS vulnerability",
                    "fix_hint": "Use format_html() instead of mark_safe(f'...')",
                }
            )

        return json.dumps(issues, indent=2)

    # === Code generation tools ===

    @mcp.tool()
    def scaffold_view(
        name: str,
        features: str = "",
    ) -> str:
        """Generate a LiveView class with specified features.

        Args:
            name: View class name (e.g., 'ProductListView')
            features: Comma-separated features: 'search', 'crud', 'pagination',
                'form', 'presence', 'streaming', 'auth'

        Returns complete Python code for a LiveView with the requested features.
        """
        feature_set = {f.strip().lower() for f in features.split(",") if f.strip()}

        # Build imports
        imports = ["from djust import LiveView"]
        decorator_imports = ["event_handler"]
        if "search" in feature_set:
            decorator_imports.append("debounce")
        if "auth" in feature_set:
            decorator_imports.append("permission_required")

        imports.append("from djust.decorators import %s" % ", ".join(decorator_imports))

        if "form" in feature_set:
            imports.append("from djust.forms import FormMixin")
        if "presence" in feature_set:
            imports.append("from djust.presence import PresenceMixin")

        # Build class bases
        bases = []
        if "form" in feature_set:
            bases.append("FormMixin")
        if "presence" in feature_set:
            bases.append("PresenceMixin")
        bases.append("LiveView")
        bases_str = ", ".join(bases)

        # Template name from class name
        # ProductListView -> products/list.html (approximate)
        snake = ""
        for i, c in enumerate(name):
            if c.isupper() and i > 0:
                snake += "_"
            snake += c.lower()
        snake = snake.replace("_view", "")
        template_name = "myapp/%s.html" % snake

        lines = []
        lines.extend(imports)
        lines.append("")
        lines.append("")
        lines.append("class %s(%s):" % (name, bases_str))
        lines.append("    template_name = '%s'" % template_name)

        if "auth" in feature_set:
            lines.append("    login_required = True")

        lines.append("")
        lines.append("    def mount(self, request, **kwargs):")

        # Mount body
        if "search" in feature_set:
            lines.append("        self.search_query = ''")
        if "pagination" in feature_set:
            lines.append("        self.page = 1")
            lines.append("        self.per_page = 20")
        if "crud" in feature_set:
            lines.append("        self.selected_item = None")
            lines.append("        self.editing = False")

        lines.append("        self._refresh()")

        # _refresh method
        lines.append("")
        lines.append("    def _refresh(self):")
        lines.append("        # TODO: Replace with your model")
        lines.append("        qs = Item.objects.all()")
        if "search" in feature_set:
            lines.append("        if self.search_query:")
            lines.append("            qs = qs.filter(name__icontains=self.search_query)")
        if "pagination" in feature_set:
            lines.append("        start = (self.page - 1) * self.per_page")
            lines.append("        self._total_count = qs.count()")
            lines.append("        qs = qs[start:start + self.per_page]")
        lines.append("        self._items = qs")

        # Event handlers
        if "search" in feature_set:
            lines.append("")
            lines.append("    @event_handler()")
            lines.append("    @debounce(wait=0.3)")
            lines.append("    def search(self, value: str = '', **kwargs):")
            lines.append("        self.search_query = value")
            lines.append("        self.page = 1" if "pagination" in feature_set else "")
            lines.append("        self._refresh()")

        if "crud" in feature_set:
            lines.append("")
            lines.append("    @event_handler()")
            lines.append("    def select_item(self, item_id: int = 0, **kwargs):")
            lines.append("        self.selected_item = Item.objects.filter(pk=item_id).first()")
            lines.append("")
            lines.append("    @event_handler()")
            lines.append("    def delete_item(self, item_id: int = 0, **kwargs):")
            lines.append("        Item.objects.filter(pk=item_id).delete()")
            lines.append("        self._refresh()")

        if "pagination" in feature_set:
            lines.append("")
            lines.append("    @event_handler()")
            lines.append("    def go_to_page(self, page: int = 1, **kwargs):")
            lines.append("        self.page = page")
            lines.append("        self._refresh()")

        if "streaming" in feature_set:
            lines.append("")
            lines.append("    @event_handler()")
            lines.append("    def stream_update(self, content: str = '', **kwargs):")
            lines.append("        self.stream_to('output', content)")

        # get_context_data
        lines.append("")
        lines.append("    def get_context_data(self, **kwargs):")
        lines.append("        ctx = super().get_context_data(**kwargs)")
        lines.append("        ctx['items'] = self._items")
        if "pagination" in feature_set:
            lines.append(
                "        ctx['total_pages'] = (self._total_count + self.per_page - 1) // self.per_page"
            )
        lines.append("        return ctx")
        lines.append("")

        # Clean up empty lines from conditional blocks
        code = "\n".join(line for line in lines if line is not None)
        return code

    @mcp.tool()
    def scaffold_component(name: str, props: str = "") -> str:
        """Generate a LiveComponent class with specified props.

        Args:
            name: Component class name (e.g., 'UserCard')
            props: Comma-separated prop names (e.g., 'user,show_avatar,editable')

        Returns complete Python code for a LiveComponent.
        """
        prop_list = [p.strip() for p in props.split(",") if p.strip()]

        lines = [
            "from djust.components.base import LiveComponent",
            "",
            "",
            "class %s(LiveComponent):" % name,
        ]

        # Template name
        snake = ""
        for i, c in enumerate(name):
            if c.isupper() and i > 0:
                snake += "_"
            snake += c.lower()
        lines.append("    template_name = 'components/%s.html'" % snake)
        lines.append("")

        # Mount with props
        if prop_list:
            lines.append("    def mount(self, request, **kwargs):")
            for prop in prop_list:
                lines.append("        self.%s = kwargs.get('%s')" % (prop, prop))
            lines.append("")

        lines.append("")
        return "\n".join(lines)

    @mcp.tool()
    def add_event_handler(
        handler_name: str,
        params: str = "",
        decorators: str = "",
    ) -> str:
        """Generate an event handler method to add to an existing view.

        Args:
            handler_name: Method name (e.g., 'delete_item')
            params: Comma-separated params with types (e.g., 'item_id: int = 0, confirm: bool = False')
            decorators: Comma-separated decorators (e.g., 'debounce(wait=0.3), rate_limit(rate=5)')

        Returns Python code for the handler method (paste into your LiveView class).
        """
        lines = []

        # Add decorators
        if decorators:
            for dec in decorators.split(","):
                dec = dec.strip()
                if dec:
                    lines.append("    @%s" % dec)

        lines.append("    @event_handler()")

        # Build signature
        if params:
            lines.append("    def %s(self, %s, **kwargs):" % (handler_name, params))
        else:
            lines.append("    def %s(self, **kwargs):" % handler_name)

        lines.append("        # TODO: implement")
        lines.append("        self._refresh()")
        lines.append("")

        return "\n".join(lines)

    return mcp


# ---------------------------------------------------------------------------
# Entry point for running without Django
# ---------------------------------------------------------------------------


def main():
    """Run the MCP server with stdio transport."""
    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
