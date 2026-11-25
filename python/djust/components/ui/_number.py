"""Number text for the simple components (#4002).

``Progress`` and ``Range`` print their numbers the way the retired Rust components printed an
``f64`` (``format!("{}", x)``): a whole number without a fraction (``40``, not ``40.0``), any
other the shortest text that reads back as the same float, never in exponent notation.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any


def number_text(value: Any) -> str:
    """``value`` as a float, written the way Rust's ``Display`` for ``f64`` writes it."""
    number = float(value)
    if math.isnan(number):
        return "NaN"
    if math.isinf(number):
        return "inf" if number > 0 else "-inf"
    if number.is_integer():
        text = str(int(number))
        return "-0" if text == "0" and math.copysign(1.0, number) < 0 else text
    text = repr(number)
    if "e" in text or "E" in text:
        text = format(Decimal(text), "f")
    return text
