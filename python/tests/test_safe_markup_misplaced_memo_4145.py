"""Pass B reads the text of a part once per run (#4145, #3864).

Pass B asks, for every string a sink is handed, where each part lands (``_Module._misplaces``): it
reads the text in front of the part through the values a name can hold and the parts of each string
built in place, and ``clean`` asks again for every sub-chain it judges. Read once per path, the same
part was read exponentially often in the number of branches that rebuild a string: the
differential fuzz of ``test_safe_markup_guard_3820.py`` went from 7 s to 111 s on seed 1 (#4145,
then for the HTML-context check that the position walk replaced, #3864).

The text of a part depends on the part, its scope and the call bindings in force, nothing else, so
it is remembered under exactly that key for the run. These tests count readings; they assert no
durations (the repo forbids wall-clock gates).
"""

from __future__ import annotations

from collections import Counter

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


def judged(source: str) -> tuple[taint._Module, list[taint.Finding], Counter]:
    """Pass B over ``source``: the analyzer, its findings, and how often each part was READ (not
    remembered) under each binding."""
    mod = taint._Module(source, call_binding=True)
    reads: Counter = Counter()
    read = mod._read_text

    def counting(e, sc, d):
        reads[(id(e), id(sc), mod._binding_state())] += 1
        return read(e, sc, d)

    mod._read_text = counting  # type: ignore[method-assign]
    return mod, mod.findings(), reads


class TestEachPartIsReadOnce:
    def test_a_part_is_read_once_however_many_paths_reach_it(self):
        mod, found, reads = judged(rebuilt(5, "f'<p>{a}</p>'"))
        assert found == []
        tree_reads = {k: n for k, n in reads.items() if k[0] in mod._tree_ids}
        assert tree_reads and max(tree_reads.values()) == 1, max(tree_reads.values())

    def test_the_readings_grow_with_the_parts_not_the_paths(self):
        counts = [sum(judged(rebuilt(n, "f'<p>{a}</p>'"))[2].values()) for n in (3, 4, 5)]
        # the paths quadruple with each branch; the distinct parts grow by a near-constant step
        assert counts[2] - counts[1] < 2 * (counts[1] - counts[0]), counts

    def test_reading_again_reads_nothing(self):
        mod, _found, reads = judged(rebuilt(4, "f'<p>{a}</p>'"))
        before = sum(reads.values())
        sink = next(c for scope in mod.scopes for c in scope.sinks)
        scope = next(s for s in mod.scopes if s.name == "f")
        mod._text_b(sink.args[0], scope)
        assert sum(reads.values()) == before


def helpers(n: int) -> str:
    """``n`` helpers, each passing its argument (bound, so read with this call's argument) to the
    next two: 2**n paths reach the last."""
    lines = []
    for i in range(n):
        if i == n - 1:
            lines.append(f"def h{i}(v):\n    return f'<b>{{escape(v)}}</b>'")
        else:
            lines.append(f"def h{i}(v):\n    return h{i + 1}(v) + h{min(i + 2, n - 1)}(v)")
    return HEADER + "\n".join(lines) + "\ndef f(x):\n    return mark_safe('<p>' + h0(x) + '</p>')\n"


class TestACallIsReadOncePerSite:
    def test_a_web_of_helpers_is_read_once_per_call_site(self):
        """A call read inside a bound call is read with no binding in force, so what a helper
        returns is read once per call site, not once per path through the helpers (#4145)."""
        counts = [sum(judged(helpers(n))[2].values()) for n in (8, 16)]
        # twice the helpers, twice the call sites: the readings at most about double
        assert counts[1] < 3 * counts[0], counts


class TestTheVerdictIsUnchanged:
    def test_a_misplaced_output_is_still_found_through_every_path(self):
        found = find_unsafe_markup(rebuilt(5, "f'<a href=\"{a}\">'"))
        assert [(f.function, f.sink) for f in found] == [("f", "mark_safe")]

    def test_the_same_part_is_not_the_same_text_under_another_binding(self):
        """``h``'s part after ``<a href="`` is an escaper's output under one call and a constant
        under the other: the call bindings are part of the key, in either order of judging."""
        helper = HEADER + "def h(u):\n    return f'<a href=\"{u}\">'\n"
        escaped = "def f(x):\n    return mark_safe(h(escape(x)))\n"
        constant = "def g(x):\n    return mark_safe(h('/home'))\n"
        for source in (helper + escaped + constant, helper + constant + escaped):
            assert [f.function for f in find_unsafe_markup(source)] == ["f"], source

    def test_the_same_part_after_another_text_is_another_position(self):
        """One string, ``w`` (and so the same ``escape(x)`` node inside it), written twice: as element
        text (enough) and at the start of a URL (not), in either order. Its text is remembered, its
        position is read where each copy lands."""
        body = "def f(x):\n    w = f'{escape(x)}'\n    return mark_safe({})\n"
        for sink in ("f'<p>{w}</p><a href=\"{w}\">'", "f'<a href=\"{w}\"><p>{w}</p>'"):
            source = HEADER + body.replace("{}", sink, 1)
            assert [f.function for f in find_unsafe_markup(source)] == ["f"], sink
        text_only = HEADER + body.replace("{}", "f'<p>{w}</p><b>{w}</b>'", 1)
        assert find_unsafe_markup(text_only) == []
