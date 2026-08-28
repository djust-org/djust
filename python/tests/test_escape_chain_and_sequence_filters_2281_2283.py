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

import ast
import inspect
import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template.defaultfilters import register, stringfilter  # noqa: E402
from django.utils.safestring import SafeData, mark_safe  # noqa: E402

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
    assert djust_out == django_out, (
        f"{source} on {value!r}: django={django_out!r} djust={djust_out!r}"
    )


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
            "add": ":'1'",
            "center": ":'20'",
            "cut": ":'b'",
            "date": ":'Y'",
            "default": ":'D'",
            "default_if_none": ":'D'",
            "dictsort": ":'k'",
            "dictsortreversed": ":'k'",
            "divisibleby": ":'2'",
            "floatformat": ":'2'",
            "get_digit": ":'1'",
            "join": ":', '",
            "ljust": ":'20'",
            "pluralize": ":'s'",
            "rjust": ":'20'",
            "slice": ":':3'",
            "stringformat": ":'s'",
            "time": ":'H'",
            "truncatechars": ":'5'",
            "truncatechars_html": ":'5'",
            "truncatewords": ":'2'",
            "truncatewords_html": ":'2'",
            "urlizetrunc": ":'15'",
            "wordwrap": ":'5'",
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
    @pytest.mark.parametrize("value", [["<b>", "x"], ("<b>", "x"), {"k": "<v>", "j": 2}])
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
            djust_out = _rust.render_template(
                "{{ p|random }}", normalize_django_value({"p": value})
            )
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
        Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "filters.rs"
    ).read_text()
    body = source.split("fn apply_builtin_filter", 1)[1]
    arms = set(re.findall(r'"(\w+)" =>[^\n]*\n?[^\n]*iter_values\(value\)', body))
    arms |= {
        name
        for name in ("join", "safeseq", "escapeseq", "unordered_list", "random")
        if re.search(r'"%s" =>(?:.|\n){0,400}?iter_values\(value\)' % name, body)
    }
    assert arms == {"join", "safeseq", "escapeseq", "unordered_list", "random"}, arms


#: The differential script, read as source by every coupling test below.
_DIFFERENTIAL = Path(__file__).resolve().parents[2] / "scripts" / "filter-parity-differential.py"


def _hot_sets() -> set[str]:
    """The names ``scripts/filter-parity-differential.py`` actually composes."""
    hot = _DIFFERENTIAL.read_text()
    swept = set(re.findall(r'"(\w+)"', hot.split("HOT2 = [", 1)[1].split("]", 1)[0]))
    swept |= set(re.findall(r'"(\w+)"', hot.split("HOT3 = [", 1)[1].split("]", 1)[0]))
    return swept


def _differential_literal(name: str):
    """A top-level literal assignment in the differential script, evaluated.

    Read as SOURCE and never imported: the script configures Django settings and
    mutates the global filter registry at import time. One reader rather than a
    copy per test, so the tests cannot disagree about what the corpus is (#1646).
    """
    tree = ast.parse(_DIFFERENTIAL.read_text())
    literal = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == name for t in node.targets)
    )
    return eval(  # noqa: S307 — a literal from a repo file, with only mark_safe bound
        compile(ast.Expression(literal), f"<{name}>", "eval"), {"mark_safe": mark_safe}
    )


def _rust_const(name: str) -> list[str]:
    """The string literals of a `const NAME: [&str; N] = [...]` in renderer.rs."""
    src = (
        Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "renderer.rs"
    ).read_text()
    body = src.split(f"const {name}: [&str;", 1)[1].split("[", 1)[1].split("];", 1)[0]
    return re.findall(r'"(\w+)"', body)


def test_every_safety_set_member_is_in_the_differential_hot_sets() -> None:
    """The coupling that would have caught the `dictsort` XSS.

    `dictsort` was added to ``ITEM_SAFETY_PRESERVING_FILTERS`` and not to the
    differential's ``HOT2``/``HOT3``. The sweep therefore never composed it, and
    the two-build compare reported ``REGRESSIONS: 0 / INTRODUCED: 0`` across a
    live XSS: djust's ``dictsort`` does not reproduce Django's
    ``except (AttributeError, TypeError): return ""``, so it carried the
    item-safety grant onto a list Django had destroyed, and
    ``{{ hostile|safeseq|dictsort:"x"|join:"" }}`` emitted raw markup.

    The individual `dictsort` decision is not the durable fix — this is. A name
    granted safety in `renderer.rs` and absent from the sweep is a blind spot
    aimed exactly where the change was made, so the two lists are coupled here
    rather than by memory.

    Deliberately one-directional: the hot sets may contain names in no safety
    set (`upper`, `pprint`, …), because composing extra filters only widens the
    sweep. What must never happen is a safety-set member missing from it.
    """
    swept = _hot_sets()

    granted: set[str] = set()
    for const in (
        "SAFE_OUTPUT_FILTERS",
        "ITEM_SAFE_OUTPUT_FILTERS",
        "ITEM_SAFETY_PRESERVING_FILTERS",
    ):
        granted |= set(_rust_const(const))
    assert len(granted) > 5, f"the constants did not parse: {granted}"

    missing = granted - swept
    assert not missing, (
        f"{sorted(missing)} are granted safety in renderer.rs but are not composed "
        f"by scripts/filter-parity-differential.py. Add them to HOT2 and HOT3 in "
        f"the SAME commit — this is the check the `dictsort` XSS defeated."
    )


def _rust_char_set(module: str, predicate: str) -> set[str]:
    """The `char` literals of a `matches!(c, ...)` predicate in a Rust module.

    Parsed rather than transcribed, for the same reason `_rust_const` is: a
    transcription is a second copy that drifts, and the whole point of these
    couplings is that the sweep cannot fall behind the code it measures.
    """
    path = (
        Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / f"{module}.rs"
    )
    body = path.read_text().split(f"fn {predicate}(", 1)[1].split("\n}", 1)[0]
    escapes = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "'": "'", "0": "\0"}
    found: set[str] = set()
    for uni, esc, plain in re.findall(
        r"'(?:\\u\{([0-9a-fA-F]+)\}|\\(.)|([^'\\]))'",
        body,
    ):
        if uni:
            found.add(chr(int(uni, 16)))
        elif esc:
            found.add(escapes[esc])
        else:
            found.add(plain)
    assert found, f"{module}.rs::{predicate} did not parse"
    return found


def test_every_whitespace_boundary_the_engine_branches_on_is_in_the_corpus() -> None:
    r"""The coupling that would have caught the ``wordwrap`` blind spot (#2293).

    ``wordwrap`` was a greedy re-joiner where Django is ``textwrap.TextWrapper``
    — four separate divergences, including a byte-vs-character width — and the
    two-build differential reported ``agree BEFORE == agree AFTER`` across the
    whole fix. Not "0 regressions": *no movement at all*, because every ``s-``
    corpus entry was a single line of ASCII-ish text with single spaces, so the
    tool could not construct one cell in which the two implementations differed.
    It correctly refuses that as a non-baseline, but only because the counts
    happened to be identical; a corpus one cell luckier would have printed
    ``REGRESSIONS: 0`` over an unmeasured change. That is the same failure shape
    as the ``dictsort`` XSS the test above exists for, on the INPUT axis instead
    of the filter-NAME axis.

    The bar is every character the engine's own whitespace predicates branch on,
    parsed out of the Rust: ``pprint::py_is_line_break`` (what a line is) and
    ``textwrap::is_textwrap_space`` (where a chunk boundary is). Those two sets
    are different from each other and from ``truncate::py_is_space``, and #2293
    is the record of what happens when the corpus samples none of them.

    Deliberately corpus-GLOBAL rather than per-filter. A sound "which filters
    read this axis?" derivation is not available — a filter that merely passes a
    character through is indistinguishable, by output, from one that branches on
    it — and inventing one would be a pin that looks mechanical and answers the
    wrong question. The global form is strictly stronger anyway: it demands the
    character be reachable regardless of which filter turns out to need it.

    ``truncate::py_is_space`` is a RANGE (`c.is_whitespace() || '\u{1c}'..='\u{1f}'`)
    rather than a literal set, so it cannot be parsed the same way; its two
    members that neither literal set contains — ``\xa0`` and ``\x1f`` — are
    asserted by name below.
    """
    corpus = "".join(v for v in _differential_literal("INPUTS").values() if isinstance(v, str))

    boundaries = _rust_char_set("pprint", "py_is_line_break") | _rust_char_set(
        "textwrap", "is_textwrap_space"
    )
    assert len(boundaries) >= 12, f"the predicates parsed too small: {sorted(map(ord, boundaries))}"

    missing = sorted(c for c in boundaries if c not in corpus)
    assert not missing, (
        f"{[f'U+{ord(c):04X}' for c in missing]} are whitespace boundaries the engine "
        f"branches on, and NO input in scripts/filter-parity-differential.py contains "
        f"one. Every cell the sweep builds is then blind to any behaviour that turns "
        f"on them — which is exactly how the #2293 wordwrap fix measured as no "
        f"movement at all. Add a character to an INPUTS value in the SAME commit as "
        f"the predicate change."
    )

    # `py_is_space` is a range, not a literal set; these are its two members that
    # neither parsed set contains, and both are load-bearing: `\xa0` is a WORD to
    # textwrap's splitter that `drop_whitespace` nonetheless discards, and `\x1f`
    # survives `splitlines` while stripping to empty.
    for char in ("\xa0", "\x1f"):
        assert char in corpus, (
            f"U+{ord(char):04X} is whitespace to `truncate::py_is_space` and to nothing "
            f"else the engine uses, and no corpus input carries it (#2293)."
        )

    # Arrangements rather than characters, and the other half of what the
    # re-joiner destroyed: it collapsed runs of spaces and dropped indentation.
    assert any("  " in v for v in _differential_literal("INPUTS").values() if isinstance(v, str)), (
        "no corpus input carries a RUN of spaces, which #2293 collapsed"
    )
    assert any(
        v[:1].isspace()
        for v in _differential_literal("INPUTS").values()
        if isinstance(v, str) and v
    ), "no corpus input carries leading indentation, which #2293 dropped"


def test_the_per_call_safety_channel_is_swept_too() -> None:
    """The same coupling, one level down — ``builtin_produced_safe`` (#2299).

    ``renderer.rs``'s three constants are the NAME-based safety channel and the
    test above couples them to the sweep. There is a SECOND channel:
    ``filters::builtin_produced_safe`` answers per CALL, for the filters whose
    safety depends on which branch of the body ran, and it grants exactly as
    much safety as a constant does. #2299 added ``first``/``last``/``random``
    to it; ``first`` and ``last`` were already on the hot sets by luck (they
    were put there for SHAPE coverage, not safety), which is precisely the kind
    of accident this makes into a rule.

    ``random`` is the exemption, and it is principled rather than a carve-out:
    the differential's ``load()`` rewrites every ``NONDET`` cell to a bare
    ``<NONDET>`` marker on BOTH sides, so a ``random`` cell always compares
    equal and can report neither a regression nor a leak. Putting it on the hot
    sets would add ~480 cells that agree by construction — coverage-shaped, and
    blind, which is worse than absent (#1859). Its real coverage is
    ``python/tests/test_context_item_safety_2287.py::
    TestRandomIsCoveredByCapabilityNotByBytes``, which asserts capabilities
    across repeated draws instead of bytes.
    """
    source = (
        Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "filters.rs"
    ).read_text()
    body = source.split("fn builtin_produced_safe", 1)[1].split("\npub fn ", 1)[0]
    granted = {
        name
        for arm in re.findall(r"^\s{8}((?:\"\w+\"\s*\|\s*)*\"\w+\") =>", body, re.M)
        for name in re.findall(r'"(\w+)"', arm)
    }
    assert granted == {
        "join",
        "cut",
        "default",
        "default_if_none",
        "add",
        "first",
        "last",
        "random",
    }, f"the arms did not parse, or a name was added without updating this pin: {granted}"

    hot = (
        Path(__file__).resolve().parents[2] / "scripts" / "filter-parity-differential.py"
    ).read_text()
    nondet = set(re.findall(r'"(\w+)"', hot.split("NONDET = {", 1)[1].split("}", 1)[0]))
    assert "random" in nondet, f"NONDET no longer holds `random`: {nondet}"

    missing = granted - _hot_sets() - nondet
    assert not missing, (
        f"{sorted(missing)} are granted safety per-call by `builtin_produced_safe` "
        f"but are neither composed by scripts/filter-parity-differential.py nor "
        f"exempt as NONDET. Add them to HOT2 and HOT3 in the SAME commit."
    )


def test_the_differential_sweeps_every_shape_the_context_grant_accepts() -> None:
    """The INPUT axis of the same coupling, which nothing pinned (#2305).

    The two tests above couple the FILTER axis: a name granted safety must be
    composed by the sweep. Neither says anything about the input shapes, and
    #2305 is exactly a bug that lived in a shape the corpus did not carry —
    ``Context::items_are_safe`` accepts ``Value::List`` *and* ``Value::Tuple``,
    the corpus carried only a marked LIST, and ``mark_input_safety``'s missing
    ``PyTuple`` arm was therefore invisible to a tool built to see it. Adding
    ``t-marked`` moved 80 cells, so the axis is load-bearing rather than
    shape-coverage tidiness.

    Parsed from the Rust rather than transcribed, so widening the grant to a
    third shape fails here until the corpus grows an input for it. The
    differential's ``INPUTS`` dict is read as a literal — never imported, since
    the script configures Django settings and mutates the global filter
    registry at import time.
    """
    ctx_src = (
        Path(__file__).resolve().parents[2] / "crates" / "djust_core" / "src" / "context.rs"
    ).read_text()
    body = ctx_src.split("pub fn items_are_safe", 1)[1].split("\n    pub fn ", 1)[0]
    accepted = set(re.findall(r"Value::(\w+)\(items\)", body))
    assert accepted, "the `items_are_safe` match did not parse"

    rust_to_python = {"List": list, "Tuple": tuple}
    unknown = accepted - set(rust_to_python)
    assert not unknown, (
        f"`items_are_safe` now accepts {sorted(unknown)}, a shape this test has no "
        f"Python counterpart for. Map it here AND add a marked input of that shape "
        f"to scripts/filter-parity-differential.py's INPUTS in the SAME commit."
    )

    inputs = _differential_literal("INPUTS")

    for variant, py_type in rust_to_python.items():
        if variant not in accepted:
            continue
        marked = [
            key
            for key, value in inputs.items()
            if type(value) is py_type and value and all(isinstance(v, SafeData) for v in value)
        ]
        assert marked, (
            f"`items_are_safe` accepts Value::{variant}, but "
            f"scripts/filter-parity-differential.py carries no {py_type.__name__} "
            f"whose every element is mark_safe'd — so the sweep cannot construct a "
            f"single cell where the CONTEXT grants item safety on that shape. This "
            f"is the blind spot #2305 lived in."
        )


class TestItemSafetyIsNeverMorePermissiveThanDjango:
    """The sweep the #2283 half was missing.

    ``test_no_chain_containing_escape_is_more_permissive_than_django`` covers
    the #2281 half and sweeps only chains containing ``escape``. The `dictsort`
    XSS was a 3-chain with no ``escape`` in it — producer → preserver →
    consumer — so no sweep in this file could see it.

    This composes the item-safety machinery against itself: every producer,
    every candidate preserver, every consumer that reads the grant, on hostile
    data nothing marked safe.
    """

    #: What a browser would execute FROM THE PAYLOAD. `tag:li`/`tag:p`/`tag:br`
    #: are deliberately absent: those are the consumer's own markup.
    PAYLOAD_CAPS = frozenset(
        {"tag:img", "tag:script", "tag:svg", "evt:onerror", "evt:onload", "url:href"}
    )

    PRODUCERS = ["safeseq", "escapeseq"]
    #: Deliberately WIDER than ``ITEM_SAFETY_PRESERVING_FILTERS``. A sweep
    #: restricted to the current set could never catch a name being ADDED to
    #: it, which is the bug this exists for.
    CANDIDATE_PRESERVERS = [
        "slice:':2'",
        "dictsort:'x'",
        "dictsort:0",
        "dictsortreversed:'x'",
        "make_list",
        "first",
        "last",
        "safe",
        "escape",
        "force_escape",
        "striptags",
        "upper",
        "default:'D'",
        "cut:'b'",
        "add:'1'",
        "length",
    ]
    CONSUMERS = ["join:''", "join:', '", "unordered_list"]

    @pytest.mark.parametrize("producer", PRODUCERS)
    def test_no_producer_preserver_consumer_chain_out_grants_django(self, producer):
        payload = ["<img src=x onerror=alert(1)>", "<script>alert(2)</script>"]
        leaks, compared = [], 0
        for preserver in self.CANDIDATE_PRESERVERS:
            for consumer in self.CONSUMERS:
                source = "{{ p|%s|%s|%s }}" % (producer, preserver, consumer)
                try:
                    django_out = DjangoTemplate(source).render(DjangoContext({"p": payload}))
                except Exception:  # noqa: BLE001 — Django raises: no bar to compare
                    continue
                try:
                    djust_out = _rust.render_template(
                        source, normalize_django_value({"p": payload})
                    )
                except Exception:  # noqa: BLE001
                    continue
                compared += 1
                # Only capabilities traceable to the PAYLOAD count. `tag:li` /
                # `tag:p` are markup the consumer generates for itself, and
                # djust legitimately emits them where Django emitted nothing at
                # all (Django's `dictsort` returns `""` for a list of strings,
                # so its `unordered_list` gets an empty sequence). Diffing the
                # raw capability sets flags that as a leak — the same
                # false-positive shape the crude tag-count metric had.
                extra = (capabilities(djust_out) - capabilities(django_out)) & self.PAYLOAD_CAPS
                if extra:
                    leaks.append((source, sorted(extra), djust_out[:90]))
        assert compared > 20, f"only {compared} cells compared — the sweep is not sweeping"
        assert leaks == [], f"{len(leaks)} chains grant capabilities Django does not: {leaks[:3]}"

    def test_the_grant_still_reaches_its_one_legitimate_preserver(self) -> None:
        """Direction 2: the sweep above must not be satisfiable by revoking
        everything. ``slice`` genuinely preserves item identity in Django, and
        ``{{ l|safeseq|slice:":1"|join:"" }}`` is live in BOTH.
        """
        assert_agrees("{{ p|safeseq|slice:':1'|join:'' }}", ["<b>", "x"])
        _, djust_out = render_both("{{ p|safeseq|slice:':1'|join:'' }}", ["<b>", "x"])
        assert djust_out == "<b>", djust_out


class TestPerCallSafetyArmsThatHadNoCoverage:
    """`builtin_produced_safe`'s four arms, two of which nothing exercised.

    The `cut` `;` carve-out and `add`'s `arg_was_quoted` term both survived the
    gate-off with the whole suite green — mutating either made
    `{{ p|safe|cut:";" }}` / `{{ p|safe|add:q }}` emit live markup and nothing
    went red. A surviving mutation is a question, not a pass; here the answer
    was simply missing coverage, so these are the asserting tests.
    """

    def test_cut_preserves_safety_except_for_a_semicolon(self) -> None:
        """Django's `cut` body is::

            safe = isinstance(value, SafeData)
            value = value.replace(arg, "")
            if safe and arg != ";":
                return mark_safe(value)
            return value

        The `";"` carve-out exists because cutting semicolons splits `&lt;`
        into a live `&lt` — so Django deliberately RE-TAINTS there, and djust
        must too. Both directions, because a fix that dropped either half would
        pass a one-sided test.
        """
        # Not ";" — safety survives, and both engines emit the markup live.
        assert_agrees("{{ p|safe|cut:'b' }}", "<i>x</i>")
        _, djust_out = render_both("{{ p|safe|cut:'b' }}", "<i>x</i>")
        assert capabilities(djust_out) == {"tag:i"}, djust_out

        # ";" — the carve-out fires and the value is escaped again.
        assert_agrees("{{ p|safe|cut:';' }}", "<i>x</i>")
        _, djust_out = render_both("{{ p|safe|cut:';' }}", "&lt;i&gt;x")
        assert capabilities(djust_out) == set(), djust_out
        assert "&amp;lt" in djust_out, djust_out

    def test_add_preserves_safety_only_for_a_quoted_literal_argument(self) -> None:
        """`SafeString.__add__` returns a `SafeString` only when the right-hand
        side is ALSO `SafeData`. A template LITERAL is (`Variable.literal` is
        `mark_safe`d); a context-resolved identifier is not, and
        `arg_was_quoted` is what separates them.
        """
        # Quoted literal: Django concatenates two SafeStrings and stays safe.
        assert_agrees("{{ p|safe|add:'!' }}", "<i>x</i>")
        _, djust_out = render_both("{{ p|safe|add:'!' }}", "<i>x</i>")
        assert capabilities(djust_out) == {"tag:i"}, djust_out

        # Bare identifier resolved from the context: NOT SafeData in Django, so
        # the concatenation re-taints and the whole thing is escaped.
        source = "{{ p|safe|add:q }}"
        ctx = {"p": "<i>x</i>", "q": "!"}
        django_out = DjangoTemplate(source).render(DjangoContext(ctx))
        djust_out = _rust.render_template(source, normalize_django_value(ctx))
        assert djust_out == django_out, f"django={django_out!r} djust={djust_out!r}"
        assert capabilities(djust_out) == set(), djust_out

    def test_a_quoted_separator_is_safe_and_a_context_one_is_not(self) -> None:
        """Django's `join` applies `conditional_escape(arg)`, and that is NOT
        "escape the separator" (F3).

        A QUOTED filter argument is `SafeData` — `Variable.__init__` does
        `self.literal = mark_safe(unescape_string_literal(var))` — so
        `{{ l|join:"<br>" }}` renders a real `<br>`. A BARE identifier is
        resolved from the context, is not `SafeData`, and is escaped.

        Escaping unconditionally was a REGRESSION, not just a wrong comment:
        `main`'s `join` joined raw and let the render escape the result, which
        matches Django whenever a later `|safe` suppresses that escape. The
        differential could not see it until `FILTER_ARGS["join"]` carried HTML.
        """
        # Quoted literal: live in both, because the template author wrote it.
        assert_agrees("{{ p|join:'<br>' }}", ["a", "b"])
        _, djust_out = render_both("{{ p|join:'<br>' }}", ["a", "b"])
        assert djust_out == "a<br>b", djust_out

        # The cell class that regressed: a trailing `|safe` removes the render
        # escape, so an eagerly-escaped separator is visible as `&lt;br&gt;`.
        assert_agrees("{{ p|escapeseq|join:'<br>'|safe }}", ["<b>", "x"])

        # Bare identifier from the CONTEXT: not `SafeData`, so it is escaped —
        # the half that keeps a user-supplied separator inert.
        source = "{{ p|join:sep }}"
        ctx = {"p": ["a", "b"], "sep": "<br>"}
        django_out = DjangoTemplate(source).render(DjangoContext(ctx))
        djust_out = _rust.render_template(source, normalize_django_value(ctx))
        assert djust_out == django_out, f"django={django_out!r} djust={djust_out!r}"
        assert capabilities(djust_out) == set(), djust_out

    def test_the_items_are_still_escaped_whatever_the_separator_is(self) -> None:
        """Direction 2: making the separator safe must not make the ITEMS safe.
        The separator is template source; the items are data."""
        payload = ["<img src=x onerror=alert(1)>"]
        _, djust_out = render_both("{{ p|join:'<br>' }}", payload)
        assert capabilities(djust_out) == set(), djust_out


class TestDictsortHasDjangosFailureBranch:
    """`dictsort`'s `except (AttributeError, TypeError): return ""` (F1).

    djust had the sort and not the failure branch, returning the input
    UNCHANGED where Django destroys it. That is harmless until something
    downstream can grant safety — and #2283 gave `safeseq`/`escapeseq` exactly
    that, so `{{ hostile|dictsort:"x"|safeseq|unordered_list }}` emitted raw
    markup on a list Django had already thrown away.

    Dropping the two names from ``ITEM_SAFETY_PRESERVING_FILTERS`` closes
    `safeseq|dictsort` and leaves `dictsort|safeseq` — the same class one step
    over. The failure branch closes both orders at the root, which is why it is
    the fix that shipped.
    """

    HOSTILE = ["<img src=x onerror=alert(1)>", "<script>alert(2)</script>"]

    @pytest.mark.parametrize("name", ["dictsort", "dictsortreversed"])
    @pytest.mark.parametrize("arg", ["'x'", "'name'", "'a.b'", "''", "'1'"])
    def test_an_unresolvable_key_destroys_the_sequence(self, name, arg) -> None:
        """Django returns `""`; anything else keeps a sequence alive that
        Django discarded."""
        source = "{{ p|%s:%s }}" % (name, arg)
        try:
            django_out = DjangoTemplate(source).render(DjangoContext({"p": self.HOSTILE}))
        except Exception:  # noqa: BLE001 — Django's own IndexError on `''`
            django_out = None
        djust_out = _rust.render_template(source, normalize_django_value({"p": self.HOSTILE}))
        assert djust_out == "", f"{source} kept the sequence: {djust_out!r}"
        if django_out is not None:
            assert djust_out == django_out, f"django={django_out!r} djust={djust_out!r}"

    @pytest.mark.parametrize(
        "source",
        [
            "{{ p|dictsort:'x'|safeseq|unordered_list }}",
            "{{ p|dictsort:'x'|safeseq|join:'' }}",
            "{{ p|dictsortreversed:'x'|escapeseq|join:'' }}",
            "{{ p|safeseq|dictsort:'x'|join:'' }}",
            "{{ p|safeseq|dictsort:'x'|unordered_list }}",
        ],
    )
    def test_neither_chain_order_revives_the_discarded_sequence(self, source: str) -> None:
        """BOTH orders, because fixing only the cited one is how this class
        re-formed after the first fix."""
        django_out = DjangoTemplate(source).render(DjangoContext({"p": self.HOSTILE}))
        djust_out = _rust.render_template(source, normalize_django_value({"p": self.HOSTILE}))
        assert capabilities(djust_out) == set(), f"{source} is LIVE: {djust_out!r}"
        assert djust_out == django_out, f"django={django_out!r} djust={djust_out!r}"

    def test_an_unquoted_integer_indexes_and_a_quoted_one_does_not(self) -> None:
        """Direction 2, and the premise correction that produced this shape.

        `_property_resolver` branches on `float(arg)` succeeding — but `arg`
        keeps the Python TYPE the template gave it. `dictsort:0` passes an
        `int`, so `itemgetter(0)` INDEXES and sorts strings by first character.
        `dictsort:"1"` passes a `str`, so `itemgetter("1")` raises `TypeError`
        on those same strings and Django returns `""`.

        "Looks numeric" is therefore the wrong discriminator — `arg_was_quoted`
        is the right one. This test exists because the first version used the
        former and this cell disagreed with Django.
        """
        # Unquoted: indexes, and actually SORTS. djust returned the sequence
        # untouched here before, because `get_dict_value` answers `Missing` for
        # every non-`Object` and the comparator saw every pair as Equal.
        assert_agrees("{{ p|dictsort:0 }}", ["ba", "ab"])
        _, djust_out = render_both("{{ p|dictsort:0 }}", ["ba", "ab"])
        assert djust_out.index("ab") < djust_out.index("ba"), djust_out

        # Quoted: a string key, unresolvable on a string item, so Django raises
        # and the sequence is discarded.
        assert_agrees("{{ p|dictsort:'1' }}", ["ba", "ab"])
        _, djust_out = render_both("{{ p|dictsort:'1' }}", ["ba", "ab"])
        assert djust_out == "", djust_out

    def test_a_real_dict_sequence_still_sorts(self) -> None:
        """Direction 2 again: the shape `dictsort` is actually for."""
        rows = [{"n": "b"}, {"n": "a"}]
        assert_agrees("{{ p|dictsort:'n' }}", rows)
        _, djust_out = render_both("{{ p|dictsort:'n' }}", rows)
        assert djust_out.index("a") < djust_out.index("b"), djust_out
