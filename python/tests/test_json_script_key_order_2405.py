"""A serialized mapping keeps INSERTION order, as `json.dumps` does (#2405).

The defect
----------
``{% for x in p %}{{ forloop|json_script:"d" }}{% endfor %}`` over ``[1]``::

    django  {"parentloop": {}, "counter0": 0, "counter": 1, …}
    djust   {"counter": 1, "counter0": 0, "first": true, …}

Same keys, same values, different insertion order — and ``json.dumps``
preserves insertion order, so the serialized BYTES differ. Cosmetic to a
consumer that parses the JSON; not cosmetic to a snapshot test, a checksum, or
a diff in CI.

The issue's cited location was wrong, and so was its fix shape
--------------------------------------------------------------
The report located it in ``Node::For``'s dict construction and called the fix
"one-line reordering". That construction is CORRECT and always was:
``{{ forloop }}``'s own repr already agrees with Django's, key for key, in
order — ``parentloop`` first, then the six counters — and this file pins that
as the premise the real diagnosis rests on.

The order was destroyed one layer down, in ``value_to_json``'s ``Object`` arm,
which ran ``parts.sort()``. So it was never a ``forloop`` defect: EVERY dict
``json_script`` touched came out alphabetized — at the top level, nested, and
inside a list. A ``forloop``-shaped fix would have special-cased one instance
of a general defect.

What was NOT fixed here, and was measured rather than assumed
-------------------------------------------------------------
``json.dumps`` defaults to ``ensure_ascii=True`` while djust emitted raw
UTF-8, and a missing ``element_id`` still emitted ``id="data"``. Two more
byte-divergences in the same filter, by two different mechanisms (string
escaping and attribute presence, not ordering), filed as #2413 rather than
folded in (#1079) and pinned at the bottom of this file as named divergences
rather than left silent. Both are now CLOSED; those pins are parity
assertions.

Every expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.template.defaultfilters import register

from djust import _rust

FILTERS_RS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "crates"
    / "djust_templates"
    / "src"
    / "filters.rs"
)

#: Deliberately NOT in alphabetical order — that is the whole point. A mapping
#: whose keys happen to be sorted cannot tell a sorting encoder from a
#: faithful one, which is how this survived a corpus that sweeps every filter
#: over a dict.
UNSORTED: dict[str, object] = {"k": 1, "j": 2, "a": 3}

#: Nested and list-wrapped, because the arm recurses and a top-level-only fix
#: would leave both.
NESTED: dict[str, object] = {"z": {"y": 1, "x": 2}, "b": [{"n": 1, "m": 2}]}


def both(source: str, ctx: dict) -> tuple[str, str]:
    return (
        DjangoTemplate(source).render(DjangoContext(dict(ctx))),
        _rust.render_template(source, dict(ctx)),
    )


def keys_in_order(script_html: str) -> list[str]:
    """The key sequence of the JSON body, read POSITIONALLY.

    `json.loads` into a dict would preserve order too, but going through the
    raw text is the honest reading: what this pins is the serialized bytes,
    and a parser that happened to sort would hide exactly the defect.
    """
    body = re.search(r">(.*)</script>", script_html, re.S)
    assert body, script_html
    return re.findall(r'"((?:[^"\\]|\\.)*)":', body.group(1))


# ---------------------------------------------------------------------------
# The premise the diagnosis rests on
# ---------------------------------------------------------------------------


class TestTheForNodeDictWasAlreadyRight:
    """`{{ forloop }}`'s repr agrees with Django's, and did before this fix.

    If this class ever goes red, the issue's cited location becomes the right
    one and the fix below is in the wrong place.
    """

    def test_the_repr_agrees_key_for_key_in_order(self) -> None:
        source = "{% for x in p %}{{ forloop }}{% endfor %}"
        dj, du = both(source, {"p": [1]})
        assert dj == du
        # And that order is Django's, not alphabetical.
        assert dj.index("parentloop") < dj.index("counter0") < dj.index("counter&")

    def test_a_nested_loop_agrees_too(self) -> None:
        source = "{% for x in p %}{% for y in p %}{{ forloop }}{% endfor %}{% endfor %}"
        dj, du = both(source, {"p": [1, 2]})
        assert dj == du


# ---------------------------------------------------------------------------
# The cell the issue names, and the class it belongs to
# ---------------------------------------------------------------------------


class TestASerializedMappingKeepsInsertionOrder:
    def test_the_forloop_cell_from_the_issue(self) -> None:
        source = '{% for x in p %}{{ forloop|json_script:"d" }}{% endfor %}'
        dj, du = both(source, {"p": [1]})
        assert du == dj

    def test_the_forloop_key_SEQUENCE_is_djangos(self) -> None:
        """The pin the issue asks for: the key order, compared to Django's
        own, so it cannot drift back."""
        source = '{% for x in p %}{{ forloop|json_script:"d" }}{% endfor %}'
        dj, du = both(source, {"p": [1, 2, 3]})
        assert keys_in_order(du) == keys_in_order(dj)
        # Non-vacuous: the sequence is not the sorted one.
        assert keys_in_order(dj) != sorted(keys_in_order(dj))

    @pytest.mark.parametrize("value", [UNSORTED, NESTED, [UNSORTED], {"o": [UNSORTED]}])
    def test_every_mapping_position_keeps_its_order(self, value: object) -> None:
        """Top level, nested, inside a list, and inside a list inside a dict —
        the arm recurses, so a top-level-only fix would leave three of these."""
        dj, du = both('{{ p|json_script:"d" }}', {"p": value})
        assert du == dj

    def test_the_sort_is_gone_from_the_encoder_itself(self) -> None:
        """A structural pin over the Rust source, so a future edit that
        reintroduces a sort in this arm is caught even if no cell reaches it.

        Scoped to `value_to_json`'s body rather than the file: `filters.rs`
        sorts elsewhere legitimately (`dictsort`, `pprint`), and a file-wide
        grep would either be vacuous or forbid those.

        COMMENTS ARE STRIPPED FIRST, and that is load-bearing. The first
        version of this test failed against the fixed engine, because the fix's
        own comment says the words ``parts.sort()`` while explaining what it
        removed — a source search finding its own needle. The strip is
        asserted to have removed something, so a change in comment syntax
        cannot silently turn this into a search over unstripped text.
        """
        source = FILTERS_RS.read_text()
        start = source.index("fn value_to_json(")
        end = source.index("\n}\n", start)
        body = source[start:end]
        code = re.sub(r"//[^\n]*", "", body)
        assert len(code) < len(body), "no comment was stripped — the strip stopped working"
        assert "parts" in code, "the body no longer builds `parts` — this pin has moved"
        assert "sort" not in code, "value_to_json sorts its object keys again — json.dumps does not"


# ---------------------------------------------------------------------------
# Every order-observable filter, enumerated from Django rather than chosen
# ---------------------------------------------------------------------------


def order_observable_filters() -> list[str]:
    """Django's filters whose output over a mapping REVEALS its key order.

    Derived by RUNNING each filter in Django's live registry over two mappings
    that differ only in insertion order and keeping the ones whose outputs
    differ. A hand-picked list would be the transcription this repo keeps
    finding one short (#2218, #2223); measuring asks the question directly.
    """
    a = {"k": 1, "j": 2}
    b = {"j": 2, "k": 1}
    out = []
    for name in sorted(register.filters):
        for spelling in (f"{{{{ p|{name} }}}}", f'{{{{ p|{name}:"5" }}}}'):
            try:
                ra = DjangoTemplate(spelling).render(DjangoContext({"p": dict(a)}))
                rb = DjangoTemplate(spelling).render(DjangoContext({"p": dict(b)}))
            except Exception:  # noqa: BLE001 — a filter that refuses this shape
                continue
            if ra != rb:
                out.append(spelling)
                break
    return out


class TestEveryOrderObservableFilterAgrees:
    """Enumerate every variant of the surface, not the one that was reported.

    #2405 arrived as a `forloop` bug and was a `value_to_json` bug; the same
    reasoning says the fix must be checked against every filter through which
    a mapping's order can be SEEN, not just the one the report used.
    """

    def test_the_enumeration_is_not_empty_and_contains_json_script(self) -> None:
        spellings = order_observable_filters()
        assert spellings, "the derivation found no order-observable filter — it is broken"
        assert any("json_script" in s for s in spellings), spellings

    def test_each_one_agrees_with_django(self) -> None:
        """NO mismatch is permitted.

        Stated as an exact set rather than an exclusion, so it goes red both
        ways: if a filter starts diverging, AND if a permitted divergence is
        fixed without this being updated. It did the second — the set was
        `{"{{ p|json_script }}"}` while the argument-less spelling still
        emitted `id="data"`, and #2413 emptied it.
        """
        mismatched = {
            spelling
            for spelling in order_observable_filters()
            if both(spelling, {"p": dict(UNSORTED)})[0] != both(spelling, {"p": dict(UNSORTED)})[1]
        }
        assert mismatched == set(), mismatched


# ---------------------------------------------------------------------------
# The corpus gap
# ---------------------------------------------------------------------------

DIFFERENTIAL = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "filter-parity-differential.py"
)


class TestTheCorpusGapThatHidThisFromTheForloopCells:
    """#2402 added seventeen `forloop` shapes and none could see this.

    Every one of them renders a MEMBER (`{{ forloop.counter }}`) or the REPR
    (`{{ forloop }}`), and both engines already agreed on the repr's order —
    so a reordering visible only inside `json.dumps` passed all seventeen. The
    corpus's own `@arg json_script:"5"` cells over `d-plain` and `d-model` DID
    carry it, which is the honest correction to the issue's "no gate covers
    it": the cells existed and nothing attributed them.
    """

    @staticmethod
    def _module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("_fpd_2405", DIFFERENTIAL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_forloop_shape_now_goes_through_a_serializer(self) -> None:
        module = self._module()
        serialized = [
            shape
            for shape, source in module.PATH_SHAPES.items()
            if "forloop" in source and "json_script" in source
        ]
        assert serialized, "no forloop cell reaches a serializer — the gap is back"

    def test_that_shape_can_TELL_a_sorting_encoder_from_a_faithful_one(self) -> None:
        """The empirical canary: a cell whose Django output happens to be
        key-sorted could not have caught this however many there were."""
        module = self._module()
        shape = next(
            source
            for source in module.PATH_SHAPES.values()
            if "forloop" in source and "json_script" in source
        )
        rendered = DjangoTemplate(shape).render(DjangoContext({"p": [1]}))
        keys = keys_in_order(rendered)
        assert keys, rendered
        assert keys != sorted(keys), keys

    def test_nothing_ELSE_masks_that_shape(self) -> None:
        """The other half, and the one a survivor found missing.

        A cell whose Django key order is unsorted can still be useless: if the
        two engines diverge on that cell for an unrelated reason, the ordering
        sits underneath and the cell attributes nothing. Which is exactly what
        an argument-less `json_script` does — it diverges on the `id`
        attribute on both builds. So the cell must AGREE today; gate the fix
        off and it stops agreeing for the ordering reason, gate the explicit
        `id` off and it stops agreeing for the masking one.
        """
        module = self._module()
        shape = next(
            source
            for source in module.PATH_SHAPES.values()
            if "forloop" in source and "json_script" in source
        )
        dj, du = both(shape, {"p": [1]})
        assert dj == du, (dj, du)

    def test_the_repr_shape_could_NOT_have_caught_it(self) -> None:
        """Why seventeen cells were not enough — stated as a measurement."""
        module = self._module()
        repr_shape = module.PATH_SHAPES["forloop-whole"]
        dj, du = both(repr_shape, {"p": [1]})
        assert dj == du, "the repr cell diverges — this explanation is wrong"


# ---------------------------------------------------------------------------
# What this did NOT close, and #2413 did
# ---------------------------------------------------------------------------


class TestKnownDivergencesOnTheSamePath:
    """Two divergences this fix deliberately left, now CLOSED by #2413.

    They were written as "these still diverge" assertions precisely so that
    fixing them would go red rather than pass silently — the signal to rewrite
    them as parity assertions, which is what they are below. The full
    treatment (astral surrogate pairs, non-ASCII KEYS, every falsy
    `element_id` shape, a randomized differential) is in
    `test_json_script_ensure_ascii_and_element_id_2413.py`; what stays here is
    the pair of cells this file's own reasoning referred to, so the narrative
    above keeps a runnable ending.
    """

    def test_ensure_ascii_IS_matched(self) -> None:
        """`json.dumps` defaults to `ensure_ascii=True`, and now so does djust.

        Was `test_ensure_ascii_is_NOT_matched`, asserting `"héllo" in du` and
        `dj != du` (#2413).
        """
        dj, du = both('{{ p|json_script:"d" }}', {"p": {"k": "héllo"}})
        assert "\\u00e9" in dj
        assert dj == du
        assert "héllo" not in du, du

    def test_a_missing_element_id_emits_no_id_attribute(self) -> None:
        """`{{ p|json_script }}` — Django omits the `id` attribute entirely
        for a falsy `element_id`, and djust no longer invents `id="data"`.

        Was `test_a_missing_element_id_still_emits_one` (#2413). This is why
        the differential could not see the ordering defect on its own
        `json_script` cells: every one of them already diverged on the `id`,
        and the key order sat underneath — a masking that is now gone, which
        is what lets the corpus's argument-less cells carry the body.
        """
        dj, du = both("{{ p|json_script }}", {"p": dict(UNSORTED)})
        assert "id=" not in dj, dj
        assert dj == du

    def test_the_order_defect_was_visible_under_the_id_one(self) -> None:
        """With an id supplied, the pre-fix divergence was pure ordering — the
        shape the `@arg json_script:"5"` cells carried all along."""
        dj, du = both('{{ p|json_script:"5" }}', {"p": dict(UNSORTED)})
        assert dj == du
        assert json.loads(re.search(r">(.*)</script>", dj, re.S).group(1)) == UNSORTED
