"""Pass B judges a part after a given text once per run (#4145).

``_Module._misplaced`` asks, for every part of a string a sink is handed, whether an HTML-only
escaper's output lands where that is not enough (#3864). It walks the values a name can hold and the
parts of each string built in place, and ``clean`` asks it again for every sub-chain it judges, so
the same part after the same literal text was judged once per path that reaches it: exponential in
the number of branches that rebuild a string. The differential fuzz of
``test_safe_markup_guard_3820.py`` went from 7 s to 111 s on seed 1.

The answer for a part depends on the part, its scope, the depth, the call bindings in force and the
text in front of it, nothing else, so it is remembered under exactly that key for the run. These
tests count judgements; they assert no durations (the repo forbids wall-clock gates).
"""

from __future__ import annotations

from scripts.lib import safe_markup_taint as taint
from scripts.lib.safe_markup_taint import find_unsafe_markup

HEADER = "from django.utils.html import escape\nfrom django.utils.safestring import mark_safe\n"


def rebuilt(branches: int, sink: str) -> str:
    """``a`` starts as an escaper's output and each branch may rebuild it two ways: 4**branches
    paths reach the sink, over a handful of distinct parts."""
    lines = ["def f(x, p):", "    a = escape(x)"]
    for _ in range(branches):
        lines += ["    if p:", "        a = a + 's'", "    if x:", "        a = f'<i>{a}</i>'"]
    lines.append(f"    return mark_safe({sink})")
    return HEADER + "\n".join(lines) + "\n"


def judged(source: str) -> tuple[taint._Module, list[taint.Finding], int]:
    """Pass B over ``source``: the analyzer, its findings, and how many times a sink's part was
    asked about from outside ``_misplaced`` (each of those walks the part's whole tree)."""
    mod = taint._Module(source, call_binding=True)
    asked = 0
    ask = mod._html_only_misplaced

    def counting(*args):
        nonlocal asked
        asked += 1
        return ask(*args)

    mod._html_only_misplaced = counting  # type: ignore[method-assign]
    return mod, mod.findings(), asked


class TestEachPartIsJudgedOncePerText:
    def test_a_part_is_judged_once_however_many_paths_reach_it(self):
        # element text: never misplaced, so every question walks to the end (no early ``True``)
        mod, found, asked = judged(rebuilt(5, "f'<p>{a}</p>'"))
        assert found == []
        assert mod.misplaced_computed == len(mod._memo_misplaced)  # each key judged once
        # 1706 questions over 341 distinct (part, text) pairs; unremembered it judged 9913 parts
        assert asked > 4 * mod.misplaced_computed, (asked, mod.misplaced_computed)

    def test_the_judgements_grow_with_the_parts_not_the_paths(self):
        counts = [judged(rebuilt(n, "f'<p>{a}</p>'"))[0].misplaced_computed for n in (3, 4, 5)]
        # the paths quadruple with each branch; the distinct parts grow by a near-constant step
        assert counts[2] - counts[1] < 2 * (counts[1] - counts[0]), counts

    def test_asking_again_judges_nothing(self):
        mod, _found, _asked = judged(rebuilt(4, "f'<p>{a}</p>'"))
        before = mod.misplaced_computed
        sink = next(c for scope in mod.scopes for c in scope.sinks)
        field = sink.args[0].values[1]
        assert isinstance(field, taint.ast.FormattedValue)
        scope = next(s for s in mod.scopes if s.name == "f")
        assert mod._html_only_misplaced(field.value, scope, "<p>") is False
        assert mod.misplaced_computed == before


class TestTheVerdictIsUnchanged:
    def test_a_misplaced_output_is_still_found_through_every_path(self):
        found = find_unsafe_markup(rebuilt(5, "f'<a href=\"{a}\">'"))
        assert [(f.function, f.sink) for f in found] == [("f", "mark_safe")]

    def test_the_same_text_in_front_is_not_the_same_question_under_another_binding(self):
        """``h``'s part after ``<a href="`` is an escaper's output under one call and a constant
        under the other: the call bindings are part of the key, in either order of judging."""
        helper = HEADER + "def h(u):\n    return f'<a href=\"{u}\">'\n"
        escaped = "def f(x):\n    return mark_safe(h(escape(x)))\n"
        constant = "def g(x):\n    return mark_safe(h('/home'))\n"
        for source in (helper + escaped + constant, helper + constant + escaped):
            assert [f.function for f in find_unsafe_markup(source)] == ["f"], source

    def test_the_same_part_after_another_text_is_another_question(self):
        """One string, ``w`` (and so the same ``escape(x)`` node inside it), written twice: as element
        text (enough) and at the start of a URL (not), in either order. The text in front is part
        of the key."""
        body = "def f(x):\n    w = f'{escape(x)}'\n    return mark_safe({})\n"
        for sink in ("f'<p>{w}</p><a href=\"{w}\">'", "f'<a href=\"{w}\"><p>{w}</p>'"):
            source = HEADER + body.replace("{}", sink, 1)
            assert [f.function for f in find_unsafe_markup(source)] == ["f"], sink
        text_only = HEADER + body.replace("{}", "f'<p>{w}</p><b>{w}</b>'", 1)
        assert find_unsafe_markup(text_only) == []

    def test_an_unknown_text_in_front_places_nothing(self):
        """``None`` in front (a builder whose text cannot be read) answers ``False`` before any part
        is walked, as walking it did: every part after it has no text either."""
        mod = taint._Module(rebuilt(2, "f'<p>{a}</p>'"), call_binding=True)
        scope = next(s for s in mod.scopes if s.name == "f")
        sink = next(c for c in scope.sinks)
        assert mod._html_only_misplaced(sink.args[0], scope, None) is False
        assert mod.misplaced_computed == 0
