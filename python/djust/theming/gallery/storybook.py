"""
Component storybook -- auto-generated documentation for each theme component.

Reads component contracts, template source, and CSS variable usage to build
rich per-component detail pages.

Supports both template-based components (24 contracted) and Python components
(all 169 djust-components).
"""

import re
from pathlib import Path

from djust.theming.contracts import COMPONENT_CONTRACTS

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
        if name in COMPONENT_CONTRACTS:
            contract = COMPONENT_CONTRACTS[name]
            comp = dict(comp)
            comp["required_count"] = len(contract.required_context)
            comp["optional_count"] = len(contract.optional_context)
            comp["slot_count"] = len(contract.available_slots)
            comp["a11y_count"] = len(contract.accessibility)
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


def build_storybook_detail_context(component_name: str) -> dict:
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
        }
    else:
        # Python component: render examples via dynamic import
        raw_examples = PYTHON_COMPONENT_EXAMPLES.get(component_name, [])
        python_examples_html = []
        for kwargs in raw_examples:
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
