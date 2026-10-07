"""CPython's own spelling of the scalar type names djust mirrors (#3255).

djust reproduces CPython's ``'X' object is not iterable`` wording verbatim, so
the tests that pin that wording need CPython's own answer. Most of those names
are constants, but ``Decimal``'s is not:

* the C ``_decimal`` accelerator is a **static** type, and CPython spells a
  static type with its module — ``decimal.Decimal``;
* without it, ``decimal`` falls back to the pure-Python ``_pydecimal``, a
  **heap** type whose ``tp_name`` is the bare ``__name__`` — ``Decimal``.

Which one is installed is a property of the INTERPRETER, not of the Python
version, so a literal expectation is wrong on any build without the C
accelerator — CI's Python 3.15 build is one, which is why those cells were red
there while passing everywhere else.

Ask CPython; never list.
"""

from __future__ import annotations

import decimal


def iter_refusal_type_name(value: object) -> str:
    """The type name CPython puts in ``'X' object is not iterable``.

    Read out of CPython's own ``TypeError`` rather than assumed, so the answer
    tracks whichever ``decimal`` implementation this interpreter has.
    """
    try:
        iter(value)  # type: ignore[call-overload]
    except TypeError as exc:
        return str(exc).split("'")[1]
    raise AssertionError(f"{value!r} is iterable — there is no refusal message to read")


#: ``Decimal``'s spelling on THIS interpreter.
DECIMAL_TYPE_NAME = iter_refusal_type_name(decimal.Decimal("2.5"))
