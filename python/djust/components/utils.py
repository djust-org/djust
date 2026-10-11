"""
Shared utilities for djust-components.

Centralises functions and constants that were previously duplicated across
templatetags, rust_handlers, mixins, and component classes.
"""

import re
from typing import Any
from urllib.parse import urlsplit

from django.utils.html import conditional_escape
from django.utils.safestring import SafeData

__all__ = [
    "CURRENCY_SYMBOLS",
    "format_cell",
    "interpolate_color",
    "interpolate_color_gradient",
    "safe_css_color",
    "safe_css_length",
    "safe_css_number",
    "safe_css_ratio",
    "safe_url",
    "url_attr",
]


# ---------------------------------------------------------------------------
# Currency symbols
# ---------------------------------------------------------------------------

CURRENCY_SYMBOLS: dict[str, str] = {
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


def format_cell(value: Any, col: Any) -> str:
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
        return str(true_label if truthy else false_label)
    elif col_type == "date":
        fmt = col.get("date_format", "")
        if fmt and hasattr(value, "strftime"):
            try:
                return str(value.strftime(fmt))
            except (ValueError, AttributeError):
                return str(value)
        return str(value)
    return str(value)


# ---------------------------------------------------------------------------
# Color interpolation
# ---------------------------------------------------------------------------


def interpolate_color(c1: str, c2: str, t: float) -> str:
    """Linearly interpolate between two hex colors.

    Args:
        c1: Start hex color (e.g. ``"#f0f9ff"``).
        c2: End hex color.
        t: Interpolation factor 0.0 .. 1.0.

    Returns:
        Interpolated hex color string.
    """

    def parse_hex(c: str) -> tuple[int, int, int]:
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


def interpolate_color_gradient(colors: list[str], ratio: float) -> str:
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


# ---------------------------------------------------------------------------
# URL attributes
# ---------------------------------------------------------------------------

# Schemes allowed in an href/action navigation context. Anything else
# (javascript:, vbscript:, data:, …) is replaced with "#".
_SAFE_URL_SCHEMES = frozenset({"http", "https", "mailto", "tel", "ftp", "ftps"})

# Browsers ignore leading/embedded ASCII control chars + whitespace when
# resolving a URL scheme, so a value such as "java\tscript:" / "java\nscript:"
# / "java\x00script:" still reads as that scheme. Strip them ALL before the
# scheme probe so the evasions collapse to the canonical form.
_CTRL_WS_RE = re.compile(r"[\x00-\x20]+")


def safe_url(value: Any) -> str:
    """Escape a URL for an HTML attribute AND neutralize dangerous schemes.

    HTML-escaping (``conditional_escape``) prevents attribute breakout but does
    NOT stop a ``javascript:`` URI (which needs no escapable characters) — so a
    built-in component rendering a user-supplied URL into ``href``/``action``
    must validate the scheme too. Use this at every
    navigation-context URL sink in the component tags; it is NOT for ``<img src>``
    (where ``javascript:`` doesn't execute and ``data:`` images are legitimate).

    Policy:
      * Relative / anchor / query / scheme-less URLs (``/x``, ``#frag``, ``?q=1``)
        → allowed (HTML-escaped).
      * Absolute URL whose scheme is in :data:`_SAFE_URL_SCHEMES` → allowed.
      * Any other scheme (``javascript:``/``vbscript:``/``data:``/…), including
        control-char/whitespace-obfuscated variants, and any value that fails to
        parse → replaced with ``"#"`` (fail-closed).
    """
    s = str(value).strip()
    if not s:
        return ""
    try:
        scheme = urlsplit(s).scheme.lower()
    except ValueError:
        return "#"
    probe = _CTRL_WS_RE.sub("", s).lower()
    if scheme and scheme not in _SAFE_URL_SCHEMES:
        return "#"
    # Belt-and-suspenders: catch parser-evasion where the obfuscated value has
    # an empty/odd urlsplit scheme but a browser would still see a bad scheme.
    if probe.startswith(("javascript:", "vbscript:", "data:")):
        return "#"
    escaped: str = conditional_escape(s)
    return escaped


# Image sources may legitimately be inline ``data:image/...`` URIs.
_DATA_IMAGE_RE = re.compile(r"^data:image/[a-z0-9.+-]+[;,]")


def url_attr(value: Any, *, image: bool = False) -> str:
    """Return ``value`` ready to place inside a quoted ``href``/``src``/``action``.

    Values the developer marked safe (``mark_safe`` / ``SafeString``) are
    returned unchanged. Anything else goes through :func:`safe_url`: it is
    HTML-escaped, and a URL whose scheme is not a navigation scheme
    (``javascript:``, ``vbscript:``, ``data:``, ...) becomes ``"#"``. With
    ``image=True`` an inline ``data:image/...`` URI is also accepted (escaped),
    for ``<img src>``.
    """
    if value is None:
        return ""
    if isinstance(value, SafeData):
        return str(value)
    if image:
        probe = _CTRL_WS_RE.sub("", str(value)).lower()
        if _DATA_IMAGE_RE.match(probe):
            escaped: str = conditional_escape(str(value).strip())
            return escaped
    return safe_url(value)


def rich_html(value: Any) -> str:
    """HTML for a slot whose content is itself HTML, such as an editor's value.

    Values marked safe pass through unchanged. Anything else is cleaned to
    the Markdown component's tag and attribute allowlist, so stored rich text
    keeps its formatting while scripts, event-handler attributes and
    non-http(s)/mailto URLs are removed.
    """
    if value is None:
        return ""
    if isinstance(value, SafeData):
        return str(value)
    from .components.markdown import _sanitize

    return _sanitize(str(value))
