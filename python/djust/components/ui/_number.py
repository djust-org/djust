"""Number text for the simple components (#4002).

``Range`` prints its numbers the way the retired Rust component printed an ``f64``
(``format!("{}", x)``): a whole number without a fraction (``40``, not ``40.0``), any other the
shortest text that reads back as the same float, never in exponent notation. A value that is
not a number (``"abc"``, ``None``) is printed as escaped text, as the template the crate fell
back to printed it.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from djust.components.utils import attr_text


def number_text(value: Any) -> str:
    """``value`` as a float, written the way Rust's ``Display`` for ``f64`` writes it.

    The result is HTML-safe: a number's text needs no escaping, and anything ``float()``
    refuses is returned as ``attr_text(value)`` (escaped; ``None`` is ``"None"``).
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return attr_text(value)
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
