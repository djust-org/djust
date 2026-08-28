"""Regression: ``unordered_list`` nests the ``<ul>`` at the PARENT's indent (#2301).

Django's ``list_formatter`` (``django/template/defaultfilters.py``) builds the
whole sublist wrapper out of the parent's ``indent`` and passes ``tabs + 1``
only to the recursive call::

    sublist = "\\n%s<ul>\\n%s\\n%s</ul>\\n%s" % (
        indent, list_formatter(children, tabs + 1), indent, indent)
    output.append("%s<li>%s%s</li>" % (indent, escaper(item), sublist))

Four ``%s`` indents, all the parent's — so only the ``<li>``s inside the
sublist step in. djust used ``depth + 1`` for the ``<ul>``/``</ul>`` too::

    django  '\\t<li>a\\n\\t<ul>\\n\\t\\t<li>b</li>\\n\\t</ul>\\n\\t</li>'
    djust   '\\t<li>a\\n\\t\\t<ul>\\n\\t\\t<li>b</li>\\n\\t\\t</ul>\\n\\t</li>'
                        ^^^^                          ^^^^

Whitespace only — the ``<li>`` content was already byte-identical, and the
divergence reproduces with nothing ``mark_safe``d, so it is neither an escaping
nor a capability change.

**The source fix is not this file's.** It landed in #2306, contributed by
@alexsmolya, as a one-line change dropping ``sub_indent``. What this file adds
is the coverage that fix shipped without: the divergence is a *recursion*
bug, so the question it raises is not "does the reported cell match" but "does
every nesting shape match", and only a sweep answers that.

Every assertion is a byte comparison against **live Django**, and
:class:`TestRandomisedShapes` is the load-bearing half: the curated table below
fixes one nesting shape per row, and a hand-written table is exactly what a
depth-bookkeeping bug survives. 3,000 randomly-shaped lists do not — the same
sweep read 2164/3000 before the fix and 3000/3000 after, and it is what found
the one remaining ``unordered_list`` shape divergence (a ``tuple`` sublist,
which Django accepts and djust does not — #2317).
"""

from __future__ import annotations

import random

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

SRC = "{{ p|unordered_list }}"


def django_render(value) -> str:
    return DjangoTemplate(SRC).render(DjangoContext({"p": value}))


def djust_render(value) -> str:
    return _rust.render_template(SRC, normalize_django_value({"p": value}))


def assert_agrees(value) -> None:
    d, r = django_render(value), djust_render(value)
    assert r == d, f"{value!r}: django={d!r} djust={r!r}"


class TestTheReportedCell:
    """The issue's reproduction, spelled out byte for byte."""

    def test_the_reported_pair_now_matches_django_exactly(self) -> None:
        assert django_render(["a", ["b"]]) == "\t<li>a\n\t<ul>\n\t\t<li>b</li>\n\t</ul>\n\t</li>"
        assert_agrees(["a", ["b"]])

    def test_the_wrapper_sits_one_tab_shallower_than_its_items(self) -> None:
        """The property the fix restores, asserted directly rather than through
        a whole-string compare — so a future change that moves BOTH by the same
        amount still reddens this."""
        out = djust_render(["a", ["b"]])
        lines = out.split("\n")
        assert lines[1] == "\t<ul>", lines
        assert lines[2] == "\t\t<li>b</li>", lines
        assert lines[3] == "\t</ul>", lines

    def test_djangos_own_docstring_example(self) -> None:
        """``['States', ['Kansas', ['Lawrence', 'Topeka'], 'Illinois']]`` — the
        example in Django's own ``unordered_list`` docstring, which exercises
        two nesting levels and a sibling after the sublist."""
        assert_agrees(["States", ["Kansas", ["Lawrence", "Topeka"], "Illinois"]])


class TestNestingShapes:
    """One row per structural shape the recursion has to get right."""

    @pytest.mark.parametrize(
        "value",
        [
            [],
            ["a"],
            ["a", "b"],
            ["a", []],
            [["a"]],
            ["a", ["b"]],
            ["a", ["b", ["c"]]],
            ["a", ["b", ["c", ["d", ["e"]]]]],
            ["a", ["b"], "c", ["d", ["e"]]],
            ["a", ["b"], ["c"]],
            ["a", ["b", "c"], "d"],
            [["a", ["b"]], "c"],
            ["<b>", ["<i>"]],
            ["a < b", [42, None, ""]],
            ["héllo→", ["wörld"]],
        ],
    )
    def test_agrees_with_django(self, value) -> None:
        assert_agrees(value)


class TestRandomisedShapes:
    """The half a curated table cannot do.

    Django is importable from the test, so "what does the reference actually
    do" is one call away and is worth preferring to a fixed set of shapes: the
    table above pins fifteen nestings, and an indent bug lives in whichever one
    was not written down. Before the fix this sweep reported 2164/3000; after,
    3000/3000.
    """

    LEAVES = ["a", "b", "<b>x</b>", "", "héllo", 42, None, "a < b"]

    @classmethod
    def _shape(cls, rng: random.Random, depth: int = 0):
        out = []
        for _ in range(rng.randint(0, 4)):
            if depth < 4 and rng.random() < 0.35:
                out.append(cls._shape(rng, depth + 1))
            else:
                out.append(rng.choice(cls.LEAVES))
        return out

    def test_three_thousand_random_nestings_all_agree(self) -> None:
        rng = random.Random(20301)  # fixed: the suite must be deterministic
        shapes = [self._shape(rng) for _ in range(3000)]
        nested = sum(1 for s in shapes if any(isinstance(x, list) for x in s))
        assert nested > 1000, (
            f"only {nested}/3000 shapes contain a sublist — the generator stopped "
            "producing the case this file is about, so the sweep is vacuous"
        )
        bad = [s for s in shapes if django_render(s) != djust_render(s)]
        assert not bad, f"{len(bad)}/3000 disagree, first: {bad[0]!r}"
