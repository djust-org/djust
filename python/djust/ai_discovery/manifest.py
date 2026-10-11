"""Settings-free capabilities document sharing the AI context renderers."""

from typing import Any, cast

MANIFEST_VERSION = 1


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
        cat = d.get("category")
        if not isinstance(cat, str) or not cat:
            cat = "other"
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


def manifest_sections(framework: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    from djust.ai_discovery.agents import DISCOVERY_COMMANDS, format_discovery_block
    from djust.ai_discovery.catalog import categories
    from djust.schema import BEST_PRACTICES, get_framework_schema
    from djust.theming._builtin_presets import THEME_PRESETS

    if framework is None:
        framework = get_framework_schema()
    components = [
        "## UI components — use these before writing markup\n",
        "{% load djust_components %}\n",
    ]
    for _, label, entries in categories():
        components.append(f"### {label}\n")
        for entry in entries:
            name = "{% " + entry.name + " %}" if entry.kind == "tag" else entry.name
            kind = " (Python class)" if entry.kind == "class" else ""
            components.append(f"- `{name}`{kind} — {entry.purpose}")
            if entry.children:
                components.append(
                    "  (children: "
                    + ", ".join("`{% " + child + " %}`" for child in entry.children)
                    + ")"
                )
        components.append("")
    theming = (
        '## Theming\n\nAdd "djust.theming" to INSTALLED_APPS.\n'
        'Configure LIVEVIEW_CONFIG["theme"] with "theme", "preset" and "default_mode".\n'
        "In the base template: `{% load theme_tags %}` and `{% theme_head %}`.\n"
        f"{len(THEME_PRESETS)} built-in presets; list them with "
        "`python manage.py djust_theme list-presets`.\n"
    )
    pitfalls = ["## Common pitfalls\n"]
    common_pitfalls = cast(list[dict[str, Any]], BEST_PRACTICES["common_pitfalls"])
    pitfalls.extend(f"- **{item['problem']}** — {item['solution']}" for item in common_pitfalls)
    return [
        (
            "discovery",
            "## Discovery commands\n\n" + format_discovery_block(DISCOVERY_COMMANDS) + "\n",
        ),
        ("components", "\n".join(components)),
        ("theming", theming),
        ("directives", _section_directives(framework)),
        ("lifecycle", _section_lifecycle(framework)),
        ("decorators", _section_decorators(framework)),
        ("conventions", _section_conventions(framework)),
        ("security", _section_security()),
        ("state", _section_state()),
        ("pitfalls", "\n".join(pitfalls)),
    ]


def render_manifest(framework: dict[str, Any] | None = None) -> str:
    from djust import __version__

    return (
        f"# djust {__version__} — capabilities manifest\n\n"
        "Discover bundled UI components and framework capabilities before building.\n\n"
        + "\n".join(section for _, section in manifest_sections(framework))
    )
