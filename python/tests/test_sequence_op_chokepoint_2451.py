"""Seven filters iterate or subscript, and Django's bodies have no guard (#2451).

The divergence
--------------
#2435 closed the ``int(value)`` column of the ``{% widthratio %}`` bucket. This
is the other one, and it is a different mechanism: five built-ins ITERATE or
SUBSCRIPT their value and two more call a string method on it, each with an
``except`` clause that catches nothing relevant, so the operation's exception
IS the filter's answer::

    def first(value):
        try: return value[0]
        except IndexError: return ""                        # IndexError ONLY
    def last(value):
        try: return value[-1]
        except IndexError: return ""                        # IndexError ONLY
    def random(value):
        try: return random_module.choice(value)             # which is value[i]
        except IndexError: return ""
    def escapeseq(value): return [conditional_escape(o) for o in value]   # nothing
    def safeseq(value):   return [mark_safe(o) for o in value]            # nothing
    def unordered_list(value, autoescape=True): … iter(item_list) …       # nothing
    def phone2numeric(phone): return "".join(… for c in phone.lower())    # nothing

djust failed soft on every one::

    {{ p|first }}         p = 5      django  <<TypeError: 'int' object is not subscriptable>>
                                     djust   ''
    {{ p|escapeseq }}     p = 5      django  <<TypeError: 'int' object is not iterable>>
                                     djust   '42'
    {{ p|phone2numeric }} p = None   django  <<AttributeError: 'NoneType' … 'lower'>>
                                     djust   '6663'      (the keypad spelling of "None")

Four premises re-derived, three of which the issue got wrong
------------------------------------------------------------
Each was run against live Django over the differential's own 41-value corpus,
not transcribed — :class:`TestTheReferenceTableIsRunNotTranscribed` is the
sweep, and it is the load-bearing test in this file.

1. **The widthratio bucket is 1,088**, as the issue says — confirmed on a fresh
   two-build run of ``scripts/filter-parity-differential.py`` over 353,909
   cells. It is **12** after this change, and every survivor is a different
   defect (below).
2. **The single-filter ``{{ }}`` column is 118, not 113**, and the composition
   differs. The issue omits ``random`` (17 cells) and includes eleven that
   belong to other classes. Measured: ``phone2numeric`` 30, ``last`` 19,
   ``random`` 17, ``first`` 16, and 12 each for the three iterators.
3. **``join`` / ``slice`` / ``default`` / ``default_if_none`` / ``cf_ident``
   are NOT this class.** The issue attributes 2 cells to each. They are all one
   thing: ``{{ p }}`` over ``Decimal("Infinity")`` or ``Decimal("NaN")`` raises
   ``TypeError: bad operand type for abs(): 'str'`` in Django's
   ``numberformat.format`` — the bare RENDER, with no filter involved at all.
   Pinned in :class:`TestTheResidueThisDoesNotTouch` and filed as #2460.
4. **``first`` and ``last`` disagree about a dict, and neither merely
   "refuses".** ``d[0]`` is a KEY lookup: four of the corpus's seven dicts have
   no ``0`` key and raise, three have one and answer its VALUE, and all seven
   lack ``-1`` so ``last`` raises on every one. A rule saying "a mapping
   refuses" would be permissive on three cells and strict on one.

The shape
---------
One error enum plus one message constructor — [`ValueOpError`] /
``value_op_error`` — mirroring #2435's ``IntValueError`` / ``int_value_error``,
because the question is the same one: WHICH exception does Python raise, since
Django's ``except`` clauses catch different subsets. Three thin probes name the
three operations (``python_iter`` / ``python_getitem`` / ``python_lower``), and
they share one ``python_type_name`` — which the ``{% for %}`` refusal arm in
``renderer.rs`` now reads too, retiring the four-arm copy it carried (#1646).
"""

from __future__ import annotations

import ast
import datetime
import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template.defaultfilters import register  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FILTERS_RS = REPO / "crates" / "djust_templates" / "src" / "filters.rs"
RENDERER_RS = REPO / "crates" / "djust_templates" / "src" / "renderer.rs"


def production(path: Path) -> str:
    """A crate module's source with every ``#[cfg(test)]`` block removed.

    Splitting on the FIRST ``#[cfg(test)]`` — the obvious shortcut — silently
    drops most of ``filters.rs``: it carries **five** inline test modules, and
    every helper this file pins lives after the first one. The first version of
    these pins did exactly that and reported zero callers for a function with
    two, which is a test that passes for the wrong reason in the direction that
    matters (#1859).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    keep, i = [], 0
    while i < len(lines):
        if lines[i].strip() == "#[cfg(test)]":
            while i < len(lines) and not lines[i].startswith("}"):
                i += 1
            i += 1  # the closing brace of the test module
            continue
        keep.append(lines[i])
        i += 1
    return "\n".join(keep)


#: The seven filters whose Django body performs an unguarded operation on the
#: VALUE. Split by which operation, because the two halves answer differently
#: for a mapping and that difference is the whole of premise 4 above.
ITERATORS = ("escapeseq", "safeseq", "unordered_list")
SUBSCRIPTERS = ("first", "last", "random")
STRING_METHOD = ("phone2numeric",)
ALL_SEVEN = ITERATORS + SUBSCRIPTERS + STRING_METHOD

#: Nondeterministic on BOTH sides — `random.choice` draws, and over a mapping
#: the draw decides whether Python raises at all.
NONDET = {"random"}


def outcome(source: str, value, engine: str) -> str:
    """One cell's answer: the rendered text, or the exception CLASS.

    The class and not the message: Django says ``KeyError: 0`` and djust says
    ``RuntimeError: filter 'first' raises KeyError: 0 — …``, and comparing the
    two texts would mark every agreeing refusal as a disagreement — the defect
    #2454 is about, one layer down.
    """
    try:
        if engine == "django":
            return DjangoTemplate(source).render(DjangoContext({"p": value}))
        return _rust.render_template(source, normalize_django_value({"p": value}))
    except Exception as exc:  # noqa: BLE001 — the refusal IS the answer
        if engine == "django":
            return f"<<{type(exc).__name__}>>"
        # djust wraps every Django exception class in one `RuntimeError`, so
        # the class it MODELS is read out of the message the chokepoint writes.
        found = re.search(r"raises (\w+Error)", str(exc))
        return f"<<{found.group(1)}>>" if found else f"<<{type(exc).__name__}>>"


#: The differential's own value corpus, read from the script rather than
#: re-typed: a private copy would drift, and every value in it was added
#: because some earlier issue found a defect only that shape could reach.
def corpus() -> dict:
    source = (REPO / "scripts" / "filter-parity-differential.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    def literal(name: str) -> ast.Dict:
        # `ast.AnnAssign` as well as `ast.Assign`: `INPUTS_LAZY` carries a type
        # annotation, and an `Assign`-only reader finds nothing for it — which
        # is a `StopIteration` at import, not a silent miss, and is how this
        # arm was written.
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                targets, value = [node.target], node.value
            else:
                continue
            if (
                isinstance(targets[0], ast.Name)
                and targets[0].id == name
                and isinstance(value, ast.Dict)
            ):
                return value
        raise AssertionError(
            f"the differential no longer assigns a dict literal to `{name}`. The "
            f"corpus this chokepoint sweeps is read from that file; fix the reader "
            f"in the same commit rather than letting it sweep a subset."
        )

    # `Decimal(...)` / `mark_safe(...)` / `datetime.timedelta(...)` are calls,
    # so `literal_eval` cannot take the dict whole; each value is evaluated in a
    # namespace holding exactly the names the corpus uses. `datetime` arrived
    # with the `timedelta` rows (#2469) — the only member of the
    # `Value::Encoded` family with a falsy inhabitant.
    from collections import namedtuple
    from decimal import Decimal

    env = {
        "Decimal": Decimal,
        "mark_safe": mark_safe,
        "datetime": datetime,
        "namedtuple": namedtuple,
    }

    def value(node: ast.expr):
        return eval(  # noqa: S307 — a repo file's own literals
            compile(ast.Expression(node), "<corpus>", "eval"), env
        )

    built = {
        ast.literal_eval(k): value(v)
        for k, v in zip(literal("INPUTS").keys, literal("INPUTS").values, strict=True)
    }
    # `INPUTS_LAZY` is the SAME corpus, spelled as zero-argument factories for
    # the rows the eager mapping cannot hold (#2482) — a `dict_keys` cannot be
    # deep-copied, which is what the differential's own `@cmp` axis does to a
    # second operand. Read here too, and CALLED: reading only the eager literal
    # would leave this chokepoint sweeping a strict subset of the corpus the
    # differential sweeps, which is the parallel-path drift (#1646) the "read it
    # from the script rather than re-type it" rule exists to prevent — one level
    # up, and invisible, because a missing row cannot fail anything.
    #
    # Each factory is self-contained over builtins plus the three names above,
    # which is the constraint the script's own comment on `INPUTS_LAZY` records.
    # `eval` injects `__builtins__` into a globals mapping that has none, so a
    # `type("C", (), {...})()` spelling evaluates here perfectly well.
    built.update(
        {
            ast.literal_eval(k): value(v)()
            for k, v in zip(literal("INPUTS_LAZY").keys, literal("INPUTS_LAZY").values, strict=True)
        }
    )
    return built


CORPUS = corpus()

# Two tables lived here — `NORMALIZER_FLATTENED` (#2477) and
# `STRINGIFIED_AT_CONVERSION` (#2482) — recording thirteen cells that RENDERED
# where Django refuses, split by which of djust's two paths was responsible.
# Both were written with the same terms: *deleted when the fix lands, rather
# than left behind as cover for the next one*.
#
# #2477/#2489 landed and all thirteen agree, so both tables are gone, along
# with the two non-vacuity tests that read them
# (`test_2477_the_recorded_four_are_the_NORMALIZERS_doing` and
# `test_2482_the_recorded_six_are_the_CONVERSIONS_doing`). What replaced them
# is not a smaller table: `test_no_cell_renders_where_django_refuses` below now
# subtracts NOTHING, which is a stronger statement than any exemption list, and
# the per-path account those two tests carried moved to
# `python/tests/test_opaque_collections_2477_2489.py`, where it is asserted
# over nineteen members and sixteen consumers rather than seven filters.
#
# The diagnosis they encoded is worth keeping in prose, because it is what made
# the two halves separable: a `set` was flattened to a sorted LIST by
# `normalize_django_value` (so `first` rendered an ELEMENT on the LiveView path
# and the raw path refused), while a non-empty `dict_keys` was stringified by
# the CONVERSION (so both paths rendered the character `d`). One symptom, two
# mechanisms, and a single table would have recorded a diagnosis that was false
# for half its rows.


class TestTheReferenceTableIsRunNotTranscribed:
    """The differential itself: every one of the seven, over every corpus value.

    A curated table samples one axis and blinds you on the next; Django is a
    subprocess-free import away, so the reference is CALLED. This sweep is what
    found premise 4 — that `first` and `last` disagree about a dict, and that
    three of the corpus's seven dicts do not raise for `first` at all.
    """

    def test_no_cell_renders_where_django_refuses(self) -> None:
        """No exceptions — and for twelve cells that is new, as of #2467.

        `outcome(..., "djust")` renders through `normalize_django_value`, so
        this sweep's djust column is the **LiveView** path. #2469 put a
        `timedelta` in the corpus and twelve cells appeared here at once: the
        normalizer flattened it to the ISO duration string `"P0DT00H00M00S"` in
        Python, so djust iterated the thirteen CHARACTERS of that string where
        Django raises `TypeError: 'datetime.timedelta' object is not iterable`
        — `{{ p|unordered_list }}` emitted thirteen `<li>`s, `{{ p|first }}` was
        `'P'`, `{{ p|phone2numeric }}` was `7038004006007`.

        They refused on the RAW path throughout, which is what identified it as
        the flattening rather than a hole in this chokepoint. #2467 stopped the
        flattening; the exact-set pin that recorded the twelve is deleted with
        it, per its own terms.

        #2477 added the `set` pair and four cells appeared the SAME way, for
        the same reason one type over: `normalize_django_value` had no arm for
        the class #2466 closed at the conversion, so on the LiveView path a
        `set` was a sorted LIST — subscriptable, where a set is not. #2482 then
        made a `dict_keys` representable and nine more appeared. All thirteen
        were recorded rather than allowed, and #2477/#2489 closed every one, so
        this assertion subtracts NOTHING — see the block comment above.
        """
        offenders = []
        for name in ALL_SEVEN:
            if name in NONDET:
                continue
            source = "{{ p|%s }}" % name
            for key, value in CORPUS.items():
                dj = outcome(source, value, "django")
                du = outcome(source, value, "djust")
                if dj.startswith("<<") and not du.startswith("<<"):
                    offenders.append((name, key, dj, du))
        assert not offenders, f"{len(offenders)} cells render where Django refuses:\n" + "\n".join(
            f"  {n} <{k}>: django={a} djust={b!r}" for n, k, a, b in offenders[:15]
        )

    def test_2467_a_timedelta_refuses_on_BOTH_paths_now(self) -> None:
        """Non-vacuity for the twelve cells the sweep above stopped reporting.

        `not offenders` passing is also what a corpus with no `timedelta` in it
        would print, so the claim is asserted directly: the same value, the same
        six filters, refused through the normalizer AND through the raw entry
        point. Before #2467 only the second half held.
        """
        for name in ("escapeseq", "safeseq", "unordered_list", "first", "last", "phone2numeric"):
            for key in ("td-zero", "td-plain"):
                with pytest.raises(Exception, match="not iterable|not subscriptable|raises"):
                    _rust.render_template("{{ p|%s }}" % name, {"p": CORPUS[key]})
                with pytest.raises(Exception, match="not iterable|not subscriptable|raises"):
                    _rust.render_template(
                        "{{ p|%s }}" % name, normalize_django_value({"p": CORPUS[key]})
                    )

    def test_no_cell_refuses_where_django_renders(self) -> None:
        """The other direction, and the one a refusal-shaped fix gets wrong.

        Over-refusing is the failure mode of "make it strict": it breaks
        templates Django compiles and renders. Measured on the two-build
        differential too — `djust REFUSES & Django RENDERS` is 38,105 before
        and 38,105 after.
        """
        offenders = []
        for name in ALL_SEVEN:
            if name in NONDET:
                continue
            source = "{{ p|%s }}" % name
            for key, value in CORPUS.items():
                dj = outcome(source, value, "django")
                du = outcome(source, value, "djust")
                if du.startswith("<<") and not dj.startswith("<<"):
                    offenders.append((name, key, dj, du))
        assert not offenders, f"{len(offenders)} cells refuse where Django renders:\n" + "\n".join(
            f"  {n} <{k}>: django={a!r} djust={b}" for n, k, a, b in offenders[:15]
        )

    def test_the_exception_CLASS_matches_django_wherever_both_refuse(self) -> None:
        """Which one it is, is observable — the whole reason for the enum.

        `first` over a dict is a `KeyError` and over an `int` a `TypeError`;
        a chokepoint that only knew "it refuses" would be wrong about both
        halves of the same filter.
        """
        mismatches = []
        for name in ALL_SEVEN:
            if name in NONDET:
                continue
            source = "{{ p|%s }}" % name
            for key, value in CORPUS.items():
                dj = outcome(source, value, "django")
                du = outcome(source, value, "djust")
                if dj.startswith("<<") and du.startswith("<<") and dj != du:
                    mismatches.append((name, key, dj, du))
        # The serialized-model map is the ONE documented mismatch, and it is a
        # deliberate narrowing rather than an oversight: the corpus hands both
        # engines a `dict`, so Django answers `KeyError`, while djust reads the
        # `__str__` marker (`python_len`'s own predicate, #2294) and answers
        # what a real MODEL does — `TypeError: not subscriptable`. Both refuse.
        assert [(n, k) for n, k, _a, _b in mismatches] == [
            ("first", "d-model"),
            ("last", "d-model"),
        ], mismatches

    def test_the_sweep_is_not_vacuous(self) -> None:
        """Gate-off for the three above: Django must actually refuse somewhere.

        Without this, a corpus of only iterables would make all three pass by
        construction. Measured over the six DETERMINISTIC filters: 135 of the
        288 cells refuse on Django (`phone2numeric` 37, `last` 25, `first` 22,
        and 17 each for the three iterators).

        Was 101 of 246 before #2469 widened the corpus by seven values — a
        falsy inhabitant of five `Value` variants plus a `timedelta` in both
        answers. Every one of the seven is non-iterable and non-subscriptable
        except the empty container pair, so the counts move by more than the
        cell count does.

        Was 129 of 276 before #2477 added the `set` pair. A `set` IS iterable,
        so the three iterators are unmoved; it is not subscriptable and has no
        `.lower()`, so `first` / `last` / `phone2numeric` each gain both rows.

        Was 141 of 288 before #2482 added the three factory rows — two dict
        views and a falsy iterable. All three are iterable, so the three
        iterators are unmoved again; none is subscriptable and none has a
        `.lower()`, so `first` / `last` / `phone2numeric` each gain all three.

        The three named-tuple rows are iterable and subscriptable, but have
        no `.lower()`, so only `phone2numeric` gains three refusals.

        `random` is excluded on purpose rather than rounded off: over a mapping
        `random.choice` draws an index and THEN looks it up, so whether Django
        raises at all depends on the draw — its own count flaps between 17 and
        19 run to run, which is exactly the outlier-sensitive assertion the
        flaky-test canon forbids.
        """
        per_filter = {
            name: sum(
                1
                for value in CORPUS.values()
                if outcome("{{ p|%s }}" % name, value, "django").startswith("<<")
            )
            for name in ALL_SEVEN
            if name not in NONDET
        }
        assert per_filter == {
            "escapeseq": 17,
            "safeseq": 17,
            "unordered_list": 17,
            "first": 27,
            "last": 30,
            "phone2numeric": 45,
        }, per_filter
        assert sum(per_filter.values()) == 153


class TestTheDictHalfIsAKeyLookupAndNotAPositionalOne:
    """Premise 4, pinned per case rather than as a class.

    `d[0]` is not "the first value"; it is the value at KEY zero. Three of the
    corpus's dicts carry one and three do not, so this is the case a
    "mappings refuse" rule would get wrong in both directions.
    """

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ({0: "at-zero", "a": 1}, "at-zero"),
            # Python conflates numeric keys: `hash(True) == hash(1)`, and
            # `{False: 'f'}[0]` finds it (#2339 already models this).
            ({False: "at-false"}, "at-false"),
            ({0.0: "at-float-zero"}, "at-float-zero"),
        ],
    )
    def test_first_reads_key_zero(self, value: dict, expected: str) -> None:
        assert _rust.render_template("{{ p|first }}", normalize_django_value({"p": value})) == (
            expected
        )
        assert DjangoTemplate("{{ p|first }}").render(DjangoContext({"p": value})) == expected

    def test_first_refuses_a_dict_without_key_zero(self) -> None:
        value = {"a": 1, "b": 2}
        with pytest.raises(KeyError):
            DjangoTemplate("{{ p|first }}").render(DjangoContext({"p": value}))
        with pytest.raises(Exception, match="KeyError"):
            _rust.render_template("{{ p|first }}", normalize_django_value({"p": value}))

    def test_last_refuses_every_dict_the_corpus_has(self) -> None:
        """`d[-1]`, and no corpus dict carries a `-1` key — which is why `last`
        raises on all eight where `first` raises on four.

        Eight since #2469 added `d-empty`, the falsy `Value::Object`. An EMPTY
        dict has no `-1` key either, so it belongs to the same answer rather
        than being a new case; the count is asserted so a dict added without
        thinking about this test is caught here.
        """
        dicts = [k for k, v in CORPUS.items() if isinstance(v, dict)]
        assert len(dicts) == 8, dicts
        for key in dicts:
            with pytest.raises(Exception, match="KeyError|not subscriptable"):
                _rust.render_template("{{ p|last }}", normalize_django_value({"p": CORPUS[key]}))

    def test_last_reads_key_minus_one_when_there_is_one(self) -> None:
        """Non-vacuity for the case above: `last` is a LOOKUP, so a dict that
        has the key answers rather than refusing."""
        value = {-1: "at-minus-one", "a": 1}
        assert _rust.render_template("{{ p|last }}", normalize_django_value({"p": value})) == (
            "at-minus-one"
        )
        assert DjangoTemplate("{{ p|last }}").render(DjangoContext({"p": value})) == (
            "at-minus-one"
        )


class TestTheIndexErrorArmIsTheOneThingDjangoCatches:
    """`except IndexError: return ""` — and it returns a `str`, not nothing.

    The first version of this fix answered `Value::Missing` for the empty case,
    which renders `""` too and is a DIFFERENT value downstream:
    `{{ p|dictsort:"k"|first|pprint }}` over a non-mapping is `''` on Django
    (dictsort fails soft to `""`, `first("")` is an IndexError, `pprint("")` is
    `"''"`) and was `None`. Nine cells; the two-build differential caught it.
    """

    @pytest.mark.parametrize("name", ["first", "last", "random"])
    @pytest.mark.parametrize("value", [[], (), ""])
    def test_an_empty_sequence_answers_the_empty_STRING(self, name: str, value) -> None:
        source = "{{ p|%s|pprint }}" % name
        assert _rust.render_template(source, normalize_django_value({"p": value})) == (
            DjangoTemplate(source).render(DjangoContext({"p": value}))
        )

    def test_the_regressing_chain_itself(self) -> None:
        """The exact cell the differential reported, as a case."""
        for source in (
            '{{ p|dictsort:"k"|first|pprint }}',
            '{{ p|dictsortreversed:"k"|first|pprint }}',
            "{{ p|striptags|first|pprint }}",
        ):
            for value in (42, ["a"], "<b>x</b>"):
                assert _rust.render_template(source, normalize_django_value({"p": value})) == (
                    DjangoTemplate(source).render(DjangoContext({"p": value}))
                ), (source, value)


class TestRandomOverAMapping:
    """`random.choice(d)` is `d[_randbelow(len(d))]`, so it is nondeterministic
    in Python too — reproduced rather than smoothed over.

    Smoothing it would be a second, quieter divergence. The two DETERMINISTIC
    ends are what can be pinned: a mapping carrying every index `0..len-1`
    never raises, and one carrying none of them always does.
    """

    def test_a_mapping_with_every_index_as_a_key_never_raises(self) -> None:
        value = {0: "a", 1: "b", 2: "c"}
        for _ in range(20):
            out = _rust.render_template("{{ p|random }}", normalize_django_value({"p": value}))
            assert out in {"a", "b", "c"}, out

    def test_a_mapping_with_no_integer_key_always_raises(self) -> None:
        value = {"x": 1, "y": 2, "z": 3}
        for _ in range(20):
            with pytest.raises(Exception, match="KeyError"):
                _rust.render_template("{{ p|random }}", normalize_django_value({"p": value}))

    def test_a_view_is_not_subscriptable_so_it_refuses_outright(self) -> None:
        """`random.choice(d.keys())` is a `TypeError` — a view has a `len()`
        and no `__getitem__` — which is the split #2340 recorded."""
        with pytest.raises(Exception, match="not subscriptable"):
            _rust.render_template(
                "{{ p.keys|random }}", normalize_django_value({"p": {"a": 1, "b": 2}})
            )


class TestPhone2numericCallsAStringMethod:
    """`phone.lower()` — and Django never coerced the value first.

    It is `@keep_lazy_text`, not `@stringfilter`, which is the fact that makes
    this an `AttributeError` rather than a no-op: 30 of the 41 corpus values
    are not strings, the largest single row in the table.
    """

    @pytest.mark.parametrize("value", [42, None, 1.5, True, [1], {"a": 1}, ("a",)])
    def test_a_non_string_refuses(self, value) -> None:
        with pytest.raises(AttributeError):
            DjangoTemplate("{{ p|phone2numeric }}").render(DjangoContext({"p": value}))
        with pytest.raises(Exception, match="AttributeError.*'lower'"):
            _rust.render_template("{{ p|phone2numeric }}", normalize_django_value({"p": value}))

    def test_the_none_case_by_name(self) -> None:
        """`str(None).lower()` is `"none"`, whose keypad spelling is `6663` —
        which is what djust rendered, on a page, for a missing value."""
        with pytest.raises(Exception, match="AttributeError"):
            _rust.render_template("{{ p|phone2numeric }}", normalize_django_value({"p": None}))

    @pytest.mark.parametrize("value", ["CALL-NOW", "abc", "", "Ärger"])
    def test_a_string_still_answers(self, value: str) -> None:
        source = "{{ p|phone2numeric }}"
        assert _rust.render_template(source, normalize_django_value({"p": value})) == (
            DjangoTemplate(source).render(DjangoContext({"p": value}))
        )

    def test_an_absent_key_is_string_if_invalid_and_renders_nothing(self) -> None:
        """`Value::Missing` is Django's `string_if_invalid` — a `str` — so it
        must NOT refuse. Naming it anything else would make an absent key 500
        the page, which is the sharpest way this fix could have been wrong."""
        assert _rust.render_template("{{ absent|phone2numeric }}", {}) == ""
        assert _rust.render_template("{{ absent|first }}", {}) == ""
        assert _rust.render_template("{{ absent|escapeseq }}", {}) == "[]"


class TestOneChokepointAnswersWhichExceptionPythonRaises:
    """The shape #2451 asks for, pinned as a SET and canaried BOTH ways.

    Seven independent ``Err(...)``s is what the fix retires. Every arm reaches
    the message through ``value_op_error``, and every operation reaches the
    type name through ``python_type_name`` — including the ``{% for %}``
    refusal in ``renderer.rs``, which carried its own four-arm copy.
    """

    @staticmethod
    def callers(source: str, name: str) -> set[str]:
        """Which top-level ``fn``s call ``name``, by brace-free scan.

        Rust is not parsed here; the file is scanned for ``fn <x>(`` markers
        and each region attributed to the preceding one. Coarse, and enough:
        what is pinned is the SET of names, and a caller moving between
        functions changes it.
        """
        marks = [(m.start(), m.end(), m.group(1)) for m in re.finditer(r"\bfn (\w+)[(<]", source)]
        found = set()
        for i, (_pos, after_marker, fn) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(source)
            # From AFTER the `fn <name>(` marker, so a function's own
            # definition is never read as a call to itself — which made the
            # first version report `value_op_error` as its own caller.
            if re.search(rf"\b{name}\(", source[after_marker:end]):
                found.add(fn)
        return found

    def test_every_message_comes_from_the_one_constructor(self) -> None:
        source = production(FILTERS_RS)
        assert self.callers(source, "value_op_error") == {
            "apply_builtin_filter",
        }, self.callers(source, "value_op_error")

    def test_the_three_probes_are_the_only_readers_of_the_operations(self) -> None:
        """The caller set of each probe, so a NEW filter that iterates or
        subscripts has to join one of them rather than growing an eighth
        bespoke arm."""
        source = production(FILTERS_RS)
        for probe in ("python_iter", "python_getitem", "python_lower"):
            assert self.callers(source, probe) == {"apply_builtin_filter"}, probe

    def test_the_type_name_has_exactly_two_readers_across_both_modules(self) -> None:
        """The unification, as a set (#1125): ``renderer.rs``'s ``{% for %}``
        refusal reads the SAME answer the filters do since #2451."""
        filters_src = production(FILTERS_RS)
        renderer_src = production(RENDERER_RS)
        assert self.callers(filters_src, "python_type_name") == {"detail"}
        assert self.callers(renderer_src, "python_type_name") == {"python_type_name_for_iteration"}
        assert "fn python_type_name(" not in renderer_src, (
            "renderer.rs defines its own type-name function again — that is the "
            "four-arm copy #2451 retired (#1646)"
        )

    def test_the_constructor_is_reached_from_every_arm_that_can_refuse(self) -> None:
        """The COUNT, so an arm that grows a bespoke `Err` is visible.

        Seven filters and eight call sites: `first`, `last`, the three
        iterators and `phone2numeric` have one each, and `random` has two —
        one for the subscript and one for the missing `len()`.
        """
        source = production(FILTERS_RS)
        arm = source.split("fn apply_builtin_filter", 1)[1].split("\n}\n", 1)[0]
        assert arm.count("value_op_error(") == 8, arm.count("value_op_error(")

    def test_the_pin_goes_red_when_an_arm_stops_using_the_constructor(self) -> None:
        """Canary, direction one: a bespoke ``Err`` in every arm."""
        source = production(FILTERS_RS)
        mutated = re.sub(
            r"value_op_error\([^;]*?\)\)",
            'DjangoRustError::TemplateError("nope".into()))',
            source,
        )
        assert mutated != source
        assert self.callers(mutated, "value_op_error") == set(), self.callers(
            mutated, "value_op_error"
        )

    def test_the_pin_goes_red_when_a_new_reader_appears(self) -> None:
        """Canary, direction two — the one a floor (`>= 1`) cannot do."""
        source = production(FILTERS_RS)
        mutated = source + "\nfn eighth_bespoke_arm(v: &Value) -> bool { python_iter(v).is_ok() }\n"
        assert self.callers(mutated, "python_iter") == {
            "apply_builtin_filter",
            "eighth_bespoke_arm",
        }


class TestTheUnificationChangedNoForMessage:
    """`{% for %}`'s refusal arm (#2382) now reads the shared answer.

    The copy it carried had four arms plus an `object` catch-all; the shared
    one names every `Value`. Only the four are REACHABLE from that arm —
    `String`, `Object`, `DictView`, `List` and `Tuple` are normalised or
    iterated above it, and `Missing`/`None` take Django's empty branch — so
    every message it can emit is byte-identical to before. Run rather than
    reasoned: this is the test the renderer's doc comment cites.
    """

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (42, "'int' object is not iterable"),
            (12345678901234567890, "'int' object is not iterable"),
            (1.5, "'float' object is not iterable"),
            (True, "'bool' object is not iterable"),
            (False, "'bool' object is not iterable"),
        ],
    )
    def test_the_for_refusal_messages_are_unchanged_by_the_unification(
        self, value, expected: str
    ) -> None:
        with pytest.raises(Exception, match=re.escape(expected)):
            _rust.render_template(
                "{% for x in p %}{{ x }}{% endfor %}", normalize_django_value({"p": value})
            )

    def test_the_shapes_that_do_NOT_reach_that_arm_still_do_not(self) -> None:
        """The reason the wider answer is invisible there: a string, a list, a
        tuple, a dict and a view all iterate, and `None` takes the empty
        branch. If one of these started refusing, the unification WOULD have
        changed a message."""
        for value in ("ab", ["a"], ("a",), {"a": 1}, None):
            _rust.render_template(
                "{% for x in p %}{{ x }}{% endfor %}", normalize_django_value({"p": value})
            )


class TestTheResidueThisDoesNotTouch:
    """What is left in the bucket after this change, each pinned to its own class.

    12 widthratio cells and 17 `{{ }}` cells survived #2451, and not one of
    them is a sequence filter reading its own value. Recorded rather than left
    as a hopeful silence, because a residue nobody wrote down is
    indistinguishable from a fix that did not work.

    **Two of the four are now settled**, each by the issue this class filed
    for it, and each method below says which:

    * `get_digit`'s return TYPE is **fixed** (#2459) — the surviving cells
      were the chokepoint being handed a `str`, not the chokepoint being
      wrong;
    * the `Decimal` specials are a **decided divergence** (#2460) — djust
      stays permissive where Django's `numberformat.format` crashes.

    **Three of the four are now settled.** The `datetime` extraction boundary
    closed too, in two halves: #2467 stopped the LiveView path flattening the
    value in Python, and #2473 gave `python_int_value` the `Encoded` arm — so
    `get_digit` refuses on BOTH paths, as Django does. Only #2429's
    `json_script` decision still stands.
    """

    def test_get_digit_over_a_datetime_is_CLOSED_on_BOTH_paths(self) -> None:
        """**CLOSED**, by #2467 and #2473 together, and the rewrite history is
        the record — this pin has now been rewritten three times and each
        rewrite corrected a *mechanism*, not a measurement.

        1. #2451 said *"a `datetime` is already a `Value::String` by the time
           any filter sees it (the PyO3 extraction boundary)"*. Wrong layer: on
           the path this file's `outcome()` cells take, the flattening happened
           in **Python**, in `normalize_django_value`, one layer before PyO3.
        2. The rewrite then said the outcome survives on both paths because
           *"`Value::Encoded` carries `str()` and the encoder's JSON, no
           integer — so it answers `ValueError`"*. True of the variant, and not
           of the question: `int(datetime)` is a `TypeError` because of the
           value's TYPE, which `Encoded` does carry. #2473 gave
           `python_int_value` the arm.
        3. A third version called what was left a PATH split — raw refuses,
           normalized renders — which was true for the length of one merge.
           #2467 removed the Python-side flattening, so the normalized path
           now carries a `Value::Encoded` too and there is no split left.

        So both paths refuse, for the same reason, and the assertion below
        walks both rather than naming one. `|first` is kept beside it as the
        control: it refuses on both paths for a DIFFERENT reason (not
        subscriptable), so a regression that broke only `python_int_value`
        would still redden `get_digit` alone.
        """
        import datetime

        value = datetime.datetime(2020, 1, 1, 12, 0, 0)
        with pytest.raises(TypeError):
            DjangoTemplate('{{ p|get_digit:"1" }}').render(DjangoContext({"p": value}))
        for context in (normalize_django_value({"p": value}), {"p": value}):
            with pytest.raises(RuntimeError) as exc:
                _rust.render_template('{{ p|get_digit:"1" }}', context)
            assert "TypeError" in str(exc.value), str(exc.value)
            assert "calls int() on its value" in str(exc.value), str(exc.value)
            # The control: both paths carry the real Python type, which is what
            # #2448 established for the raw path and #2467 for the normalized
            # one. Without this the reason above is prose again.
            with pytest.raises(RuntimeError) as exc:
                _rust.render_template("{{ p|first }}", context)
            assert "datetime.datetime" in str(exc.value), str(exc.value)

    def test_the_survivors_were_get_digits_RETURN_TYPE_and_are_CLOSED(self) -> None:
        """**Inverted by #2459**, as this test's first version said it should be.

        It read: *"the divergence is in the SUBJECT's type, not in the filter
        that consumes it: fix `get_digit`'s return and these close with it"*.
        They did. Django's `get_digit` is `return int(str(value)[-arg])` — an
        `int`, and its docstring says *"output is always an integer"* — and
        djust answered a one-character STRING, which iterates and subscripts
        where an `int` does neither.

        Kept rather than deleted, because the pair below is the whole claim in
        two lines: the chokepoint this file is about was ALWAYS right, and the
        subject it was handed was wrong. The full sweep lives in
        `python/tests/test_get_digit_returns_an_int_2459.py`.
        """
        assert (
            DjangoTemplate('{{ p|get_digit:"1"|pprint }}').render(DjangoContext({"p": 42})) == "2"
        )
        assert (
            _rust.render_template('{{ p|get_digit:"1"|pprint }}', normalize_django_value({"p": 42}))
            == "2"
        )
        # And the consequence: this file's own chokepoint now fires, with no
        # change to it — it was being given a `str`.
        with pytest.raises(TypeError):
            DjangoTemplate('{{ p|get_digit:"1"|escapeseq }}').render(DjangoContext({"p": 42}))
        with pytest.raises(RuntimeError, match="'int' object is not iterable"):
            _rust.render_template(
                '{{ p|get_digit:"1"|escapeseq }}', normalize_django_value({"p": 42})
            )

    @pytest.mark.parametrize("name", ["default", "default_if_none", "join", "slice"])
    def test_the_decimal_special_cells_are_the_bare_RENDER_not_the_filter(self, name: str) -> None:
        """Premise 3, as an executable statement.

        The issue attributes 2 cells each to `default`, `default_if_none`,
        `join`, `slice` and a custom-filter probe. All ten are one thing, and
        no filter is involved: `{{ p }}` over `Decimal("Infinity")` raises
        `TypeError: bad operand type for abs(): 'str'` in Django's
        `numberformat.format`. The filter merely passes the value through to a
        render that was already going to refuse. Filed as #2460, as a DECISION
        issue in the shape of #2429 rather than a fix.

        **Decided in #2460: djust stays permissive**, because Django's
        behaviour here is a crash rather than a considered refusal — it renders
        the FLOAT forms of the same values happily, the line that raises is a
        >200-digit performance guard, and `"{:f}".format(Decimal("Infinity"))`
        (the arm one line below it) computes exactly the text djust emits. The
        argument and its four measured facts live in
        `python/tests/test_decimal_special_render_decision_2460.py`; this stays
        as the premise it always was, and remains true.
        """
        from decimal import Decimal

        for value in (Decimal("Infinity"), Decimal("NaN")):
            with pytest.raises(TypeError, match="abs"):
                DjangoTemplate("{{ p }}").render(DjangoContext({"p": value}))
            args = (
                ':"D"' if name.startswith("default") else (':", "' if name == "join" else ':"1:"')
            )
            with pytest.raises(TypeError, match="abs"):
                DjangoTemplate("{{ p|%s%s }}" % (name, args)).render(DjangoContext({"p": value}))

    def test_json_script_stays_permissive_by_decision(self) -> None:
        """The 2 remaining widthratio cells, decided in #2429 and not reopened."""
        value = {0: "a", (1, "t"): "e"}
        with pytest.raises(TypeError, match="keys must be str"):
            DjangoTemplate('{{ p|json_script:"x" }}').render(DjangoContext({"p": value}))
        assert _rust.render_template(
            '{{ p|json_script:"x" }}', normalize_django_value({"p": value})
        )


class TestTheCorpusDeclaresItReachesThisErrorClass:
    """The differential's manifest, which reported the new message MISSING.

    `_ARG_ERROR_MARK` keeps every literal naming a filter, on the reasoning
    that "every argument error does, and nothing else in these modules does".
    The second half stopped being true when the value-side chokepoints arrived.
    #2435's `int_value_error` hid it — `get_digit` and `divisibleby` take an
    argument, so `arg_cells()` reaches it by coincidence — and #2451's does
    not, because every filter that raises it takes NO argument.
    """

    def test_the_value_op_axis_exists_and_is_reachable(self) -> None:
        import json
        import os
        import subprocess
        import sys

        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO / "python"), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
        )
        proc = subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
            [
                sys.executable,
                str(REPO / "scripts" / "filter-parity-differential.py"),
                "--manifest",
                "--json",
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO),
            check=False,
        )
        assert proc.returncode == 0, proc.stderr[-3000:]
        axes = {row["axis"]: row for row in json.loads(proc.stdout)["axes"]}
        assert "value-op" in axes, sorted(axes)
        assert not axes["value-op"].get("missing"), axes["value-op"]
        assert len(axes["value-op"]["required"]) == 2, axes["value-op"]
        # And it did not simply move the problem: `argument` is still clean.
        assert not axes["argument"].get("missing"), axes["argument"]

    def test_the_split_is_derived_from_the_source_not_a_name_list(self) -> None:
        """The two constructors are named, and their bodies located mechanically
        — so a third value-side constructor is a one-line addition rather than
        a silent misfiling."""
        source = (REPO / "scripts" / "filter-parity-differential.py").read_text(encoding="utf-8")
        assert '_VALUE_OP_ERROR_FNS = ("int_value_error", "value_op_error")' in source
        for name in ("int_value_error", "value_op_error"):
            assert f"fn {name}(" in FILTERS_RS.read_text(encoding="utf-8"), name


def test_every_filter_that_iterates_or_subscripts_is_in_one_of_the_three_lists() -> None:
    """The enumeration this file is written against, CHECKED against Django.

    Grep the SINK, not the callers you expect — and here the sink is Django
    itself, so it is called rather than read. The first version of this test
    grepped filter SOURCE for `for … in value` / `value[` / `.lower()` and got
    it wrong in both directions: it claimed `capfirst`, `lower` and `title`
    (all `@stringfilter`, which coerces before the body runs, so they cannot
    raise) and MISSED `unordered_list` (its `iter()` is on a local name inside
    a nested generator that has its own unrelated `try`). Calling every
    registered one-argument filter on four scalars answers the question
    directly, and a new Django release adding an eighth lands here rather than
    in a differential run six months later.
    """
    import inspect

    from django.core.exceptions import ImproperlyConfigured

    raising: dict[str, set[str]] = {}
    for name, fn in register.filters.items():
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        required = [
            prm
            for prm in sig.parameters.values()
            if prm.default is prm.empty
            and prm.kind in (prm.POSITIONAL_ONLY, prm.POSITIONAL_OR_KEYWORD)
        ]
        if len(required) != 1:
            continue
        for value in (42, None, 1.5, True):
            try:
                fn(value)
            except ImproperlyConfigured:
                # A settings artifact of calling the filter DIRECTLY (`date`,
                # `floatformat`, `filesizeformat`, `time`, `yesno` reach L10N),
                # not a refusal about the value. They render fine in a template.
                continue
            except Exception as exc:  # noqa: BLE001 — the class IS the answer
                raising.setdefault(name, set()).add(type(exc).__name__)

    #: The one OTHER class of value-shape refusal among the built-ins, closed
    #: by #2344 rather than here: `timesince`/`timeuntil` reach
    #: `value.utcoffset()` on a non-date.
    OTHER_CLASS = {"timesince", "timeuntil"}
    assert set(raising) - OTHER_CLASS == set(ALL_SEVEN), sorted(
        set(raising) - OTHER_CLASS - set(ALL_SEVEN)
    )
    # And the class each one raises, which is what the enum has to model.
    assert raising["first"] == raising["last"] == raising["random"] == {"TypeError"}
    assert raising["phone2numeric"] == {"AttributeError"}
    for name in ITERATORS:
        assert raising[name] == {"TypeError"}, (name, raising[name])


# ---------------------------------------------------------------------------
# Two axes the differential's INPUTS corpus cannot reach (#2448 merge)
# ---------------------------------------------------------------------------


class TestTheDatetimeFamilyReachesTheChokepointWithItsRealTypeName:
    """The seven, over the four `datetime` types — reachable only since #2448.

    Before that variant these crossed the PyO3 boundary as
    `Value::String(str(o))`, so every one of the seven treated a `datetime` as
    a STRING: `{{ dt|first }}` answered `'2'` and `{{ dt|unordered_list }}`
    emitted one `<li>` per character. The chokepoint was already correct; it
    was being handed the wrong value.

    **Which path, and why it matters.** djust has TWO ways into the renderer
    and they hand it different things:

    * `render_template(tpl, raw_dict)` — the raw PyO3 conversion, which is what
      `djust/template/backend.py` (a plain Django view rendering through
      `DjustTemplateBackend`) takes. The Python object crosses intact, and this
      is the path `Value::Encoded` exists for.
    * the LiveView path, which runs the context through
      `djust.serialization.normalize_django_value` first — and that converts a
      `datetime` to an ISO **string** in Python, so Rust never sees a datetime
      at all.

    These cases use the RAW dict deliberately. Routing them through
    `outcome()`, which normalizes, silently tested the second path and reported
    the first as broken — the reproduction-fidelity failure (#1650) caught by
    28 red cells on the first run of this class. The normalized path's own
    residue is pinned in `TestTheLiveViewPathNormalizesBeforeRustSeesIt`.

    The corpus this file reads from `scripts/filter-parity-differential.py`
    carries no `datetime` — `INPUTS` is about escaping shapes — so these cells
    are invisible to `TestTheReferenceTableIsRunNotTranscribed` above.
    """

    FAMILY = {
        "datetime": datetime.datetime(2020, 1, 1, 3, 4, 5),
        "date": datetime.date(2020, 1, 1),
        "time": datetime.time(3, 4, 5),
        "timedelta": datetime.timedelta(seconds=90),
    }

    @staticmethod
    def _raw_outcome(source: str, value) -> str:
        """`outcome()`'s djust half, WITHOUT the normalizer — see the docstring."""
        try:
            return _rust.render_template(source, {"p": value})
        except Exception as exc:  # noqa: BLE001 — the refusal IS the answer
            found = re.search(r"raises (\w+Error)", str(exc))
            return f"<<{found.group(1)}>>" if found else f"<<{type(exc).__name__}>>"

    @pytest.mark.parametrize("shape", sorted(FAMILY))
    @pytest.mark.parametrize("name", ALL_SEVEN)
    def test_both_engines_refuse_with_the_same_exception_class(self, name: str, shape: str) -> None:
        value = self.FAMILY[shape]
        source = "{{ p|%s }}" % name
        dj = outcome(source, value, "django")
        du = self._raw_outcome(source, value)
        assert dj.startswith("<<"), f"Django moved for {shape}/{name}: {dj!r}"
        assert du == dj, f"{shape}/{name}: django={dj} djust={du}"

    @pytest.mark.parametrize("shape", sorted(FAMILY))
    def test_the_message_names_the_real_python_type(self, shape: str) -> None:
        """Not merely "both refuse": CPython's own `tp_name`.

        `datetime.datetime`, not `datetime` and not `str` — which is what it
        said before #2448, because the value really was a string by then.
        """
        value = self.FAMILY[shape]
        expected = "datetime.%s" % shape
        with pytest.raises(TypeError) as django_exc:
            DjangoTemplate("{{ p|first }}").render(DjangoContext({"p": value}))
        assert expected in str(django_exc.value), str(django_exc.value)
        with pytest.raises(RuntimeError) as djust_exc:
            _rust.render_template("{{ p|first }}", {"p": value})
        assert expected in str(djust_exc.value), str(djust_exc.value)

    def test_the_string_that_looks_like_one_is_still_a_string(self) -> None:
        """The non-vacuity half, and the reason the fix had to be at the
        conversion rather than in a filter.

        `"2020-01-01 03:04:05"` is a `str` and subscripts fine on both engines.
        Nothing downstream of the boundary can tell it from a `datetime`'s
        display text — which is exactly why the type has to survive the
        crossing rather than be re-derived.
        """
        text = str(self.FAMILY["datetime"])
        source = "{{ p|first }}"
        assert outcome(source, text, "django") == self._raw_outcome(source, text) == "2"


class TestTheLiveViewPathCarriesTheTypeSince2467:
    """The residue this class was written to record, INVERTED (#2467).

    It read: *"`normalize_django_value` converts a `datetime` to an ISO string
    in PYTHON, so on the LiveView path the type is gone before `Value::Encoded`
    can carry it and the seven filters see a `str` — which subscripts and
    iterates. This is NOT closed by #2448 and is not claimed to be."*

    That was exactly right, and writing it down is what made it findable: the
    normalizer now carries the value UNCONVERTED (the `Decimal` split, #2239),
    so `Value::Encoded` reaches this chokepoint on both paths and both refuse.
    Inverted rather than deleted, the way #2462 inverted #2448's pins — a
    residue that leaves no test behind is one nobody would notice reopening.

    The class name changed with the claim, deliberately: a class still called
    `…NormalizesBeforeRustSeesIt` with inverted bodies is worse than either.
    """

    VALUE = datetime.datetime(2020, 1, 1, 3, 4, 5)

    def test_the_normalizer_carries_the_type_into_python(self) -> None:
        carried = normalize_django_value({"p": self.VALUE})["p"]
        assert carried is self.VALUE, type(carried)

    @pytest.mark.parametrize("name", SUBSCRIPTERS + ITERATORS)
    def test_so_the_seven_see_the_OBJECT_and_refuse_as_django_does(self, name: str) -> None:
        source = "{{ p|%s }}" % name
        # Django, holding the real object, refuses...
        assert outcome(source, self.VALUE, "django").startswith("<<")
        # ...and so does djust on the normalized path, which is the change.
        assert outcome(source, self.VALUE, "djust").startswith("<<")

    def test_and_the_raw_path_answers_the_SAME_which_is_what_closed_the_split(
        self,
    ) -> None:
        """Non-vacuity, inverted with the rest: the same value, same template,
        now ONE answer.

        The old version asserted the two paths differ; this asserts they agree,
        and names the type in the message so a future regression that refuses
        for an unrelated reason is not mistaken for this passing.
        """
        source = "{{ p|first }}"
        assert outcome(source, self.VALUE, "djust").startswith("<<")
        with pytest.raises(RuntimeError) as exc:
            _rust.render_template(source, normalize_django_value({"p": self.VALUE}))
        assert "datetime.datetime" in str(exc.value), str(exc.value)
        with pytest.raises(RuntimeError) as raw:
            _rust.render_template(source, {"p": self.VALUE})
        assert "datetime.datetime" in str(raw.value), str(raw.value)


class TestAnAbsentVariableIsStringIfInvalidOnEveryOneOfTheSeven:
    """`{{ nope|first }}` — the shape a refusal-flavoured fix gets wrong.

    Django substitutes `string_if_invalid`, which is `""`: a `str` with length
    0 that subscripts to an `IndexError`. So all seven RENDER, and refusing on
    any of them breaks templates Django serves.

    `TestTheReferenceTableIsRunNotTranscribed` cannot see this: it binds a
    value for `p` on every cell, and it skips `NONDET`. Both exclusions applied
    to the same filter, which is how `{{ nope|random }}` came to refuse with
    `'str' object is not subscriptable` — a message that is false of every
    `str` — while every other cell agreed. The cause was `python_len`
    answering `None` for a `Value::Missing` where `python_type_name` and
    `python_getitem` both modelled `""`; see
    `python_len_agrees_with_the_other_two_probes_about_missing` in
    `crates/djust_templates/src/filters.rs` for the probe-level pin.

    `random` is NOT skipped here — over an absent variable there is nothing to
    draw from, so the answer is deterministic.
    """

    @pytest.mark.parametrize("name", ALL_SEVEN)
    def test_it_renders_on_both_engines(self, name: str) -> None:
        source = "{{ nope|%s }}" % name
        django_out = DjangoTemplate(source).render(DjangoContext({}))
        djust_out = _rust.render_template(source, {})
        assert not django_out.startswith("<<"), django_out
        assert djust_out == django_out, f"{name}: django={django_out!r} djust={djust_out!r}"

    @pytest.mark.parametrize("name", ALL_SEVEN)
    def test_it_answers_exactly_what_the_empty_string_answers(self, name: str) -> None:
        """The claim stated as an equality rather than as a literal.

        A `Missing` IS `""` to Django, so every one of the seven must give the
        same answer for both. Pinning literals instead would let the two drift
        apart while each stayed individually plausible.
        """
        source = "{{ p|%s }}" % name
        absent = _rust.render_template("{{ nope|%s }}" % name, {})
        empty = _rust.render_template(source, normalize_django_value({"p": ""}))
        assert absent == empty, f"{name}: absent={absent!r} empty-string={empty!r}"
