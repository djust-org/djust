"""A dict key keeps its Python type (#2339).

The bug, and the premise that had to be falsified first
-------------------------------------------------------
``Value::Object`` was an ``IndexMap<String, Value>``: every key was a Rust
``String``. Two symptoms followed, and #2339 argued they pulled in opposite
directions:

1. ``{% if 0 in d %}`` matched a ``"0"`` key, because the ``in`` arm did
   ``map.contains_key(&needle.to_string())`` — a gate opening on a coincidence
   of ``Display`` formatting.
2. A dict with any non-string key was **not a mapping at all**: PyO3's
   extraction required string keys, so ``{0: 1}`` fell through to its ``repr``
   and ``{% for k in d %}`` iterated that string BY CHARACTER.

#2339 said the two could not both be fixed, because "djust's own wire format
coerces every dict key to a string, so the ``to_string()`` coercion is the only
thing that keeps ``{% if pk in d %}`` working". PR #2341 wrote the
Python-faithful fix, measured it, and REVERTED it on that reasoning.

**The premise is false, and measuring it is what unblocked this.** Through the
real ``LiveView.render()`` path there is no JSON hop at all — the live Python
dict goes straight to PyO3 — so an int-keyed dict never became a mapping and
``{% if pk in d %}`` answered ``MISS`` on it *already*. The coercion bought
nothing for the int-keyed case it was said to protect; its only effect was to
make djust wrong for the string-keyed one. Both halves are pinned below
(``TestThePremiseThatBlockedThisFix``), because the whole design turns on them.

So the key type is preserved, and the two symptoms are one fix:

* ``0 in {"0": 1}`` is False — ``Integer(0)`` is not ``Str("0")``.
* ``1234567 in {1234567: "x"}`` is True — both are ``Integer``.

Numeric keys are conflated the way Python conflates them
--------------------------------------------------------
``hash(1) == hash(1.0) == hash(True)`` and ``1 == 1.0 == True``, so
``{1: "a"}[True]`` and ``{1: "a"}[1.0]`` both resolve in Python. A typed key
that compared by variant would answer False there — a NEW divergence, in
exactly the shape the "a partial model is not a fix" argument warns about. So
``ObjectKey`` hashes and compares numerics by value across ``Int`` / ``Float``
/ ``Bool`` / ``Decimal`` / ``BigInt``, while keeping the variant for DISPLAY
(``repr({True: 1})`` is ``{True: 1}``, not ``{1: 1}``).

What this deliberately does NOT fix
------------------------------------
A JSON or msgpack round trip still stringifies the key, because JSON has no
other kind of key — ``json.dumps({0: 1})`` is ``'{"0": 1}'`` in CPython too.
That is a property of the wire format, not of the renderer, and it is pinned
in ``TestTheWireStillStringifies`` rather than left silent.
"""

from __future__ import annotations

import json
import random

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

#: A live payload, so every cell doubles as a permissiveness probe.
XSS = "<img src=x onerror=alert(1)>"


def django_render(src: str, ctx: dict) -> str:
    return DjangoTemplate(src).render(DjangoContext(ctx))


def djust_render(src: str, ctx: dict) -> str:
    return _rust.render_template(src, ctx)


def both(src: str, ctx: dict) -> tuple[str, str]:
    try:
        d = django_render(src, ctx)
    except Exception as exc:  # noqa: BLE001
        d = f"<<EXC {type(exc).__name__}>>"
    try:
        r = djust_render(src, ctx)
    except Exception as exc:  # noqa: BLE001
        r = f"<<EXC {type(exc).__name__}>>"
    return d, r


def assert_agrees(src: str, ctx: dict) -> None:
    d, r = both(src, ctx)
    assert r == d, f"{src} on {ctx!r}: django={d!r} djust={r!r}"


IN = "{% if p in q %}Y{% else %}N{% endif %}"


# ===========================================================================
# The premise the previous attempt was reverted on.
# ===========================================================================


class TestThePremiseThatBlockedThisFix:
    """#2339 claimed the coercion was load-bearing. It was not.

    Both cells go through the real ``LiveView.render()``, because the claim was
    specifically about djust's *wire format* — a claim no ``render_template``
    call could have tested.
    """

    @staticmethod
    def _render_view(template: str, **state) -> str:
        from django.test import RequestFactory

        from djust import LiveView

        view = type("_V", (LiveView,), {"template": template})()
        request = RequestFactory().get("/")
        view.mount(request)
        for k, v in state.items():
            setattr(view, k, v)
        view.request = request
        return view.render()

    def test_the_render_path_has_no_json_hop_so_keys_arrive_untouched(self) -> None:
        """The load-bearing measurement.

        If the wire really coerced keys, an int-keyed dict would reach the
        renderer string-keyed and this would render HIT even before the fix.
        It rendered MISS — the dict was not a mapping at all — which is what
        proves the coercion protected nothing.
        """
        out = self._render_view(
            '<div dj-id="0">{% if n in d %}HIT{% else %}MISS{% endif %}</div>',
            n=1234567,
            d={1234567: "x"},
        )
        assert "HIT" in out, (
            "an int-keyed dict must be a real mapping on the render path — "
            "this is the case the reverted fix was said to break, and it was "
            "already broken"
        )

    def test_an_int_keyed_dict_is_iterable_rather_than_a_repr(self) -> None:
        """The second half of the same premise.

        Before the fix this rendered the dict's ``repr`` one character at a
        time: ``[{][1][:][ ][2][}]``.
        """
        out = self._render_view(
            '<div dj-id="0">{% for k in d %}[{{ k }}]{% endfor %}</div>', d={1: 2}
        )
        assert "[1]" in out and "[{]" not in out, out


# ===========================================================================
# Symptom 1 — `in` no longer stringifies its needle.
# ===========================================================================


class TestInOverADictComparesTypes:
    def test_the_issue_table_verbatim(self) -> None:
        for ctx in (
            {"p": 0, "q": {"0": 1}},
            {"p": 1.0, "q": {"1.0": 1}},
            {"p": None, "q": {"None": 1}},
            {"p": True, "q": {"True": 1}},
        ):
            assert_agrees(IN, ctx)
            assert djust_render(IN, ctx) == "N", ctx

    def test_a_matching_type_still_hits(self) -> None:
        for ctx in (
            {"p": "0", "q": {"0": 1}},
            {"p": 0, "q": {0: 1}},
            {"p": 1234567, "q": {1234567: "x"}},
            {"p": None, "q": {None: 1}},
        ):
            assert_agrees(IN, ctx)
            assert djust_render(IN, ctx) == "Y", ctx

    def test_python_conflates_numeric_keys_and_so_does_djust(self) -> None:
        """``{1: 'a'}[True]`` and ``{1: 'a'}[1.0]`` both resolve in Python.

        Comparing by VARIANT would answer N here — a new divergence bought by
        the fix, which is the failure shape this test exists to catch.
        """
        for ctx in (
            {"p": True, "q": {1: "a"}},
            {"p": 1.0, "q": {1: "a"}},
            {"p": 1, "q": {True: "a"}},
            {"p": 1, "q": {1.0: "a"}},
        ):
            assert_agrees(IN, ctx)
            assert djust_render(IN, ctx) == "Y", ctx

    def test_and_a_non_integral_float_still_misses_an_int(self) -> None:
        assert_agrees(IN, {"p": 1.5, "q": {1: "a"}})
        assert djust_render(IN, {"p": 1.5, "q": {1: "a"}}) == "N"


# ===========================================================================
# Symptom 2 — a non-string-keyed dict is a mapping.
# ===========================================================================


class TestANonStringKeyedDictIsAMapping:
    def test_iteration_emits_the_key_with_its_type(self) -> None:
        for d in ({0: 1}, {1: "x", 2: "y"}, {None: 1}, {True: 1}, {(1, 2): 3}):
            assert_agrees("{% for k in p %}[{{ k }}]{% endfor %}", {"p": d})

    def test_length_counts_entries_not_repr_characters(self) -> None:
        assert_agrees("{{ p|length }}", {"p": {1234567: "x"}})
        assert djust_render("{{ p|length }}", {"p": {1234567: "x"}}) == "1"

    def test_items_keys_values_over_a_typed_key(self) -> None:
        for src in (
            "{% for k, v in p.items %}{{ k }}={{ v }};{% endfor %}",
            "{% for k in p.keys %}{{ k }};{% endfor %}",
            "{% for v in p.values %}{{ v }};{% endfor %}",
        ):
            assert_agrees(src, {"p": {1: "a", 2: "b"}})

    def test_the_repr_keeps_pythons_key_forms(self) -> None:
        for d in ({0: 1}, {True: 1}, {None: 1}, {1.5: 1}, {"a": 1}, {(1, "b"): 2}):
            assert_agrees("{{ p }}", {"p": d})

    def test_the_bound_key_is_the_TYPE_not_its_text(self) -> None:
        """The observation that distinguishes the fix from a lookalike.

        ``{{ k }}`` renders ``0`` whether the loop bound ``Integer(0)`` or the
        text ``"0"``, so every cell above would pass against a version that
        stringified the key on the way out. Only a COMPARISON tells them
        apart — and the gate-off mutation for
        ``djust_core::object_key::dict_iteration_values`` survived until this
        test existed.
        """
        for src in (
            "{% for k in p %}{% if k == 0 %}INT{% else %}STR{% endif %}{% endfor %}",
            "{% for k in p.keys %}{% if k == 0 %}INT{% else %}STR{% endif %}{% endfor %}",
            "{% for k, v in p.items %}{% if k == 0 %}INT{% else %}STR{% endif %}{% endfor %}",
        ):
            assert_agrees(src, {"p": {0: 1}})
            assert djust_render(src, {"p": {0: 1}}) == "INT", src
            # …and the STRING key is genuinely the other answer, so the
            # assertion above is not simply "whatever djust does".
            assert_agrees(src, {"p": {"0": 1}})
            assert djust_render(src, {"p": {"0": 1}}) == "STR", src

    def test_a_bool_key_stays_a_bool(self) -> None:
        src = "{% for k in p %}{% if k is True %}T{% else %}F{% endif %}{% endfor %}"
        assert_agrees(src, {"p": {True: 1}})
        assert djust_render(src, {"p": {True: 1}}) == "T"

    def test_an_unresolved_needle_misses_rather_than_matching_an_empty_key(self) -> None:
        """``Value::Missing`` is an ABSENT variable, not the empty string.

        Mapping it onto a key would make ``{% if nosuchvar in d %}`` open on a
        dict that happens to carry a ``""`` key — a gate deciding on a
        variable that does not exist. Verified reachable: with the arm mutated
        to `ObjectKey::Str("")` this renders ``Y`` where Django renders ``N``.
        """
        src = "{% if nosuchvar in p %}Y{% else %}N{% endif %}"
        assert_agrees(src, {"p": {"": 1}})
        assert djust_render(src, {"p": {"": 1}}) == "N"

    def test_an_unhashable_needle_misses_rather_than_matching_by_text(self) -> None:
        """``[] in {}`` raises in Python; djust renders the else-branch rather
        than matching a key whose text happens to read ``[]``.
        """
        src = "{% if p in q %}Y{% else %}N{% endif %}"
        for ctx in ({"p": [], "q": {"[]": 1}}, {"p": {}, "q": {"{}": 1}}):
            assert_agrees(src, ctx)
            assert djust_render(src, ctx) == "N", ctx

    def test_a_mixed_key_dict_keeps_every_key(self) -> None:
        assert_agrees("{% for k in p %}[{{ k }}]{% endfor %}", {"p": {"a": 1, 2: 3, None: 4}})
        assert_agrees("{{ p }}", {"p": {"a": 1, 2: 3, None: 4}})


# ===========================================================================
# Not more permissive than Django.
# ===========================================================================


class TestNotMorePermissive:
    def test_a_hostile_key_of_every_type_is_escaped(self) -> None:
        """The key is what ``{% for k in d %}`` emits, so it is the sink."""
        for d in (
            {XSS: 1},
            {"a": 1, XSS: 2},
            {(XSS,): 1},
        ):
            for src in (
                "{% for k in p %}[{{ k }}]{% endfor %}",
                "{{ p }}",
                "{% for k, v in p.items %}{{ k }}{% endfor %}",
            ):
                d_out, r_out = both(src, {"p": d})
                assert r_out == d_out, f"{src} on {d!r}"
                assert "<img" not in r_out, f"LIVE PAYLOAD from {src} on {d!r}: {r_out!r}"

    def test_the_loop_safe_key_mapping_is_still_not_registered_for_a_dict(self) -> None:
        """``_collect_safe_keys`` writes a dict's paths BY KEY NAME while the
        loop mapping is BY POSITION, so registering it for a dict lets an
        attacker-controlled key resolve a sibling's mark (#2341).

        Typed keys change how a key is *represented*, so the gate is
        re-verified here rather than assumed to have survived.
        """
        from django.utils.safestring import mark_safe

        from djust.mixins.rust_bridge import _collect_safe_keys

        # The key spelled `"1"` is a STRING, so the collector writes `p.1` —
        # the same path a positional mapping would write for the loop's index
        # 1, which is the hostile key. That collision is the whole hazard.
        d = {"1": mark_safe("<b>ok</b>"), XSS: "v"}
        safe_keys = _collect_safe_keys(d, "p")
        assert "p.1" in safe_keys, (
            "premise: the collector must mark `p.1` here, or this test proves "
            f"nothing. got {safe_keys!r}"
        )
        out = _rust.render_template_with_dirs(
            "{% for k in p %}[{{ k }}]{% endfor %}", {"p": d}, [], safe_keys
        )
        assert "<img" not in out, out
        assert "&lt;img" in out, out

    def test_the_same_gate_holds_when_the_key_is_an_INT(self) -> None:
        """The typed-key half of the same hazard.

        With `ObjectKey`, `{1: …}` is now a real mapping whose first key is an
        `Integer` — a shape that could not reach the loop at all before, so
        the gate had never been exercised for it.
        """
        from django.utils.safestring import mark_safe

        from djust.mixins.rust_bridge import _collect_safe_keys

        d = {1: mark_safe("<b>ok</b>"), XSS: "v"}
        safe_keys = _collect_safe_keys(d, "p")
        out = _rust.render_template_with_dirs(
            "{% for k in p %}[{{ k }}]{% endfor %}", {"p": d}, [], safe_keys
        )
        assert "<img" not in out, out
        assert "&lt;img" in out, out


# ===========================================================================
# The wire is still lossy, and says so.
# ===========================================================================


class TestTheWireStillStringifies:
    def test_json_loses_the_key_type_exactly_as_cpython_does(self) -> None:
        """Not a djust limitation — ``json.dumps`` has no other kind of key.

        Asserted against live CPython rather than from memory, so the claim
        the docstring makes is the claim the runtime makes.
        """
        assert json.loads(json.dumps({0: 1})) == {"0": 1}
        rt = json.loads(json.dumps({0: 1}))
        assert djust_render(IN, {"p": 0, "q": rt}) == "N"
        assert djust_render(IN, {"p": "0", "q": rt}) == "Y"

    def test_a_state_snapshot_round_trip_is_the_same_lossy_hop(self) -> None:
        from djust.live_view import StateRoundtripJSONEncoder

        rt = json.loads(json.dumps({1234567: "x"}, cls=StateRoundtripJSONEncoder))
        assert list(rt) == ["1234567"], rt


# ===========================================================================
# Randomised differential against live Django.
# ===========================================================================


def _random_key(rng: random.Random):
    return rng.choice(
        [
            lambda: rng.choice(["a", "0", "1", "None", "True", "", XSS]),
            lambda: rng.randint(-3, 3),
            lambda: rng.choice([0.0, 1.0, 1.5, -2.0]),
            lambda: rng.choice([True, False]),
            lambda: None,
            lambda: (rng.randint(0, 2), rng.choice(["a", "b"])),
        ]
    )()


def _random_needle(rng: random.Random):
    return _random_key(rng)


SHAPES = [
    IN,
    "{% for k in q %}[{{ k }}]{% endfor %}",
    "{% for k, v in q.items %}{{ k }}={{ v }};{% endfor %}",
    "{{ q }}",
    "{{ q|length }}",
    "{{ q|join:'-' }}",
    "{% for k in q.keys %}{{ k }};{% endfor %}",
    "{% if q %}T{% else %}F{% endif %}",
]


class TestRandomisedAgainstDjango:
    """A curated table samples one axis and blinds you on the next.

    Every cell is run through BOTH engines; a disagreement is a failure, with
    no exemption list — this fix has no residue to carve out.
    """

    def test_sweep(self) -> None:
        rng = random.Random(20339)
        bad = []
        cells = 0
        for _ in range(400):
            d = {}
            for _ in range(rng.randint(0, 3)):
                try:
                    d[_random_key(rng)] = rng.choice([1, "v", None, True])
                except TypeError:  # pragma: no cover - unhashable, skipped
                    continue
            ctx = {"p": _random_needle(rng), "q": d}
            for src in SHAPES:
                cells += 1
                dj, ru = both(src, ctx)
                if dj != ru:
                    bad.append((src, ctx, dj, ru))
        assert cells >= 3000, cells
        assert not bad, "\n".join(
            f"{s} on {c!r}: django={a!r} djust={b!r}" for s, c, a, b in bad[:25]
        )

    def test_the_sweep_is_never_more_permissive(self) -> None:
        rng = random.Random(920339)
        leaks = []
        for _ in range(300):
            d = {}
            for _ in range(rng.randint(1, 3)):
                k = rng.choice([XSS, f"{XSS}{rng.randint(0, 9)}", rng.randint(0, 3), None])
                d[k] = rng.choice([XSS, 1, None])
            for src in SHAPES:
                out = djust_render(src, {"p": XSS, "q": d})
                if "<img" in out:
                    leaks.append((src, d, out))
        assert not leaks, leaks[:5]
