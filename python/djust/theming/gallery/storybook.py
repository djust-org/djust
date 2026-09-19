"""
Component storybook -- auto-generated documentation for each theme component.

Reads component contracts, template source, and CSS variable usage to build
rich per-component detail pages.

Supports both template-based components (24 contracted) and Python components
(all 169 djust-components).
"""

import logging
import re
from typing import Any
from pathlib import Path

from django.utils.html import escape

from djust._log_utils import sanitize_for_log
from djust.theming.contracts import COMPONENT_CONTRACTS

logger = logging.getLogger(__name__)

# Path to the default component templates shipped with the package.
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "djust_theming"
_COMPONENTS_DIR = _TEMPLATES_DIR / "components"

# CSS files that define component styles.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "djust_theming" / "css"

# Pattern to extract CSS custom property references: var(--name, ...)
_CSS_VAR_RE = re.compile(r"var\(\s*(--[a-zA-Z0-9_-]+)")

# Allowlist of valid component identifiers built at import time from the actual
# HTML files present on disk. CodeQL recognizes `x in frozenset(...)` as a
# taint-clearing sanitizer for py/path-injection.
_ALLOWED_COMPONENTS: frozenset[str] = (
    frozenset(p.stem for p in _COMPONENTS_DIR.glob("*.html"))
    if _COMPONENTS_DIR.exists()
    else frozenset()
)


def get_component_template_source(component_name: str) -> str:
    """Read and return the raw HTML source of a default component template.

    Args:
        component_name: Component name, e.g. "button", "card".

    Returns:
        Template source as a string, or empty string if not found.
    """
    # Allowlist-based validation — CodeQL recognizes `in frozenset(...)` as
    # a taint-clearing sanitizer for path-injection.
    if component_name not in _ALLOWED_COMPONENTS:
        return ""
    template_path = _COMPONENTS_DIR / f"{component_name}.html"
    # Defense-in-depth: resolve + relative_to ensures the path stays within
    # the components directory even if the allowlist were somehow bypassed.
    try:
        template_path.resolve().relative_to(_COMPONENTS_DIR.resolve())
    except ValueError:
        return ""
    return template_path.read_text()


def extract_css_variables(source: str) -> list[str]:
    """Extract unique CSS custom property names from a source string.

    Searches for ``var(--name)`` patterns and returns a deduplicated,
    sorted list of variable names.

    Args:
        source: CSS or HTML source text.

    Returns:
        Sorted list of unique CSS variable names, e.g. ["--background", "--primary"].
    """
    matches = _CSS_VAR_RE.findall(source)
    return sorted(set(matches))


def _get_component_css_variables(component_name: str) -> list[str]:
    """Extract CSS variables used by a component from the CSS files.

    Searches ``components.css`` and ``base.css`` for class selectors that
    match the component name and returns all ``var(--name)`` references
    found in matching rule blocks.
    """
    # Map component names to CSS class prefixes
    class_prefix_map = {
        "button": "btn",
        "card": "card",
        "alert": "alert",
        "badge": "badge",
        "input": "input",
        "modal": "modal",
        "dropdown": "dropdown",
        "tabs": "tab",
        "table": "table",
        "pagination": "pagination",
        "select": "select",
        "textarea": "textarea",
        "checkbox": "checkbox",
        "radio": "radio",
        "breadcrumb": "breadcrumb",
        "avatar": "avatar",
        "toast": "toast",
        "progress": "progress",
        "skeleton": "skeleton",
        "tooltip": "tooltip",
        "nav_item": "nav-link",
        "nav_group": "sidebar",
        "nav": "navbar",
        "sidebar_nav": "sidebar",
    }

    css_prefix = class_prefix_map.get(component_name, component_name)
    all_vars: set[str] = set()

    for css_file in ("components.css", "base.css"):
        css_path = _STATIC_DIR / css_file
        if not css_path.is_file():
            continue
        content = css_path.read_text()
        # Find all rule blocks that contain the component's class prefix
        # Simple approach: scan whole file for var() refs in lines near the class
        in_matching_block = False
        brace_depth = 0
        for line in content.splitlines():
            if f".{css_prefix}" in line and "{" in line:
                in_matching_block = True
                brace_depth = 0
            if in_matching_block:
                brace_depth += line.count("{") - line.count("}")
                all_vars.update(_CSS_VAR_RE.findall(line))
                if brace_depth <= 0:
                    in_matching_block = False

    # Also check the template source for inline var() references
    template_source = get_component_template_source(component_name)
    if template_source:
        all_vars.update(_CSS_VAR_RE.findall(template_source))

    return sorted(all_vars)


def component_description(component_name: str) -> str:
    """One line a card can show: the first line of a python component's
    class docstring, or ``""`` when nothing documents it. Template components
    carry no prose in their contract, so they get ``""`` too — an honest gap
    the docs pass can fill, not a generated sentence."""
    from .component_registry import _load_component_class

    cls, _ = _load_component_class(component_name)
    doc = (getattr(cls, "__doc__", None) or "").strip() if cls is not None else ""
    if not doc:
        return ""
    first = doc.splitlines()[0].strip()
    return first if len(first) <= 140 else first[:137].rstrip() + "…"


_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,15}$")


def playground_options(examples: list[dict]) -> list[dict]:
    """The example kwargs a reader can flip in the playground.

    Derived from the examples themselves rather than a per-component table:
    a string kwarg that takes at least two distinct short values across the
    examples (``variant``: primary / secondary / …, ``size``: sm / md / lg)
    or a bool one becomes a row of chips. Long strings (labels, content) and
    structured values (lists of items) are not options — changing them is
    editing the example, not exploring the component.
    """
    seen: dict[str, list] = {}
    for example in examples:
        for key, value in example.items():
            if key.startswith("slot_"):
                continue
            # A token (`primary`, `sm`, `top-left`), not prose: a label such
            # as `text="Primary"` also varies across examples but is content.
            if isinstance(value, bool) or (isinstance(value, str) and _TOKEN_RE.match(value)):
                seen.setdefault(key, [])
                if value not in seen[key]:
                    seen[key].append(value)
    options = []
    for key, values in seen.items():
        if all(isinstance(v, bool) for v in values):
            options.append({"key": key, "values": [False, True]})
        elif len(values) >= 2 and all(isinstance(v, str) for v in values) and len(values) <= 8:
            options.append({"key": key, "values": values})
    return options


def build_storybook_index_context() -> dict:
    """Build context data for the storybook index page.

    Returns a dict with:
    - ``components``: flat list of all component dicts (for sidebar)
    - ``components_by_category``: list of {category, components} dicts
    - ``total_count``: total number of components
    """
    from .component_registry import get_all_components_with_metadata, COMPONENT_CATEGORIES

    all_components = get_all_components_with_metadata()

    # Enrich template components with contract data
    enriched = []
    for comp in all_components:
        name = comp["name"]
        comp = dict(comp)
        if name in COMPONENT_CONTRACTS:
            contract = COMPONENT_CONTRACTS[name]
            comp["required_count"] = len(contract.required_context)
            comp["optional_count"] = len(contract.optional_context)
            comp["slot_count"] = len(contract.available_slots)
            comp["a11y_count"] = len(contract.accessibility)
        comp["description"] = component_description(name)
        enriched.append(comp)

    # Group by category
    components_by_category = []
    for category in COMPONENT_CATEGORIES.keys():
        cat_comps = [c for c in enriched if c["category"] == category]
        if cat_comps:
            components_by_category.append(
                {
                    "category": category,
                    "components": cat_comps,
                    "count": len(cat_comps),
                }
            )

    return {
        "components": enriched,
        "components_by_category": components_by_category,
        "total_count": len(enriched),
    }


#: Components whose template renders no trigger of its own.
#:
#: Most components carry their own control: `tabs` has tab buttons, `dropdown`
#: has a trigger, `collapsible` has a header. A `modal` does not — by design,
#: its trigger belongs to the page that opens it, not to the dialog — so on a
#: storybook page the preview was a closed dialog with nothing to open it and
#: no way to tell the component worked. The storybook supplies the missing
#: control here.
#:
#: The markup dispatches the same event the component's descriptor listens for,
#: so this is the real server path rather than a demo-only shim.
_STORYBOOK_TRIGGERS = {
    "modal": (
        '<button type="button" dj-click="toggle_modal" '
        'style="font: inherit; padding: 0.375rem 0.75rem; border-radius: 0.375rem; '
        "border: 1px solid hsl(var(--border)); background: hsl(var(--card)); "
        'color: hsl(var(--card-foreground)); cursor: pointer; margin-bottom: 0.75rem;">'
        "Open modal</button>"
    ),
}


def _render_template_examples(component_name: str, examples: list[dict]) -> list[dict]:
    """Render a template component's examples through its own tag.

    Returns ``[{"html": ..., "kwargs": ...}]`` in the shape the python branch
    already uses, so the page has one preview mechanism rather than two.
    A failure renders a visible message instead of nothing — the same contract
    `render_python_component_example` now honours.

    Through its **tag**, not its template: those are different programs. The
    template is written to be rendered by ``{% theme_progress %}`` and reads
    names the tag computes — ``percentage``, ``is_indeterminate``, ``css_prefix``,
    ``attrs``, ``slot_*``. Passing the example's kwargs straight to the template
    leaves those names unfilled, and Django's default ``string_if_invalid=""``
    turns an unfilled name into silence rather than an error: the progress
    preview read ``style="width: %"`` on the page while the same tag rendered
    ``width: 25.0%`` for any real caller. An empty string is indistinguishable
    from a correctly-rendered empty value, so nothing caught it.

    Rendering through the tag means the preview is the developer's own path, so
    a preview can no longer be wrong in a way the component is not.
    """
    from django.template import RequestContext

    from djust.theming.templatetags import theme_components

    tag = getattr(theme_components, f"theme_{component_name}", None)
    rendered = []
    for example in examples:
        try:
            if tag is not None:
                # `simple_tag` compiles to a node whose `render` calls the
                # wrapped function with the context first; calling it directly
                # is that same call without the template machinery.
                html = str(tag(RequestContext({}), **example))
            else:
                # No `theme_<name>` tag: nothing computes the derived context,
                # so the template is the only thing there is to render.
                from django.template.loader import render_to_string

                html = render_to_string(
                    f"djust_theming/components/{component_name}.html", dict(example)
                )
        except Exception as exc:
            logger.warning(
                "Could not render template component %s: %s",
                sanitize_for_log(component_name),
                sanitize_for_log(str(exc)),
            )
            html = (
                f'<div class="dj-component-preview-error" role="status">'
                f"<code>{escape(component_name)}</code> failed to render: "
                f"<code>{escape(type(exc).__name__)}: {escape(str(exc))}</code></div>"
            )
        # The trigger goes before the component's own markup, not inside it: a
        # modal's backdrop is `position: fixed`, so a control rendered within it
        # would be covered once the dialog opened.
        trigger = _STORYBOOK_TRIGGERS.get(component_name, "")
        rendered.append({"html": f"{trigger}{html}", "kwargs": example})
    return rendered


#: The stylesheets a storybook page loads, with the label to show for each.
#: Derived from disk rather than hand-listed: a component's styling moves
#: between files, and a list maintained by hand is one that goes stale quietly.
_CSS_TREES: list[tuple[str, Path]] = [
    ("djust_theming", Path(__file__).resolve().parent.parent / "static" / "djust_theming" / "css"),
    (
        "djust_components",
        Path(__file__).resolve().parent.parent.parent
        / "components"
        / "static"
        / "djust_components",
    ),
]


def _classes_in(html: str) -> list[str]:
    """Every class the component's own markup uses, in first-seen order."""
    seen: dict[str, None] = {}
    for match in re.finditer(r'class="([^"]*)"', html):
        for cls in match.group(1).split():
            seen.setdefault(cls, None)
    return list(seen)


def styles_for(html: str) -> list[dict]:
    """Which stylesheets define the classes this markup uses, and where.

    Answers "what do I override to change how this looks?" with a file and a
    line rather than a shrug. A developer can read the rule they need to beat
    without grepping the package, and see which of the two `components.css`
    files (the theming one and the components app's) is actually in play.
    """
    classes = _classes_in(html)
    if not classes:
        return []

    patterns = {cls: re.compile(r"\." + re.escape(cls) + r"(?![\w-])") for cls in classes}
    found: list[dict] = []

    for label, tree in _CSS_TREES:
        if not tree.is_dir():
            continue
        for path in sorted(tree.rglob("*.css")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:  # pragma: no cover — unreadable file mid-scan
                continue
            hits: dict[str, int] = {}
            for lineno, line in enumerate(text.splitlines(), start=1):
                for cls, pattern in patterns.items():
                    if cls not in hits and pattern.search(line):
                        hits[cls] = lineno
            if hits:
                found.append(
                    {
                        "label": label,
                        "path": f"{label}/{path.relative_to(tree)}",
                        "classes": sorted(hits),
                        # Several classes routinely share a line
                        # (`.a { … }` and `.b { … }` written together), and a
                        # repeated line number reads as a mistake.
                        "lines": sorted(set(hits.values())),
                    }
                )

    # The file that styles most of the component is the one to start with.
    found.sort(key=lambda row: -len(row["classes"]))
    return found


_EVENT_ATTR_RE = re.compile(
    r'dj-(?:click|change|input|submit|keydown|keyup|blur|focus)="([A-Za-z_][\w]*)"'
)


def component_events(rendered_html: str) -> list[str]:
    """The server events a component's markup emits, in the order they appear:
    every ``dj-click`` / ``dj-change`` / ``dj-input`` / … name in the rendered
    example. These are the handlers a host view has to answer."""
    seen: list[str] = []
    for name in _EVENT_ATTR_RE.findall(rendered_html or ""):
        if name not in seen:
            seen.append(name)
    return seen


def usage_with_events(
    snippet: str,
    events: list[str],
    *,
    descriptor_class: str = "",
    descriptor_event: str = "",
    demo_keys: dict | None = None,
    class_name: str = "",
    example: dict | None = None,
) -> str:
    """Add the event side of the story to a usage snippet.

    A component that renders ``dj-click="set_rating"`` needs a host that
    answers it — that is the contract, and the part a reader copying the
    assignment alone would miss. Two shapes:

    * a descriptor (``Accordion``, ``Tabs``, ``Modal`` …): the class-level
      form owns per-view state and wires its ``Meta.event`` itself, so the
      snippet shows that instead of a handler;
    * any other event: an ``@event_handler`` stub on the view that rebuilds
      the component with the new value, the kwarg it drives named when the
      storybook knows it (``demo_keys``).
    """
    if not events and not descriptor_class:
        return snippet
    views_part, sep, template_part = snippet.partition("\n\n\n# my_template.html\n")
    lines = views_part.splitlines()
    if descriptor_class:
        # Replace the instance assignment with the class-level slot.
        lines = [ln for ln in lines if "self.component = " not in ln and "def mount(" not in ln]
        while lines and lines[-1] == "":
            lines.pop()
        lines += [
            "",
            f"    # Per-view state; clicks inside it send `{descriptor_event}` and the",
            "    # descriptor handles it — nothing to write. Read `self.component.state`.",
            f"    component = {descriptor_class}()",
        ]
        others = [e for e in events if e != descriptor_event]
    else:
        others = list(events)
    if others:
        if "from djust.decorators import event_handler" not in lines:
            lines.insert(2, "from djust.decorators import event_handler")
        for event in others:
            key = (demo_keys or {}).get(event)
            lines += ["", "    @event_handler()", f'    def {event}(self, value="", **kwargs):']
            if key and class_name:
                # The mount-time call again, with the kwarg this event drives
                # bound to the incoming value — what the reader would write.
                # The wire carries a string; convert to the kwarg's own type.
                reference = (example or {}).get(key)
                if isinstance(reference, bool):
                    incoming = 'value == "true"'
                elif isinstance(reference, int):
                    incoming = "int(value)"
                elif isinstance(reference, float):
                    incoming = "float(value)"
                else:
                    incoming = "value"
                kwargs_src = ", ".join(
                    f"{k}={incoming if k == key else repr(v)}"
                    for k, v in (example or {}).items()
                    if not k.startswith("slot_")
                )
                if key not in (example or {}):
                    kwargs_src = f"{kwargs_src}, {key}=value" if kwargs_src else f"{key}=value"
                lines.append(f"        self.component = {class_name}({kwargs_src})")
            else:
                lines.append("        ...  # update state; the re-render carries it")
    return "\n".join(lines) + sep + template_part


def split_usage(snippet: str) -> dict:
    """``{"view": …, "template": …}`` — the two files the snippet shows."""
    views_part, _, template_part = snippet.partition("\n\n\n# my_template.html\n")
    return {"view": views_part.replace("# views.py\n", "", 1), "template": template_part}


def _usage_snippet(
    component_name: str,
    component_type: str,
    examples: list[dict],
    class_name: str,
    import_line: str,
) -> str:
    """The two-file example a developer actually writes.

    The page used to show only the component in isolation —
    `progress(value=0, max=100, label=None, …)` then `component.render()` —
    which is not how any of this is used. A djust component is assigned in a
    LiveView and interpolated in a template; showing the assignment without the
    view it lives in, or the `{{ … }}` that renders it, documents the one part
    that was never in question.
    """
    first = dict(examples[0]) if examples else {}
    # Show the arguments that produce the example's own output, not the
    # signature's defaults: `value=0, label=None` documents nothing a reader can
    # picture, and it is not what the preview above shows.
    args = ", ".join(f"{k}={v!r}" for k, v in first.items() if not k.startswith("slot_"))
    slot_args = [k for k in first if k.startswith("slot_")]

    if component_type == "template":

        def is_literal(value: object) -> bool:
            return value is None or isinstance(value, (str, int, float, bool))

        named = {k: v for k, v in first.items() if not k.startswith("slot_")}
        # A template argument is parsed by Django, not Python: `repr()` of a
        # list of dicts — `[{'label': 'Home'}]` — is a TemplateSyntaxError, not
        # a value. Anything the template cannot write as a literal is held on
        # the view and passed by name, which is what a developer does anyway.
        on_view = {k: v for k, v in named.items() if not is_literal(v)}
        # One value always comes from the view even when it could be inlined,
        # so the two files visibly connect instead of reading as unrelated.
        first_name = next(iter(on_view), next(iter(named), "value"))
        on_view.setdefault(first_name, named.get(first_name))

        # Tag arguments are space-separated; a comma between them is a syntax
        # error in the template, not a style choice.
        tag_args = " ".join(f"{k}={k if k in on_view else repr(v)}" for k, v in named.items())

        lines = [
            "# views.py",
            "from djust import LiveView",
            "",
            "",
            "class MyView(LiveView):",
            '    template_name = "my_template.html"',
            "",
            "    def mount(self, request, **kwargs):",
        ]
        for key, value in on_view.items():
            lines.append(f"        self.{key} = {value!r}")
        lines += [
            "",
            "",
            "# my_template.html",
            "{% load theme_components %}",
            f"{{% theme_{component_name}{' ' + tag_args if tag_args else ''} %}}",
        ]
        return "\n".join(lines)

    if not import_line:
        return (
            f"# `{component_name}` defines no component class of its own. It ships\n"
            "# shared parts — mixins and helpers — that other components are built\n"
            "# from."
        )

    assignment = (
        f"self.component = {class_name}({args})" if args else f"self.component = {class_name}()"
    )
    lines = [
        "# views.py",
        "from djust import LiveView",
        import_line,
        "",
        "",
        "class MyView(LiveView):",
        '    template_name = "my_template.html"',
        "",
        "    def mount(self, request, **kwargs):",
        f"        {assignment}",
    ]
    if slot_args:
        lines.append("")
        lines.append("        # `" + "` / `".join(slot_args) + "` carry markup, so they are")
        lines.append("        # passed the rendered HTML rather than a value.")
    lines += [
        "",
        "",
        "# my_template.html",
        # `render()` marks the component's HTML safe, and the LiveView path
        # carries that mark to the template — no `|safe` needed.
        "{{ component }}",
    ]
    return "\n".join(lines)


def _module_path(component_name: str) -> str:
    """The module a python component's class is defined in, or ""."""
    from .component_registry import get_python_component_import

    module_path, _names = get_python_component_import(component_name)
    return module_path or ""


def _import_line(component_name: str) -> str:
    """The USAGE snippet's import line, or "" when there is nothing to import."""
    from .component_registry import get_python_component_import

    _module_path, names = get_python_component_import(component_name)
    if not names:
        return ""
    return f"from djust.components import {', '.join(names)}"


def _first_class_name(component_name: str) -> str:
    """The class to instantiate in the snippet; "" when the module has none."""
    from .component_registry import get_python_component_import

    _module_path, names = get_python_component_import(component_name)
    return names[0] if names else ""


def build_storybook_detail_context(component_name: str, *, render_examples: bool = True) -> dict:
    """Build context data for a single component's storybook detail page.

    Handles both template-based (contracted) and Python components.

    Args:
        component_name: Component name, e.g. "button" or "spinner".

    Returns:
        Dict with contract info (or python signature), examples, and metadata.

    Raises:
        KeyError: If component_name is not recognized in either registry.
    """
    from .component_registry import (
        get_component_category,
        render_python_component_example,
        get_python_component_signature,
        PYTHON_COMPONENT_EXAMPLES,
        _COMPONENT_TO_CATEGORY,
    )

    if component_name not in COMPONENT_CONTRACTS and component_name not in _COMPONENT_TO_CATEGORY:
        raise KeyError(f"Unknown component: {component_name}")

    category = get_component_category(component_name)
    display_name = component_name.replace("_", " ").title()

    if component_name in COMPONENT_CONTRACTS:
        # Template-based component: full contract + examples
        contract = COMPONENT_CONTRACTS[component_name]
        template_source = get_component_template_source(component_name)
        css_variables = _get_component_css_variables(component_name)

        from .context import _EXAMPLE_BUILDERS

        builder = _EXAMPLE_BUILDERS.get(component_name)
        examples = builder() if builder else []
        # ``render_examples=False``: the LiveView's preview component renders
        # them (and derives ``styles``); rendering here too doubled every GET.
        rendered_examples = (
            _render_template_examples(component_name, examples) if render_examples else []
        )

        return {
            "name": component_name,
            "display_name": display_name,
            "category": category,
            "component_type": "template",
            "required_context": [
                {"name": v.name, "type": v.type, "default": v.default, "required": v.required}
                for v in contract.required_context
            ],
            "optional_context": [
                {"name": v.name, "type": v.type, "default": v.default, "required": v.required}
                for v in contract.optional_context
            ],
            "required_elements": [
                {"tag": e.tag, "attrs": e.attrs} for e in contract.required_elements
            ],
            "accessibility": [
                {
                    "description": a.description,
                    "selector_hint": a.selector_hint,
                    "attr": a.attr,
                    "value": a.value,
                }
                for a in contract.accessibility
            ],
            "available_slots": list(contract.available_slots),
            "template_source": template_source,
            "css_variables": css_variables,
            "examples": examples,
            # Rendered here rather than by a hand-written chain of
            # `{% if name == "button" %}…{% elif %}` in the template. That chain
            # covered 11 of these 24 components; every other one fell through to
            # an `{% else %}` that printed the invocation as text — so a section
            # headed LIVE PREVIEW showed `tabs(id=…, active=0)` for 13 of them.
            # The component's own template with the contract's example kwargs is
            # what the tag renders anyway, and it needs no per-component entry.
            "template_examples_html": rendered_examples,
            "template_path": f"djust_theming/components/{component_name}.html",
            "module_path": "",
            # Which stylesheets give this markup its look, and where in them.
            # Read off the rendered examples rather than a hand-kept table, so
            # a component that gains a class gains its stylesheet entry too.
            "styles": styles_for("".join(e["html"] for e in rendered_examples)),
            "usage_snippet": _usage_snippet(component_name, "template", examples, "", ""),
        }
    else:
        # Python component: render examples via dynamic import
        raw_examples = PYTHON_COMPONENT_EXAMPLES.get(component_name, [])
        # Annotated because the dict is heterogeneous (`html` is a string, the
        # others are the example's kwargs), and without it `e["html"]` infers
        # as a collection rather than a str at the `join` below.
        python_examples_html: list[dict[str, Any]] = []
        for kwargs in raw_examples if render_examples else []:
            html = render_python_component_example(component_name, kwargs)
            # Pre-render kwargs display string in Python to avoid Django template
            # resolving .items as a dict-key lookup instead of dict.items().
            kwargs_display = ", ".join(f"{k}={repr(v)}" for k, v in kwargs.items())
            python_examples_html.append(
                {
                    "html": html,
                    "kwargs": kwargs,
                    "kwargs_display": kwargs_display,
                }
            )

        # Get parameter signature
        python_params = get_python_component_signature(component_name) or []

        return {
            "name": component_name,
            "display_name": display_name,
            "category": category,
            "component_type": "python",
            # The USAGE snippet's import line, computed here rather than spelled
            # out in the template. The template used to hardcode
            # `from djust_components.components.<name> import <Name>`, and both
            # halves of that were wrong: `djust_components` is the static
            # namespace, not a package (so nothing imported at all), and the
            # class name is not always the snake→CamelCase of the module name
            # (`qr_code` defines `QRCode`; `form_validation` defines two
            # components; `server_event_toast` defines only a mixin).
            #
            # Derived from the module and emitted over the public
            # `djust.components` namespace, which resolves these lazily. Empty
            # when the module defines no component class, so the page documents
            # no import rather than a wrong one.
            "import_line": _import_line(component_name),
            "class_name": _first_class_name(component_name),
            "required_context": [],
            "optional_context": [],
            "required_elements": [],
            "accessibility": [],
            "available_slots": [],
            "template_source": "",
            "css_variables": [],
            "examples": [],
            "python_examples_html": python_examples_html,
            "python_params": python_params,
            # A python component renders itself, so there is no contract
            # template — the module is the source a reader would open.
            "template_path": "",
            "module_path": _module_path(component_name),
            "styles": styles_for("".join(e["html"] for e in python_examples_html)),
            "usage_snippet": _usage_snippet(
                component_name,
                "python",
                raw_examples,
                _first_class_name(component_name),
                _import_line(component_name),
            ),
        }


def get_component_coverage(theme_name: str, themes_dir: Path) -> dict:
    """Compute component coverage for a theme.

    Scans the theme's ``components/`` directory for template overrides and
    compares against ``COMPONENT_CONTRACTS``.

    Args:
        theme_name: Theme directory name.
        themes_dir: Root directory containing theme subdirectories.

    Returns:
        Dict with keys: ``overridden`` (list), ``inherited`` (list),
        ``coverage_pct`` (float).

    Raises:
        FileNotFoundError: If the theme directory does not exist.
    """
    theme_dir = Path(themes_dir) / theme_name
    if not theme_dir.is_dir():
        raise FileNotFoundError(f"Theme directory not found: {theme_dir}")

    comp_dir = theme_dir / "components"
    all_names = list(COMPONENT_CONTRACTS.keys())

    overridden = []
    if comp_dir.is_dir():
        for f in comp_dir.iterdir():
            if f.suffix == ".html":
                name = f.stem
                if name in COMPONENT_CONTRACTS:
                    overridden.append(name)

    overridden.sort()
    inherited = sorted(n for n in all_names if n not in overridden)
    total = len(all_names)
    coverage_pct = round(len(overridden) / total * 100, 1) if total > 0 else 0.0

    return {
        "overridden": overridden,
        "inherited": inherited,
        "coverage_pct": coverage_pct,
    }
