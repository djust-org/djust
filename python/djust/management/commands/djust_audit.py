"""
Management command for auditing all LiveViews and LiveComponents in a project.

Generates a comprehensive report of every LiveView and LiveComponent: what they
expose, how they're configured, and what decorators protect them.

Usage:
    python manage.py djust_audit                  # pretty terminal output
    python manage.py djust_audit --json           # machine-readable JSON
    python manage.py djust_audit --app myapp      # filter to one Django app
    python manage.py djust_audit --verbose        # include template variables
"""

import json
import logging

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

    result = {
        "class": "%s.%s" % (cls.__module__, cls.__qualname__),
        "type": cls_type,
        "template": template,
        "mixins": mixins,
        "handlers": handlers,
        "config": config,
    }

    # Template variables (optional, requires Rust extension)
    if verbose:
        result["template_variables"] = _extract_vars(cls)

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
        pass
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
            help="Include template variable analysis (requires Rust extension)",
        )

    def handle(self, *args, **options):
        json_output = options.get("json_output", False)
        app_label = options.get("app_label")
        verbose = options.get("verbose", False)

        audits = self._collect_audits(app_label, verbose)

        if json_output:
            self._output_json(audits)
        else:
            self._output_pretty(audits)

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
            pass

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
            pass

        return audits

    def _output_json(self, audits):
        """Output audit results as JSON."""
        view_count = sum(1 for a in audits if a["type"] == "LiveView")
        component_count = sum(1 for a in audits if a["type"] == "LiveComponent")
        handler_count = sum(len(a["handlers"]) for a in audits)

        output = {
            "audits": audits,
            "summary": {
                "views": view_count,
                "components": component_count,
                "handlers": handler_count,
            },
        }
        self.stdout.write(json.dumps(output, indent=2))

    def _output_pretty(self, audits):
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

                # Template variables (verbose)
                if "template_variables" in audit and audit["template_variables"]:
                    self.stdout.write("    Template variables:")
                    for var, paths in sorted(audit["template_variables"].items()):
                        if paths:
                            self.stdout.write("      %s → %s" % (var, ", ".join(paths)))
                        else:
                            self.stdout.write("      %s" % var)

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
