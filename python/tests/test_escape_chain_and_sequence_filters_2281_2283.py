"""#2281 — ``escape`` is EAGER; #2283 — sequence filters iterate a string.

#2281 was a LIVE XSS, not a cosmetic parity gap.  djust's ``escape`` returned
its input unchanged and left the escaping to render time, which is
indistinguishable from Django for ``{{ p|escape }}`` alone and wrong for every
chain — because the next filter saw the RAW value where Django's sees a
``SafeString`` of the escaped text.  The sharpest cell::

    {{ p|escape|safe }}   on  <img src=x onerror=alert(1)>
        django  &lt;img src=x onerror=alert(1)&gt;
        djust   <img src=x onerror=alert(1)>          <- LIVE

``|safe`` suppressed the deferred escape that was, by then, the only escaping
left.  The idiom reads as "escape it, then it is safe to emit", which is
exactly what Django's semantics make true — and djust turned it into a bare
``|safe`` on attacker input.  A registry-wide probe over every
``{{ p|escape|X }}`` and every length-3 chain containing ``escape`` found 104
live-markup cells on ``main``, every one of them an ``escape`` … ``safe`` pair.

#2283 is the shape half: Python iterates a ``str`` as its CHARACTERS, so
``join`` / ``safeseq`` / ``escapeseq`` / ``unordered_list`` / ``random`` each
produce a per-character answer that djust never produced.  ``first`` / ``last``
/ ``slice`` were named alongside them and were **already correct** — the issue's
list of five is the accurate one.

#2291 is a third instance the #2281 probe found on its own: ``linenumbers``
defers its escape the same way, so ``{{ p|linenumbers|safe }}`` is live too.
It is filed and fixed separately so it stays a reviewable unit; the sweep in
:meth:`TestEscapeIsEager.
test_no_chain_containing_escape_is_more_permissive_than_django` is what found
it, and that sweep is here.

Both fixes are measured the same way and asserted from BOTH directions
(#2259's rule): a hostile payload from an UNSAFE source must still be inert,
AND markup the filter generates itself must stay live.  A probe that checks
only the first passes on fully-escaped output and proves nothing.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template.defaultfilters import register, stringfilter  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

from test_safe_survives_is_safe_filter_2274 import capabilities  # noqa: E402

HOSTILE = [
    "<img src=x onerror=alert(1)>",
    "</script><script>alert(1)</script>",
    '"><svg onload=alert(1)>',
]

#: Django's ``@stringfilter`` wrapper, identified by its closure's code object
#: rather than by name (the #2250 discriminator). A ``@stringfilter`` coerces
#: with ``str()`` before the body runs and therefore can never iterate.
_STRINGFILTER_CODE = stringfilter(lambda x: x).__code__


def render_both(source: str, value) -> tuple[str, str]:
    django_out = DjangoTemplate(source).render(DjangoContext({"p": value}))
    djust_out = _rust.render_template(source, normalize_django_value({"p": value}))
    return django_out, djust_out


def assert_agrees(source: str, value) -> None:
    django_out, djust_out = render_both(source, value)
    assert djust_out == django_out, f"{source} on {value!r}: django={django_out!r} djust={djust_out!r}"


# ---------------------------------------------------------------------------
# #2281 — escape
# ---------------------------------------------------------------------------


class TestEscapeIsEager:
    """Django's ``escape_filter`` is ``conditional_escape(value)``: eager, and
    returning a ``SafeString`` so the rest of the chain sees escaped text."""

    @pytest.mark.parametrize("payload", HOSTILE)
    def test_escape_then_safe_is_not_a_live_xss(self, payload: str) -> None:
        """The reported sink, asserted as a CAPABILITY rather than a string.

        ``{{ p|escape|safe }}`` was an exact synonym for ``{{ p|safe }}`` on
        ``main`` — the whole security claim of #2281 in one cell.
        """
        django_out, djust_out = render_both("{{ p|escape|safe }}", payload)
        assert capabilities(djust_out) == set(), f"LIVE: {djust_out!r}"
        assert djust_out == django_out, f"django={django_out!r} djust={djust_out!r}"

    @pytest.mark.parametrize(
        ("source", "value"),
        [
            # The two cells the issue reports.
            ("{{ p|escape|upper }}", "a < b"),
            ("{{ p|escape|striptags }}", "a<b>c</b>d"),
            # `upper` is `is_safe=False` in Django precisely because
            # upper-casing `&lt;` yields `&LT;`, so the value re-taints and is
            # escaped a second time. That double escape is CORRECT.
            ("{{ p|escape|upper }}", "<img src=x onerror=alert(1)>"),
            # The neighbours the issue asked to check at the same time.
            ("{{ p|escape|force_escape }}", "a < b"),
            ("{{ p|escape|escapejs }}", "a < b"),
            ("{{ p|escape|cut:'b' }}", "<b>x</b>"),
            ("{{ p|escape|default:'D' }}", "<b>x</b>"),
            ("{{ p|escape|add:'1' }}", "<b>x</b>"),
            ("{{ p|escape|linebreaks }}", "<b>x</b>"),
            ("{{ p|escape|urlize }}", "<b>x</b>"),
            ("{{ p|escape|truncatechars_html:8 }}", "<?pi?>"),
            # `escape` alone was always right; keep it pinned so a fix to the
            # chain cannot regress the base case.
            ("{{ p|escape }}", "<img src=x onerror=alert(1)>"),
        ],
    )
    def test_the_reported_and_neighbouring_cells_agree_with_django(
        self, source: str, value: str
    ) -> None:
        assert_agrees(source, value)

    def test_escape_is_conditional_and_force_escape_is_not(self) -> None:
        """The one property that separates the two filters, both directions.

        Django's ``escape`` is ``conditional_escape`` — a ``SafeString`` passes
        through untouched.  ``force_escape`` is ``escape`` — it escapes a
        ``SafeString`` again.  ``force_escape`` exists *because* of that
        difference, so a fix that made ``escape`` unconditional would collapse
        them into one behaviour and this asserts it did not.
        """
        assert_agrees("{{ p|safe|escape }}", "<b>x</b>")
        assert_agrees("{{ p|safe|force_escape }}", "<b>x</b>")
        _, conditional = render_both("{{ p|safe|escape }}", "<b>x</b>")
        _, forced = render_both("{{ p|safe|force_escape }}", "<b>x</b>")
        assert conditional != forced, "escape and force_escape have collapsed"

    def test_no_chain_containing_escape_is_more_permissive_than_django(self) -> None:
        """The registry-wide sweep, not a sample.

        Every ``{{ p|escape|X }}`` and ``{{ p|X|escape }}`` for X across
        Django's LIVE filter registry, asserting djust grants no capability
        Django does not.  Django is the bar, not "nothing is live": Django
        itself emits the payload for ``{{ p|safe|escape }}``, because
        ``conditional_escape`` is a no-op on a value the template author
        already blessed.  On ``main`` this reported the whole ``escape … safe``
        family, where djust was live and Django was not.
        """
        args = {
            "add": ":'1'", "center": ":'20'", "cut": ":'b'", "date": ":'Y'",
            "default": ":'D'", "default_if_none": ":'D'", "dictsort": ":'k'",
            "dictsortreversed": ":'k'", "divisibleby": ":'2'", "floatformat": ":'2'",
            "get_digit": ":'1'", "join": ":', '", "ljust": ":'20'", "pluralize": ":'s'",
            "rjust": ":'20'", "slice": ":':3'", "stringformat": ":'s'", "time": ":'H'",
            "truncatechars": ":'5'", "truncatechars_html": ":'5'", "truncatewords": ":'2'",
            "truncatewords_html": ":'2'", "urlizetrunc": ":'15'", "wordwrap": ":'5'",
            "yesno": ":'y,n,m'",
        }
        leaks, compared = [], 0
        for name in sorted(register.filters):
            spec = name + args.get(name, "")
            for source in ("{{ p|escape|%s }}" % spec, "{{ p|%s|escape }}" % spec):
                for payload in HOSTILE:
                    try:
                        django_out = DjangoTemplate(source).render(DjangoContext({"p": payload}))
                    except Exception:  # noqa: BLE001 — Django raises: no bar to compare against
                        continue
                    try:
                        djust_out = _rust.render_template(
                            source, normalize_django_value({"p": payload})
                        )
                    except Exception:  # noqa: BLE001 — a raise grants no capability
                        continue
                    compared += 1
                    extra = capabilities(djust_out) - capabilities(django_out)
                    if extra:
                        leaks.append((source, payload, sorted(extra), djust_out))
        assert compared > 300, f"the sweep only compared {compared} cells — it is not sweeping"
        assert leaks == [], f"{len(leaks)} cells more permissive than Django: {leaks[:3]}"


# ---------------------------------------------------------------------------
# #2283 — the sequence filters
# ---------------------------------------------------------------------------

#: The five the issue names. Not derived from the Rust source — a test that
#: recomputes the implementation's own answer asserts nothing (#1859).
ITERATING_FIVE = ["join:', '", "safeseq", "escapeseq", "unordered_list", "random"]


class TestSequenceFiltersIterateAString:
    @pytest.mark.parametrize("spec", [s for s in ITERATING_FIVE if not s.startswith("random")])
    @pytest.mark.parametrize("value", ["<b>x</b>", "abc", "", "héllo"])
    def test_a_string_is_a_sequence_of_characters(self, spec: str, value: str) -> None:
        assert_agrees("{{ p|%s }}" % spec, value)

    @pytest.mark.parametrize("spec", ITERATING_FIVE)
    @pytest.mark.parametrize(
        "value", [["<b>", "x"], ("<b>", "x"), {"k": "<v>", "j": 2}]
    )
    def test_the_list_tuple_and_dict_paths_agree_too(self, spec, value) -> None:
        """A dict iterates its KEYS in Python, which is the same question asked
        of a different variant — the axis a string-only fix would have missed.
        """
        if spec == "random":
            # `random.choice` INDEXES, so Python raises `KeyError` on a dict
            # and there is no Django answer to compare against; djust fails
            # soft with a key. The pick itself is asserted below.
            if isinstance(value, dict):
                pytest.skip("random.choice raises on a dict — no Django bar")
            djust_out = _rust.render_template("{{ p|random }}", normalize_django_value({"p": value}))
            rendered = {
                _rust.render_template("{{ p }}", normalize_django_value({"p": item}))
                for item in value
            }
            assert djust_out in rendered, f"{djust_out!r} not one of {rendered}"
            return
        assert_agrees("{{ p|%s }}" % spec, value)

    def test_random_picks_one_character_not_the_whole_string(self) -> None:
        """``random`` is ``random.choice(value)`` — it INDEXES.

        Returning the whole string was not "a random pick of one element", it
        was the sequence itself, which is why this needs its own assertion
        rather than a byte comparison against a nondeterministic Django.
        """
        value = "abcdefghij"
        for _ in range(25):
            out = _rust.render_template("{{ p|random }}", {"p": value})
            assert out in set(value), f"{out!r} is not one character of {value!r}"

    def test_the_iterating_five_are_djangos_iterating_set(self) -> None:
        """Enumerated from the LIVE registry, not from memory (#2250's rule).

        A ``@stringfilter`` coerces with ``str()`` and can never iterate; of the
        rest, a filter ITERATES exactly when ``f("abc")`` equals
        ``f(list("abc"))`` — it never saw the difference between a string and
        its characters.  This asserts the five the fix targets are a SUBSET of
        what that discriminator finds, so the set was measured rather than
        recalled.
        """

        def call(fn, value):
            params = [
                p
                for p in inspect.signature(fn).parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ][1:]
            return fn(value, *["-" for p in params if p.default is p.empty])

        iterating = set()
        for name, fn in register.filters.items():
            if getattr(fn, "__code__", None) is _STRINGFILTER_CODE:
                continue
            try:
                if call(fn, "abc") == call(fn, list("abc")):
                    iterating.add(name)
            except Exception:  # noqa: BLE001
                continue
        # `random` is nondeterministic, so the equality above cannot see it;
        # it is Django's `random.choice`, which indexes, and is asserted by
        # `test_random_picks_one_character_not_the_whole_string` instead.
        named = {s.split(":")[0] for s in ITERATING_FIVE} - {"random"}
        assert named <= iterating, f"not iterating filters in Django: {named - iterating}"

    def test_first_last_and_slice_were_already_correct(self) -> None:
        """The premise correction.

        These three were named alongside the five as string-iteration bugs.
        They were not — they already handled ``Value::String`` — and asserting
        it keeps a future 'fix' from breaking them.
        """
        for source in ("{{ p|first }}", "{{ p|last }}", "{{ p|slice:':3' }}", "{{ p|make_list }}"):
            assert_agrees(source, "<b>x</b>")


class TestItemLevelSafety:
    """Django marks the ITEMS of ``safeseq``/``escapeseq``, never the sequence.

    Two mechanisms, and each needs a test that reddens when only IT is removed
    (#2129): the item GRANT (what ``join``/``unordered_list`` read) and the
    absence of a container grant (what rendering the sequence directly shows).
    """

    def test_the_container_is_not_safe_so_rendering_the_sequence_escapes(self) -> None:
        """Mechanism 1 alone.

        ``safeseq`` sat in ``SAFE_OUTPUT_FILTERS`` on ``main``, so
        ``{{ items|safeseq }}`` emitted the list's repr RAW where Django
        escapes it — djust was MORE permissive than Django on the list path the
        issue described as already correct.
        """
        django_out, djust_out = render_both("{{ p|safeseq }}", ["<b>", "x"])
        assert djust_out == django_out
        assert "&lt;b&gt;" in djust_out and "<b>" not in djust_out, djust_out

    def test_the_items_are_safe_so_join_does_not_escape_them(self) -> None:
        """Mechanism 2 alone — and the documented reason ``safeseq`` exists.

        ``{{ items|safeseq|join:'' }}`` is the idiom; its items must come out
        live.  Removing the item grant leaves this red while the test above
        stays green, which is what makes the two independently reachable.
        """
        django_out, djust_out = render_both("{{ p|safeseq|join:'' }}", ["<b>", "x"])
        assert djust_out == django_out
        assert djust_out == "<b>x", djust_out

    @pytest.mark.parametrize(
        ("source", "value"),
        [
            ("{{ p|safeseq|join:', ' }}", ["<b>", "x"]),
            ("{{ p|safeseq|unordered_list }}", ["<b>", "x"]),
            ("{{ p|escapeseq|join:', ' }}", ["<b>", "x"]),
            ("{{ p|escapeseq|unordered_list }}", ["<b>", "x"]),
            ("{{ p|safeseq|slice:':1'|join:'' }}", ["<b>", "x"]),
            ("{{ p|safeseq|join:', ' }}", "<b>"),
            ("{{ p|escapeseq|join:', ' }}", "<b>"),
        ],
    )
    def test_the_grant_reaches_exactly_the_two_filters_that_read_it(
        self, source: str, value
    ) -> None:
        assert_agrees(source, value)

    def test_a_plain_sequence_is_still_escaped_per_item(self) -> None:
        """Direction 2: the fix must not be 'never escape'."""
        django_out, djust_out = render_both("{{ p|join:', ' }}", ["<img src=x onerror=1>"])
        assert djust_out == django_out
        assert capabilities(djust_out) == set(), djust_out


class TestMarkSafeCollapsesASequence:
    """``mark_safe(list)`` is ``SafeString(str(list))`` — a STRING of the repr.

    Django's ``|safe`` on a container, and its ``is_safe=True`` re-mark after
    ``safeseq``/``escapeseq``, both go through ``mark_safe``, which stringifies.
    So a sequence that was already ``SafeData`` STOPS being a sequence, and the
    item-level grant goes with it.

    These three mechanisms — the ``|safe`` stringification, the
    ``safeseq``/``escapeseq`` collapse, and the item grant refusing to survive
    that collapse — each need a case that reddens when only THAT one is removed
    (#2129).  Without them all three survived a gate-off, because every case in
    the suite exercised at most one.
    """

    @pytest.mark.parametrize(
        ("source", "value"),
        [
            # `|safe` stringifies: Django slices the REPR, not the list.
            ("{{ p|safe|slice:':3' }}", ["<b>", "x"]),
            ("{{ p|safe|first }}", ["<b>", "x"]),
            ("{{ p|safe|join:'' }}", ["<b>", "x"]),
            ("{{ p|safe }}", ["<b>", "x"]),
            ("{{ p|safe }}", {"k": "<v>"}),
            # `safeseq`/`escapeseq` collapse when their input was already safe.
            ("{{ p|safe|safeseq }}", ["<b>", "x"]),
            ("{{ p|safe|escapeseq }}", ["<b>", "x"]),
            ("{{ p|escape|safeseq|slice:':3' }}", "<b>x"),
            ("{{ p|escape|escapeseq|slice:':3' }}", "<b>x"),
            # …and the item grant must not survive that collapse.
            ("{{ p|safe|safeseq|join:'' }}", ["<b>", "x"]),
            ("{{ p|safe|safeseq|unordered_list }}", ["<b>", "x"]),
            ("{{ p|safe|escapeseq|join:'' }}", ["<b>", "x"]),
            ("{{ p|escape|safeseq|join:'' }}", "<b>x"),
        ],
    )
    def test_the_collapsed_chain_agrees_with_django(self, source: str, value) -> None:
        assert_agrees(source, value)

    @pytest.mark.parametrize(
        "source",
        [
            "{{ p|safe|safeseq|join:'' }}",
            "{{ p|safe|safeseq|unordered_list }}",
            "{{ p|safe|escapeseq|join:'' }}",
        ],
    )
    def test_the_collapse_is_not_a_way_back_to_live_markup(self, source: str) -> None:
        """``|safe`` on a container blesses its REPR, not its items.

        Django escapes the repr's characters once they have been re-collected
        into a plain list, and djust must not do less.  Two explicit safety
        assertions in one template is exactly the shape that made this worth
        checking.

        ``{{ p|safe|slice:':40' }}`` is deliberately NOT here: Django emits that
        one live, because ``|safe`` blessed the whole repr and ``slice`` is
        ``is_safe=True``, so the ``SafeString`` survives intact.  Asserting
        inertness there would assert something Django does not do — it is
        covered by the equality parametrization above instead.
        """
        payload = ["<img src=x onerror=alert(1)>"]
        django_out, djust_out = render_both(source, payload)
        assert djust_out == django_out, f"django={django_out!r} djust={djust_out!r}"
        assert capabilities(djust_out) <= capabilities(django_out), djust_out
        assert "evt:onerror" not in capabilities(djust_out), djust_out


class TestNonIterableFallThrough:
    """#2285's escape on the non-sequence branch, and what became of it.

    #2285 added ``html_escape`` there because ``safeseq``/``unordered_list``
    hold an unconditional safe-output grant and were handing a hostile STRING
    back raw under it.  #2283 moved every markup-carrying value onto the
    ITERATING side, so the branch is now reachable only by numbers, booleans,
    ``None`` and ``Decimal`` — none of which contain a character the escape
    changes.

    The escape is kept: it is a no-op for every value reachable TODAY, and
    load-bearing again the moment a non-iterable markup-carrying value exists.
    ``every_non_iterable_variant_is_markup_free`` in
    ``crates/djust_templates/src/filters.rs`` is the enum-side pin; this is the
    behavioural one.
    """

    @pytest.mark.parametrize("value", [42, -1, 1.5, True, False, None])
    @pytest.mark.parametrize("name", ["safeseq", "unordered_list", "escapeseq", "random"])
    def test_the_reachable_non_iterables_carry_no_markup(self, name: str, value) -> None:
        out = _rust.render_template("{{ p|%s }}" % name, normalize_django_value({"p": value}))
        assert capabilities(out) == set(), out
        assert "<" not in out and "&" not in out, (
            f"{name} on {value!r} produced {out!r} — a non-iterable value now "
            f"carries markup, so #2285's escape is load-bearing again"
        )

    def test_join_returns_a_non_iterable_untouched_as_django_does(self) -> None:
        """Django's ``except TypeError: return value``.

        Returning an escaped STRING here instead changes the TYPE the rest of
        the chain sees, which ``{{ n|join:', '|length }}`` measures: ``0`` in
        Django for the int, ``2`` for the string ``"42"``.
        """
        assert_agrees("{{ p|join:', ' }}", 42)
        assert_agrees("{{ p|join:', '|length }}", 42)
        assert_agrees("{{ p|join:', '|pprint }}", None)


def test_the_iteration_sink_has_exactly_the_callers_it_claims() -> None:
    """Grep the SINK, and pin the caller SET rather than a floor (#1125).

    The five filters each grew their own ``Value::List | Value::Tuple`` match
    and fell through to the input for everything else — the same question asked
    five times.  ``iter_values`` is the one answer; this fails if a sixth
    sequence filter is added without routing through it, and if one of the five
    stops using it.
    """
    source = (
        Path(__file__).resolve().parents[2]
        / "crates"
        / "djust_templates"
        / "src"
        / "filters.rs"
    ).read_text()
    body = source.split("fn apply_builtin_filter", 1)[1]
    arms = set(re.findall(r'"(\w+)" =>[^\n]*\n?[^\n]*iter_values\(value\)', body))
    arms |= {
        name
        for name in ("join", "safeseq", "escapeseq", "unordered_list", "random")
        if re.search(r'"%s" =>(?:.|\n){0,400}?iter_values\(value\)' % name, body)
    }
    assert arms == {"join", "safeseq", "escapeseq", "unordered_list", "random"}, arms
