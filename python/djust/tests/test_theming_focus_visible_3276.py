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
_BRACE = re.compile(r"[{}]")
_DECL = re.compile(r"(?<![\w-])([a-zA-Z-]+)\s*:\s*([^;]*)(?:;|$)")
# An f-string interpolation inside a Python CSS literal: ``{p}``, ``{x.y}``.
_INTERPOLATION = re.compile(r"\{[\w.\[\]'\"()]*\}")
_NON_PROPERTY = {"ring", "ring-offset", "ring-color", "ring-width"}
_REMOVES_OUTLINE = re.compile(r"^(?:none|0|0px)$", re.I)
# A halo counts as a focus indicator only when it is a solid ring: no blur, a
# spread of at least 2px and an alpha of at least 0.5. A 2px ``ring / 0.2`` halo
# measures ~1.2:1 against the page, which nobody can see.
_SHADOW_RING = re.compile(r"(?:^|\s)0(?:px)?\s+0(?:px)?\s+0(?:px)?\s+(\d+(?:\.\d+)?)px\s+(.*)$")
_ALPHA = re.compile(r"(?:/|,)\s*(\d*\.?\d+)\s*(%?)\s*\)\s*$")
MIN_HALO_SPREAD_PX = 2.0
MIN_HALO_ALPHA = 0.5


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

    ``@media``/``@layer`` wrappers are transparent: a rule is the text between
    an opening brace and the closing brace that follows it with no brace in
    between. One linear pass over the braces (a ``[^{}]+`` regex backtracks
    quadratically on a Python file that has no braces for pages).
    """
    text = _normalise(text, python=python)
    found = []
    boundary = 0  # end of the previous brace: the selector starts here
    pending = None  # index of an opening brace not yet closed
    for match in _BRACE.finditer(text):
        if match.group() == "{":
            if pending is not None:  # nested at-rule wrapper: its prelude is not a selector
                boundary = pending + 1
            pending = match.start()
            continue
        if pending is not None:
            header = text[boundary:pending]
            selector = re.split(r'"""|[;}]', header)[-1].strip()
            body = text[pending + 1 : match.start()]
            decls = {m.group(1).lower(): m.group(2).strip() for m in _DECL.finditer(body)}
            found.append((text.count("\n", 0, pending) + 1, " ".join(selector.split()), decls))
        pending = None
        boundary = match.end()
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


def _is_solid_halo(shadow: str) -> bool:
    for layer in re.split(r",\s*(?![^(]*\))", shadow):
        ring = _SHADOW_RING.search(layer.strip())
        if not ring or float(ring.group(1)) < MIN_HALO_SPREAD_PX:
            continue
        alpha = _ALPHA.search(ring.group(2))
        value = 1.0
        if alpha:
            value = float(alpha.group(1)) / (100 if alpha.group(2) else 1)
        if value >= MIN_HALO_ALPHA:
            return True
    return False


def _supplies_indicator(decls: dict[str, str]) -> bool:
    """A real outline, an underline, or a solid halo. A bare border-colour change
    is not one: against a 3:1 ``--input`` border it can be a 1.06:1 step."""
    outline = decls.get("outline", "")
    if outline and not _REMOVES_OUTLINE.match(outline.split()[0]):
        return True
    if "text-decoration" in decls or "text-decoration-line" in decls:
        return True
    return _is_solid_halo(decls.get("box-shadow", ""))


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
        # A border-colour change and a faint halo are not indicators (#3276 review).
        ".a:focus { outline: none; border-color: hsl(var(--ring)); }",
        ".a:focus { outline: none; box-shadow: 0 0 0 2px hsl(var(--ring) / 0.2); }",
        ".a:focus { outline: none; border-color: red; box-shadow: 0 0 0 3px hsl(var(--ring) / 0.4); }",
        ".a:focus { outline: none; box-shadow: 0 0 6px 2px hsl(var(--ring)); }",
        ".a:focus { outline: none; box-shadow: 0 0 0 1px hsl(var(--ring)); }",
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
        ".a:focus { outline: none; box-shadow: 0 0 0 2px hsl(var(--bg)), 0 0 0 4px hsl(var(--ring)); }",
        ".a:focus { outline: 2px solid hsl(var(--ring)); border-color: hsl(var(--ring)); }",
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
