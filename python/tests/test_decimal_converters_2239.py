"""The Python `Decimal` converters keep every digit (#2239).

#2214 fixed the Rust half and deferred the Python pair —
`DjangoJSONEncoder.default` and `normalize_django_value` — because three
verified constraints pulled against each other: they are a tested pair, `str`
regresses templates, and the raw `Decimal` breaks a JSON consumer.

The audit that closes it found the pair is one function with **three**
destinations, not one:

| destination | who | Decimal must be |
|---|---|---|
| template context | `mixins/jit.py`, `mixins/context.py`, `template/rendering.py`, `mixins/rust_bridge.py` | the `Decimal` — Rust carries it as `Value::Decimal` (#2214) |
| client wire | `websocket.py`, `sse.py`, `api/dispatch.py` | the exact digit string — what Django's own encoder returns |
| round trip back ONTO the view | the Django session (`mixins/request.py`, `runtime.py`, `mixins/sticky.py`, `mixins/components.py`) and the signed snapshot (`live_view.py`) | `float`, and still lossy — see below |

The third is the residue this PR does NOT close. Django's session serializer is
`json.dumps(obj, separators=(",", ":"))` with no encoder, so it cannot take the
`Decimal`; and a string restored into view state is a string in the template on
the very next render, which is the regression that blocked #2214 arriving one
hop later. Those boundaries keep today's `float` through the single
`decimal_for_state_roundtrip` chokepoint. The lossless fix needs a TAGGED
round trip plus a decode at every restore site — a separate change, #2252.

Every template and encoder assertion below is a **differential against real
Django**, not a hand-written table (v1.1.1-2 retro): Django is importable here,
so there is no reason to guess. The curated cases are paired with a randomized
sweep, because a table samples the axis you thought of.
"""

from __future__ import annotations

import ast
import json
import pathlib
import random
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("django")

from django.core.serializers.json import DjangoJSONEncoder as RealDjangoEncoder  # noqa: E402
from django.core.signing import JSONSerializer as DjangoSessionSerializer  # noqa: E402
from django.db import models  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import DjangoJSONEncoder as DjustEncoder  # noqa: E402
from djust.serialization import (  # noqa: E402
    StateRoundtripJSONEncoder,
    decimal_for_state_roundtrip,
    normalize_django_value,
)

#: 29 significant digits. A binary double holds ~15, so this is the value that
#: makes the loss visible rather than merely present.
HUGE = Decimal("12345678901234567890.123456789")

#: Every idiom a `DecimalField` plausibly meets in a template — the three the
#: issue names plus the neighbours a fix could break.
VALUES = [Decimal("19.99"), Decimal("0.00"), Decimal("-3.5"), HUGE]

#: (template, str(value)) pairs where djust and Django disagree — measured, not
#: assumed.
#:
#: EMPTY since #2253. It held four cells when #2239 shipped, all in the Rust
#: filter layer and all out of scope for a Python-converter fix. #2253 closed
#: them by porting Django's `floatformat` algorithm (exact decimal digits,
#: `ROUND_HALF_UP`, the `-1` default, the `g` suffix) and by widening `add`'s
#: arithmetic to exact `i128`. The set is kept, empty, because
#: `test_the_known_divergences_are_exactly_those_four` asserts it as a SET —
#: so a future regression re-populates it and goes red, which a deleted
#: constant could not do (#1125).
KNOWN_FILTER_DIVERGENCES: set[tuple[str, str]] = set()

TEMPLATES = [
    "{{ p }}",
    "{{ p|floatformat }}",
    "{{ p|floatformat:2 }}",
    "{% if p > 10 %}BIG{% else %}small{% endif %}",
    "{% if p == 0 %}ZERO{% else %}NONZERO{% endif %}",
    "{{ p|add:1 }}",
    "{% if p %}T{% else %}F{% endif %}",
]

_PriceModel = type(
    "D2239PricedThing",
    (models.Model,),
    {
        "__module__": __name__,
        "name": models.CharField(max_length=50, default=""),
        "price": models.DecimalField(max_digits=40, decimal_places=9, default=Decimal("0")),
        # The @property is the point: the Rust serializer cannot read one, so a
        # template that references it sends the WHOLE object down the
        # `normalize_django_value` fallback in mixins/jit.py.
        "label": property(lambda self: f"{self.name} @ {self.price}"),
        "__str__": lambda self: f"thing({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
    },
)


def _priced(price: Decimal) -> Any:
    obj = _PriceModel(name="widget", price=price)
    obj.pk = 3
    obj.id = 3
    return obj


# ---------------------------------------------------------------------------
# The encoder — a differential against Django's own.
# ---------------------------------------------------------------------------


class TestEncoderMatchesRealDjango:
    """djust's `DjangoJSONEncoder` shadows Django's name; for every type they
    both handle it must now also produce the same bytes."""

    @pytest.mark.parametrize(
        "value",
        [
            Decimal("19.99"),
            Decimal("0"),
            Decimal("0.00"),
            Decimal("-3.5"),
            HUGE,
            Decimal("1E-10"),
            Decimal("1E+10"),
        ],
        ids=["simple", "zero", "zero_scaled", "negative", "huge", "tiny_exp", "big_exp"],
    )
    def test_decimal_bytes_match(self, value: Decimal) -> None:
        assert json.dumps(value, cls=DjustEncoder) == json.dumps(value, cls=RealDjangoEncoder)

    def test_the_huge_value_keeps_every_digit(self) -> None:
        """The bug, stated as the reporter measured it."""
        out = json.loads(json.dumps(HUGE, cls=DjustEncoder))
        assert out == "12345678901234567890.123456789"
        assert "e+" not in out.lower()

    def test_randomized_decimal_sweep_matches_django(self) -> None:
        """A curated table samples one axis; this sweeps the space.

        1,000 decimals across sign, magnitude and scale — the shapes a
        `DecimalField` actually holds — each compared byte-for-byte with
        Django's encoder.
        """
        rng = random.Random(22390)
        for _ in range(1000):
            digits = rng.randint(1, 30)
            scale = rng.randint(0, 12)
            sign = "-" if rng.random() < 0.4 else ""
            raw = "".join(str(rng.randint(0, 9)) for _ in range(digits))
            value = Decimal(f"{sign}{raw}E-{scale}")
            assert json.dumps(value, cls=DjustEncoder) == json.dumps(
                value, cls=RealDjangoEncoder
            ), f"diverged on {value!r}"

    @pytest.mark.parametrize(
        "value",
        [
            None,
            True,
            0,
            42,
            -7,
            3.14,
            "",
            "hello",
            __import__("uuid").UUID("12345678-1234-5678-1234-567812345678"),
            __import__("datetime").datetime(2024, 6, 15, 12, 30, 45),
            __import__("datetime").date(2024, 6, 15),
            __import__("datetime").time(8, 0, 0),
        ],
        ids=[
            "None",
            "bool",
            "zero",
            "int",
            "neg_int",
            "float",
            "empty_str",
            "str",
            "UUID",
            "datetime",
            "date",
            "time",
        ],
    )
    def test_every_other_shared_type_still_matches_django(self, value: Any) -> None:
        """The fix narrowed one branch; this is the guard that it narrowed only
        that one."""
        assert json.dumps(value, cls=DjustEncoder) == json.dumps(value, cls=RealDjangoEncoder)

    def test_timedelta_is_a_known_pre_existing_gap(self) -> None:
        """Not in the sweep above, and stated rather than quietly omitted.

        Django's encoder handles `timedelta`; djust's does not (only
        `normalize_django_value` does, as a documented enhancement). Predates
        #2239 and is untouched by it — pinned so the omission above is a fact
        about the code and not about the test's parameter list.
        """
        td = __import__("datetime").timedelta(days=1, seconds=90)
        assert json.dumps(td, cls=RealDjangoEncoder) == '"P1DT00H01M30S"'
        with pytest.raises(TypeError):
            json.dumps(td, cls=DjustEncoder)
        assert normalize_django_value(td) == "P1DT00H01M30S"

    def test_the_differential_would_catch_a_regression(self) -> None:
        """Gate-off for the harness (#1468): a wrong answer must fail it."""
        assert json.dumps(Decimal("19.99"), cls=DjustEncoder) != json.dumps(
            Decimal("19.98"), cls=RealDjangoEncoder
        )


# ---------------------------------------------------------------------------
# The template path — the reason the answer here is NOT the encoder's answer.
# ---------------------------------------------------------------------------


class TestTemplatePathMatchesRealDjango:
    """`normalize_django_value`'s output is written into the template context,
    so the renderer sees it. Rendering the normalized value must equal Django
    rendering the original."""

    @pytest.mark.parametrize("source", TEMPLATES)
    @pytest.mark.parametrize("value", VALUES, ids=str)
    def test_scalar_renders_as_django_renders_it(self, source: str, value: Decimal) -> None:
        if (source, str(value)) in KNOWN_FILTER_DIVERGENCES:  # pragma: no cover - empty since #2253
            pytest.skip("known pre-existing Rust filter divergence; see the table")
        normalized = normalize_django_value({"p": value})
        assert _rust.render_template(source, normalized) == DjangoTemplate(source).render(
            DjangoContext({"p": value})
        )

    def test_this_change_turns_no_agreeing_case_into_a_disagreeing_one(self) -> None:
        """The whole matrix, as a non-regression claim rather than a pass list.

        For every (template, value) cell: if the PRE-fix context value (a
        `float`, which is what `normalize_django_value` used to produce) agreed
        with Django, the post-fix value must agree too. Skipping the four known
        divergences would leave exactly the cells this could regress
        unexamined, so nothing is skipped here.
        """
        regressions = []
        for source in TEMPLATES:
            for value in VALUES:
                django_out = DjangoTemplate(source).render(DjangoContext({"p": value}))
                post = _rust.render_template(source, normalize_django_value({"p": value}))
                pre = _rust.render_template(source, {"p": float(value)})
                if pre == django_out and post != django_out:
                    regressions.append((source, str(value), django_out, pre, post))
        assert not regressions, "cells that agreed before the fix and do not now: " + repr(
            regressions
        )

    def test_the_known_divergences_are_exactly_those_four(self) -> None:
        """Pin the divergence SET, not a floor (#1125).

        The four cells this pinned when #2239 shipped were all in the Rust
        filter layer, and all four are closed by #2253 — so the expected set is
        now EMPTY and this asserts full parity across the matrix. Kept as a set
        comparison rather than a "no divergences" assertion so that a
        regression names the cell it broke.
        """
        actual = set()
        for source in TEMPLATES:
            for value in VALUES:
                django_out = DjangoTemplate(source).render(DjangoContext({"p": value}))
                post = _rust.render_template(source, normalize_django_value({"p": value}))
                if post != django_out:
                    actual.add((source, str(value)))
        assert actual == KNOWN_FILTER_DIVERGENCES, (
            "The Decimal template-parity divergence set changed.\n"
            f"  new:   {sorted(actual - KNOWN_FILTER_DIVERGENCES)}\n"
            f"  fixed: {sorted(KNOWN_FILTER_DIVERGENCES - actual)}"
        )

    def test_the_fix_repairs_two_cases_the_float_got_wrong(self) -> None:
        """Not every cell was merely preserved — two were repaired.

        `Decimal('0.00')` rendered as `0.0` under the float (Django says
        `0.00`, because the SCALE is part of the value), and `{% if p == 0 %}`
        answered NONZERO. Both now match Django.
        """
        zero = Decimal("0.00")
        assert _rust.render_template("{{ p }}", normalize_django_value({"p": zero})) == "0.00"
        assert _rust.render_template("{{ p }}", {"p": float(zero)}) == "0.0"

    @pytest.mark.parametrize("source", TEMPLATES)
    def test_nested_in_a_dict_renders_as_django_renders_it(self, source: str) -> None:
        """The shape a serialized model actually takes."""
        nested_source = source.replace("p", "row.p")
        normalized = normalize_django_value({"row": {"p": Decimal("19.99")}})
        assert _rust.render_template(nested_source, normalized) == DjangoTemplate(
            nested_source
        ).render(DjangoContext({"row": {"p": Decimal("19.99")}}))

    def test_a_string_in_the_context_would_fail_these(self) -> None:
        """Gate-off for the choice, not just the code (#1468).

        `str` is the right answer at the wire and the WRONG one here, and this
        is the measurement that says so: with the exact digits as a *string*,
        `>` compares lexically. Any future "just make both converters return
        str" runs into this.

        NARROWED by #2253. This used to assert the same of `|floatformat`,
        because the filter returned a non-numeric `Value` unchanged and so
        stopped rounding a string. Django's `floatformat` coerces a string
        (`Decimal(str(text))` accepts one), and djust's now does too — so the
        filter no longer distinguishes the two, and asserting that it does
        would be asserting a bug. The comparison still does, which is what
        keeps this gate-off load-bearing rather than decorative (#1859).
        """
        as_string = {"p": str(Decimal("19.99"))}
        assert _rust.render_template(
            "{% if p > 10 %}BIG{% else %}small{% endif %}", as_string
        ) != DjangoTemplate("{% if p > 10 %}BIG{% else %}small{% endif %}").render(
            DjangoContext({"p": Decimal("19.99")})
        )
        # And the half that #2253 closed, stated rather than silently dropped:
        # the filter now agrees for BOTH shapes.
        assert (
            _rust.render_template("{{ p|floatformat }}", as_string)
            == DjangoTemplate("{{ p|floatformat }}").render(DjangoContext({"p": Decimal("19.99")}))
            == "20.0"
        )

    def test_a_float_in_the_context_loses_the_digits(self) -> None:
        """And the other gate-off: `float` renders fine but is not the value."""
        assert _rust.render_template("{{ p }}", {"p": float(HUGE)}) != DjangoTemplate(
            "{{ p }}"
        ).render(DjangoContext({"p": HUGE}))

    def test_a_decimal_survives_the_rust_state_backend_round_trip(self) -> None:
        """The context does not go straight to the renderer — it goes through
        `update_state`, and the in-memory state backend clones views by
        msgpack. `Value::Decimal` has a binary tag for exactly this (#2214);
        pinned here because #2239 is what starts sending Decimals down it."""
        source = "{{ p }}|{{ row.p }}"
        view = _rust.RustLiveView(source)
        view.update_state(normalize_django_value({"p": HUGE, "row": {"p": HUGE}}))
        cloned = _rust.RustLiveView.deserialize_msgpack(view.serialize_msgpack())
        assert cloned.render() == view.render()
        assert str(HUGE) in cloned.render()


# ---------------------------------------------------------------------------
# The jit.py fallback — why this is not a rare path.
# ---------------------------------------------------------------------------


class TestJitFallbackCarriesTheDecimal:
    """`mixins/jit.py` calls `normalize_django_value` at seven sites. Its
    fallbacks are ordinary — and a model with a `@property` in the template is
    the one the issue names, because the Rust serializer cannot read one."""

    def test_a_model_serialized_by_the_fallback_keeps_its_decimal(self) -> None:
        serialized = normalize_django_value(_priced(HUGE))
        assert serialized["price"] == HUGE
        assert isinstance(serialized["price"], Decimal)

    def test_the_jit_extraction_none_fallback_keeps_its_decimal(self, monkeypatch) -> None:
        """Exercise the real branch, not a stand-in (reproduction fidelity).

        `_jit_serialize_model` falls back to `normalize_django_value` when
        template-variable extraction yields nothing — one of four ordinary ways
        in.
        """
        from djust.mixins import jit as jit_mod

        monkeypatch.setattr(jit_mod, "_cached_extract_template_variables", lambda _tc: None)

        class _Host(jit_mod.JITMixin):
            pass

        out = _Host()._jit_serialize_model(_priced(HUGE), "{{ thing.label }}", "thing")
        assert out["price"] == HUGE

    def test_the_fallback_model_renders_as_django_renders_it(self) -> None:
        obj = _priced(Decimal("19.99"))
        normalized = normalize_django_value({"thing": obj})
        for source in ("{{ thing.price }}", "{{ thing.price|floatformat }}"):
            assert _rust.render_template(source, normalized) == DjangoTemplate(source).render(
                DjangoContext({"thing": obj})
            )


# ---------------------------------------------------------------------------
# The state-round-trip boundary — the audit's actual work.
# ---------------------------------------------------------------------------


class TestStateRoundtripBoundary:
    """The consumer that made option 3 a consumer audit rather than a branch
    change: Django's session serializer, which passes no encoder."""

    def test_django_session_serializer_refuses_the_bare_decimal(self) -> None:
        """The premise, run rather than assumed. This is why the flag exists —
        without it, every session write of a view holding a DecimalField would
        raise."""
        with pytest.raises(TypeError):
            DjangoSessionSerializer().dumps(normalize_django_value({"price": HUGE}))

    def test_django_session_serializer_accepts_the_roundtrip_form(self) -> None:
        payload = normalize_django_value({"price": HUGE}, state_roundtrip=True)
        assert DjangoSessionSerializer().dumps(payload)

    def test_the_restored_value_still_behaves_like_a_number(self) -> None:
        """Why the boundary does NOT take the exact string either.

        Whatever is stored is restored back onto the view and lands in the
        template context on the next render. A string there stops
        `|floatformat` rounding — the #2214 regression, one hop later.
        """
        stored = json.loads(
            DjangoSessionSerializer()
            .dumps(normalize_django_value({"p": Decimal("19.99")}, state_roundtrip=True))
            .decode("latin-1")
        )
        for source in ("{{ p|floatformat }}", "{% if p > 10 %}BIG{% else %}small{% endif %}"):
            assert _rust.render_template(source, stored) == DjangoTemplate(source).render(
                DjangoContext({"p": Decimal("19.99")})
            )

    def test_the_snapshot_encoder_agrees_with_the_normalizer(self) -> None:
        """Two adapters, one rule. The signed snapshot round-trips through
        `StateRoundtripJSONEncoder`; the session through
        `state_roundtrip=True`. They must not drift (#1646)."""
        via_encoder = json.loads(json.dumps({"p": HUGE}, cls=StateRoundtripJSONEncoder))
        via_normalizer = normalize_django_value({"p": HUGE}, state_roundtrip=True)
        assert via_encoder == via_normalizer

    def test_the_snapshot_encoder_still_matches_django_on_every_other_type(self) -> None:
        """It overrides exactly one branch."""
        for value in (
            __import__("uuid").UUID("12345678-1234-5678-1234-567812345678"),
            __import__("datetime").date(2024, 6, 15),
            __import__("datetime").datetime(2024, 6, 15, 12, 30, 45),
            {"nested": [1, "two", None]},
        ):
            assert json.dumps(value, cls=StateRoundtripJSONEncoder) == json.dumps(
                value, cls=RealDjangoEncoder
            )

    def test_one_function_decides_it_for_both_adapters(self, monkeypatch) -> None:
        """Load-bearing single-source pin, not a decorative one (#1859).

        Redefining `decimal_for_state_roundtrip` must change BOTH adapters. If
        either had its own inline `float(...)`, this goes red — which is the
        only way a "they share a chokepoint" claim means anything.
        """
        import djust.serialization as ser

        monkeypatch.setattr(ser, "decimal_for_state_roundtrip", lambda d: f"SENTINEL:{d}")
        assert ser.normalize_django_value(Decimal("1.5"), state_roundtrip=True) == "SENTINEL:1.5"
        assert (
            json.loads(json.dumps(Decimal("1.5"), cls=ser.StateRoundtripJSONEncoder))
            == "SENTINEL:1.5"
        )

    def test_the_roundtrip_form_is_documented_as_lossy(self) -> None:
        """The residue, pinned so it is not mistaken for fixed.

        `decimal_for_state_roundtrip` is deliberately lossy. When the tagged
        round trip lands (#2252), this test is the one that should change.
        """
        assert decimal_for_state_roundtrip(HUGE) == float(HUGE)
        assert Decimal(str(decimal_for_state_roundtrip(HUGE))) != HUGE


# ---------------------------------------------------------------------------
# The inventory — grep the SINK, and pin the SET (v1.1.1-2 retro, #1125).
# ---------------------------------------------------------------------------

_PKG = pathlib.Path(__file__).resolve().parents[1] / "djust"

#: Every production call of `normalize_django_value` (under any alias) that
#: passes `state_roundtrip=True`, as (module, enclosing function). These are the
#: boundaries whose output is written to the Django session and later restored
#: back onto a view. Pinned as a SET, not a floor: a new session write that
#: forgets the flag will not appear here, and a new call that adds it without
#: being such a boundary will fail too. Either way the author has to decide
#: deliberately rather than by default.
EXPECTED_ROUNDTRIP_SITES = {
    ("mixins/components.py", "_save_components_to_session"),
    ("mixins/request.py", "get"),
    ("mixins/request.py", "post"),
    ("mixins/sticky.py", "save_sticky_child_state"),
    ("mixins/sticky.py", "save_sticky_child_state_sync"),
    # The inner `_save` closure of `_persist_state_after_event`.
    ("runtime.py", "_save"),
}

#: Modules holding at least one call that deliberately does NOT pass the flag —
#: every one of them feeds the template context or the Rust extractor, where a
#: `Decimal` is the correct and exact value.
EXPECTED_TEMPLATE_BOUND_MODULES = {
    "mixins/context.py",
    "mixins/jit.py",
    "mixins/request.py",
    "mixins/rust_bridge.py",
    "mixins/template.py",
    "serialization.py",
    "template/rendering.py",
}

_NORMALIZER_NAMES = {"normalize_django_value", "_normalize"}


def _enclosing_function(tree: ast.AST, lineno: int) -> str:
    best = "<module>"
    best_line = -1
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= lineno <= end and node.lineno > best_line:
                best, best_line = node.name, node.lineno
    return best


def _normalizer_calls() -> list[tuple[str, str, str]]:
    """(module, enclosing function, flag) for every production call.

    flag is "true" (an explicit `state_roundtrip=True`), "forwarded" (internal
    recursion passing the caller's value along), or "absent".
    """
    found: list[tuple[str, str, str]] = []
    for path in sorted(_PKG.rglob("*.py")):
        rel = path.relative_to(_PKG).as_posix()
        if "/tests/" in f"/{rel}" or rel.startswith("tests/"):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else None
            if name not in _NORMALIZER_NAMES:
                continue
            flag = "absent"
            for kw in node.keywords:
                if kw.arg != "state_roundtrip":
                    continue
                if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    flag = "true"
                elif isinstance(kw.value, ast.Name) and kw.value.id == "state_roundtrip":
                    flag = "forwarded"
                else:  # pragma: no cover - a shape nobody has written
                    flag = f"other:{ast.dump(kw.value)}"
            found.append((rel, _enclosing_function(tree, node.lineno), flag))
    return found


class TestBoundaryInventory:
    """Grep the SINK, not the callers you expect."""

    def test_the_roundtrip_call_sites_are_exactly_the_expected_set(self) -> None:
        actual = {(m, f) for m, f, flag in _normalizer_calls() if flag == "true"}
        assert actual == EXPECTED_ROUNDTRIP_SITES, (
            "The set of `state_roundtrip=True` call sites changed. Every one of "
            "them must be a boundary whose output is written to the Django "
            "session (or another encoder-less json.dumps) and restored back onto "
            "a view. If you added a session write, add it here; if you added an "
            "ordinary call, it does not belong here.\n"
            f"  added:   {sorted(actual - EXPECTED_ROUNDTRIP_SITES)}\n"
            f"  missing: {sorted(EXPECTED_ROUNDTRIP_SITES - actual)}"
        )

    def test_every_session_write_of_normalized_state_passes_the_flag(self) -> None:
        """The sink-side check, independent of the call-site inventory above.

        Reads the source for `session[...] = ` / `.aset(...)` writes whose value
        comes from the normalizer, and asserts each one sets the flag. This is
        what catches a NEW session write, which the pinned set above cannot see
        until someone updates it.
        """
        offenders: list[str] = []
        for path in sorted(_PKG.rglob("*.py")):
            rel = path.relative_to(_PKG).as_posix()
            if "/tests/" in f"/{rel}":
                continue
            src = path.read_text()
            tree = ast.parse(src)
            lines = src.splitlines()
            for node in ast.walk(tree):
                target: Any = None
                if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Subscript) for t in node.targets
                ):
                    subs = [t for t in node.targets if isinstance(t, ast.Subscript)]
                    if any("session" in ast.dump(t.value).lower() for t in subs):
                        target = node.value
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "aset" and "session" in ast.dump(node.func.value).lower():
                        target = node.args[1] if len(node.args) > 1 else None
                if target is None:
                    continue
                for inner in ast.walk(target):
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id in _NORMALIZER_NAMES
                    ):
                        flagged = any(
                            kw.arg == "state_roundtrip"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is True
                            for kw in inner.keywords
                        )
                        if not flagged:
                            offenders.append(
                                f"{rel}:{inner.lineno}: {lines[inner.lineno - 1].strip()}"
                            )
        assert not offenders, (
            "A Django session write carries `normalize_django_value` output "
            "without `state_roundtrip=True`. Django's session serializer runs "
            "json.dumps with NO encoder, so a Decimal anywhere in that state "
            "raises TypeError (#2239). Offenders:\n  " + "\n  ".join(offenders)
        )

    def test_the_sink_check_would_catch_an_unflagged_write(self) -> None:
        """Gate-off for the checker itself (#1468/#2135).

        A structural check that cannot go red is decorative. Feed it the exact
        pre-fix shape and it must report it.
        """
        pre_fix = "request.session[view_key] = normalize_django_value(state)\n"
        tree = ast.parse(pre_fix)
        hits = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id in _NORMALIZER_NAMES
            and not any(kw.arg == "state_roundtrip" for kw in n.keywords)
        ]
        assert len(hits) == 1

    def test_the_template_bound_calls_are_the_expected_modules(self) -> None:
        actual = {m for m, _f, flag in _normalizer_calls() if flag == "absent"}
        assert actual == EXPECTED_TEMPLATE_BOUND_MODULES, (
            "A module gained (or lost) a `normalize_django_value` call that does "
            "NOT set state_roundtrip. Those calls must feed the template context "
            "or the Rust extractor, where the Decimal is the exact and correct "
            "value. If the new call feeds an encoder-less json.dumps instead, it "
            "needs the flag.\n"
            f"  added:   {sorted(actual - EXPECTED_TEMPLATE_BOUND_MODULES)}\n"
            f"  missing: {sorted(EXPECTED_TEMPLATE_BOUND_MODULES - actual)}"
        )

    def test_recursion_forwards_the_flag_and_only_inside_the_normalizer(self) -> None:
        """A recursive call that drops the flag would apply it to the top level
        and silently not to nested values."""
        forwarded = {m for m, _f, flag in _normalizer_calls() if flag == "forwarded"}
        assert forwarded == {"serialization.py"}

    def test_the_snapshot_captures_use_the_roundtrip_encoder(self) -> None:
        """The other half of the boundary: the signed snapshot round-trips
        through json.dumps and is restored onto the view by `_restore_snapshot`,
        so it takes the same rule as the session."""
        src = (_PKG / "live_view.py").read_text()
        tree = ast.parse(src)
        by_function: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "dumps":
                continue
            for kw in node.keywords:
                if kw.arg == "cls" and isinstance(kw.value, ast.Name):
                    fn = _enclosing_function(tree, node.lineno)
                    by_function.setdefault(fn, set()).add(kw.value.id)

        assert by_function.get("_capture_snapshot_state") == {"StateRoundtripJSONEncoder"}
        assert by_function.get("_capture_components_snapshot") == {"StateRoundtripJSONEncoder"}
        # The serializability PROBES keep the plain encoder on purpose — they
        # discard the output, so the representation is irrelevant there.
        assert by_function.get("_get_private_state") == {"DjangoJSONEncoder"}
        assert by_function.get("_is_serializable") == {"DjangoJSONEncoder"}
