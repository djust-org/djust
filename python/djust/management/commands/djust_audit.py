"""
Management command for auditing all LiveViews and LiveComponents in a project.

Generates a comprehensive report of every LiveView and LiveComponent: what they
expose, how they're configured, and what decorators protect them.

Usage:
    python manage.py djust_audit                  # pretty terminal output
    python manage.py djust_audit --json           # machine-readable JSON
    python manage.py djust_audit --app myapp      # filter to one Django app
    python manage.py djust_audit --verbose        # include template variable sub-paths
"""

import ast
import inspect
import json
import logging
import textwrap
from typing import Dict

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

# Optional mixins that users explicitly add to their LiveView classes.
# Base LiveView already includes StreamsMixin, StreamingMixin,
# ModelBindingMixin, NavigationMixin — those are not interesting to report.
KNOWN_MIXINS = {
    "PresenceMixin",
    "TenantMixin",
    "TenantScopedMixin",
    "PWAMixin",
    "OfflineMixin",
    "SyncMixin",
    "FormMixin",
}

# Decorator keys stored in _djust_decorators (besides 'event_handler')
_DECORATOR_KEYS = {
    "debounce",
    "throttle",
    "rate_limit",
    "cache",
    "optimistic",
    "client_state",
    "permission_required",
}


def _walk_subclasses(cls):
    """Recursively yield all subclasses of cls."""
    for sub in cls.__subclasses__():
        yield sub
        yield from _walk_subclasses(sub)


def _is_user_class(cls):
    """Return True if cls is a user-defined class (not internal djust framework)."""
    module = getattr(cls, "__module__", "") or ""
    if module.startswith("djust.") or module.startswith("djust_"):
        if "test" not in module and "example" not in module:
            return False
    return True


def _app_label_for_class(cls):
    """Extract the Django app label from a class's module path."""
    module = getattr(cls, "__module__", "") or ""
    # The app label is typically the first component of the module path
    # e.g., "demo_app.views" -> "demo_app"
    parts = module.split(".")
    return parts[0] if parts else ""


def _get_handler_metadata(cls, base_classes=None):
    """Extract event handler metadata from class without instantiating.

    Skips handlers that are defined only on base framework classes (e.g.,
    update_model from ModelBindingMixin) unless overridden by the user class.
    """
    # Collect handler names defined on framework base classes
    base_handler_names = set()
    if base_classes:
        for base in base_classes:
            for name in dir(base):
                if name.startswith("_"):
                    continue
                try:
                    attr = getattr(base, name, None)
                except Exception:
                    continue
                if callable(attr) and hasattr(attr, "_djust_decorators"):
                    if "event_handler" in attr._djust_decorators:
                        base_handler_names.add(name)

    for name in sorted(dir(cls)):
        if name.startswith("_"):
            continue
        # Skip handlers inherited unchanged from framework base
        if name in base_handler_names and name not in cls.__dict__:
            continue
        try:
            attr = getattr(cls, name, None)
        except Exception:
            continue
        if callable(attr) and hasattr(attr, "_djust_decorators"):
            meta = attr._djust_decorators
            if "event_handler" in meta:
                yield name, meta


def _format_handler_params(handler_meta):
    """Format handler parameters as a human-readable signature string."""
    eh = handler_meta.get("event_handler", {})
    params = eh.get("params", [])
    if not params and eh.get("accepts_kwargs"):
        return "**kwargs"
    parts = []
    for p in params:
        s = p["name"]
        ptype = p.get("type")
        if ptype:
            s = "%s: %s" % (s, ptype)
        if not p.get("required", True):
            default = p.get("default")
            if default == "":
                s += ' = ""'
            elif default is None:
                s += " = None"
            else:
                s += " = %s" % repr(default)
        parts.append(s)
    if eh.get("accepts_kwargs"):
        parts.append("**kwargs")
    return ", ".join(parts)


def _format_decorator_tags(handler_meta):
    """Return list of formatted decorator annotations like '@debounce(wait=0.3)'."""
    tags = []
    for key in sorted(_DECORATOR_KEYS):
        val = handler_meta.get(key)
        if val is None:
            continue
        if val is True:
            tags.append("@%s" % key)
        elif isinstance(val, dict):
            inner = ", ".join("%s=%s" % (k, v) for k, v in val.items() if v is not None)
            if inner:
                tags.append("@%s(%s)" % (key, inner))
            else:
                tags.append("@%s" % key)
        else:
            tags.append("@%s(%s)" % (key, val))
    return tags


def _extract_exposed_state(cls):
    """Extract public state attributes set via self.xxx = ... using AST inspection.

    Walks the class MRO (stopping at LiveView/LiveComponent) and scans all
    non-dunder methods for ``self.xxx = ...`` assignments where ``xxx`` does
    not start with ``_``.  These are the attributes that get_context_data()
    will expose to the template context.

    Returns:
        dict mapping attribute name to {"source": method_name, "defined_in": class_qualname}
    """
    assigns = {}  # name → {"source": method_name, "defined_in": qualname}

    for klass in cls.__mro__:
        # Stop at framework base classes — don't parse internals
        if klass.__name__ in ("LiveView", "LiveComponent", "object"):
            break

        for method_name, method in klass.__dict__.items():
            if method_name.startswith("__"):
                continue
            if not callable(method):
                continue

            try:
                source = inspect.getsource(method)
                source = textwrap.dedent(source)
                tree = ast.parse(source)
            except (OSError, TypeError, IndentationError, SyntaxError):
                continue

            for node in ast.walk(tree):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AugAssign):
                    targets = [node.target]

                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                        and not target.attr.startswith("_")
                    ):
                        if target.attr not in assigns:
                            assigns[target.attr] = {
                                "source": method_name,
                                "defined_in": klass.__qualname__,
                            }

    return assigns


def _has_auth_mixin(cls):
    """Check if any class in the MRO provides auth via dispatch() or naming.

    Detects Django-style auth mixins (LoginRequiredMixin, etc.) and
    dispatch()-based patterns like djust-auth's LoginRequiredLiveViewMixin.
    """
    _AUTH_MIXIN_PATTERNS = ("LoginRequired", "PermissionRequired")
    _FRAMEWORK_BASES = frozenset(("LiveView", "LiveComponent", "View", "object"))
    for klass in cls.__mro__:
        if klass.__name__ in _FRAMEWORK_BASES:
            continue
        name = klass.__name__
        if any(pat in name for pat in _AUTH_MIXIN_PATTERNS):
            return True
        # Check for dispatch() override that likely does auth
        if "dispatch" in klass.__dict__ and klass.__name__ != cls.__name__:
            # A mixin that overrides dispatch is likely doing auth
            return True
    return False


def _extract_auth_info(cls):
    """Extract authentication/authorization configuration from a class.

    Returns:
        dict with keys like 'login_required', 'permission_required',
        'custom_check', 'dispatch_mixin'.
    """
    info = {}
    if getattr(cls, "login_required", None):
        info["login_required"] = True
    perm = getattr(cls, "permission_required", None)
    if perm:
        info["permission_required"] = perm if isinstance(perm, list) else [perm]
    # Check if check_permissions is overridden by a user class
    for klass in cls.__mro__:
        if klass.__name__ in ("LiveView", "LiveComponent", "object"):
            break
        if "check_permissions" in klass.__dict__:
            info["custom_check"] = True
            break
    # Check for dispatch-based auth mixins (e.g. LoginRequiredLiveViewMixin)
    if not info and _has_auth_mixin(cls):
        info["dispatch_mixin"] = True
    return info


def _audit_class(cls, cls_type, verbose=False, base_classes=None):
    """Introspect a LiveView or LiveComponent class and return an audit dict."""
    template = getattr(cls, "template_name", None)
    if template is None:
        if getattr(cls, "template", None):
            template = "(inline)"
        else:
            template = "(none)"

    # Detect mixins from MRO
    mixins = [c.__name__ for c in cls.__mro__ if c.__name__ in KNOWN_MIXINS]

    # Gather handler info
    handlers = []
    for name, meta in _get_handler_metadata(cls, base_classes=base_classes):
        eh = meta.get("event_handler", {})
        handler_info = {
            "name": name,
            "params": _format_handler_params(meta),
            "description": eh.get("description", ""),
            "decorators": _format_decorator_tags(meta),
        }
        # Include raw rate_limit info for JSON output
        if "rate_limit" in meta:
            handler_info["rate_limit"] = meta["rate_limit"]
        handlers.append(handler_info)

    # Config flags
    config = {}
    tick = getattr(cls, "tick_interval", None)
    if tick is not None:
        config["tick_interval"] = tick
    temp_assigns = getattr(cls, "temporary_assigns", None)
    if temp_assigns:
        config["temporary_assigns"] = (
            list(temp_assigns.keys()) if isinstance(temp_assigns, dict) else temp_assigns
        )
    if getattr(cls, "use_actors", False):
        config["use_actors"] = True

    # Exposed state (always included — core security info)
    exposed_state = _extract_exposed_state(cls)

    # Template variable sub-paths (optional, requires Rust extension)
    template_vars = None
    if verbose:
        template_vars = _extract_vars(cls)

    # Auth info
    auth = _extract_auth_info(cls)

    result = {
        "class": "%s.%s" % (cls.__module__, cls.__qualname__),
        "type": cls_type,
        "template": template,
        "auth": auth,
        "mixins": mixins,
        "exposed_state": exposed_state,
        "handlers": handlers,
        "config": config,
    }

    if template_vars is not None:
        result["template_vars"] = template_vars

    return result


def _extract_vars(cls):
    """Try to extract template variables using the Rust extension."""
    try:
        from djust._rust import extract_template_variables
    except ImportError:
        return None

    template_name = getattr(cls, "template_name", None)
    if not template_name:
        inline = getattr(cls, "template", None)
        if inline:
            try:
                return extract_template_variables(inline)
            except Exception:
                return None
        return None

    # Try to resolve template file path via Django's template loader
    try:
        from django.template.loader import get_template

        t = get_template(template_name)
        # Django template objects have .origin.name for file path
        if hasattr(t, "origin") and hasattr(t.origin, "name"):
            with open(t.origin.name) as f:
                return extract_template_variables(f.read())
    except Exception:
        pass  # Template resolution may fail (missing file, syntax error) — non-fatal
    return None


class Command(BaseCommand):
    help = "Audit all LiveViews and LiveComponents in this project"

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Output results as JSON (CI-friendly)",
        )
        parser.add_argument(
            "--app",
            type=str,
            dest="app_label",
            help="Only audit views in a specific Django app",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Include template variable sub-paths for exposed state (requires Rust extension)",
        )
        parser.add_argument(
            "--permissions",
            type=str,
            dest="permissions_path",
            help=(
                "Path to a YAML permissions document (e.g. permissions.yaml). "
                "Validates every LiveView's auth config against the document "
                "and reports deviations (#657)."
            ),
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help=(
                "Exit non-zero on any --permissions finding (including "
                "undeclared views). Intended for CI."
            ),
        )
        parser.add_argument(
            "--dump-permissions",
            action="store_true",
            dest="dump_permissions",
            help=(
                "Print a starter permissions.yaml seeded from the discovered "
                "views and exit. Review each entry before committing."
            ),
        )
        parser.add_argument(
            "--live",
            type=str,
            dest="live_url",
            help=(
                "Runtime security-header probe: fetch the given URL and "
                "validate HSTS, CSP, X-Frame-Options, cookies, plus probe "
                "publicly-accessible .git/.env and CSWSH defense (#661). "
                "Exit code 1 on any error finding, 0 otherwise."
            ),
        )
        parser.add_argument(
            "--paths",
            nargs="+",
            dest="live_paths",
            help=(
                "Additional paths or URLs to include in --live mode "
                "(e.g. '/auth/login/' '/api/'). Relative paths are joined "
                "against --live."
            ),
        )
        parser.add_argument(
            "--no-websocket-probe",
            action="store_true",
            dest="no_websocket_probe",
            help="Skip the WebSocket CSWSH probe in --live mode.",
        )
        parser.add_argument(
            "--header",
            action="append",
            dest="live_headers",
            default=[],
            help=(
                "Extra HTTP header to send with --live requests, in "
                "'Name: Value' format. Repeatable. Use for staging "
                "environments behind basic auth or bearer tokens."
            ),
        )
        parser.add_argument(
            "--skip-path-probes",
            action="store_true",
            dest="skip_path_probes",
            help=(
                "Skip the information-disclosure path probes "
                "(/.git/config, /.env, /__debug__/). Useful behind WAFs "
                "that would 403 every request."
            ),
        )

    def handle(self, *args, **options):
        json_output = options.get("json_output", False)
        app_label = options.get("app_label")
        verbose = options.get("verbose", False)
        permissions_path = options.get("permissions_path")
        strict = options.get("strict", False)
        dump_permissions = options.get("dump_permissions", False)
        live_url = options.get("live_url")

        # --live: runtime probe (#661). Mutually informative with other modes
        # but runs independently — no LiveView collection needed.
        if live_url:
            return self._run_live_audit(options)

        audits = self._collect_audits(app_label, verbose)

        # --dump-permissions: print a starter YAML and exit
        if dump_permissions:
            from djust.permissions import (
                PermissionsDocumentError,
                dump_starter_document,
            )

            try:
                self.stdout.write(dump_starter_document(audits))
            except PermissionsDocumentError as exc:
                self.stderr.write(self.style.ERROR(str(exc)))
                raise SystemExit(1) from exc
            return

        # --permissions: validate against a committed permissions document
        findings = []
        if permissions_path:
            findings = self._run_permissions_check(audits, permissions_path)

        if json_output:
            self._output_json(audits, findings=findings)
        else:
            self._output_pretty(audits, findings=findings)

        # --strict: fail CI on any finding
        if strict and findings:
            # ERROR / WARN findings fail; INFO alone does not.
            has_failures = any(f.severity in ("error", "warning") for f in findings)
            if has_failures:
                raise SystemExit(1)

    def _run_live_audit(self, options):
        """Run the --live runtime probe and return the command exit code."""
        from djust.audit_live import run_live_audit

        live_url = options["live_url"]
        paths = options.get("live_paths") or None
        no_ws_probe = options.get("no_websocket_probe", False)
        skip_path_probes = options.get("skip_path_probes", False)
        extra_header_lines = options.get("live_headers") or []
        json_output = options.get("json_output", False)
        strict = options.get("strict", False)

        # Parse repeatable --header 'Name: Value' args
        extra_headers: Dict[str, str] = {}
        for line in extra_header_lines:
            if ":" not in line:
                self.stderr.write(
                    self.style.ERROR(f"--header value must be 'Name: Value' — got {line!r}")
                )
                raise SystemExit(2)
            name, _, value = line.partition(":")
            extra_headers[name.strip()] = value.strip()

        report = run_live_audit(
            target=live_url,
            paths=paths,
            extra_headers=extra_headers or None,
            probe_websocket=not no_ws_probe,
            skip_path_probes=skip_path_probes,
        )

        if json_output:
            self.stdout.write(json.dumps(report.to_dict(), indent=2))
        else:
            self._output_live_pretty(report)

        # Exit code: strict mode fails on warnings too
        if strict and (report.errors or report.warnings):
            raise SystemExit(1)
        if report.errors:
            raise SystemExit(1)
        return

    def _output_live_pretty(self, report):
        """Pretty-print a LiveAuditReport to the terminal."""
        line = "=" * 50
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(line))
        self.stdout.write(self.style.MIGRATE_HEADING("  djust_audit --live runtime probe"))
        self.stdout.write(self.style.MIGRATE_HEADING(line))
        self.stdout.write(f"  Target:  {report.target}")
        self.stdout.write(f"  Pages:   {report.pages_fetched} fetched")
        self.stdout.write("")

        errors = [f for f in report.findings if f.severity == "error"]
        warnings_ = [f for f in report.findings if f.severity == "warning"]
        infos = [f for f in report.findings if f.severity == "info"]

        if errors:
            self.stdout.write(self.style.ERROR("  ERRORS:"))
            for f in errors:
                self.stdout.write(self.style.ERROR("  " + f.format_line()))
            self.stdout.write("")
        if warnings_:
            self.stdout.write(self.style.WARNING("  WARNINGS:"))
            for f in warnings_:
                self.stdout.write(self.style.WARNING("  " + f.format_line()))
            self.stdout.write("")
        if infos:
            self.stdout.write("  INFO:")
            for f in infos:
                self.stdout.write("  " + f.format_line())
            self.stdout.write("")

        if not report.findings:
            self.stdout.write(
                self.style.SUCCESS("  No findings — target passes all runtime checks.")
            )

        self.stdout.write(self.style.MIGRATE_HEADING("-" * 50))
        self.stdout.write(
            f"  Summary: {report.errors} error(s), "
            f"{report.warnings} warning(s), {report.infos} info"
        )
        self.stdout.write("")

    def _run_permissions_check(self, audits, path):
        """Load a permissions document and compare it against the audits."""
        from djust.permissions import PermissionsDocument, PermissionsDocumentError

        try:
            doc = PermissionsDocument.load(path)
        except (FileNotFoundError, PermissionsDocumentError) as exc:
            self.stderr.write(self.style.ERROR(f"permissions document error: {exc}"))
            raise SystemExit(2) from exc

        # Build {dotted_name: auth_info} map from the audits
        actual = {a["class"]: (a.get("auth") or {}) for a in audits}
        return doc.compare_all(actual)

    def _collect_audits(self, app_label, verbose):
        """Discover and audit all LiveView and LiveComponent subclasses."""
        audits = []

        # Discover LiveViews
        try:
            from djust.live_view import LiveView

            for cls in _walk_subclasses(LiveView):
                if not _is_user_class(cls):
                    continue
                if app_label and _app_label_for_class(cls) != app_label:
                    continue
                audits.append(_audit_class(cls, "LiveView", verbose, base_classes=[LiveView]))
        except ImportError:
            pass  # LiveView not available (Rust extension not built)

        # Discover LiveComponents
        try:
            from djust.components.base import LiveComponent

            for cls in _walk_subclasses(LiveComponent):
                if not _is_user_class(cls):
                    continue
                if app_label and _app_label_for_class(cls) != app_label:
                    continue
                audits.append(
                    _audit_class(cls, "LiveComponent", verbose, base_classes=[LiveComponent])
                )
        except ImportError:
            pass  # LiveComponent not available (optional module)

        return audits

    def _output_json(self, audits, findings=None):
        """Output audit results as JSON."""
        view_count = sum(1 for a in audits if a["type"] == "LiveView")
        component_count = sum(1 for a in audits if a["type"] == "LiveComponent")
        handler_count = sum(len(a["handlers"]) for a in audits)

        unprotected = sum(1 for a in audits if not a.get("auth") and a.get("exposed_state"))

        output = {
            "audits": audits,
            "summary": {
                "views": view_count,
                "components": component_count,
                "handlers": handler_count,
                "unprotected_with_state": unprotected,
            },
        }
        if findings:
            output["permissions_findings"] = [f.to_dict() for f in findings]
            output["summary"]["permissions_errors"] = sum(
                1 for f in findings if f.severity == "error"
            )
            output["summary"]["permissions_warnings"] = sum(
                1 for f in findings if f.severity == "warning"
            )
        self.stdout.write(json.dumps(output, indent=2))

    def _output_pretty(self, audits, findings=None):
        """Output audit results with formatted terminal display."""
        if not audits:
            self.stdout.write(self.style.SUCCESS("No LiveViews or LiveComponents found."))
            return

        # Header
        self.stdout.write("")
        line = "=" * 50
        self.stdout.write(self.style.MIGRATE_HEADING(line))
        self.stdout.write(self.style.MIGRATE_HEADING("  djust audit — Project Report"))
        self.stdout.write(self.style.MIGRATE_HEADING(line))

        # Group by app
        by_app = {}
        for audit in audits:
            app = audit["class"].split(".")[0]
            by_app.setdefault(app, []).append(audit)

        total_views = 0
        total_components = 0
        total_handlers = 0

        for app_name in sorted(by_app.keys()):
            app_audits = by_app[app_name]
            views = [a for a in app_audits if a["type"] == "LiveView"]
            components = [a for a in app_audits if a["type"] == "LiveComponent"]
            total_views += len(views)
            total_components += len(components)

            parts = []
            if views:
                parts.append("%d view%s" % (len(views), "s" if len(views) != 1 else ""))
            if components:
                parts.append(
                    "%d component%s" % (len(components), "s" if len(components) != 1 else "")
                )

            self.stdout.write("")
            self.stdout.write(
                self.style.MIGRATE_LABEL("App: %s (%s)" % (app_name, ", ".join(parts)))
            )
            self.stdout.write(self.style.MIGRATE_HEADING("-" * 50))

            for audit in app_audits:
                self.stdout.write("")
                self.stdout.write(
                    "  %s: %s"
                    % (
                        self.style.HTTP_INFO(audit["type"]),
                        self.style.SUCCESS(audit["class"]),
                    )
                )
                self.stdout.write("    Template:   %s" % audit["template"])

                # Auth info
                auth = audit.get("auth", {})
                if auth:
                    parts = []
                    if auth.get("login_required"):
                        parts.append("login_required")
                    if auth.get("permission_required"):
                        perms = auth["permission_required"]
                        parts.append("permission: %s" % ", ".join(perms))
                    if auth.get("custom_check"):
                        parts.append("custom check_permissions()")
                    if auth.get("dispatch_mixin"):
                        parts.append("dispatch-based mixin")
                    self.stdout.write("    Auth:       %s" % ", ".join(parts))
                else:
                    # Warn if view exposes state without auth
                    exposed = audit.get("exposed_state", {})
                    if exposed:
                        self.stdout.write(
                            "    Auth:       %s"
                            % self.style.WARNING("(none)  \u26a0 exposes state without auth")
                        )
                    else:
                        self.stdout.write("    Auth:       (none)")

                if audit["mixins"]:
                    self.stdout.write("    Mixins:     %s" % ", ".join(audit["mixins"]))
                else:
                    self.stdout.write("    Mixins:     (none)")

                # Config flags
                config = audit["config"]
                if config.get("tick_interval"):
                    self.stdout.write("    Tick:       %dms" % config["tick_interval"])
                if config.get("temporary_assigns"):
                    self.stdout.write(
                        "    Temp assigns: %s"
                        % ", ".join(str(t) for t in config["temporary_assigns"])
                    )
                if config.get("use_actors"):
                    self.stdout.write("    Actors:     enabled")

                # Exposed state
                exposed = audit.get("exposed_state", {})
                template_vars = audit.get("template_vars", {}) or {}
                if exposed:
                    self.stdout.write("    Exposed state:")
                    for attr_name in sorted(exposed.keys()):
                        info = exposed[attr_name]
                        source = info["source"]
                        sub_paths = template_vars.get(attr_name, [])
                        if sub_paths:
                            self.stdout.write(
                                "      %-24s (%s)  %s %s"
                                % (
                                    attr_name,
                                    source,
                                    self.style.NOTICE("→"),
                                    ", ".join(sub_paths),
                                )
                            )
                        else:
                            self.stdout.write("      %-24s (%s)" % (attr_name, source))
                else:
                    self.stdout.write("    Exposed state: (none)")

                # Handlers
                handlers = audit["handlers"]
                if handlers:
                    self.stdout.write("    Handlers:")
                    for h in handlers:
                        total_handlers += 1
                        sig = "%s(%s)" % (h["name"], h["params"])
                        dec_str = "  ".join(h["decorators"])
                        if dec_str:
                            self.stdout.write(
                                "      %s %-40s %s"
                                % (
                                    self.style.WARNING("*"),
                                    sig,
                                    self.style.NOTICE(dec_str),
                                )
                            )
                        else:
                            self.stdout.write("      %s %s" % (self.style.WARNING("*"), sig))
                        if h.get("description"):
                            # Show only the first line of multi-line docstrings
                            desc = h["description"].strip().split("\n")[0]
                            self.stdout.write("        %s" % desc)
                else:
                    self.stdout.write("    Handlers:   (none)")

        # Permissions findings (#657)
        if findings:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING("-" * 50))
            self.stdout.write(self.style.MIGRATE_HEADING("  Permissions document findings"))
            self.stdout.write(self.style.MIGRATE_HEADING("-" * 50))
            errors = [f for f in findings if f.severity == "error"]
            warnings = [f for f in findings if f.severity == "warning"]
            infos = [f for f in findings if f.severity == "info"]
            for f in errors:
                self.stdout.write(self.style.ERROR(f.format_line()))
            for f in warnings:
                self.stdout.write(self.style.WARNING(f.format_line()))
            for f in infos:
                self.stdout.write(f.format_line())
            self.stdout.write("")
            self.stdout.write(
                "  %d error%s, %d warning%s, %d info"
                % (
                    len(errors),
                    "s" if len(errors) != 1 else "",
                    len(warnings),
                    "s" if len(warnings) != 1 else "",
                    len(infos),
                )
            )

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("-" * 50))
        self.stdout.write(
            "  Summary: %d view%s, %d component%s, %d handler%s"
            % (
                total_views,
                "s" if total_views != 1 else "",
                total_components,
                "s" if total_components != 1 else "",
                total_handlers,
                "s" if total_handlers != 1 else "",
            )
        )
        self.stdout.write("")
