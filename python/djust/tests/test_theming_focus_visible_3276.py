"""Themed controls keep a visible keyboard focus indicator (#3276, WCAG 2.4.7).

``.btn:focus-visible`` and ``.input:focus`` once set ``outline: none`` and then
``ring: 2px solid hsl(var(--ring))``. ``ring`` is a Tailwind utility name, not a
CSS property, so the browser dropped it and keyboard focus was invisible on
every themed button and input.

This test scans every piece of CSS ``djust.theming`` ships (static ``.css`` files, inline
``<style>`` in templates, and CSS written as string literals in the Python
generators) and fails when a rule

* declares ``ring:`` / ``ring-offset:``, which are not CSS properties;
* removes the outline without supplying a replacement indicator in the same
  rule (a ``box-shadow``, a restyled ``outline``, a ``text-decoration`` or a
  border colour change); or
* builds a focus colour with ``hsla(var(--token), alpha)``, which mixes the
  space-separated token syntax with a comma alpha and is invalid.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.theming

PACKAGE_ROOT = Path(__file__).resolve().parents[1]  # python/djust
# ``djust.theming`` owns the theme CSS. The component library's own stylesheets
# (``djust/components``) and the debug panel are separate surfaces.
THEMING_ROOT = PACKAGE_ROOT / "theming"

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECL = re.compile(r"(?<![\w-])([a-zA-Z-]+)\s*:\s*([^;]*)(?:;|$)")
# An f-string interpolation inside a Python CSS literal: ``{p}``, ``{x.y}``.
_INTERPOLATION = re.compile(r"\{[\w.\[\]'\"()]*\}")
_NON_PROPERTY = {"ring", "ring-offset", "ring-color", "ring-width"}
_REMOVES_OUTLINE = re.compile(r"^(?:none|0|0px)$", re.I)
_BORDER_COLOUR = re.compile(r"^border(?:-(?:top|right|bottom|left))?-color$")


def _normalise(text: str, *, python: bool) -> str:
    text = _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    if python:
        # ``{{`` / ``}}`` are literal braces in an f-string; drop interpolations.
        text = text.replace("{{", "\x01").replace("}}", "\x02")
        text = _INTERPOLATION.sub("X", text)
        text = text.replace("\x01", "{").replace("\x02", "}")
    return text


def rules(text: str, *, python: bool = False) -> list[tuple[int, str, dict[str, str]]]:
    """``(line, selector, {property: value})`` for every flat rule in ``text``.

    ``@media``/``@layer`` wrappers are transparent: the regex matches the
    innermost ``selector { declarations }`` pairs.
    """
    text = _normalise(text, python=python)
    found = []
    for match in _RULE.finditer(text):
        selector = re.split(r'"""|[;}]', match.group(1))[-1].strip()
        decls = {m.group(1).lower(): m.group(2).strip() for m in _DECL.finditer(match.group(2))}
        line = text.count("\n", 0, match.start(2)) + 1
        found.append((line, " ".join(selector.split()), decls))
    return found


def _removes_outline(decls: dict[str, str]) -> bool:
    outline = decls.get("outline")
    if outline is not None and _REMOVES_OUTLINE.match(outline.split()[0] if outline else ""):
        return True
    style = decls.get("outline-style")
    if style is not None and style.lower() == "none":
        return True
    width = decls.get("outline-width")
    return width is not None and _REMOVES_OUTLINE.match(width) is not None


def _supplies_indicator(decls: dict[str, str]) -> bool:
    shadow = decls.get("box-shadow", "")
    if shadow and not re.match(r"^(?:none|0)$", shadow, re.I):
        return True
    outline = decls.get("outline", "")
    if outline and not _REMOVES_OUTLINE.match(outline.split()[0]):
        return True
    if "text-decoration" in decls or "text-decoration-line" in decls:
        return True
    return any(_BORDER_COLOUR.match(prop) for prop in decls)


def violations(text: str, *, python: bool = False) -> list[tuple[int, str]]:
    """``(line, message)`` for each focus-visibility violation in ``text``."""
    problems = []
    for line, selector, decls in rules(text, python=python):
        for prop in sorted(_NON_PROPERTY & decls.keys()):
            problems.append((line, f"{selector}: `{prop}:` is not a CSS property"))
        if _removes_outline(decls):
            # ``:focus:not(:focus-visible)`` only mutes the mouse-click ring.
            if ":not(:focus-visible)" not in selector and not _supplies_indicator(decls):
                problems.append((line, f"{selector}: removes the outline with no replacement"))
        if ":focus" in selector and any("hsla(var(" in v for v in decls.values()):
            problems.append((line, f"{selector}: hsla(var(--token), a) is invalid"))
    return problems


def _shipped_css_sources() -> list[Path]:
    sources = [p for ext in ("css", "html", "py") for p in THEMING_ROOT.rglob(f"*.{ext}")]
    return sorted(p for p in sources if "node_modules" not in p.parts)


@pytest.mark.parametrize(
    "css",
    [
        ".btn:focus-visible { outline: none; ring: 2px solid hsl(var(--ring)); }",
        ".a:focus { outline: none; }",
        ".a { outline: 0; }",
        ".a:focus { outline-style: none; }",
        ".a:focus { outline: none; box-shadow: none; }",
        ".a:focus-visible { box-shadow: 0 0 0 3px hsla(var(--ring), 0.3); }",
        "@media (x) {\n  .a:focus { outline: none; ring-offset: 2px; }\n}",
    ],
)
def test_scanner_flags_invisible_focus(css):
    assert violations(css), css


@pytest.mark.parametrize(
    "css",
    [
        ".a:focus-visible { outline: 2px solid hsl(var(--ring)); outline-offset: 2px; }",
        ".a:focus { outline: none; box-shadow: 0 0 0 2px hsl(var(--ring)); }",
        ".a:focus { outline: none; border-color: hsl(var(--ring)); }",
        ".a:focus-visible { outline: none; text-decoration: underline; }",
        ".a:focus:not(:focus-visible) { outline: none; }",
        ".a:focus { color: red; }",
    ],
)
def test_scanner_accepts_a_real_indicator(css):
    assert not violations(css), css


def test_scanner_reads_python_fstring_css():
    src = 'css = f"""\n.{p}x:focus {{\n  outline: none;\n}}\n"""'
    assert violations(src, python=True)
    ok = 'css = f"""\n.{p}x:focus {{\n  outline: none;\n  box-shadow: 0 0 0 2px red;\n}}\n"""'
    assert not violations(ok, python=True)


def test_scan_covers_the_known_sources():
    names = {p.name for p in _shipped_css_sources()}
    assert {"components.css", "scaffold.css", "pack_css_generator.py"} <= names


def test_no_shipped_css_hides_keyboard_focus():
    offenders = []
    for path in _shipped_css_sources():
        text = path.read_text(encoding="utf-8")
        for line, message in violations(text, python=path.suffix == ".py"):
            offenders.append(f"{path.relative_to(PACKAGE_ROOT)}:{line}: {message}")
    assert not offenders, "Keyboard focus must stay visible (WCAG 2.4.7):\n  " + "\n  ".join(
        offenders
    )
