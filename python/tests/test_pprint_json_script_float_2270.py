"""``pprint`` and ``json_script`` spell a float with Python's ``repr`` (#2270).

#2258 fixed three of the five float→string sinks — ``Display``, the
``@stringfilter`` boundary and ``py_repr``. These are the other two, and both
predate it: neither is ``Display`` and neither is a ``@stringfilter``, so each
had its own arm spelling the float with Rust's ``{}``.

**The issue's table names ``1e20``, ``NaN`` and ``inf``, and the ordinary case is
none of those.** Rust's ``{}`` drops a float's trailing ``.0``, so
``{{ 1.0|pprint }}`` rendered ``1`` and ``{{ 0.0|json_script:"d" }}`` put a JSON
**integer** on the wire where Django puts a float. Measured over the float
spectrum × five container shapes, 303 of 648 cells moved DIFF→AGREE and the
exponent cases the issue leads with are a minority of them.

**The two sinks do NOT share a spelling, which is why one helper could not serve
both.** ``pprint.pformat(f)`` is ``repr(f)`` exactly. ``json.dumps(f)`` is
``repr(f)`` for a finite value and ``NaN`` / ``Infinity`` / ``-Infinity``
otherwise. On ``main`` the coincidences ran in opposite directions: Rust's
``NaN`` happened to match ``json.dumps`` while its ``inf`` did not, and Rust's
``inf`` happened to match ``pprint`` while its ``NaN`` did not.

**The ``Infinity`` decision.** ``django.utils.html.json_script`` calls
``json.dumps(value, cls=encoder or DjangoJSONEncoder)``; ``DjangoJSONEncoder``
overrides only ``default()``, so ``allow_nan`` stays ``True`` and Django really
does emit ``Infinity`` into a ``<script type="application/json">`` body.
``Infinity`` is not valid JSON — ``JSON.parse`` throws — and djust matches Django
anyway. See ``test_infinity_is_django_parity_and_is_not_parseable_json`` for the
reasoning and the recorded consequence.
"""

from __future__ import annotations

import json
import pprint as _pprint
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

# The same spectrum #2258 used, plus the values whose defect is the missing
# trailing `.0` rather than the exponent form.
FLOATS = [
    0.0,
    -0.0,
    1.0,
    -1.0,
    0.5,
    0.1,
    19.99,
    2.675,
    1e-5,
    1e-4,
    1e-3,
    1e15,
    1e16,
    1e17,
    1e20,
    1e100,
    1e199,
    1e200,
    1e201,
    1e300,
    -1e300,
    1.7976931348623157e308,
    1e-199,
    1e-200,
    1e-201,
    1e-300,
    5e-324,
    2.2250738585072014e-308,
    float(2**53),
    float(2**63),
    1.5e18,
    123456789.123456789,
    -1.5e-7,
    float("nan"),
    float("inf"),
    float("-inf"),
]


# `pprint` and `json_script` both RECURSE, so the container shapes are part of
# the surface and not decoration — a fix applied to the scalar arm only would
# pass every scalar row here and fail every nested one.
#
# A Python tuple is absent deliberately: `normalize_django_value` flattens it to
# a list at the PyO3 boundary, so `Value::Tuple` is unreachable from a view
# context and `{{ p }}` on a tuple already disagrees with Django on `main` for
# a reason that has nothing to do with floats. Tracked separately.
def shapes(f: float) -> list[tuple[str, Any]]:
    return [
        ("scalar", f),
        ("list", [f]),
        ("dict", {"k": f}),
        ("nested", [{"k": [f]}]),
        ("mixed", [1, "s", f, None, True]),
    ]


def render_both(source: str, value: Any) -> tuple[str, str]:
    django_out = DjangoTemplate(source).render(DjangoContext({"p": value}))
    djust_out = _rust.render_template(source, normalize_django_value({"p": value}))
    return django_out, djust_out


@pytest.mark.parametrize("value", FLOATS, ids=repr)
@pytest.mark.parametrize("shape", [s[0] for s in shapes(0.0)])
def test_pprint_agrees_with_django(shape: str, value: float) -> None:
    payload = dict(shapes(value))[shape]
    django_out, djust_out = render_both("{{ p|pprint }}", payload)
    assert djust_out == django_out


@pytest.mark.parametrize("value", FLOATS, ids=repr)
@pytest.mark.parametrize("shape", [s[0] for s in shapes(0.0)])
def test_json_script_agrees_with_django(shape: str, value: float) -> None:
    payload = dict(shapes(value))[shape]
    django_out, djust_out = render_both('{{ p|json_script:"d" }}', payload)
    assert djust_out == django_out


@pytest.mark.parametrize("value", FLOATS, ids=repr)
def test_pprint_is_pformat_and_pformat_is_repr(value: float) -> None:
    """Django-independent: the contract is CPython's, asserted against CPython.

    ``pprint.pformat`` delegates to ``repr`` for a float at every magnitude,
    which is what makes ``python_float_repr`` — written for #2258's ``repr``
    sites — the right helper here rather than a near-miss.
    """
    assert _pprint.pformat(value) == repr(value)
    _, djust_out = render_both("{{ p|pprint }}", value)
    assert djust_out == repr(value)


@pytest.mark.parametrize("value", FLOATS, ids=repr)
def test_json_script_body_is_json_dumps(value: float) -> None:
    """Django-independent twin: the body is exactly ``json.dumps``'s float."""
    _, djust_out = render_both('{{ p|json_script:"d" }}', [value])
    prefix = '<script id="d" type="application/json">'
    assert djust_out.startswith(prefix) and djust_out.endswith("</script>")
    body = djust_out[len(prefix) : -len("</script>")]
    assert body == json.dumps([value])


def test_the_two_sinks_disagree_on_the_non_finite_values() -> None:
    """One helper could not have served both, and the coincidences hid it.

    On ``main`` Rust's ``{}`` gave ``NaN``/``inf``/``-inf``. That matched
    ``json.dumps`` on NaN and not on the infinities; it matched ``pprint`` on
    the infinities and not on NaN. Half of each sink was accidentally right,
    which is why neither showed up as a whole-filter failure.
    """
    for value, want_pprint, want_json in [
        (float("nan"), "nan", "NaN"),
        (float("inf"), "inf", "Infinity"),
        (float("-inf"), "-inf", "-Infinity"),
    ]:
        dj_p, du_p = render_both("{{ p|pprint }}", value)
        assert du_p == dj_p == want_pprint
        dj_j, du_j = render_both('{{ p|json_script:"d" }}', value)
        assert du_j == dj_j
        assert want_json in du_j


def test_an_integral_float_keeps_its_point_zero_in_both() -> None:
    """The ordinary case the issue's table does not name.

    Rust's ``{}`` writes ``1.0`` as ``1``. In ``pprint`` that is a wrong repr;
    in ``json_script`` it changes the JSON **type** the client reads, from a
    float to an integer, for every whole-numbered value — which is what a money
    column or a percentage rounded to a whole number looks like.
    """
    assert render_both("{{ p|pprint }}", 1.0) == ("1.0", "1.0")
    assert render_both("{{ p|pprint }}", -0.0) == ("-0.0", "-0.0")
    _, body = render_both('{{ p|json_script:"d" }}', [1.0, 0.0, -0.0])
    assert ">[1.0, 0.0, -0.0]<" in body


def test_infinity_is_django_parity_and_is_not_parseable_json() -> None:
    """The decision, with its consequence recorded rather than implied (#2270).

    ``Infinity`` and ``NaN`` are Python extensions to JSON; ECMA-404 has
    neither, so a browser's ``JSON.parse`` throws on the body djust emits here.
    djust emits it anyway, because Django does:

    * ``null`` — what ``JSON.stringify`` writes — is valid and **silently**
      lossy. The client cannot tell an infinity from a ``None``. ``Infinity``
      fails loudly, at the parse site, naming the token.
    * Python's own ``json.loads`` accepts it, so a body read back server-side
      still carries the value. That is asserted below rather than described.
    * Answering ``null`` where Django answers ``Infinity`` would make djust the
      one that changed the data.

    This is deliberately not the #2241 outcome, and the two differ in both
    halves: there Django emitted VALID JSON and djust did not, so parity and
    validity agreed; and the mechanism was structure INJECTION from an
    attacker-reachable key. ``Infinity`` is a fixed token chosen from the
    float's own class and injects nothing.
    """
    prefix = '<script id="d" type="application/json">'
    django_out, djust_out = render_both(
        '{{ p|json_script:"d" }}', {"x": float("inf"), "y": float("nan")}
    )
    assert djust_out == django_out
    body = djust_out[len(prefix) : -len("</script>")]
    assert "Infinity" in body and "NaN" in body

    # The consequence, recorded: a strict parser rejects this. `json.loads` is
    # NOT strict (`parse_constant` accepts the three names), so the assertion
    # that shows the defect has to turn them off — which is what a browser does.
    assert json.loads(body) == {"x": float("inf"), "y": pytest.approx(float("nan"), nan_ok=True)}
    with pytest.raises(ValueError):
        json.loads(
            body,
            parse_constant=lambda name: (_ for _ in ()).throw(
                ValueError(f"strict JSON has no {name}")
            ),
        )

    # And `null` — the alternative that was rejected — is what Django does NOT
    # emit. Pinned so a future "make it valid JSON" change has to argue with the
    # docstring above rather than land quietly.
    assert "null" not in body
