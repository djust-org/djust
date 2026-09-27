"""Theme colour tokens are bare HSL triplets; every colour use must wrap them (#3209).

The theme generator emits colour tokens as bare HSL components, for example
``--link: 28 80% 35%``. Used as ``color: var(--link)``, the computed value is
the string ``28 80% 35%``, which is not a colour, so the browser drops the
declaration at computed-value time and the element silently inherits. The same
goes for ``rgba(var(--primary) / 0.3)``: a triplet with percentages is not an
RGB channel list. Only ``hsl()`` / ``hsla()`` turn a triplet into a colour.

This test scans every piece of CSS djust ships (static ``.css`` files, inline
``<style>`` in templates, and CSS written as string literals in the Python
generators) and fails on any ``var(--<hsl-token>)`` whose nearest enclosing
non-``var`` function is not ``hsl``/``hsla``. The token list is taken from the
generator's own output, so a new token is covered without editing this file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from djust.theming.css_generator import ThemeCSSGenerator
from djust.theming.presets import THEME_PRESETS

pytestmark = pytest.mark.theming

PACKAGE_ROOT = Path(__file__).resolve().parents[1]  # python/djust

# A generated declaration whose value is a bare HSL triplet: ``--x: 28 80% 35%;``
_TRIPLET_DECL = re.compile(r"--([\w-]+):\s*-?[\d.]+(?:deg)?\s+[\d.]+%\s+[\d.]+%\s*;")
# ``prop: value`` up to the end of the declaration. In a stylesheet a value may
# span lines. In a template or a Python file the CSS sits inside a string or a
# ``style="..."`` attribute, so a quote also ends it, and a value stops at the
# line end so prose (a docstring's ``Properties:`` list) is never read as CSS.
_DECL_CSS = re.compile(r"(?<![\w-])(-{0,2}[a-zA-Z][\w-]*)\s*:\s*([^;{}]*)[;}]")
_DECL_EMBEDDED = re.compile(r"(?<![\w-])(-{0,2}[a-zA-Z][\w-]*)\s*:\s*([^;{}\"'\n]*)[;}\"']")
# A quoted key holding a quoted value: ``{"color": "var(--primary)"}`` in a
# Python or JS mapping that ends up in a ``style`` attribute (#3209 review,
# ``components/meter.py``'s docstring example).
_DECL_QUOTED_KEY = re.compile(r"[\"']([\w-]+)[\"']\s*:\s*[\"']([^\"'\n]*)[\"']")
_STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
_VAR = re.compile(r"var\(\s*--([\w-]+)")
_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _hsl_tokens() -> frozenset[str]:
    """Every custom property the theme generator emits as a bare HSL triplet."""
    names: set[str] = set()
    for preset in THEME_PRESETS:
        names.update(_TRIPLET_DECL.findall(ThemeCSSGenerator(preset).generate_variables_only()))
    return frozenset(names)


HSL_TOKENS = _hsl_tokens()


def _enclosing_function(value: str, pos: int) -> str | None:
    """Name of the nearest function around ``value[pos]`` that is not ``var``."""
    depth = 0
    for i in range(pos - 1, -1, -1):
        ch = value[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth:
                depth -= 1
                continue
            match = re.search(r"([\w-]+)$", value[:i])
            name = match.group(1).lower() if match else ""
            if name != "var":
                return name
    return None


def bare_token_uses(
    css: str, tokens: frozenset[str] = HSL_TOKENS, *, stylesheet: bool = False
) -> list[tuple[int, str]]:
    """Return ``(line, declaration)`` for each unwrapped HSL-token colour use."""
    # Blank comments out but keep their newlines so line numbers stay right.
    css = _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), css)
    if stylesheet:
        decls = list(_DECL_CSS.finditer(css))
    else:
        decls = list(_DECL_EMBEDDED.finditer(css)) + list(_DECL_QUOTED_KEY.finditer(css))
        # Inside an inline ``<style>`` block a value may span lines, as in a
        # stylesheet. Blank everything outside the blocks, keeping newlines,
        # so line numbers stay those of the file.
        only_styles = "".join(
            re.sub(r"[^\n]", " ", css[prev : m.start(1)]) + m.group(1)
            for prev, m in _pairs_with_previous_end(_STYLE_BLOCK.finditer(css))
        )
        decls += list(_DECL_CSS.finditer(only_styles))
    found = []
    seen = set()
    for decl in decls:
        prop, value = decl.group(1), decl.group(2)
        if prop.startswith("--"):
            # Aliasing a token (``--x: var(--primary)``) keeps it a triplet;
            # the consumer of ``--x`` is checked where it is used.
            continue
        for use in _VAR.finditer(value):
            if use.group(1) not in tokens:
                continue
            if _enclosing_function(value, use.start()) not in ("hsl", "hsla"):
                line = decl.string.count("\n", 0, decl.start()) + 1
                if (line, prop) not in seen:
                    seen.add((line, prop))
                    found.append((line, f"{prop}: {value.strip()}"))
                break
    return sorted(found)


def _pairs_with_previous_end(matches):
    """``(end of the previous match, match)`` for each match, starting at 0."""
    prev = 0
    for match in matches:
        yield prev, match
        prev = match.end(1)


def _shipped_css_sources() -> list[Path]:
    sources = [p for p in PACKAGE_ROOT.rglob("*.css")]
    sources += [p for p in PACKAGE_ROOT.rglob("*.html")]
    sources += [p for p in PACKAGE_ROOT.rglob("*.js")]
    sources += [
        p for p in PACKAGE_ROOT.rglob("*.py") if "tests" not in p.relative_to(PACKAGE_ROOT).parts
    ]
    return sorted(p for p in sources if "node_modules" not in p.parts)


def test_token_list_comes_from_the_generator():
    # Guards the scan against silently matching nothing.
    assert {"link", "primary", "foreground", "muted-foreground", "border"} <= HSL_TOKENS


@pytest.mark.parametrize(
    "css",
    [
        ".a { color: var(--link, var(--primary)); }",
        ".a { color: var(--foreground); }",
        ".a { border-top: 1px solid var(--border); }",
        ".a { box-shadow: 0 0 20px rgba(var(--primary) / 0.3); }",
        ".a { color: var(--muted-foreground, var(--color-text-secondary)); }",
        '<div style="color: var(--primary)">',
        '{"value": 40, "color": "var(--primary)", "label": "Used"}',
        "el.style = {'background': 'var(--warning)'};",
        "<style>\n.a {\n  box-shadow:\n    0 0 4px var(--primary);\n}\n</style>",
    ],
)
def test_scanner_flags_bare_token_colours(css):
    assert bare_token_uses(css), css


def test_scanner_reads_a_stylesheet_value_across_lines():
    css = ".a {\n  box-shadow:\n    0 0 0 1px hsl(var(--ring)),\n    0 0 4px var(--primary);\n}"
    [(line, decl)] = bare_token_uses(css, stylesheet=True)
    assert line == 2 and "var(--primary)" in decl


@pytest.mark.parametrize(
    "css",
    [
        ".a { color: hsl(var(--link, var(--primary))); }",
        ".a { color: hsl(var(--foreground) / 0.5); }",
        ".a { border: 1px solid hsla(var(--border), 1); }",
        ".a { --x: var(--primary); }",
        ".a { padding: var(--space-4, 1rem); }",
        "/* color: var(--primary); */",
        '{"color": "hsl(var(--primary))"}',
        '{"rounded": "var(--radius)"}',
        "Properties:\n    --dj-x: toggle colour (default: var(--primary, #2563eb))\n",
    ],
)
def test_scanner_accepts_wrapped_or_non_colour_uses(css):
    assert not bare_token_uses(css), css


def test_no_shipped_css_uses_an_hsl_token_without_hsl():
    offenders = []
    for path in _shipped_css_sources():
        text = path.read_text(encoding="utf-8")
        for line, decl in bare_token_uses(text, stylesheet=path.suffix == ".css"):
            offenders.append(f"{path.relative_to(PACKAGE_ROOT)}:{line}: {decl}")
    assert not offenders, (
        "Theme colour tokens are bare HSL triplets; wrap them in hsl():\n  "
        + "\n  ".join(offenders)
    )
