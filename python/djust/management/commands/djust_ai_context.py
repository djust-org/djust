"""
Management command to generate AI assistant context files for a djust project.

Generates project-aware documentation files for AI coding assistants:
- CLAUDE.md for Claude Code
- .cursorrules for Cursor
- .github/copilot-instructions.md for GitHub Copilot

Each file includes: framework directives, project views with handlers,
template bindings, and available components.

Usage:
    python manage.py djust_ai_context --format claude    # -> CLAUDE.md
    python manage.py djust_ai_context --format cursor    # -> .cursorrules
    python manage.py djust_ai_context --format copilot   # -> .github/copilot-instructions.md
    python manage.py djust_ai_context --format claude --output /path/to/file
    python manage.py djust_ai_context --format claude --force   # replace an existing file

An existing target file is never overwritten unless ``--force`` is passed.
"""

import contextlib
import os
import shutil
import tempfile
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser


class Command(BaseCommand):
    help = "Generate AI assistant context files for this djust project"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--format",
            type=str,
            choices=["claude", "cursor", "copilot"],
            default="claude",
            help="Output format: claude (CLAUDE.md), cursor (.cursorrules), copilot (.github/copilot-instructions.md)",
        )
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Custom output path. Defaults to format-specific location.",
        )
        parser.add_argument(
            "--print",
            action="store_true",
            dest="print_stdout",
            help="Print to stdout instead of writing to file",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Replace the target file if it already exists (it is refused otherwise)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from djust.schema import get_framework_schema, get_project_schema

        fmt = options["format"]
        output_path = options.get("output")
        to_stdout = options.get("print_stdout", False)

        framework = get_framework_schema()
        project = get_project_schema()

        content = _generate_content(framework, project, fmt)

        if to_stdout:
            self.stdout.write(content)
            return

        if output_path is None:
            output_path = _default_path(fmt)

        # Ensure parent directory exists
        parent = os.path.dirname(output_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

        # The target is often a hand-written file (a CLAUDE.md with project
        # rules), and the generated one replaces it whole: refuse (#3297).
        # Mode "x" makes the existence check and the create one atomic step.
        # The text is UTF-8 whatever the locale (it carries an em dash, and an
        # ASCII default encoding failed AFTER the file was created), and a
        # failed write must not leave a half-written file behind (#3312).
        if options.get("force", False):
            _replace_existing(output_path, content)
        else:
            try:
                _write_new(output_path, content)
            except FileExistsError:
                raise CommandError(
                    "%s already exists and was not written; the generated file would replace it "
                    "entirely. Rerun with --force to overwrite it, --output to write elsewhere, "
                    "or --print to see the generated text." % output_path
                ) from None

        self.stdout.write(self.style.SUCCESS("Wrote %s" % output_path))


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _replace_existing(path: str, content: str) -> None:
    """Replace ``path`` with ``content`` so that a failure keeps what was there.

    The text is written to a uniquely named file beside the REAL target (a
    symlinked ``AGENTS.md -> CLAUDE.md`` is written through, as ``open(path, "w")``
    did, instead of being replaced by a regular file) and renamed over it. The
    original's permission bits and, where allowed, owner are carried over, so a
    0600 file stays 0600.
    """
    real = os.path.realpath(path)
    if not os.path.exists(real):
        _write_new(real, content)
        return
    original = os.stat(real)
    fd, scratch = tempfile.mkstemp(
        dir=os.path.dirname(real) or ".", prefix=".%s." % os.path.basename(real), suffix=".tmp"
    )
    os.close(fd)
    try:
        with open(scratch, "w", encoding="utf-8") as f:
            f.write(content)
        shutil.copymode(real, scratch)
        with contextlib.suppress(OSError):  # chown needs privilege; the mode is what matters
            os.chown(scratch, original.st_uid, original.st_gid)
        os.replace(scratch, real)
    except BaseException:
        _discard(scratch)
        raise


def _write_new(path: str, content: str) -> None:
    """Create ``path`` (it must not exist) holding ``content``, as UTF-8.

    Raises ``FileExistsError`` if it exists. Anything that goes wrong after the
    file was created removes it again, so the rerun is not refused as "already
    exists" by a 0-byte or truncated leftover.
    """
    try:
        with open(path, "x", encoding="utf-8") as f:
            f.write(content)
    except FileExistsError:
        raise  # not ours: someone else's file, left alone
    except BaseException:
        _discard(path)
        raise


def _default_path(fmt: str) -> str:
    """Return the default output path for the given format."""
    if fmt == "claude":
        return "CLAUDE.md"
    elif fmt == "cursor":
        return ".cursorrules"
    elif fmt == "copilot":
        return os.path.join(".github", "copilot-instructions.md")
    return "CLAUDE.md"


def _generate_content(framework: dict, project: dict, fmt: str) -> str:
    """Generate the full AI context document."""
    sections = []

    # Header
    if fmt == "claude":
        sections.append("# CLAUDE.md — djust Project Context\n")
        sections.append("Auto-generated by `python manage.py djust_ai_context --format claude`.\n")
    elif fmt == "cursor":
        sections.append("# .cursorrules — djust Project Context\n")
        sections.append("Auto-generated by `python manage.py djust_ai_context --format cursor`.\n")
    elif fmt == "copilot":
        sections.append("# Copilot Instructions — djust Project Context\n")
        sections.append("Auto-generated by `python manage.py djust_ai_context --format copilot`.\n")

    sections.append(
        "Regenerate it with the same command after upgrading djust or adding views. The command "
        "refuses to overwrite an existing file unless you pass `--force`, so keep your own rules "
        "in a separate file.\n"
    )

    sections.append(
        "This project uses the **djust** framework (v%s) — "
        "a hybrid Python/Rust framework bringing Phoenix LiveView-style "
        "reactive server-side rendering to Django.\n" % framework.get("version", "?")
    )

    # Framework quick reference
    sections.append(_section_directives(framework))
    sections.append(_section_lifecycle(framework))
    sections.append(_section_decorators(framework))
    sections.append(_section_conventions(framework))
    sections.append(_section_security())
    sections.append(_section_state())

    # Project-specific
    if project.get("views") or project.get("components"):
        sections.append(_section_project_views(project))

    if project.get("routes"):
        sections.append(_section_routes(project))

    return "\n".join(sections)


#: Categories rendered in full (description, modifiers, fenced example), in this
#: order. Any other category in `DIRECTIVES` is emitted after them (see
#: `_category_order`) in a compact two-line form, so a directive added to the
#: schema reaches the generated files without anyone editing this list (#3353).
_FULL_CATEGORIES = (
    "event",
    "binding",
    "dom",
    "loading",
    "client",
    "modifier",
    "hooks",
    "navigation",
    "streaming",
    "upload",
)


def _category_order(categories: Any) -> list[str]:
    """`_FULL_CATEGORIES` that are present, then every other category by name."""
    present = set(categories)
    full = [c for c in _FULL_CATEGORIES if c in present]
    return full + sorted(present.difference(_FULL_CATEGORIES))


def _section_directives(framework: dict) -> str:
    """Generate the template directives reference section.

    Every category present in the schema is emitted: the full-format ones in
    `_FULL_CATEGORIES` order, the rest compactly, alphabetically.
    """
    lines = ["## Template Directives\n"]

    by_category: dict[str, list[Any]] = {}
    for d in framework.get("directives", []):
        cat = d.get("category", "other")
        by_category.setdefault(cat, []).append(d)

    for cat in _category_order(by_category):
        lines.append("### %s\n" % cat.title())
        compact = cat not in _FULL_CATEGORIES
        for d in by_category[cat]:
            lines.append("- **`%s`** = `%s`" % (d["name"], d.get("value", "")))
            lines.append("  %s" % d.get("description", ""))
            if d.get("modifiers"):
                lines.append("  Modifiers: %s" % ", ".join(".%s" % m for m in d["modifiers"]))
            if d.get("example"):
                if compact and not {"`", "\n"} & set(d["example"]):
                    lines.append("  Example: `%s`" % d["example"])
                else:
                    lines.append("  ```html\n  %s\n  ```" % d["example"])
            if not compact:
                lines.append("")
        if compact:
            lines.append("")

    # Data attribute types
    lines.append("### Data Attribute Type Coercion\n")
    for suffix, desc in framework.get("data_attribute_types", {}).items():
        lines.append("- `data-name:%s` — %s" % (suffix, desc))
    lines.append("")

    return "\n".join(lines)


def _section_lifecycle(framework: dict) -> str:
    """Generate the lifecycle methods section."""
    lines = ["## Lifecycle Methods\n"]

    for method in framework.get("lifecycle_methods", []):
        lines.append("- **`%s`** — %s" % (method["name"], method["description"]))
        lines.append("  ```python\n  %s\n  ```" % method["signature"])

    lines.append("")

    # Navigation methods
    lines.append("### Server-Side Navigation\n")
    for m in framework.get("navigation_methods", []):
        lines.append("- `%s` — %s" % (m["signature"], m["description"]))

    # Stream methods
    lines.append("\n### Streams API\n")
    for m in framework.get("stream_methods", []):
        lines.append("- `%s` — %s" % (m["signature"], m["description"]))

    # Push events
    lines.append("\n### Push Events\n")
    for m in framework.get("push_event_methods", []):
        lines.append("- `%s` — %s" % (m["signature"], m["description"]))

    lines.append("")
    return "\n".join(lines)


def _section_decorators(framework: dict) -> str:
    """Generate the decorators section."""
    lines = ["## Decorators\n"]

    for dec in framework.get("decorators", []):
        lines.append("### `%s`\n" % dec["name"])
        lines.append("%s\n" % dec["description"])
        lines.append("`%s`\n" % dec["import"])
        if dec.get("params"):
            lines.append("Parameters:")
            for pname, pdesc in dec["params"].items():
                lines.append("- `%s`: %s" % (pname, pdesc))
            lines.append("")
        if dec.get("usage"):
            lines.append("```python")
            lines.append(dec["usage"][0])
            lines.append("```\n")

    return "\n".join(lines)


def _section_conventions(framework: dict) -> str:
    """Generate the conventions section."""
    lines = ["## Conventions\n"]

    for key, conv in framework.get("conventions", {}).items():
        lines.append("### %s\n" % key.replace("_", " ").title())
        lines.append("%s\n" % conv["description"])
        if "examples" in conv:
            for example, desc in conv["examples"].items():
                lines.append("- `%s` — %s" % (example, desc))
            lines.append("")

    return "\n".join(lines)


# Kept as one constant, apart from the schema-driven sections above, so the
# guidance can be pinned by a test and reviewed as a unit (#3292). Every name in
# it is documented in docs/website/guides/BEST_PRACTICES.md and
# docs/website/guides/djust-audit.md; add nothing here that those do not say.
_SECURITY_SECTION = """\
## Security

These practices catch common mistakes. They do not make generated code secure on their own: review it.

- **Never mark user-supplied content safe.** Do not use `{{ value|safe }}`, `{% autoescape off %}` or `mark_safe()` on anything a user can influence; `{{ value }}` is auto-escaped. If rich text is required, sanitise it with an allow-list sanitiser first. Build server-side HTML with `format_html()`, and pass values into JavaScript with `json.dumps()`.
- **Gate who can open a view.** Set `login_required = True` or `permission_required = "app.codename"` on the view (or use `LoginRequiredMixin` / `PermissionRequiredMixin`, imported with `from djust import LoginRequiredMixin, PermissionRequiredMixin`). A view with neither is public.
- **Re-check permissions in event handlers that change data.** Auth runs when the view mounts, not on every event. Stack `@permission_required("app.codename")` under `@event_handler`, check object ownership and raise `PermissionDenied` when it fails, and validate every handler argument: it comes from the browser.
- **Include `{% csrf_token %}`** in every form.
- **Do not call `login()` in an event handler.** It rotates the session key, and a WebSocket cannot set the cookie that carries the new one, so the browser stays anonymous. Sign in through an HTTP login view, the accounts pages djust ships (`{% url 'djust_auth:login' %}`), or a LiveView form whose `action` is the real login URL, with `dj-submit` and `dj-trigger-action`, where the handler validates and then calls `self.trigger_submit("#form-id")` so the browser posts the form natively.
- **Before finishing, run `python manage.py check` and `python manage.py djust_audit --ast`** and fix what they report. `djust_audit --ast` is a heuristic scan, not a proof: it flags patterns such as `|safe` on a template variable (X006), `{% autoescape off %}` (X007), `mark_safe()` around an interpolated string (X005), event handlers that write to the database with no permission check (X002), object lookups by URL parameter that are not scoped to the user (X001), detail views with no object-permission override (X008), SQL built by string formatting (X003) and open redirects (X004).
- In production, set `LIVEVIEW_ALLOWED_MODULES` to the modules that hold your views **and `"djust"`**, for example `["myapp.views", "djust"]`. It limits which views a client may mount over the WebSocket; an explicit list replaces the default, and without `"djust"` in it djust's own LiveViews (component and theme galleries, admin extensions) stop mounting. Keep `ALLOWED_HOSTS` specific: the WebSocket handshake's `Origin` header is checked against it.
"""


def _section_security() -> str:
    """Generate the security guidance section."""
    return _SECURITY_SECTION


# Documented in docs/website/api-reference/liveview.md (get_context_data) and
# docs/website/guides/http-only-mode.md (what the page-POST fallback keeps).
_STATE_SECTION = """\
## State and `get_context_data()`

- **Set state in `mount()`, and always call `super().get_context_data(**kwargs)`** in an override, adding to its result instead of replacing it. On the HTTP page-POST fallback a view on the default state policy is rebuilt from the dict `get_context_data()` returned on the previous request, so an attribute your override leaves out is unset when the next event handler runs. The same events can work over the WebSocket and fail on the fallback.
"""


def _section_state() -> str:
    """Generate the state and get_context_data guidance section."""
    return _STATE_SECTION


def _section_project_views(project: dict) -> str:
    """Generate the project views and components section."""
    lines = ["## Project Views\n"]

    for view in project.get("views", []):
        lines.append("### `%s`\n" % view["class"])
        if view.get("template"):
            lines.append("Template: `%s`" % view["template"])
        if view.get("mixins"):
            lines.append("Mixins: %s" % ", ".join(view["mixins"]))
        if view.get("auth"):
            auth_parts = []
            if view["auth"].get("login_required"):
                auth_parts.append("login_required")
            if view["auth"].get("permission_required"):
                auth_parts.append("permission: %s" % ", ".join(view["auth"]["permission_required"]))
            if auth_parts:
                lines.append("Auth: %s" % ", ".join(auth_parts))

        # Handlers
        if view.get("handlers"):
            lines.append("\nHandlers:")
            for h in view["handlers"]:
                params_str = ", ".join(
                    "%s: %s" % (p["name"], p.get("type", "Any")) for p in h.get("params", [])
                )
                dec_tags = []
                for dec_name, dec_val in h.get("decorators", {}).items():
                    if dec_val is True:
                        dec_tags.append("@%s" % dec_name)
                    else:
                        dec_tags.append("@%s" % dec_name)
                dec_str = " ".join(dec_tags)
                line = "- `%s(%s)`" % (h["name"], params_str)
                if dec_str:
                    line += "  %s" % dec_str
                if h.get("description"):
                    desc = h["description"].strip().split("\n")[0]
                    line += " — %s" % desc
                lines.append(line)

        # Exposed state
        if view.get("exposed_state"):
            lines.append(
                "\nTemplate variables: %s"
                % ", ".join("`%s`" % k for k in sorted(view["exposed_state"].keys()))
            )

        lines.append("")

    # Components
    if project.get("components"):
        lines.append("## Project Components\n")
        for comp in project["components"]:
            lines.append("### `%s`\n" % comp["class"])
            if comp.get("template"):
                lines.append("Template: `%s`" % comp["template"])
            if comp.get("handlers"):
                lines.append("Handlers: %s" % ", ".join(h["name"] for h in comp["handlers"]))
            lines.append("")

    return "\n".join(lines)


def _section_routes(project: dict) -> str:
    """Generate the URL routes section."""
    lines = ["## URL Routes\n"]

    for route in project.get("routes", []):
        name = route.get("name", "")
        name_str = " (name='%s')" % name if name else ""
        lines.append("- `%s` -> `%s`%s" % (route["pattern"], route["view"], name_str))

    lines.append("")
    return "\n".join(lines)
