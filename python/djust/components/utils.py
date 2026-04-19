"""
Shared utilities for djust-components.

Centralises functions and constants that were previously duplicated across
templatetags, rust_handlers, mixins, and component classes.
"""

__all__ = [
    "CURRENCY_SYMBOLS",
    "format_cell",
    "interpolate_color",
    "interpolate_color_gradient",
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
