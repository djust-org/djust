"""Filtered tag operands (#2325) and Python slice semantics (#2326).

Both bugs render **nothing**, silently. That is the worst failure shape a
template engine has: no exception, no warning, no console message — just an
empty region where the page had a list, and a template author with no thread
to pull.

#2325 — a filter on a tag operand was dropped
---------------------------------------------
Django resolves a tag's operand with a ``FilterExpression``, the same object
``{{ }}`` uses. djust had one filter-aware resolver (``get_value``) and four
tags that each open-coded a bare variable lookup instead, so the filter chain
after the ``|`` was never applied — the lookup asked for a variable literally
NAMED ``p|slice:":2"``, missed, and the tag proceeded on the miss:

======================================== ==================== ================
cell                                     django               djust (before)
======================================== ==================== ================
``{% for x in p|slice:":2" %}``          ``ab``               ``''``
``{% if p|slice:":1" %}Y{% else %}N``    ``Y``                ``N``
``{% with q=p|upper %}{{ q }}``          ``HI``               ``p|upper``
``{% include "t" with q=p|upper %}``     ``HI``               ``p|upper``
======================================== ==================== ================

The ``{% with %}`` / ``{% include with %}`` rows are the loud ones: their miss
fell back to ``Value::String(expression)``, so the template's OWN SOURCE was
echoed into the page — ``{% with q="lit" %}`` rendered ``&quot;lit&quot;`` and
``{% with q=nope %}`` rendered the variable name ``nope``.

Four spellings of one lookup is the parallel-path-drift class (CLAUDE.md
#1646), so the fix points all four at ``get_value`` rather than teaching each
one about filters. :class:`TestEveryFilteredOperandSiteIsAccountedFor` pins
that enumeration mechanically — a fifth tag that grows its own bare lookup
fails it.

#2326 — slice reimplemented Python's index math, and got it wrong
-----------------------------------------------------------------
Django's ``slice`` is ``value[slice(*bits)]`` — a passthrough, so every Python
rule applies. djust parsed at most two parts and CLAMPED instead of wrapping,
which failed in the two directions an author notices::

    {{ items|slice:":-1" }}   drop the last   -> rendered NOTHING
    {{ items|slice:"-3:" }}   last three      -> rendered EVERYTHING

Seven of ten specs diverged. Patching those two would have left the rest — a
one-part spec is ``slice(stop)`` so ``"2"`` means ``[:2]`` and not ``[2:]``, a
``:step`` was parsed and discarded, a negative step never reversed — so the fix
reproduces CPython's algorithm (``PySlice_AdjustIndices`` plus the walk) rather
than the answers. Value-by-value fixes on a semantics gap do not converge
(v1.1.1-2 retro).

Why these are one PR
--------------------
They are independent defects — one is expression resolution, one is index math
— and each is reachable alone. They ship together because they COMPOSE: the
issue's own headline cell, ``{% for x in items|slice:":-1" %}``, needs both,
and fixing either alone leaves it empty.

Method
------
Curated tables sample one axis and blind you on the next, so the load-bearing
assertions here are randomised differentials against LIVE Django (and, for
``slice``, against Python's own ``slice()`` — the actual source of the rule).
The curated tables that remain are doc-claim pins, one per sentence of the
prose above.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template.defaultfilters import register as django_filter_registry  # noqa: E402
from django.template.defaultfilters import slice_filter  # noqa: E402

from djust import _rust  # noqa: E402

RENDERER_RS = (
    Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "renderer.rs"
)
FILTERS_RS = (
    Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "filters.rs"
)


def django_render(src: str, ctx: dict) -> str:
    return DjangoTemplate(src).render(DjangoContext(ctx))


def djust_render(src: str, ctx: dict) -> str:
    return _rust.render_template(src, ctx)


def both(src: str, ctx: dict) -> tuple[str, str]:
    """Both engines, with a raise recorded as a comparable outcome."""
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


# ===========================================================================
# #2325 — the four operand sites
# ===========================================================================

#: A live payload, so every cell below doubles as a permissiveness probe: if
#: djust ever emits one of these unescaped where Django does not, the
#: differential in :class:`TestNotMorePermissiveThanDjango` says so.
XSS = "<img src=x onerror=alert(1)>"


class TestTheFourOperandSites:
    """One cell per row of the module docstring's table, verbatim."""

    def test_for_applies_the_filter_to_its_iterable(self) -> None:
        assert (
            djust_render('{% for x in p|slice:":2" %}{{ x }}{% endfor %}', {"p": list("abc")})
            == "ab"
        )

    def test_if_tests_the_truthiness_of_a_filtered_operand(self) -> None:
        src = '{% if p|slice:":1" %}Y{% else %}N{% endif %}'
        assert djust_render(src, {"p": list("ab")}) == "Y"

    def test_with_assigns_a_filtered_expression(self) -> None:
        assert djust_render("{% with q=p|upper %}{{ q }}{% endwith %}", {"p": "hi"}) == "HI"

    def test_with_no_longer_echoes_the_template_source_on_a_miss(self) -> None:
        """The loudest half of #2325: the fallback was the expression TEXT.

        Three separate wrong outputs, all from one
        ``unwrap_or_else(|| Value::String(expression))``.
        """
        assert djust_render("{% with q=p|upper %}{{ q }}{% endwith %}", {"p": "hi"}) != "p|upper"
        assert djust_render('{% with q="lit" %}{{ q }}{% endwith %}', {}) == "lit"
        assert djust_render("{% with q=nope %}{{ q }}{% endwith %}", {}) == ""

    def test_include_with_applies_the_filter_to_its_assignment(self) -> None:
        assert_agrees("{% with q=p|upper %}{{ q }}{% endwith %}", {"p": "hi"})

    def test_the_issues_headline_cells(self) -> None:
        """#2325's own reported table, run rather than transcribed."""
        for src, p in [
            ('{% for x in p|slice:":2" %}{{ x }}{% endfor %}', ["a", "b", "c"]),
            ("{% for x in p|safeseq %}{{ x }}{% endfor %}", ["a", "b"]),
            ("{% for x in p|dictsort:0 %}{{ x }}{% endfor %}", [["b"], ["a"]]),
            ("{% for x in p|upper %}{{ x }}{% endfor %}", "ab"),
            # The unfiltered rows the issue reports as already agreeing.
            ("{% for x in p %}{{ x }}{% endfor %}", ["a", "b"]),
            ("{% for x in p %}{{ x }}{% endfor %}", ("a", "b")),
        ]:
            assert_agrees(src, {"p": p})

    def test_the_composed_cell_that_needs_both_fixes(self) -> None:
        """``{% for %}`` + a negative slice — #2325 and #2326 in one operand.

        Empty under either bug alone, which is why the two ship together.
        """
        assert_agrees('{% for x in p|slice:":-1" %}{{ x }}{% endfor %}', {"p": list("abcd")})
        assert_agrees('{% for x in p|slice:"-2:" %}{{ x }}{% endfor %}', {"p": list("abcd")})


class TestForIteratesEveryShapePythonDoes:
    """A filter that returns a STRING is still an iterable.

    ``upper`` / ``join`` / ``first`` / ``last`` all hand ``{% for %}`` a
    string, and Python iterates one by character. Without this the filter
    resolves correctly and the loop STILL renders nothing — the same silent
    symptom one step further along, which is why it is part of #2325 and not a
    follow-up.
    """

    def test_a_string_iterable_yields_characters(self) -> None:
        assert_agrees("{% for x in p|upper %}{{ x }}{% endfor %}", {"p": "ab"})
        assert_agrees("{% for x in p|join:'-' %}{{ x }}{% endfor %}", {"p": ["a", "b"]})
        assert_agrees("{% for x in p|first %}{{ x }}{% endfor %}", {"p": [["a", "b"], ["c"]]})

    def test_a_bare_string_iterable_yields_characters_too(self) -> None:
        """The same arm, with no filter — the rule is about the VALUE."""
        assert_agrees("{% for x in p %}{{ x }}{% endfor %}", {"p": "abc"})

    def test_a_multibyte_string_iterates_by_codepoint_not_byte(self) -> None:
        assert_agrees("{% for x in p %}[{{ x }}]{% endfor %}", {"p": "héllo→"})

    def test_an_empty_string_takes_the_empty_block(self) -> None:
        assert_agrees("{% for x in p %}{{ x }}{% empty %}E{% endfor %}", {"p": ""})

    def test_the_whole_loop_body_works_over_a_string(self) -> None:
        """Normalised into the existing arm, so the loop body comes free.

        A string is turned into a sequence BEFORE the match rather than given
        a fourth match arm, so ``reversed``, ``{% empty %}``, ``{% cycle %}``
        and the nested-loop bookkeeping all behave as they do for a list
        instead of needing a parallel copy (#1646).
        """
        assert_agrees("{% for x in p reversed %}{{ x }}{% endfor %}", {"p": "abc"})
        assert_agrees("{% for x in p %}{% cycle 'A' 'B' %}{{ x }}{% endfor %}", {"p": "abc"})
        assert_agrees(
            "{% for x in p %}{% for y in q %}{{ x }}{{ y }}{% endfor %}{% endfor %}",
            {"p": "ab", "q": "xy"},
        )


class TestFilteredOperandsRandomised:
    """Every filter in Django's LIVE registry, on the ``{% for %}`` operand.

    Read from the registry rather than transcribed, so a Django release that
    adds or drops one is picked up instead of diverging silently. This is the
    load-bearing assertion for #2325: the curated table above says the four
    named cells work, this says the mechanism does.
    """

    #: One benign argument per filter that needs one, so the filter RUNS
    #: rather than raising — the point is the operand channel, not argument
    #: parsing.
    ARGS = {
        "add": '"1"',
        "center": '"6"',
        "cut": '"b"',
        "date": '"Y-m-d"',
        "default": '"D"',
        "default_if_none": '"D"',
        "dictsort": "0",
        "dictsortreversed": "0",
        "divisibleby": '"2"',
        "floatformat": '"2"',
        "get_digit": '"1"',
        "join": '"-"',
        "ljust": '"6"',
        "make_list": None,
        "phone2numeric": None,
        "pluralize": '"s"',
        "rjust": '"6"',
        "slice": '":2"',
        "stringformat": '"s"',
        "time": '"H:i"',
        "truncatechars": '"3"',
        "truncatechars_html": '"3"',
        "truncatewords": '"2"',
        "truncatewords_html": '"2"',
        "urlizetrunc": '"10"',
        "wordwrap": '"5"',
        "yesno": '"y,n"',
    }

    #: Time- or randomness-dependent: the same cell answers differently on two
    #: calls, so an equality assertion would be measuring the clock.
    NONDET = {"random", "timesince", "timeuntil", "date", "time", "filesizeformat"}

    INPUTS = {
        "list": ["a", "b", "c"],
        "list-xss": [XSS, "b"],
        "tuple": ("a", "b", "c"),
        "str": "abc",
        "str-xss": XSS,
        "empty": [],
        "nested": [["b", 2], ["a", 1]],
        "ints": [3, 1, 2],
    }

    #: The refusal shape an operand cell may still differ in, with a mechanical
    #: predicate rather than a name on a list — a name list would need editing
    #: every time a filter's safety changed, and would quietly absorb a real
    #: regression along the way.
    #:
    #: 1. BOTH refuse, and only the exception TYPE differs — every djust
    #:    render error crosses PyO3 as a ``RuntimeError`` where Django raises
    #:    its own class, so the two outcome strings can never compare equal
    #:    however faithful the message is. Reached by a non-iterable operand
    #:    (``{% for x in p|length %}``), which djust rendered the
    #:    ``{% empty %}`` block for until #2382 and now refuses with Django's
    #:    own wording. The type alone would be a name list, so the predicate
    #:    compares the MESSAGES: djust's must END WITH Django's, byte for
    #:    byte, so a djust failure that merely coincided with a Django one is
    #:    ``None`` and gates. Lifted verbatim from
    #:    ``test_dict_iteration_and_sequence_equality_2334_2335.py``'s
    #:    ``_same_reason`` (#2387), which is the same predicate for the same
    #:    reason — each file carries its own ``both``/``render`` pair, which is
    #:    this directory's convention.
    @classmethod
    def _classify(cls, d: str, r: str, src: str, ctx: dict) -> str | None:
        if d.startswith("<<EXC") and r.startswith("<<EXC"):
            return "both-raised" if cls._same_reason(src, ctx) else None
        return None

    @staticmethod
    def _same_reason(src: str, ctx: dict) -> bool:
        """Did the two engines refuse for the same STATED reason?

        ``both()`` records only the exception CLASS, and djust's is always
        ``RuntimeError``, so the class cannot answer this. Re-raise and
        compare the text: djust's is Django's message under a
        ``Template error: `` prefix.
        """
        try:
            django_render(src, ctx)
            return False
        except Exception as exc:  # noqa: BLE001
            django_message = str(exc)
        try:
            djust_render(src, ctx)
            return False
        except Exception as exc:  # noqa: BLE001
            return bool(django_message) and str(exc).endswith(django_message)

    def _cells(self):
        """Every (filter, input) whose ``{{ p|f }}`` cell ALREADY agrees.

        The gate is what makes this a measurement of the OPERAND CHANNEL
        rather than of the filters. Without it the sweep re-reports every
        pre-existing per-filter divergence (``add`` on a list, ``dictsort`` on
        a string) as though this PR caused it, and a real operand regression
        would be one row among dozens of expected ones.
        """
        for name in sorted(django_filter_registry.filters):
            if name in self.NONDET:
                continue
            arg = self.ARGS.get(name)
            spec = f"{name}:{arg}" if arg else name
            for key, value in self.INPUTS.items():
                base_d, base_r = both("{{ p|%s }}" % spec, {"p": value})
                if base_d != base_r:
                    continue
                yield spec, key, value

    def _sweep(self, shape: str) -> None:
        unexplained, cells, residue = [], 0, {}
        for spec, key, value in self._cells():
            src = shape % spec
            cells += 1
            d, r = both(src, {"p": value})
            if d == r:
                continue
            kind = self._classify(d, r, src, {"p": value})
            if kind is None:
                unexplained.append((spec, key, d, r))
            else:
                residue[kind] = residue.get(kind, 0) + 1
        assert cells > 300, f"the sweep collapsed to {cells} cells — the gate is too wide"
        assert not unexplained, (
            f"{len(unexplained)}/{cells} disagree in neither documented shape "
            f"(residue so far: {residue}), first three: {unexplained[:3]!r}"
        )

    def test_every_filter_on_a_for_operand_agrees_with_django(self) -> None:
        self._sweep("{%% for x in p|%s %%}[{{ x }}]{%% empty %%}E{%% endfor %%}")

    def test_every_filter_on_a_with_operand_agrees_with_django(self) -> None:
        self._sweep("{%% with q=p|%s %%}[{{ q }}]{%% endwith %%}")

    def test_every_filter_on_an_if_operand_agrees_with_django(self) -> None:
        """The ``{% if %}`` operand has no residue at all — truthiness is not
        an escaping question, so neither documented shape can apply."""
        cells, bad = 0, []
        for spec, key, value in self._cells():
            cells += 1
            d, r = both("{%% if p|%s %%}Y{%% else %%}N{%% endif %%}" % spec, {"p": value})
            if d != r:
                bad.append((spec, key, d, r))
        assert cells > 300
        assert not bad, f"{len(bad)}/{cells} disagree, first three: {bad[:3]!r}"

    def test_the_residue_is_only_the_documented_refusal_shape(self) -> None:
        """The refusal shape must actually OCCUR, or ``_classify`` is dead code.

        A classifier that never fires is an exemption the sweep is silently
        carrying: it would let a future divergence of that shape through
        without anyone having decided it was acceptable.
        """
        seen = set()
        for shape in (
            "{%% for x in p|%s %%}[{{ x }}]{%% empty %%}E{%% endfor %%}",
            "{%% with q=p|%s %%}[{{ q }}]{%% endwith %%}",
        ):
            for spec, _key, value in self._cells():
                src = shape % spec
                d, r = both(src, {"p": value})
                if d != r:
                    seen.add(self._classify(d, r, src, {"p": value}))
        assert seen == {"both-raised"}, (
            f"the documented residue shapes are no longer the ones that occur: {seen}"
        )

    def test_the_operand_channel_matches_the_variable_channel(self) -> None:
        """djust-internal, so per-filter divergences cancel on both sides.

        ``{% with q=p|f %}{{ q }}{% endwith %}`` and ``{{ p|f }}`` resolve the
        SAME expression; #2325 was precisely that they did not. Asserting it
        against djust's own ``{{ }}`` output isolates the channel from filter
        correctness entirely — this one needs no gate.
        """

        def resolve(src: str, value: object) -> str:
            # A filter that REFUSES is an outcome the two channels must share
            # too, and since #2435 several do (`divisibleby` and `get_digit`
            # over a value `int()` rejects). Capturing the raise keeps those
            # cells IN the sweep — dropping them would have quietly shrunk it
            # by the very cells the refusal introduced.
            try:
                return djust_render(src, {"p": value})
            except Exception as exc:  # noqa: BLE001
                return f"<<{type(exc).__name__}: {exc}>>"

        unexplained, cells, refused = [], 0, 0
        for name in sorted(django_filter_registry.filters):
            if name in self.NONDET:
                continue
            arg = self.ARGS.get(name)
            spec = f"{name}:{arg}" if arg else name
            for _key, value in self.INPUTS.items():
                cells += 1
                var = resolve("{{ p|%s }}" % spec, value)
                with_ = resolve("{%% with q=p|%s %%}{{ q }}{%% endwith %%}" % spec, value)
                refused += var.startswith("<<")
                if with_ != var:
                    unexplained.append((spec, var, with_))
        assert refused, "no cell refused — the raise-capturing branch is dead code"
        assert cells > 400
        assert not unexplained, (
            "a {% with %} operand resolved to something other than what the "
            f"same expression resolves to in {{{{ }}}}: {unexplained[:3]!r}"
        )


class TestOperandFixesDoNotDisturbTheOperatorForms:
    """``{% if %}``'s filter arm is LAST, and this is why.

    An operator form can contain a pipe too. Resolving on the presence of a
    ``|`` any earlier would capture these and hand ``get_value`` a whole
    comparison to look up as one variable name.
    """

    @pytest.mark.parametrize(
        "src",
        [
            "{% if p|length == 3 %}Y{% else %}N{% endif %}",
            "{% if p|length > 1 %}Y{% else %}N{% endif %}",
            "{% if p|length < 9 %}Y{% else %}N{% endif %}",
            "{% if p|length >= 3 %}Y{% else %}N{% endif %}",
            "{% if p|length <= 3 %}Y{% else %}N{% endif %}",
            "{% if p|length != 4 %}Y{% else %}N{% endif %}",
            '{% if not p|slice:":0" %}Y{% else %}N{% endif %}',
            '{% if p|slice:":1" and p %}Y{% else %}N{% endif %}',
            '{% if p|slice:":0" or p %}Y{% else %}N{% endif %}',
            '{% if "a" in p|slice:":2" %}Y{% else %}N{% endif %}',
        ],
    )
    def test_operator_forms_with_a_filtered_operand_agree(self, src: str) -> None:
        assert_agrees(src, {"p": list("abc")})

    @pytest.mark.parametrize(
        "src",
        [
            "{% if p %}Y{% else %}N{% endif %}",
            "{% if not p %}Y{% else %}N{% endif %}",
            "{% if p and p %}Y{% else %}N{% endif %}",
            "{% if True %}Y{% else %}N{% endif %}",
            "{% if p.0 == 'a' %}Y{% else %}N{% endif %}",
        ],
    )
    def test_unfiltered_conditions_are_untouched(self, src: str) -> None:
        assert_agrees(src, {"p": list("abc")})


class TestNotMorePermissiveThanDjango:
    """The direction this fix must not move.

    Four live XSSes were fixed in this machinery in one week, so a parity
    improvement that also widens what reaches the page is a regression even
    when every cell "agrees more". ``get_value_safe`` reports a runtime-safe
    flag and all four operand sites DISCARD it, so a filtered operand can only
    ever be escaped at least as hard as before.
    """

    #: Fragments that are live markup if they reach the page unescaped.
    LIVE = ("<img", "onerror=", "<script")

    @pytest.mark.parametrize(
        "src",
        [
            "{% for x in p %}{{ x }}{% endfor %}",
            "{% for x in p|safe %}{{ x }}{% endfor %}",
            "{% for x in p|safeseq %}{{ x }}{% endfor %}",
            '{% for x in p|slice:":2" %}{{ x }}{% endfor %}',
            "{% with q=p|safe %}{{ q }}{% endwith %}",
            "{% with q=p %}{{ q }}{% endwith %}",
            "{% with q=p|upper %}{{ q }}{% endwith %}",
            '{% if p|slice:":1" %}{{ p.0 }}{% endif %}',
        ],
    )
    def test_no_operand_site_emits_a_live_fragment_django_withholds(self, src: str) -> None:
        for value in ([XSS, XSS], XSS):
            d, r = both(src, {"p": value})
            leaked = {f for f in self.LIVE if f in r and f not in d}
            assert not leaked, (
                f"{src} on {value!r} emits {sorted(leaked)} that Django does not: "
                f"django={d!r} djust={r!r}"
            )

    def test_a_filtered_iterable_registers_no_loop_safe_key_mapping(self) -> None:
        """The mapping asserts ``item`` IS ``<iterable>.<index>``.

        A filter breaks that correspondence — ``slice`` shifts indices,
        ``dictsort`` reorders — so establishing it could resolve a safety mark
        belonging to a DIFFERENT element. The fix registers nothing when the
        operand carries a filter; this pins the guard in the source, because
        the effect is invisible without a ``safe_keys``-carrying context.

        The guard grew a second term in #2334 — ``!normalised`` — for the same
        argument one shape over: a string or a dict is turned INTO a sequence
        before the loop, so the loop index is not an index into the resolved
        value at all. For a dict that one is a live XSS, exercised end to end
        in ``test_dict_iteration_and_sequence_equality_2334_2335.py``.
        """
        src = RENDERER_RS.read_text()
        assert "if !normalised && !iterable.contains('|') {" in src, (
            "the {% for %} arm must gate set_loop_mapping on the operand being "
            "a bare variable path AND on the iterated sequence being the "
            "resolved value's own elements — neither a filtered operand's "
            "indices nor a normalised one's correspond to the source's "
            "(#2325, #2334)"
        )


class TestOperandSitesKeepTheGetattrWalk:
    """The `#806` sidecar path, which routing through ``get_value`` had to keep.

    ``{% for %}`` used to call ``context.resolve()`` directly, and that is
    strictly richer than ``context.get()``: on a miss it walks ``getattr`` /
    ``__getitem__`` over the RAW Python objects attached by
    ``set_raw_py_values``, which is how ``{% for x in user.orders %}`` resolves
    a DB relation or a ``@property`` that no JSON state contains. Pointing the
    tag at ``get_value`` would have silently dropped that — every such loop
    back to rendering empty, the exact symptom #2325 is about — so
    ``get_value_safe`` grew the same fallback as its last arm.

    These need ``RustLiveView`` rather than ``render_template``: the sidecar is
    the whole point, and ``render_template`` cannot attach one. Written
    because the gate-off found the fallback SURVIVED every other test in this
    file — a mutation that survives is a question, and the answer here was
    "genuinely uncovered", not "equivalent mutation" (gating it off makes the
    bare ``{% for x in u.orders %}`` row below render ``''``).
    """

    class _User:
        """Both attributes are properties, so neither is in the JSON state."""

        @property
        def orders(self):
            return ["a", "b", "c"]

        @property
        def label(self):
            return "hi"

    @staticmethod
    def _render(src: str) -> str:
        view = _rust.RustLiveView(src, [])
        view.set_raw_py_values({"u": TestOperandSitesKeepTheGetattrWalk._User()})
        return view.render()

    def test_the_sidecar_is_actually_attached(self) -> None:
        """The premise, pinned: without it every row below is vacuous.

        ``{{ u.label }}`` goes through the ``Node::Variable`` arm, which has
        always used ``resolve()`` — so this row proves the harness works
        independently of anything #2325 changed.
        """
        assert self._render("{{ u.label }}") == "hi"

    @pytest.mark.parametrize(
        ("src", "want"),
        [
            # The #806 case itself: no filter, through the rewritten arm.
            ("{% for x in u.orders %}[{{ x }}]{% endfor %}", "[a][b][c]"),
            # And the same lookup with a filter chain on top — the cell that
            # needs BOTH the fallback and #2325's operand fix.
            ('{% for x in u.orders|slice:":2" %}[{{ x }}]{% endfor %}', "[a][b]"),
            ("{% for x in u.orders|last %}[{{ x }}]{% endfor %}", "[c]"),
            ("{% with q=u.label|upper %}[{{ q }}]{% endwith %}", "[HI]"),
            ("{% with q=u.label %}[{{ q }}]{% endwith %}", "[hi]"),
            ('{% if u.orders|slice:":1" %}Y{% else %}N{% endif %}', "Y"),
            ("{% if u.orders %}Y{% else %}N{% endif %}", "Y"),
        ],
    )
    def test_a_tag_operand_resolves_through_the_raw_python_sidecar(
        self, src: str, want: str
    ) -> None:
        assert self._render(src) == want


class TestEveryFilteredOperandSiteIsAccountedFor:
    """The enumeration, mechanically.

    Four spellings of one lookup is what #2325 was. A fifth tag that grows its
    own bare ``context.get(expr)`` operand lookup fails this test, which is the
    point: it forces the author to route it through ``get_value`` or say why
    not.
    """

    #: The FAMILY of shared resolvers, and it is a closed set on purpose.
    #:
    #: All four are one function: ``get_value_safe_inner`` holds the body, and
    #: the other three are wrappers that pick Django's ``ignore_failures``
    #: parameter and whether to keep the safety half of the return. The #2325
    #: invariant is "an operand goes through the ONE filter-aware resolver
    #: rather than a bare ``context.get``", and a wrapper satisfies it; a FIFTH
    #: name appearing here would not, which is why this is enumerated rather
    #: than matched by prefix.
    SHARED_RESOLVERS = frozenset(
        {
            "get_value",
            "get_value_safe",
            "get_value_ignoring_failures",
            "get_value_safe_ignoring_failures",
        }
    )

    #: Every renderer site that resolves a user-written tag OPERAND — an
    #: expression Django would build a ``FilterExpression`` for. Each must go
    #: through one of :attr:`SHARED_RESOLVERS`.
    OPERAND_SITES = {
        (
            "let iterable_value = get_value_ignoring_failures(iterable, context)?;"
        ): "{% for %} iterable (#2325)",
        # These two moved from `get_value` to `get_value_safe` in #2363: the
        # binding sites now KEEP the safety half of the return instead of
        # discarding it, so the grant travels with the value. `get_value` is a
        # thin wrapper over `get_value_safe` that drops that `bool`, so the
        # #2325 invariant this row pins — the operand goes through the ONE
        # filter-aware resolver rather than a bare `context.get` — is unchanged.
        (
            "let (value, runtime_safe) = get_value_safe(expression, context)?;"
        ): "{% with %} assignment (#2325, #2363)",
        (
            "let (value, runtime_safe) = get_value_safe(value_expr, context)?;"
        ): "{% include ... with %} (#2325, #2363)",
        (
            "Ok(get_value_ignoring_failures(condition, context)?.is_truthy())\n}"
        ): "{% if %} truthiness (#2325)",
        # What makes routing them through the family safe: the shared
        # resolver's last arm keeps the #806 getattr walk the `{% for %}` arm
        # used to do itself. See TestOperandSitesKeepTheGetattrWalk.
        "if let Some(value) = context.resolve(expr)? {": "get_value_safe's #806 fallback",
    }

    #: Which operand sites resolve under Django's ``ignore_failures=True``
    #: (#2528, ADR-027), and which do NOT. `FilterExpression.resolve` turns a
    #: resolution failure into ``None`` for the first set and into
    #: ``string_if_invalid`` for the second, and the difference is observable
    #: the moment a filter follows: `{% if x|default_if_none:y %}` fires the
    #: filter, `{% with x=y|default_if_none:'D' %}` does not.
    #:
    #: Enumerated as a SPLIT rather than as two lists of names, because the
    #: failure this guards is one site drifting to the other side — which a
    #: per-side test cannot see, since each side stays internally consistent.
    IGNORE_FAILURES_SPLIT = {
        "get_value_ignoring_failures(iterable, context)": True,
        "get_value_ignoring_failures(condition, context)": True,
        "get_value_safe(expression, context)": False,
        "get_value_safe(value_expr, context)": False,
    }

    def test_every_operand_site_routes_through_the_shared_resolver(self) -> None:
        src = RENDERER_RS.read_text()
        missing = [why for line, why in self.OPERAND_SITES.items() if line not in src]
        assert not missing, (
            "these tag operands no longer resolve through the shared resolver family "
            f"{sorted(self.SHARED_RESOLVERS)}: {missing}. Django resolves each with a "
            "FilterExpression; a bare context lookup drops the filter chain and renders "
            "nothing (#2325)."
        )

    def test_the_resolver_family_is_exactly_four_wrappers_of_one_body(self) -> None:
        """The family is closed, and it is one function underneath.

        #2325's claim is that there is ONE filter-aware resolver. Four names
        keep that true only while three of them are wrappers: if a wrapper ever
        grows its own pipe loop, the claim quietly becomes false and every site
        above still passes.
        """
        src = RENDERER_RS.read_text()
        defined = {name for name in self.SHARED_RESOLVERS if f"fn {name}(" in src}
        assert defined == self.SHARED_RESOLVERS, (
            f"the resolver family changed: {sorted(defined)} — update "
            "SHARED_RESOLVERS and IGNORE_FAILURES_SPLIT with it"
        )
        assert src.count("fn get_value_safe_inner(") == 1, "the shared body is not one function"
        # Exactly one pipe loop in the file, and it is the body's.
        assert src.count("let pipe_parts = crate::filter_lexer::split_pipes(expr);") == 1, (
            "a second resolver grew its own filter chain — #2325's 'one filter-aware "
            "resolver' claim is no longer true"
        )
        # Each wrapper reaches the body — directly, or through another
        # wrapper: `get_value` delegates to `get_value_safe`, which delegates
        # to the body. What matters is that no wrapper resolves for itself,
        # and the single-pipe-loop assertion above is what proves that.
        for wrapper in (
            "get_value",
            "get_value_safe",
            "get_value_ignoring_failures",
            "get_value_safe_ignoring_failures",
        ):
            body = src.split(f"fn {wrapper}(", 1)[1].split("\n}\n", 1)[0]
            delegates = [
                other
                for other in self.SHARED_RESOLVERS | {"get_value_safe_inner"}
                if other != wrapper and f"{other}(" in body
            ]
            assert delegates, f"{wrapper} no longer delegates — it resolves for itself"

    def test_each_operand_site_passes_djangos_own_ignore_failures(self) -> None:
        """The SPLIT, pinned. `{% if %}` / `{% for %}` ignore failures;
        `{% with %}` and `{% include ... with %}` do not — which is Django, and
        which #2539 got wrong in one direction before this pin existed."""
        src = RENDERER_RS.read_text()
        wrong = []
        for site, ignores in self.IGNORE_FAILURES_SPLIT.items():
            if site not in src:
                wrong.append(f"{site} — site not found")
                continue
            uses_sibling = "ignoring_failures" in site
            if uses_sibling is not ignores:
                wrong.append(f"{site} — expected ignore_failures={ignores}")
        assert not wrong, (
            f"{wrong}. Django passes ignore_failures=True from exactly the tags whose "
            "operand may legitimately be absent ({% if %}, {% for %}, {% cycle %}, "
            "{% firstof %}, {% regroup %}) and False everywhere else — a `{% with %}` "
            "operand that resolves to None fires a `default_if_none` Django does not."
        )

    def test_no_operand_site_kept_the_raw_expression_text_fallback(self) -> None:
        """``{% with %}`` and ``{% include with %}`` echoed the template source.

        ``Value::String(expression)`` / ``Value::String(value_expr)`` as a
        MISS fallback is the specific shape that put ``p|upper`` on the page.
        """
        src = RENDERER_RS.read_text()
        for bad in (
            "Value::String(expression.clone())",
            "Value::String(value_expr.clone())",
        ):
            assert bad not in src, (
                f"{bad} is back: a missed operand lookup must resolve to "
                "Value::Missing (which renders empty, as Django's "
                "string_if_invalid default does), never to the expression's "
                "own source text (#2325)"
            )

    def test_the_for_arm_no_longer_open_codes_its_own_resolution(self) -> None:
        src = RENDERER_RS.read_text()
        assert ".resolve(iterable)?" not in src, (
            "the {% for %} arm resolves its own iterable again — that is the "
            "bare-variable path #2325 removed (#1646)"
        )


# ===========================================================================
# #2326 — Python slice semantics
# ===========================================================================


class TestSliceIsPythonsSlice:
    """Django's ``slice`` is ``value[slice(*bits)]``. So is djust's, now."""

    def test_the_issues_reported_table(self) -> None:
        """#2326's own table, run rather than transcribed."""
        p = ["a", "b", "c", "d"]
        for spec in ("-1:", ":-1", "-2:-1", "::2", "1::2", ":3:2", "::-1"):
            for container in (list, tuple):
                assert_agrees('{{ p|slice:"%s" }}' % spec, {"p": container(p)})

    def test_the_two_headline_failures(self) -> None:
        """Both failed OPEN — showing everything, or an empty region."""
        p = ["a", "b", "c", "d"]
        assert djust_render('{{ p|slice:":-1" }}', {"p": p}) == django_render(
            '{{ p|slice:":-1" }}', {"p": p}
        )
        assert "d" not in djust_render('{{ p|slice:":-1" }}', {"p": p})
        assert "a" not in djust_render('{{ p|slice:"-3:" }}', {"p": p})

    def test_one_part_is_slice_stop_not_slice_start(self) -> None:
        """``int("2")`` is ``bits[0]``, and ``slice(2)`` is ``[:2]``.

        The pre-#2326 code read a one-part spec as the START, so it returned
        the exact complement of what Django returns.
        """
        p = ["a", "b", "c", "d"]
        assert djust_render('{{ p|slice:"2" }}', {"p": p}) == django_render(
            '{{ p|slice:"2" }}', {"p": p}
        )
        assert djust_render('{{ p|slice:"2" }}', {"p": p}) == djust_render(
            '{{ p|slice:":2" }}', {"p": p}
        )


class TestSliceRandomised:
    """The load-bearing assertion for #2326.

    Against Django AND against Python's own ``slice()`` — the latter is the
    actual source of the rule, and would still hold if Django's implementation
    changed. A curated table of seven specs is what let a one-part spec, a
    zero step and a bigint bound through in the first place.
    """

    CONTAINERS = {
        "list4": ["a", "b", "c", "d"],
        "list1": ["a"],
        "list0": [],
        "tuple4": ("a", "b", "c", "d"),
        "tuple0": (),
        "str5": "abcde",
        "str0": "",
        "xss": [XSS, "b", "c"],
    }

    @staticmethod
    def _spec(rng: random.Random) -> str:
        parts = []
        for _ in range(rng.choice([1, 2, 3])):
            parts.append("" if rng.random() < 0.3 else str(rng.randint(-7, 7)))
        return ":".join(parts)

    def test_random_specs_agree_with_django(self) -> None:
        rng = random.Random(2326)
        bad, cells = [], 0
        for _ in range(4000):
            spec = self._spec(rng)
            key = rng.choice(list(self.CONTAINERS))
            cells += 1
            d, r = both('{{ p|slice:"%s" }}' % spec, {"p": self.CONTAINERS[key]})
            if d != r:
                bad.append((spec, key, d, r))
        assert cells == 4000
        assert not bad, f"{len(bad)}/{cells} disagree, first three: {bad[:3]!r}"

    def test_random_specs_match_pythons_own_slice(self) -> None:
        """PYTHON as the reference, not Django.

        ``p[::-1]`` reversing is a language fact; Django agrees with it only
        because its filter is a passthrough.
        """
        rng = random.Random(999)
        for _ in range(4000):
            spec = self._spec(rng)
            key = rng.choice(list(self.CONTAINERS))
            value = self.CONTAINERS[key]
            bits = [int(x) if x else None for x in spec.split(":")]
            if len(bits) == 3 and bits[2] == 0:
                continue  # ValueError in Python; the filter fails silently.
            want = value[slice(*bits)]
            got = djust_render('{{ p|slice:"%s" }}' % spec, {"p": value})
            expect = django_render("{{ q }}", {"q": want})
            assert got == expect, f"slice:{spec!r} of {value!r}: djust={got!r} python={expect!r}"

    def test_a_slice_preserves_the_container_it_was_given(self) -> None:
        """#2321's rule, re-pinned across the specs #2326 unlocked."""
        rng = random.Random(4242)
        for _ in range(500):
            spec = self._spec(rng)
            as_list = djust_render('{{ p|slice:"%s" }}' % spec, {"p": ["a", "b", "c"]})
            as_tuple = djust_render('{{ p|slice:"%s" }}' % spec, {"p": ("a", "b", "c")})
            assert as_list.startswith("["), f"slice:{spec!r} of a list: {as_list!r}"
            assert as_tuple.startswith("("), f"slice:{spec!r} of a tuple: {as_tuple!r}"


class TestSliceFailsSilentlyExactlyWhereDjangoDoes:
    """``except (ValueError, TypeError, KeyError): return value``.

    Read from Django's source rather than assumed — the issue's own quote of
    the ``except`` clause was missing ``KeyError``. Each row below was
    confirmed by running ``slice_filter`` itself.
    """

    #: spec -> the reason Python raises, i.e. why the input comes back whole.
    UNCHANGED = {
        ":::": "four parts: slice() takes at most three (TypeError)",
        "1:2:3:4": "five parts (TypeError)",
        "::0": "zero step (ValueError from the indexing)",
        "1:x": "int('x') (ValueError)",
        "x": "int('x') (ValueError)",
        "1: ": "a lone-space part is not empty and not an int (ValueError)",
        "1.5:": "int('1.5') (ValueError)",
        "--1:": "int('--1') (ValueError)",
        "_1:": "an underscore must be BETWEEN digits (ValueError)",
        "1_:": "trailing underscore (ValueError)",
        "1__0:": "doubled underscore (ValueError)",
    }

    @pytest.mark.parametrize("spec", sorted(UNCHANGED))
    def test_an_unparseable_spec_returns_the_input_unchanged(self, spec: str) -> None:
        for value in (["a", "b", "c", "d"], ("a", "b"), "abcde"):
            assert slice_filter(value, spec) == value, "premise: Django is unchanged here"
            assert_agrees('{{ p|slice:"%s" }}' % spec, {"p": value})

    #: spec -> why Rust's `str::parse::<isize>()` is NOT Python's `int()`.
    ACCEPTED = {
        " 1 : 2 ": "int() accepts surrounding whitespace",
        ":: -2": "and does so in the step too",
        "+1:": "a leading + is accepted",
        "1_0:": "single underscores BETWEEN digits are accepted (int 10)",
        "99999999999999999999999999:": "a bigint bound, past every len",
        ":-99999999999999999999999999": "and a negative one",
    }

    @pytest.mark.parametrize("spec", sorted(ACCEPTED))
    def test_int_accepts_more_than_rusts_parse_does(self, spec: str) -> None:
        for value in (["a", "b", "c", "d"], ("a", "b"), "abcde"):
            assert_agrees('{{ p|slice:"%s" }}' % spec, {"p": value})

    def test_the_underscore_case_is_the_one_that_fails_open(self) -> None:
        """Rejecting ``1_0`` would render EVERY element where Django renders none.

        The others fail closed; this one is the reason ``python_int`` bothers
        with underscores at all.
        """
        p = ["a", "b", "c", "d"]
        assert django_render('{{ p|slice:"1_0:" }}', {"p": p}) == django_render(
            "{{ q }}", {"q": []}
        )

    @pytest.mark.parametrize("value", [123, 1.5, None, True, {"a": 1}])
    def test_a_non_sequence_comes_back_unchanged(self, value) -> None:
        assert slice_filter(value, ":2") == value, "premise: Django is unchanged here"
        assert_agrees('{{ p|slice:":2" }}', {"p": value})

    def test_an_empty_or_bare_colon_spec_is_everything(self) -> None:
        for spec in ("", ":", "::"):
            assert_agrees('{{ p|slice:"%s" }}' % spec, {"p": ["a", "b", "c"]})


class TestSliceHelpersAreShared:
    """One index-math helper, not one per container branch.

    The ``String`` and sequence branches each had their own copy of the
    clamp before #2326, which is the pair that would drift apart again
    (CLAUDE.md #1646).
    """

    def test_the_index_math_lives_in_one_function(self) -> None:
        src = FILTERS_RS.read_text()
        assert src.count("fn slice_positions(") == 1
        # Both branches of apply_slice call it, and nothing else does.
        start = src.index("fn apply_slice(")
        end = src.index("\nfn python_int(")
        body = src[start:end]
        assert body.count("slice_positions(") == 2, (
            "apply_slice's String and sequence branches must BOTH go through "
            f"slice_positions; found {body.count('slice_positions(')}"
        )
        assert "parse_slice_indices" not in src, "the pre-#2326 clamping parser is back"

    def test_a_string_and_a_list_slice_identically(self) -> None:
        """The property the shared helper buys, measured rather than asserted."""
        rng = random.Random(77)
        for _ in range(600):
            parts = [
                "" if rng.random() < 0.3 else str(rng.randint(-6, 6))
                for _ in range(rng.choice([1, 2, 3]))
            ]
            spec = ":".join(parts)
            src = '{{ p|slice:"%s" }}' % spec
            as_str = djust_render(src, {"p": "abcde"})
            as_list = djust_render(src, {"p": list("abcde")})
            # `['a', 'c']` vs `ac` — compare the ELEMENTS the two picked.
            picked = re.findall(r"#x27;(.)&#x27;", as_list)
            assert "".join(picked) == as_str, (
                f"slice:{spec!r}: string picked {as_str!r}, list picked {picked!r}"
            )


class TestTheCorpusGapThatHidThisFromTheDifferential:
    """``scripts/filter-parity-differential.py`` never built a tag cell.

    Its corpus is ``{{ p|... }}`` chains only, so #2325 was structurally
    invisible to the tool that exists to catch exactly this class — the same
    reason it reported clean over a live XSS once already. The script now
    carries a ``{% for %}`` / ``{% with %}`` / ``{% if %}`` axis; this pins
    that it does, because a corpus gap is silent by construction.
    """

    SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "filter-parity-differential.py"

    def test_the_differential_corpus_contains_a_tag_operand_axis(self) -> None:
        src = self.SCRIPT.read_text()
        for fragment in ("{% for x in p|", "{% with q=p|", "{% if p|"):
            assert fragment in src, (
                f"the differential builds no {fragment!r} cell — a filter on a "
                "tag operand is a different resolution path from {{ p|f }} and "
                "#2325 lived entirely in the part it did not measure"
            )

    def _compare(self, tmp_path, base: dict, after: dict):
        """Run the script's own ``--compare`` in a subprocess.

        A subprocess rather than an import: the script registers custom filters
        on Django's default ``Engine`` and on the Rust registry at import time,
        which would leak into every later test in the session.
        """
        import json
        import subprocess
        import sys

        b, a = tmp_path / "b.json", tmp_path / "a.json"
        b.write_text(json.dumps(base))
        a.write_text(json.dumps(after))
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), "--compare", str(b), str(a)],
            capture_output=True,
            text=True,
            cwd=self.SCRIPT.parents[1],
        )

    def test_a_coincidental_tag_agreement_is_not_reported_as_a_regression(self, tmp_path) -> None:
        """The classifier that made #2325's 445 reported regressions readable.

        Both cells below stop agreeing. The tag one agreed on the baseline only
        because the operand bug rendered nothing and Django rendered nothing
        too, and its ``{{ }}`` twin diverges on BOTH builds — so it is
        coincidental. The other has an AGREEING twin and is a real regression,
        which must still gate.
        """
        base = {
            # Twin diverges on both builds -> the tag cell's agreement was luck.
            "f\tk": ["X", "Y"],
            "f\tk\tfor": ["E", "E"],
            # Twin agrees -> nothing is masking anything.
            "g\tk": ["Q", "Q"],
            "g\tk\tfor": ["Z", "Z"],
        }
        after = dict(base, **{"f\tk\tfor": ["E", "R"], "g\tk\tfor": ["Z", "W"]})
        r = self._compare(tmp_path, base, after)
        assert "coincidental (the filter itself diverges on both builds): 1" in r.stdout, r.stdout
        assert "REGRESSIONS : 1" in r.stdout, r.stdout
        assert r.returncode == 1, "a real regression must still gate"

    def test_the_classifier_does_not_excuse_a_variable_cell(self, tmp_path) -> None:
        """A ``{{ }}`` cell has no mask to be behind, so it can never be
        classified coincidental however its neighbours behave."""
        base = {"f\tk": ["X", "X"]}
        after = {"f\tk": ["X", "Y"]}
        r = self._compare(tmp_path, base, after)
        assert "coincidental (the filter itself diverges on both builds): 0" in r.stdout, r.stdout
        assert "REGRESSIONS : 1" in r.stdout, r.stdout
        assert r.returncode == 1

    def test_an_unescaped_payload_still_gates_while_escaped_text_does_not(self, tmp_path) -> None:
        """The other half: ``live()`` substring-matches, so a fragment inside
        fully-escaped text is not a leak — but a real one must still fail."""
        payload = "<img src=x onerror=alert(1)>"
        escaped = "&lt;img src=x onerror=alert(1)&gt;"
        # Every cell here DISAGREES on both builds, so none of them is a
        # regression — otherwise the exit code would gate on the regression
        # and this would assert nothing about the leak half. `h` exists only
        # to make the two agreement counts differ, which the script requires
        # as its "is the baseline real" check.
        base = {"f\ts-img": ["", "x"], "g\ts-img": ["", "y"], "h\ts-img": ["A", "B"]}
        after = {
            "f\ts-img": ["", escaped],
            "g\ts-img": ["", payload],
            "h\ts-img": ["A", "A"],
        }
        r = self._compare(tmp_path, base, after)
        assert "REGRESSIONS : 0" in r.stdout, r.stdout
        assert "fragment inside fully-escaped text (not live): 1" in r.stdout, r.stdout
        assert "LIVE (an unescaped tag opener)              : 1" in r.stdout, r.stdout
        assert r.returncode == 1, "an unescaped payload must gate"

    def test_escaped_only_leaks_alone_do_not_gate(self, tmp_path) -> None:
        """The gate-off sibling of the case above: drop the LIVE cell and the
        same run must pass, or the previous test proves nothing about which
        half gated."""
        escaped = "&lt;img src=x onerror=alert(1)&gt;"
        base = {"f\ts-img": ["", "x"], "h\ts-img": ["A", "B"]}
        after = {"f\ts-img": ["", escaped], "h\ts-img": ["A", "A"]}
        r = self._compare(tmp_path, base, after)
        assert "REGRESSIONS : 0" in r.stdout, r.stdout
        assert "fragment inside fully-escaped text (not live): 1" in r.stdout, r.stdout
        assert "LIVE (an unescaped tag opener)              : 0" in r.stdout, r.stdout
        assert r.returncode == 0, r.stdout


class TestAdjacentDivergencesNotFixedHere:
    """Scope discipline (CLAUDE.md #1079): found, filed, pinned — not fixed.

    Four of the divergences this class pinned — ``{% regroup %}`` with a
    filtered source (#2333), ``{% for k, v in d.items %}`` (#2334),
    sequence equality in ``{% if %}`` (#2335) and ``{{ forloop.* }}``
    (#2402) — have since been FIXED, so their pins are gone and their
    coverage lives in
    ``test_dict_iteration_and_sequence_equality_2334_2335.py`` and
    ``test_forloop_parity_2402.py``. That is the contract those pins
    carried: each named itself as the thing to delete.
    """

    def test_forloop_IS_now_available_through_render_template(self) -> None:
        """Was ``test_forloop_is_not_available_…``, and pinned the #2402 bug.

        Kept rather than simply deleted, and inverted in place, because the
        claim it makes is about THIS entry point and THIS pair of operand
        shapes: a list and the string that #2325 normalises into the same
        arm. The fix must reach both, and the string is the one a
        normalisation could plausibly lose.
        """
        for value in (["a", "b"], "ab"):
            assert (
                djust_render(
                    "{% for x in p %}{{ forloop.counter }}{{ x }}{% endfor %}", {"p": value}
                )
                == "1a2b"
            )

    def test_ifchanged_is_an_unsupported_tag(self) -> None:
        """Not a divergence to fix: djust's system checks flag it as
        unsupported, so raising is the documented behaviour.
        """
        from djust.checks.templates import _UNSUPPORTED_TAGS_RE

        assert _UNSUPPORTED_TAGS_RE.search("{% ifchanged x %}")
