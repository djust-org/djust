"""
Color conversion utilities for djust-theming.

Consolidates HSL, RGB, and hex color math in one place.
All functions are pure — no side effects or class dependencies.
"""

import colorsys
from typing import Tuple


def hsl_to_rgb(h: int, s: int, l: int) -> Tuple[int, int, int]:
    """Convert HSL (h: 0-360, s: 0-100, l: 0-100) to RGB (0-255 each)."""
    # colorsys uses HLS order (not HSL), with all values in 0-1 range
    r, g, b = colorsys.hls_to_rgb(h / 360.0, l / 100.0, s / 100.0)
    return (round(r * 255), round(g * 255), round(b * 255))


def rgb_to_hsl(r: int, g: int, b: int) -> Tuple[int, int, int]:
    """Convert RGB (0-255 each) to HSL (h: 0-360, s: 0-100, l: 0-100)."""
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    return (round(h * 360), round(s * 100), round(l * 100))


def hex_to_rgb(hex_str: str) -> Tuple[int, int, int]:
    """Convert hex string (#RRGGBB or RRGGBB, 3 or 6 digit) to RGB (0-255)."""
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join(c * 2 for c in hex_str)
    if len(hex_str) != 6:
        raise ValueError(f"Invalid hex color: #{hex_str}")
    try:
        return (int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16))
    except ValueError:
        raise ValueError(f"Invalid hex color: #{hex_str}")


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB (0-255 each) to hex string (#rrggbb, lowercase)."""
    return f"#{r:02x}{g:02x}{b:02x}"


def hex_to_hsl(hex_str: str) -> Tuple[int, int, int]:
    """Convert hex string to HSL tuple (h: 0-360, s: 0-100, l: 0-100)."""
    return rgb_to_hsl(*hex_to_rgb(hex_str))


def hsl_to_hex(h: int, s: int, l: int) -> str:
    """Convert HSL (h: 0-360, s: 0-100, l: 0-100) to hex string."""
    return rgb_to_hex(*hsl_to_rgb(h, s, l))
