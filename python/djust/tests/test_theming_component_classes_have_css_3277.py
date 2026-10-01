"""Every class a theming component tag emits has a rule in ``components.css`` (#3277).

``theme_nav_item`` emitted ``.nav-link``, ``theme_mode_button`` emitted
``.theme-mode-toggle`` and ``theme_select`` / ``theme_textarea`` emitted
``.select*`` / ``.textarea*``, none of which ``components.css`` styled, so they
rendered as unstyled browser controls. ``components.css`` is the stylesheet
``{% theme_head %}`` always links; ``scaffold.css`` is opt-in and cannot be
counted on.

The emitted class list is derived from the component templates and the
variant lists in the tag docstrings, so a new component or variant is covered
without editing this file.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.theming

THEMING = Path(__file__).resolve().parents[1] / "theming"
COMPONENT_TEMPLATES = THEMING / "templates" / "djust_theming" / "components"
COMPONENTS_CSS = THEMING / "static" / "djust_theming" / "css" / "components.css"
TAGS_PY = THEMING / "templatetags" / "theme_components.py"

_CLASS_ATTR = re.compile(r'\bclass="([^"]*)"')
_TAG = re.compile(r"\{%.*?%\}", re.S)
_VAR = re.compile(r"\{\{\s*([^}|\s]+)[^}]*\}\}")
_DYNAMIC = re.compile(r"\x00([^\x00]+)\x00")
_PREFIX = re.compile(r"\{\{\s*css_prefix\s*\}\}")
# No lookbehind: in a selector the second class of ``.a.b`` is a class too.
_CSS_CLASS = re.compile(r"\.([a-zA-Z_][\w-]*)")
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_QUOTED = re.compile(r"'([\w-]+)'")

# ``active`` is a state class every rule composes with (``.nav-link.active``).
_NOT_COMPONENT_CLASSES = frozenset({"active"})

SCAFFOLD_CSS = THEMING / "static" / "djust_theming" / "css" / "scaffold.css"

# Layout components whose rules live in the opt-in ``scaffold.css`` (its header:
# "Include this for pre-built navbar, sidebar, layout"). Each entry is checked
# against ``scaffold.css``, so a rule removed from there fails the test.
SCAFFOLD_OWNED = frozenset(
    {
        "navbar",
        "navbar-inner",
        "navbar-brand",
        "navbar-nav",
        "navbar-actions",
        "sidebar",
        "sidebar-footer",
        "sidebar-title",
        "sidebar-menu",
        "sidebar-item",
        "sidebar-item-count",
    }
)

# Baseline variants whose look IS the unmodified base class.
BASELINE_VARIANTS = {"table-default": "table"}


def _css_classes(path: Path = COMPONENTS_CSS) -> set[str]:
    css = _COMMENT.sub("", path.read_text(encoding="utf-8"))
    # Only selectors: text before a ``{``.
    selectors = re.findall(r"([^{}]+)\{", css)
    return {c for sel in selectors for c in _CSS_CLASS.findall(sel)}


def _tag_variants() -> dict[str, dict[str, list[str]]]:
    """``{component: {"variant": [...], "size": [...]}}`` from the tag docstrings."""
    options: dict[str, dict[str, list[str]]] = {}
    for node in ast.walk(ast.parse(TAGS_PY.read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("theme_"):
            doc = ast.get_docstring(node) or ""
            found = {}
            for line in doc.splitlines():
                m = re.match(r"\s*(\w+):\s*(.*)", line)
                if m and _QUOTED.findall(m.group(2)):
                    found[m.group(1)] = _QUOTED.findall(m.group(2))
            options[node.name[len("theme_") :]] = found
    return options


def emitted_classes() -> dict[str, set[str]]:
    """``{class: {template names that emit it}}``, dynamic suffixes expanded."""
    variants = _tag_variants()
    emitted: dict[str, set[str]] = {}
    for template in sorted(COMPONENT_TEMPLATES.glob("*.html")):
        component = template.stem
        text = _TAG.sub(" ", template.read_text(encoding="utf-8"))
        for attr in _CLASS_ATTR.findall(text):
            # ``{{ expr }}`` becomes ``\x00expr\x00`` so a token has no spaces.
            attr = _VAR.sub(lambda m: "\x00" + m.group(1) + "\x00", _PREFIX.sub("", attr))
            for token in attr.split():
                dynamic = _DYNAMIC.search(token)
                if dynamic is None:
                    names = [token]
                elif dynamic.start() == 0:
                    continue  # ``{{ attrs.class }}`` and the like: caller-supplied
                else:
                    stem = token[: dynamic.start()]
                    options = variants.get(component, {}).get(dynamic.group(1), [])
                    names = [stem + o for o in options] or [stem.rstrip("-") + "-*"]
                for name in names:
                    emitted.setdefault(name, set()).add(component)
    return emitted


def test_derivation_sees_the_known_components():
    emitted = emitted_classes()
    assert {"btn", "btn-primary", "alert-actions", "nav-link", "select", "textarea"} <= set(emitted)
    assert "theme-mode-toggle" in emitted


def test_every_emitted_class_has_a_components_css_rule():
    css = _css_classes()
    missing = []
    for name, components in sorted(emitted_classes().items()):
        if name in _NOT_COMPONENT_CLASSES or name in SCAFFOLD_OWNED or name in BASELINE_VARIANTS:
            continue
        if name.endswith("-*"):
            if not any(c.startswith(name[:-1]) for c in css):
                missing.append(f".{name} ({', '.join(sorted(components))})")
        elif name not in css:
            missing.append(f".{name} ({', '.join(sorted(components))})")
    assert not missing, "components.css has no rule for:\n  " + "\n  ".join(missing)


# ---------------------------------------------------------------------------
# box-sizing: width/height plus padding/border only mean what they say under
# border-box, and the theme ships no global reset.
# ---------------------------------------------------------------------------

_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECL = re.compile(r"(?<![\w-])([a-z-]+)\s*:\s*([^;]*)(?:;|$)")


def _flat_rules() -> list[tuple[int, str, dict[str, str]]]:
    css = _COMMENT.sub(
        lambda m: re.sub(r"[^\n]", " ", m.group(0)), COMPONENTS_CSS.read_text("utf-8")
    )
    found = []
    for m in _RULE.finditer(css):
        selector = " ".join(m.group(1).split())
        decls = {d.group(1): d.group(2).strip() for d in _DECL.finditer(m.group(2))}
        found.append((css.count("\n", 0, m.start(2)) + 1, selector, decls))
    return found


def _sized_and_boxed(decls: dict[str, str]) -> bool:
    sized = bool(decls.keys() & {"width", "height"})
    padded = any(k.startswith("padding") for k in decls)
    bordered = any(
        k.startswith("border") and not re.search(r"\b(none|0)\b", v)
        for k, v in decls.items()
        if k in {"border", "border-top", "border-right", "border-bottom", "border-left"}
    )
    return sized and (padded or bordered)


def test_sized_component_boxes_are_border_box():
    offenders = [
        f"components.css:{line}: {selector[:70]}"
        for line, selector, decls in _flat_rules()
        if not selector.startswith(("@", "[dir"))
        and _sized_and_boxed(decls)
        and decls.get("box-sizing") != "border-box"
    ]
    assert not offenders, (
        "These rules set a width/height together with padding/border but not "
        "`box-sizing: border-box`; without a global reset they render oversized:\n  "
        + "\n  ".join(offenders)
    )


def test_exemptions_are_still_true():
    """An exemption that stops being accurate must be removed, not left to rot."""
    emitted = emitted_classes()
    components_css = _css_classes()
    scaffold_css = _css_classes(SCAFFOLD_CSS)
    for name in sorted(SCAFFOLD_OWNED):
        assert name in emitted, f".{name} is no longer emitted; drop it from SCAFFOLD_OWNED"
        assert name not in components_css, f".{name} now has a components.css rule; drop it"
        assert name in scaffold_css, f".{name} has no scaffold.css rule either"
    for name, base in BASELINE_VARIANTS.items():
        assert name in emitted, f".{name} is no longer emitted; drop it from BASELINE_VARIANTS"
        assert name not in components_css, f".{name} now has a rule; drop it"
        assert base in components_css, f".{base} (the baseline) has no rule"


def test_classes_templates_emit_unprefixed_stay_unprefixed_under_a_css_prefix():
    from djust.theming.component_css_generator import generate_component_css

    css = generate_component_css("dj-")
    for name in ("theme-mode-toggle", "theme-preset-grid", "theme-preset-btn", "theme-preset-list"):
        assert f".{name}" in css and f".dj-{name}" not in css, name
    # A class the templates DO prefix is still prefixed.
    assert ".dj-select" in css and ".dj-nav-link" in css


def test_new_rules_do_not_restyle_djust_components_markup():
    """djust.components emits ``.alert-icon`` and a Bootstrap-style ``.nav-link`` in its
    own markup and links its own stylesheet beside this one (#3305 review)."""
    selectors = [s for _line, s, _d in _flat_rules()]
    assert ".nav-link" not in selectors and ".nav-link:hover" not in selectors
    assert ".nav-link.active" not in selectors
    assert any(".nav-link.theme-nav-link" in s for s in selectors)
    for _line, selector, decls in _flat_rules():
        if selector in {".alert-icon", ".alert-actions"}:
            assert not [k for k in decls if k.startswith("margin")], selector
    templates = {p.stem: p.read_text("utf-8") for p in COMPONENT_TEMPLATES.glob("nav*.html")}
    for name in ("nav", "nav_item"):
        assert "theme-nav-link" in templates[name], name
