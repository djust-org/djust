"""``mark_item``'s non-``str`` branch had no producer left (#2337).

The finding
-----------
``filter_registry::mark_item`` — the helper both the ``PyList`` and
``PyTuple`` arms of ``mark_input_safety`` call — wrapped a sequence element in
``mark_safe`` **only if it was a ``str``**, and passed anything else through
untouched. #2324 closed the last thing that could hand it something else:
``safeseq`` now replaces every item with ``str(item)``, so both
``ITEM_SAFE_OUTPUT_FILTERS`` construct all-``String`` lists.

Why the guard was NOT simply deleted in #2331
---------------------------------------------
Removing it means calling ``mark_safe`` on a non-``str``, which
**stringifies**: ``mark_safe(42)`` is ``SafeString("42")``. If the enumeration
is wrong for some producer nobody listed, the deletion silently changes the
type a custom filter receives — and worse, ``mark_safe(["<b>"])`` is
``SafeString("['<b>']")``, a string carrying a raw ``<`` that then bypasses
escaping. That is the MORE-permissive-than-Django direction
``Context::items_are_safe``'s own doc-comment says this code must never take.

So proving unreachability is the work, and it is not rhetorical: the tuple arm
of ``mark_input_safety`` is a worked example of exactly this claim EXPIRING.
#2290 deleted a parallel ``PyTuple`` arm as unreachable — correct on the
evidence available — and #2287 then added a second grant producer that reached
it, so the arm came back (#2305).

What was proven, three ways
---------------------------
1. **Analytically.** Every writer of ``InputSafety.items = true`` can only
   grant on a sequence whose elements are ``Value::String``, and
   ``IntoPyObject`` turns those into ``PyString``. The three, pinned
   mechanically by ``TestTheProducerEnumerationIsComplete`` below rather than
   asserted in prose:

   * ``Context::items_are_safe`` (#2287) requires
     ``matches!(item, Value::String(_))`` for EVERY element.
   * ``safeseq`` / ``escapeseq`` CONSTRUCT ``Value::String`` elements
     unconditionally.
   * ``slice`` only preserves a grant already held, and selects elements
     rather than building new ones.

   The renderer's three seed sites each read ``context.items_are_safe(k)`` for
   the same ``k`` they resolve the value from, and ``Context::resolve`` returns
   ``Context::get``'s value verbatim on a hit — so the grant and the value can
   never describe different objects.

2. **Empirically, by instrumentation.** The branch was replaced with a
   ``panic!`` and the extension rebuilt. Nothing reached it across the full
   14,163-test suite, the 95,275-cell
   ``scripts/filter-parity-differential.py`` corpus (custom-filter axis
   included), or the adversarial sweep below.

3. **Adversarially.** The two producer classes below cross every producer with
   every non-``str`` element shape — int, float, bool, ``None``, ``Decimal``, a
   bigint, a nested list, a nested tuple, a dict — through explicit
   ``safe_keys``, the loop-variable alias arm, the stale-grant case
   ``items_are_safe``'s docstring names, ``slice``'s fail-soft arm, and all
   three renderer seed sites.

The producers split in two, and conflating them is the mistake the first draft
of this file made
------------------------------------------------------------------------------
``safeseq`` / ``escapeseq`` **stringify every element themselves** — and so
does Django, whose ``[mark_safe(obj) for obj in value]`` turns
``['<b>', 2]`` into two ``SafeString``s (verified against live Django, not
assumed). So on those paths "no non-``str`` was marked" is not the assertion;
"no non-``str`` SURVIVES to the wrap" is, and Django parity is what says the
stringification is Django's behaviour rather than djust's licence.

Only ``Context::items_are_safe`` (and ``slice`` over its grant) is
non-converting, and that is the one place the deleted guard could ever have
mattered. There the assertion is TYPE PRESERVATION: a non-``str`` element must
come back as its own type, because ``mark_safe`` stringifies and a widened
grant would show up as the element's type changing.

What replaces the guard
-----------------------
The guard is deleted (#1859: an unreachable branch is decorative, not
defensive; and while both mechanisms exist no test can tell them apart —
v1.1.1-2 retro). ``TestANonConvertingProducerRefusesANonStrSequence`` is what
replaces it, and it is load-bearing against exactly the regression the deletion
risks — gating off ``items_are_safe``'s ``String`` narrowing reddens 30 of its
cases. ``TestAConvertingProducerLeavesNoNonStrElement`` covers the other half,
and ``TestTheWrapStillFiresForStrings`` is the non-vacuity sibling for both:
gating the wrap off (``mark_item`` as ``Ok(item)``) reddens 59 cases, so the
refusals measure an upstream narrowing rather than a dead path downstream.

One thing the gate-off proved could NOT be tested from outside, recorded here
because the absence would otherwise look like an oversight: on a CONVERTING
path there is no assertion that can detect the wrap being handed a non-``str``.
With the guard gone, ``mark_safe`` stringifies it, and the evidence is
destroyed before any probe runs. That axis is covered by the structural pin on
``safeseq``/``escapeseq``'s constructors plus Django parity on the downstream
sinks — see the comment in ``TestAConvertingProducerLeavesNoNonStrElement``.
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("django")

from django import template  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Engine  # noqa: E402
from django.template.base import Template as DjangoTemplate  # noqa: E402
from django.utils.safestring import SafeData  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

#: Deliberately un-guessable: the Rust filter registry is process-global and is
#: NOT cleared by ``reset_djust_globals``, so a collision with another module's
#: filter would be a silent cross-test leak rather than an error.
TYPES = "_dj2337_types"

_library = template.Library()


@_library.filter(name=TYPES, needs_autoescape=True)
def _types(value, autoescape=True):
    """Report every element's type and ``SafeData``-ness.

    The whole question is what the wrap did to each ELEMENT, so the probe
    reports per element rather than per sequence.
    """
    if isinstance(value, (list, tuple)):
        return repr(
            (
                type(value).__name__,
                isinstance(value, SafeData),
                [(type(v).__name__, isinstance(v, SafeData)) for v in value],
            )
        )
    return repr((type(value).__name__, isinstance(value, SafeData)))


@pytest.fixture(scope="module", autouse=True)
def _registered():
    for name, fn in _library.filters.items():
        _rust.register_custom_filter(
            name,
            fn,
            bool(getattr(fn, "is_safe", False)),
            bool(getattr(fn, "needs_autoescape", False)),
        )
    yield
    for name in _library.filters:
        _rust.unregister_custom_filter(name)


_engine = Engine(libraries={}, builtins=[])
_engine.template_builtins.append(_library)


def django_render(src: str, ctx: dict) -> str:
    return DjangoTemplate(src, engine=_engine).render(DjangoContext(ctx))


def djust_render(src: str, ctx: dict, safe_keys: list[str] | None = None) -> str:
    return _rust.render_template_with_dirs(src, normalize_django_value(ctx), [], safe_keys or [])


def djust_render_raw(src: str, ctx: dict, safe_keys: list[str] | None = None) -> str:
    """Without ``normalize_django_value`` — the ONLY way to reach the tuple arm.

    ``normalize_django_value`` collapses every tuple to a list before it
    crosses into Rust, which is what keeps every FRAMEWORK path off
    ``mark_input_safety``'s ``PyTuple`` arm and is why that arm was a parity
    gap rather than a regression (#2305). Both entry points that skip it are
    public in ``_rust.pyi``; this is the first of them.
    """
    return _rust.render_template_with_dirs(src, ctx, [], safe_keys or [])


def django_render_normalized(src: str, ctx: dict) -> str:
    """Django on the SAME shapes djust receives.

    A tuple in the context reaches djust as a list and Django as a tuple, so a
    parity assertion over a tuple-bearing context measures
    ``normalize_django_value`` rather than element safety. That normalization
    is a separate, documented question; feeding both engines the normalized
    context is what keeps these assertions about the wrap.
    """
    return django_render(src, normalize_django_value(ctx))


#: Every non-``str`` element shape a sequence can carry across the boundary.
#: ``Value``'s variants, not an arbitrary sample: `Integer`, `Float`, `Bool`,
#: `None`, `Decimal`, `BigInt`, `List`, `Tuple`, `Object`.
NON_STR = {
    "int": 2,
    "float": 2.5,
    "bool": True,
    "none": None,
    "decimal": Decimal("1.5"),
    "bigint": 2**70,
    "nested-list": ["<b>b</b>"],
    "nested-tuple": ("<b>b</b>",),
    "dict": {"k": "<b>v</b>"},
}
STR_ELEM = "<b>x</b>"

#: The grant producers, split by whether the producer itself CONVERTS the
#: elements — because that split is the whole finding, and conflating the two
#: is the mistake the first draft of this file made.
#:
#: * **Non-converting.** ``Context::items_are_safe`` issues the grant and the
#:   elements reach the wrap exactly as the view wrote them. ``slice`` over
#:   such a grant selects elements and builds nothing. These are where the
#:   deleted guard could ever have mattered, and where a widened
#:   ``items_are_safe`` would be observable as a TYPE CHANGE.
#: * **Converting.** ``safeseq`` / ``escapeseq`` replace every element with a
#:   string before any grant exists — measured against live Django, whose own
#:   ``[mark_safe(obj) for obj in value]`` stringifies too, so
#:   ``safeseq(['<b>', 2])`` is two ``SafeString``s in BOTH engines. A
#:   non-``str`` cannot survive them, which is precisely why the guard has no
#:   producer left.
NON_CONVERTING = {
    # No filter at all; the grant is the safe_keys.
    "ctx": "",
    # ITEM_SAFETY_PRESERVING_FILTERS over a context grant.
    "ctx|slice": "|slice:':9'",
    # slice's fail-soft arm returns its input UNTOUCHED — the shape most
    # likely to carry an element a constructing producer never could.
    "ctx|slice-invalid": "|slice:'zz'",
    "ctx|slice|slice": "|slice:':9'|slice:':9'",
}

CONVERTING = {
    "safeseq": "|safeseq",
    "escapeseq": "|escapeseq",
    "safeseq|slice": "|safeseq|slice:':9'",
    "escapeseq|slice": "|escapeseq|slice:':9'",
    "slice|safeseq": "|slice:':9'|safeseq",
}

PRODUCERS = {**NON_CONVERTING, **CONVERTING}

#: The three renderer sites that seed ``items_safe`` from the context. All
#: three read ``context.items_are_safe`` and all three call
#: ``apply_filter_full_safe`` with the same ``InputSafety`` — so a proof at one
#: is not a proof at the others (#1646).
SEED_SITES = {
    "variable": "{{ p@EXPR@|%s }}" % TYPES,
    "if-operand": "{%% if p@EXPR@|%s %%}Y{%% else %%}N{%% endif %%}" % TYPES,
    "firstof": "{%% firstof p@EXPR@|%s %%}" % TYPES,
}

#: Type names that ARE a Python ``str``. Anything else reaching the wrap would
#: be stringified now that the guard is gone, which is the observable every
#: refusal assertion below is built on.
STR_TYPES = {"str", "SafeString"}


def elements(out: str) -> list[tuple[str, bool]]:
    """Parse the probe's per-element report back out of the rendered page."""
    import ast
    import html

    parsed = ast.literal_eval(html.unescape(out).strip())
    assert len(parsed) == 3, f"probe saw a non-sequence: {parsed!r}"
    return [tuple(e) for e in parsed[2]]


# ===========================================================================
# The load-bearing assertion
# ===========================================================================


class TestANonConvertingProducerRefusesANonStrSequence:
    """The grant is REFUSED when the sequence still holds a non-``str``.

    This is what replaces the deleted guard, and it is load-bearing against
    exactly the regression the deletion risks. ``mark_safe`` stringifies
    (``mark_safe(42)`` is ``SafeString("42")``), so if
    ``Context::items_are_safe`` ever widened, the non-``str`` element would
    stop arriving as its own type — every assertion here detects that as a
    TYPE CHANGE, which is the observable the guard was silently providing.
    """

    @pytest.mark.parametrize("shape", sorted(NON_STR))
    @pytest.mark.parametrize("producer", sorted(NON_CONVERTING))
    def test_a_mixed_sequence_keeps_every_element_type(self, producer: str, shape: str) -> None:
        src = "{{ p%s|%s }}" % (NON_CONVERTING[producer], TYPES)
        out = djust_render(src, {"p": [STR_ELEM, NON_STR[shape]]}, ["p.0", "p.1"])
        seen = elements(out)
        assert len(seen) == 2, out
        assert seen[1][0] not in STR_TYPES, (
            f"{producer} granted item safety to a sequence holding a {shape}: "
            "the element was stringified into a SafeString. Either "
            "Context::items_are_safe widened or mark_item's non-str guard "
            "must come back (#2337).\n" + out
        )
        assert not seen[1][1], out
        # And the whole sequence is refused, not just the offending element —
        # Django escapes per element, so a partial grant is not expressible.
        assert seen[0] == ("str", False), out

    @pytest.mark.parametrize("shape", sorted(NON_STR))
    @pytest.mark.parametrize("producer", sorted(NON_CONVERTING))
    def test_the_non_str_element_first(self, producer: str, shape: str) -> None:
        """Ordering must not matter — a check that read only element 0 would
        pass every case above."""
        src = "{{ p%s|%s }}" % (NON_CONVERTING[producer], TYPES)
        out = djust_render(src, {"p": [NON_STR[shape], STR_ELEM]}, ["p.0", "p.1"])
        seen = elements(out)
        assert seen[0][0] not in STR_TYPES and not seen[0][1], out
        assert seen[1] == ("str", False), out

    @pytest.mark.parametrize("shape", sorted(NON_STR))
    def test_an_all_non_str_sequence(self, shape: str) -> None:
        for producer in sorted(NON_CONVERTING):
            src = "{{ p%s|%s }}" % (NON_CONVERTING[producer], TYPES)
            out = djust_render(src, {"p": [NON_STR[shape], NON_STR[shape]]}, ["p.0", "p.1"])
            for tname, is_safe in elements(out):
                assert tname not in STR_TYPES and not is_safe, f"{producer}: {out}"

    @pytest.mark.parametrize("shape", sorted(NON_STR))
    def test_a_tuple_input(self, shape: str) -> None:
        """The tuple arm reaches the same helper, and it is the arm whose own
        reachability claim already expired once (#2290 -> #2305).

        RAW context: ``normalize_django_value`` collapses a tuple to a list, so
        going through it would silently test the list arm twice — the sharpest
        version of a reproduction-fidelity miss, since the assertion would look
        like tuple coverage and be none.
        """
        for producer in sorted(NON_CONVERTING):
            src = "{{ p%s|%s }}" % (NON_CONVERTING[producer], TYPES)
            out = djust_render_raw(src, {"p": (STR_ELEM, NON_STR[shape])}, ["p.0", "p.1"])
            assert "&#x27;tuple&#x27;," in out, (
                "the context tuple did not survive as a tuple — this is "
                "testing the list arm, not the tuple arm\n" + out
            )
            assert elements(out)[1][0] not in STR_TYPES, f"{producer}/{shape}: {out}"

    @pytest.mark.parametrize("site", sorted(SEED_SITES))
    @pytest.mark.parametrize("shape", sorted(NON_STR))
    def test_at_every_renderer_seed_site(self, site: str, shape: str) -> None:
        """Three sites read ``items_are_safe``; a proof at one is not a proof
        at the others (#1646)."""
        for producer in sorted(NON_CONVERTING):
            src = SEED_SITES[site].replace("@EXPR@", NON_CONVERTING[producer])
            out = djust_render(src, {"p": [STR_ELEM, NON_STR[shape]]}, ["p.0", "p.1"])
            assert "SafeString" not in out, f"{site}/{producer}/{shape}: {out}"

    @pytest.mark.parametrize("shape", sorted(NON_STR))
    def test_the_loop_variable_alias_arm(self, shape: str) -> None:
        """``items_are_safe`` resolves ``row`` to ``rows.<i>`` inside a loop —
        a second prefix, and a second way to hold a grant."""
        src = "{%% for row in rows %%}{{ row|%s }}{%% endfor %%}" % TYPES
        out = djust_render(src, {"rows": [[STR_ELEM, NON_STR[shape]]]}, ["rows.0.0", "rows.0.1"])
        assert elements(out)[1][0] not in STR_TYPES, out

    @pytest.mark.parametrize("shape", sorted(NON_STR))
    def test_the_stale_grant_case(self, shape: str) -> None:
        """``mark_safe_keys`` accumulates and is never cleared (#2300), so a
        later render can put a different shape at an already-marked index.

        This is what the ``String`` narrowing in ``items_are_safe`` exists for,
        and the single most likely way a non-``str`` would reach the wrap.
        """
        marks = ["p.0", "p.1", "p.2", "p.3"]
        out = djust_render("{{ p|%s }}" % TYPES, {"p": [NON_STR[shape], NON_STR[shape]]}, marks)
        for tname, is_safe in elements(out):
            assert tname not in STR_TYPES and not is_safe, out


class TestAConvertingProducerLeavesNoNonStrElement:
    """``safeseq`` / ``escapeseq`` stringify, so the wrap can only see strings.

    Measured against live Django rather than asserted: its own
    ``[mark_safe(obj) for obj in value]`` stringifies too — ``mark_safe(2)`` is
    ``SafeString("2")`` — so both engines turn ``['<b>', 2]`` into two
    ``SafeString``s. That conversion is exactly WHY ``mark_item``'s non-``str``
    branch lost its last producer (#2324).
    """

    # There is deliberately NO
    # `test_every_element_reaching_the_wrap_is_a_string` here, and its absence
    # is a gate-off finding rather than an oversight.
    #
    # A first draft carried exactly that test. Gating off #2324 — reverting
    # `safeseq` to `.map(|item| item.clone())`, so it hands the wrap a real
    # `Value::Integer` — left it GREEN, because with the guard gone the wrap
    # stringifies that element into a `SafeString` and the evidence it was
    # ever an int is destroyed before the probe can see it. A test that cannot
    # go red for the thing it names is the two-mechanisms-shadowing shape
    # (v1.1.1-2 retro), and the remedy there is to delete the redundant one
    # rather than test around it.
    #
    # What DOES catch that mutation, and did: the structural pin
    # `test_both_item_safe_output_filters_construct_string_elements` (the
    # cause) and `test_the_downstream_sinks_are_unchanged` (the user-visible
    # consequence, via Django parity on `join`). Those two are the coverage on
    # this axis.

    @pytest.mark.parametrize("shape", sorted(NON_STR))
    @pytest.mark.parametrize("producer", sorted(CONVERTING))
    def test_django_agrees(self, producer: str, shape: str) -> None:
        """The stringification is Django's behaviour, not djust's licence."""
        src = "{{ p%s|%s }}" % (CONVERTING[producer], TYPES)
        ctx = {"p": [STR_ELEM, NON_STR[shape]]}
        d, r = django_render_normalized(src, ctx), djust_render(src, ctx)
        assert r == d, f"{producer}/{shape}: django={d!r} djust={r!r}"

    def test_every_chain_of_two_producers(self) -> None:
        """A grant can be re-granted; the composition is its own surface."""
        chain = ["safeseq", "escapeseq", "slice:':9'"]
        for a, b in itertools.product(chain, chain):
            for shape, bad in NON_STR.items():
                src = "{{ p|%s|%s|%s }}" % (a, b, TYPES)
                ctx = {"p": [STR_ELEM, bad]}
                out = djust_render(src, ctx, ["p.0", "p.1"])
                # Either every element is a string (a converting filter ran),
                # or the non-str kept its type (the grant was refused). What
                # must NEVER happen is a non-str silently becoming a
                # SafeString while a sibling keeps its own type.
                types = [t for t, _ in elements(out)]
                assert all(t in STR_TYPES for t in types) or types[1] not in STR_TYPES, out
                assert djust_render(src, ctx) == django_render_normalized(src, ctx), (
                    f"{a}|{b}/{shape}"
                )


class TestTheWrapStillFiresForStrings:
    """Non-vacuity (#1859).

    Most assertions above are refusals, which would pass just as happily
    against a build where the wrap never ran at all. These pin that it DOES
    run — so the refusals measure an upstream narrowing rather than a dead
    code path downstream.
    """

    @pytest.mark.parametrize("producer", sorted(PRODUCERS))
    def test_an_all_str_sequence_is_marked(self, producer: str) -> None:
        src = "{{ p%s|%s }}" % (PRODUCERS[producer], TYPES)
        out = djust_render(src, {"p": [STR_ELEM, "<i>y</i>"]}, ["p.0", "p.1"])
        assert "SafeString" in out, (
            f"{producer} never marked an all-str sequence — the wrap is not "
            "running, so every refusal assertion in this module is vacuous\n" + out
        )

    def test_the_marks_are_PER_ELEMENT_and_not_on_the_container(self) -> None:
        """The distinction the whole mechanism exists for: Django's ``safeseq``
        output is a plain list of ``SafeString``s, not a ``SafeData`` list."""
        out = djust_render("{{ p|safeseq|%s }}" % TYPES, {"p": [STR_ELEM, "<i>y</i>"]})
        assert "(&#x27;list&#x27;, False," in out, out
        assert out.count("SafeString") == 2, out

    def test_a_tuple_grant_still_marks_its_elements(self) -> None:
        """RAW context — see ``test_a_tuple_input``. Through
        ``normalize_django_value`` this would be the list arm wearing a tuple's
        name.
        """
        out = djust_render_raw("{{ p|%s }}" % TYPES, {"p": (STR_ELEM, "<i>y</i>")}, ["p.0", "p.1"])
        assert "(&#x27;tuple&#x27;, False," in out, out
        assert out.count("SafeString") == 2, out


class TestParityWithDjangoIsUnchanged:
    """The deletion is behaviour-preserving, so djust must still agree with
    Django on every shape — including the ones where the grant is refused and
    both engines escape.
    """

    def test_django_agrees_on_an_all_str_sequence(self) -> None:
        for producer in sorted(CONVERTING):
            src = "{{ p%s|%s }}" % (CONVERTING[producer], TYPES)
            ctx = {"p": [STR_ELEM, "<i>y</i>"]}
            assert djust_render(src, ctx) == django_render_normalized(src, ctx), producer

    def test_the_downstream_sinks_are_unchanged(self) -> None:
        """``join`` / ``unordered_list`` are what the item grant exists to
        feed, and they are the observable consequence of the wrap."""
        for src in (
            "{{ p|safeseq|join:', ' }}",
            "{{ p|escapeseq|join:', ' }}",
            "{{ p|safeseq|unordered_list }}",
            "{{ p|safeseq|slice:':2'|join:'' }}",
        ):
            for ctx in (
                {"p": [STR_ELEM, "<i>y</i>"]},
                {"p": [STR_ELEM, 2]},
                {"p": [1, 2]},
                {"p": [None, ["b"]]},
            ):
                d, r = django_render_normalized(src, ctx), djust_render(src, ctx)
                assert r == d, f"{src} on {ctx!r}: django={d!r} djust={r!r}"

    def test_no_chain_became_more_permissive(self) -> None:
        """The direction that matters: djust must grant no capability Django
        does not, on any element shape."""
        hostile = "<img src=x onerror=alert(1)>"
        for expr in PRODUCERS.values():
            for extra in ("|join:''", "|unordered_list", ""):
                src = "{{ p%s%s }}" % (expr, extra)
                for bad in NON_STR.values():
                    ctx = {"p": [hostile, bad]}
                    d = django_render_normalized(src, ctx)
                    r = djust_render(src, ctx, ["p.0", "p.1"])
                    if "<img" in r:
                        assert "<img" in d, (
                            f"{src} on {ctx!r} is LIVE in djust and not in "
                            f"Django: django={d!r} djust={r!r}"
                        )


# ===========================================================================
# The structural pin the issue asks for
# ===========================================================================


class TestTheProducerEnumerationIsComplete:
    """Step 1 of #2337: enumerate every writer of ``InputSafety.items``
    MECHANICALLY, so a fourth producer is a red test rather than prose that
    quietly went stale.

    The deletion's whole warrant is that the enumeration is complete. Writing
    it in a doc-comment is exactly what made the tuple arm's claim expire
    unnoticed (#2290 → #2305), so it is pinned here instead.
    """

    CRATES = Path(__file__).resolve().parents[2] / "crates"
    RENDERER = CRATES / "djust_templates" / "src" / "renderer.rs"
    CONTEXT = CRATES / "djust_core" / "src" / "context.rs"
    REGISTRY = CRATES / "djust_templates" / "src" / "filter_registry.rs"

    def test_items_is_written_from_exactly_one_expression(self) -> None:
        """Every ``InputSafety`` literal reads the same local, so there is one
        question rather than three (#1646).
        """
        src = self.RENDERER.read_text()
        assert src.count("items: items_safe,") == 3, (
            "an InputSafety literal writes `items` from something other than "
            "the threaded `items_safe` local — the enumeration below no "
            "longer covers every producer"
        )
        assert src.count("filters::InputSafety {") == 3

    def test_items_safe_is_assigned_from_exactly_two_functions(self) -> None:
        """The seed and the fold. A third assignment is a new producer."""
        src = self.RENDERER.read_text()
        assert src.count("let mut items_safe = context.items_are_safe(") == 3, (
            "a seed site no longer reads Context::items_are_safe"
        )
        assert src.count("items_safe = filter_output_items_are_safe(") == 3, (
            "a fold site no longer reads filter_output_items_are_safe"
        )
        # Nothing else writes it.
        writes = [
            line
            for line in src.splitlines()
            if "items_safe" in line and "=" in line.split("items_safe")[1][:3] and "==" not in line
        ]
        assert len(writes) == 6, (
            "there are now %d assignments to `items_safe`, not the 6 this "
            "enumeration covers (3 seeds + 3 folds) — a new producer means "
            "mark_item's deleted non-str guard may be reachable again "
            "(#2337)\n%s" % (len(writes), "\n".join(writes))
        )

    def test_the_filter_producers_are_the_two_named_sets(self) -> None:
        src = self.RENDERER.read_text()
        assert 'const ITEM_SAFE_OUTPUT_FILTERS: [&str; 2] = ["safeseq", "escapeseq"];' in src
        assert 'const ITEM_SAFETY_PRESERVING_FILTERS: [&str; 1] = ["slice"];' in src
        start = src.index("fn filter_output_items_are_safe(")
        body = src[start : src.index("\n}", start)]
        assert "ITEM_SAFE_OUTPUT_FILTERS" in body and "ITEM_SAFETY_PRESERVING_FILTERS" in body
        assert body.count("contains(&filter_name)") == 2, (
            "filter_output_items_are_safe grants from a set this enumeration does not name"
        )

    def test_items_are_safe_still_requires_every_element_to_be_a_string(self) -> None:
        """The narrowing the whole deletion rests on.

        Not a style pin: without it, a list of ints whose indices are still in
        the accumulated ``safe_keys`` (#2300) grants item safety, and every
        element reaches ``mark_safe`` to be stringified.
        """
        src = self.CONTEXT.read_text()
        start = src.index("pub fn items_are_safe(")
        body = src[start : src.index("\n    pub fn get(", start)]
        assert "matches!(item, Value::String(_))" in body, (
            "Context::items_are_safe no longer requires every element to be a "
            "String — that narrowing is what makes mark_item's deleted "
            "non-str guard unreachable (#2337)"
        )
        assert "Some(Value::List(items)) | Some(Value::Tuple(items)) => items," in body
        assert "_ => return false," in body

    def test_both_item_safe_output_filters_construct_string_elements(self) -> None:
        """``safeseq`` since #2324, ``escapeseq`` always."""
        src = (self.CRATES / "djust_templates" / "src" / "filters.rs").read_text()
        for name, builder in (
            ("safeseq", "Value::String(item.py_str())"),
            ("escapeseq", "Value::String(conditional_escape(item, input_safety.items))"),
        ):
            start = src.index('"%s" => match iter_values(value)' % name)
            body = src[start : start + 700]
            assert builder in body, (
                f"{name} no longer constructs Value::String elements — it can "
                "hand mark_item a non-str, and #2337's deletion is unsafe"
            )

    def test_mark_item_no_longer_carries_the_dead_guard(self) -> None:
        src = self.REGISTRY.read_text()
        start = src.index("fn mark_item<'py>(")
        body = src[start : src.index("\n}", start)]
        assert "is_instance_of::<PyString>()" not in body, (
            "mark_item's non-str guard is back — if a producer reopened the "
            "branch, say so in #2337 and keep the guard; if not, it is "
            "decorative (#1859)"
        )
        assert "mark_safe.call1((item,))" in body

    def test_mark_input_safety_reaches_mark_item_from_both_sequence_arms(self) -> None:
        """The reason the helper exists: two arms, one policy (#1646)."""
        src = self.REGISTRY.read_text()
        start = src.index("fn mark_input_safety<'py>(")
        body = src[start : src.index("\n/// One ELEMENT", start)]
        assert body.count("mark_item(&mark_safe, item)") == 2
        assert "cast::<PyList>()" in body and "cast::<PyTuple>()" in body
