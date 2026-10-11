"""
Shared utilities for djust-components.

Centralises functions and constants that were previously duplicated across
templatetags, rust_handlers, mixins, and component classes.
"""

import re
from typing import Any

__all__ = [
    "CURRENCY_SYMBOLS",
    "format_cell",
    "interpolate_color",
    "interpolate_color_gradient",
    "safe_css_color",
    "safe_css_length",
    "safe_css_number",
    "safe_css_ratio",
]


# ---------------------------------------------------------------------------
# Currency symbols
# ---------------------------------------------------------------------------

CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "\u20ac",
    "GBP": "\u00a3",
    "JPY": "\u00a5",
    "CAD": "CA$",
    "AUD": "A$",
    "CHF": "CHF",
    "CNY": "\u00a5",
    "INR": "\u20b9",
    "BRL": "R$",
    "KRW": "\u20a9",
    "MXN": "MX$",
}


# ---------------------------------------------------------------------------
# Cell formatting
# ---------------------------------------------------------------------------


def format_cell(value, col):
    """Format a cell value based on column type declaration.

    Supported types: number, currency, date, percentage, boolean.

    Args:
        value: The raw cell value.
        col: Column definition dict (or non-dict, in which case the value is
             simply stringified).

    Returns:
        Formatted string.
    """
    if not isinstance(col, dict):
        return str(value) if value is not None else ""
    col_type = col.get("type", "")
    if not col_type or value is None or value == "":
        return str(value) if value is not None else ""

    if col_type == "number":
        try:
            num = float(value)
            decimals = col.get("decimals", 0)
            if decimals > 0:
                return f"{num:,.{decimals}f}"
            if num == int(num):
                return f"{int(num):,}"
            return f"{num:,.2f}"
        except (ValueError, TypeError):
            return str(value)
    elif col_type == "currency":
        try:
            num = float(value)
            symbol = col.get("currency_symbol", "$")
            decimals = col.get("decimals", 2)
            return f"{symbol}{num:,.{decimals}f}"
        except (ValueError, TypeError):
            return str(value)
    elif col_type == "percentage":
        try:
            num = float(value)
            decimals = col.get("decimals", 1)
            return f"{num:.{decimals}f}%"
        except (ValueError, TypeError):
            return str(value)
    elif col_type == "boolean":
        truthy = str(value).lower() in ("true", "1", "yes")
        true_label = col.get("true_label", "Yes")
        false_label = col.get("false_label", "No")
        return true_label if truthy else false_label
    elif col_type == "date":
        fmt = col.get("date_format", "")
        if fmt and hasattr(value, "strftime"):
            try:
                return value.strftime(fmt)
            except (ValueError, AttributeError):
                return str(value)
        return str(value)
    return str(value)


# ---------------------------------------------------------------------------
# Color interpolation
# ---------------------------------------------------------------------------


def interpolate_color(c1, c2, t):
    """Linearly interpolate between two hex colors.

    Args:
        c1: Start hex color (e.g. ``"#f0f9ff"``).
        c2: End hex color.
        t: Interpolation factor 0.0 .. 1.0.

    Returns:
        Interpolated hex color string.
    """

    def parse_hex(c):
        c = c.lstrip("#")
        if len(c) == 3:
            c = c[0] * 2 + c[1] * 2 + c[2] * 2
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)

    r1, g1, b1 = parse_hex(c1)
    r2, g2, b2 = parse_hex(c2)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def interpolate_color_gradient(colors, ratio):
    """Interpolate across a multi-stop color gradient.

    Args:
        colors: List of hex color strings (at least 1).
        ratio: Position in the gradient, 0.0 .. 1.0.

    Returns:
        Interpolated hex color string.
    """
    if len(colors) < 2:
        return colors[0] if colors else "#000000"

    if len(colors) == 2:
        idx = 0
        local_ratio = ratio
    else:
        segments = len(colors) - 1
        segment = min(int(ratio * segments), segments - 1)
        idx = segment
        local_ratio = (ratio * segments) - segment

    c1 = colors[idx]
    c2 = colors[idx + 1] if idx + 1 < len(colors) else colors[idx]
    return interpolate_color(c1, c2, local_ratio)


# ---------------------------------------------------------------------------
# CSS values written into a style attribute
# ---------------------------------------------------------------------------
#
# HTML escaping keeps a value inside its attribute, but not inside its CSS
# declaration: a value such as ``red;position:fixed`` would add declarations.
# The helpers below accept only the value shapes a component documents and
# return ``default`` for anything else. A value that is accepted is returned
# unchanged, so callers keep escaping it as before.

_CSS_NAMED_COLORS = frozenset(
    """
    aliceblue antiquewhite aqua aquamarine azure beige bisque black
    blanchedalmond blue blueviolet brown burlywood cadetblue chartreuse
    chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan
    darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta
    darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen
    darkslateblue darkslategray darkslategrey darkturquoise darkviolet
    deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite
    forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green
    greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender
    lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
    lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon
    lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue
    lightyellow lime limegreen linen magenta maroon mediumaquamarine
    mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue
    mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream
    mistyrose moccasin navajowhite navy oldlace olive olivedrab orange
    orangered orchid palegoldenrod palegreen paleturquoise palevioletred
    papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red
    rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell
    sienna silver skyblue slateblue slategray slategrey snow springgreen
    steelblue tan teal thistle tomato turquoise violet wheat white
    whitesmoke yellow yellowgreen transparent currentcolor
    """.split()
)

_CSS_MAX_VALUE_LEN = 200
_CSS_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
# A custom property reference such as ``var(--primary)`` (no fallback).
_CSS_VAR = r"var\(\s*--[A-Za-z0-9_-]+\s*\)"
_CSS_COLOR_ARG = rf"(?:{_CSS_NUM}(?:%|deg|grad|rad|turn)?|none|{_CSS_VAR})"
_CSS_COLOR_SEP = r"(?:\s*[,/]\s*|\s+)"
_HEX_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
_COLOR_FUNC_RE = re.compile(
    rf"(?:rgba?|hsla?)\(\s*{_CSS_COLOR_ARG}(?:{_CSS_COLOR_SEP}{_CSS_COLOR_ARG}){{0,3}}\s*\)",
    re.IGNORECASE,
)
_CSS_VAR_RE = re.compile(_CSS_VAR)


def safe_css_color(value: Any, default: str = "") -> str:
    """Return ``value`` if it is a plain CSS colour, else ``default``.

    Accepted: ``#rgb``, ``#rgba``, ``#rrggbb``, ``#rrggbbaa``; a CSS named
    colour (including ``transparent`` and ``currentcolor``, any case);
    ``rgb()``/``rgba()``/``hsl()``/``hsla()`` whose arguments are plain
    numbers, percentages, angles or ``var(--name)`` references; and a bare
    ``var(--name)`` reference (the theme palette, e.g. ``hsl(var(--primary))``).
    Surrounding whitespace is allowed. Anything else returns ``default``.
    """
    if value is None:
        return default
    text = str(value)
    candidate = text.strip()
    if not candidate or len(candidate) > _CSS_MAX_VALUE_LEN:
        return default
    if (
        _HEX_COLOR_RE.fullmatch(candidate)
        or candidate.lower() in _CSS_NAMED_COLORS
        or _COLOR_FUNC_RE.fullmatch(candidate)
        or _CSS_VAR_RE.fullmatch(candidate)
    ):
        return text
    return default


_CSS_LENGTH_KEYWORDS = frozenset(
    {
        "auto",
        "none",
        "inherit",
        "initial",
        "unset",
        "revert",
        "fit-content",
        "min-content",
        "max-content",
    }
)
_CSS_LENGTH_CHARS_RE = re.compile(r"[A-Za-z0-9.%+\-*/ ,()_]+")
_CSS_LENGTH_UNITS = (
    "cqmin|cqmax|vmin|vmax|cqw|cqh|cqi|cqb|svh|svw|lvh|lvw|dvh|dvw|rem|rlh|"
    "px|em|ex|ch|vw|vh|vi|vb|cm|mm|in|pt|pc|fr|lh|q"
)
_CSS_LENGTH_NUM_RE = re.compile(
    rf"{_CSS_NUM}(?:%|(?:{_CSS_LENGTH_UNITS})(?![A-Za-z0-9_]))?", re.IGNORECASE
)
_CSS_LENGTH_FUNC_RE = re.compile(r"(?:calc|min|max|clamp)\(", re.IGNORECASE)
_CSS_LENGTH_REST_RE = re.compile(r"[0\s+\-*/,()]*")


def safe_css_length(value: Any, default: str = "") -> str:
    """Return ``value`` if it is a plain CSS length, else ``default``.

    Accepted: numbers with an optional length unit or ``%`` (one value or
    several separated by spaces, as ``gap`` takes); the keywords ``auto``,
    ``none``, ``inherit``, ``initial``, ``unset``, ``revert``, ``fit-content``,
    ``min-content`` and ``max-content``; ``var(--name)`` references; and
    ``calc()``, ``min()``, ``max()`` and ``clamp()`` built from those.
    Anything else (another function, a quote, ``;``, ``:``, a backslash,
    a comment) returns ``default``.
    """
    if value is None:
        return default
    text = str(value)
    candidate = text.strip()
    if not candidate or len(candidate) > _CSS_MAX_VALUE_LEN:
        return default
    if candidate.lower() in _CSS_LENGTH_KEYWORDS:
        return text
    if not _CSS_LENGTH_CHARS_RE.fullmatch(candidate) or "/*" in candidate or "*/" in candidate:
        return default
    rest = _CSS_VAR_RE.sub("0", candidate)
    rest = _CSS_LENGTH_NUM_RE.sub("0", rest)
    rest = _CSS_LENGTH_FUNC_RE.sub("(", rest)
    if not _CSS_LENGTH_REST_RE.fullmatch(rest):
        return default
    depth = 0
    for ch in rest:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return default
    return text if depth == 0 else default


_CSS_RATIO_RE = re.compile(
    rf"(?:auto\s+)?{_CSS_NUM}(?:\s*/\s*{_CSS_NUM})?(?:\s+auto)?|auto", re.IGNORECASE
)


def safe_css_ratio(value: Any, default: str = "") -> str:
    """Return ``value`` if it is a CSS ``aspect-ratio`` (``16/9``, ``1.5``,
    ``auto``, ``auto 4/3``), else ``default``."""
    if value is None:
        return default
    text = str(value)
    candidate = text.strip()
    if candidate and len(candidate) <= _CSS_MAX_VALUE_LEN and _CSS_RATIO_RE.fullmatch(candidate):
        return text
    return default


_CSS_NUMBER_RE = re.compile(_CSS_NUM)
_CSS_INTEGER_RE = re.compile(r"[+-]?\d+")


def safe_css_number(value: Any, default: str = "", *, integer: bool = False) -> str:
    """Return ``value`` (as text) if it is a plain number, else ``default``.

    With ``integer=True`` only whole numbers (and ``auto``, as ``z-index``
    takes) are accepted."""
    if value is None or isinstance(value, bool):
        return default
    text = str(value)
    candidate = text.strip()
    if len(candidate) > _CSS_MAX_VALUE_LEN:
        return default
    if integer:
        ok = bool(_CSS_INTEGER_RE.fullmatch(candidate)) or candidate.lower() == "auto"
    else:
        ok = bool(_CSS_NUMBER_RE.fullmatch(candidate))
    return text if ok else default
