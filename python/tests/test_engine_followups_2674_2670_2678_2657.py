"""Django-vs-djust parity for #2674, #2670, #2678 and #2657.

Four follow-ups of the ADR-027 opaque-carrier work, each measured against
Django 5.2 rather than argued:

* #2674 — a one-shot iterator was consumed by ``{% for %}`` (#2613) but not
  by ``|join`` / ``{% if x in g %}`` / ``|safeseq``, which saw the carrier
  with no items and answered EMPTY. All the item sinks now consume through
  the one ``Encoded::consume_live_items`` path, once, under the same cap.
  Also: a context key literally named ``"0"`` shadowed a numeric literal in
  a filter argument, because the argument was resolved before it was read as
  a literal — Django's ``Variable()`` tries the literal first.
* #2670 — a legacy sequence with no ``__len__`` crossed as ``str(o)``, so
  ``{{ v.0 }}`` indexed the STRING (``'n'`` of ``'never-raises'``) where
  Django calls ``__getitem__`` once (``'x'``).
* #2678 — a sequence whose stated ``__len__`` is huge and whose
  ``__getitem__`` never raises was read in full (ten million calls, each
  converted) and hung the render; ``range(10**9)`` was the honest twin.
* #2657 — ``{% cycle %}`` state leaked across an ``{% include %}`` (both
  plain and ``only``); Django's ``Template.render`` pushes a fresh
  ``render_context`` frame per included render.

Every case renders the SAME source on both engines and asserts they agree,
so a future divergence in either direction reddens.
"""

from __future__ import annotations

import collections
import itertools
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest
from django.template import Context, Engine

from adr027_flag import resolve_lazy

from djust import _rust

#: A render that must TERMINATE, run where a hang is reportable.
#:
#: `#2678`'s defect is a render that never returns, and pytest cannot fail on
#: that — it stalls until the whole run is killed, which reads as
#: infrastructure trouble rather than as this test. The child gives it an
#: exit code (mirrors `test_value_conversion_crashes_2555_2624_2572.py`).
_CHILD = textwrap.dedent(
    """
    import sys
    import django
    from django.conf import settings

    settings.configure(
        SECRET_KEY="x",
        DEBUG=False,
        TEMPLATES=[{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [],
        }],
        LIVEVIEW_CONFIG={},
    )
    django.setup()
    from djust import _rust

    class Liar:
        def __len__(self): return 10**9
        def __getitem__(self, i): return "x"

    VALUES = {"liar": Liar()}
    sys.stdout.write(_rust.render_template(sys.argv[1], {"v": VALUES[sys.argv[2]]}))

    """
)


def _render_in_child(source: str, value: str, timeout: int = 25) -> str:
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, source, value],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, f"exit {result.returncode}\n{result.stderr[-2000:]}"
    return result.stdout


# --------------------------------------------------------------------- setup


@pytest.fixture(scope="module")
def template_dir():
    path = Path(tempfile.mkdtemp(prefix="djust-2657-"))
    (path / "cyc.html").write_text("{% cycle 'a' 'b' 'c' %}")
    (path / "cyc2.html").write_text("{% cycle 'a' 'b' 'c' %}{% cycle 'a' 'b' 'c' %}")
    yield path
    shutil.rmtree(path, ignore_errors=True)


def render_both(template_dir, source: str, context_factory) -> tuple[str, str]:
    """Render on both engines with a FRESH context each — a one-shot iterator
    consumed by the first engine must not be handed, spent, to the second."""
    django = Engine(dirs=[str(template_dir)]).from_string(source).render(Context(context_factory()))
    djust = _rust.render_template_with_dirs(source, context_factory(), [str(template_dir)])
    return django, djust


def assert_agree(template_dir, source, context_factory):
    django, djust = render_both(template_dir, source, context_factory)
    assert djust == django, f"{source!r}: django={django!r} djust={djust!r}"
    return django


# --------------------------------------------------------------------- #2674


class _Obj:
    def gen(self):
        yield "a"
        yield "b"


class TestEveryItemSinkConsumesAOneShotIterator2674:
    """The sinks #2672 left behind. Each row was ``''`` before #2674 because
    the carrier reached them with ``items: None`` and the arm answered an
    empty list — a silently wrong answer, not a refusal."""

    @pytest.mark.parametrize(
        "src,ctx",
        [
            ('{{ g|join:"," }}', lambda: {"g": iter([1, 2, 3])}),
            ('{{ g|join:"-" }}', lambda: {"g": (c for c in "abc")}),
            ('{{ g|join:"," }}', lambda: {"g": iter([])}),
            ('{{ o.gen|join:"" }}', lambda: {"o": _Obj()}),
            ("{% if 2 in g %}T{% else %}F{% endif %}", lambda: {"g": iter([1, 2, 3])}),
            ("{% if 9 in g %}T{% else %}F{% endif %}", lambda: {"g": iter([1, 2, 3])}),
            ("{% if 9 not in g %}T{% else %}F{% endif %}", lambda: {"g": iter([1, 2, 3])}),
            ("{% if 'b' in g %}T{% else %}F{% endif %}", lambda: {"g": (c for c in "abc")}),
            ("{{ g|safeseq|join:'' }}", lambda: {"g": iter(["a", "b"])}),
            ("{{ g|escapeseq|join:'' }}", lambda: {"g": iter(["<a>", "b"])}),
            ("{{ g|unordered_list }}", lambda: {"g": iter(["a", "b"])}),
            # The consumption is ONCE and it is shared: whatever sink runs
            # first spends the iterator, exactly as in Django.
            ('{{ g|join:"," }}|{% for k in g %}{{ k }}{% endfor %}', lambda: {"g": iter([1, 2])}),
            ('{% for k in g %}{{ k }}{% endfor %}|{{ g|join:"," }}', lambda: {"g": iter([1, 2])}),
            ("{% if 1 in g %}T{% endif %}|{{ g|join:',' }}", lambda: {"g": iter([1, 2])}),
        ],
    )
    def test_agrees_with_django(self, template_dir, src, ctx):
        assert_agree(template_dir, src, ctx)

    def test_the_cited_row_is_no_longer_empty(self, template_dir):
        """The issue's own table: `1,2,3`, not `''`."""
        assert _rust.render_template('{{ g|join:"," }}', {"g": iter([1, 2, 3])}) == "1,2,3"
        assert _rust.render_template("{% if 2 in g %}T{% endif %}", {"g": iter([1, 2, 3])}) == "T"

    def test_an_unbounded_iterator_raises_rather_than_hanging(self):
        """The cap is the same one `{% for %}` uses — a decline, never a
        truncated (silently wrong) join.

        `|join` RAISES rather than failing soft, and that is Django's split
        rather than an inconsistency: its `join` catches `TypeError` only, so
        an error out of the iteration propagates there too. `{% if %}` is the
        opposite — see `TestInFailsSoftLikeDjangosSmartIf`."""
        with pytest.raises(Exception, match="more than"):
            _rust.render_template('{{ g|join:"," }}', {"g": itertools.count()})
        # `1 in count()` short-circuits at the second element in Python too,
        # so this reaches no cap and must simply be True.
        assert _rust.render_template("{% if 1 in g %}T{% endif %}", {"g": itertools.count()}) == "T"

    def test_a_raising_next_propagates(self):
        """A generator that raises mid-iteration must not be swallowed into
        an empty join — Django propagates it."""

        def boom():
            yield 1
            raise ValueError("boom")

        with pytest.raises(Exception, match="boom"):
            _rust.render_template('{{ g|join:"," }}', {"g": boom()})


class TestFilterArgumentLiteralsWinOverContextKeys2674:
    """Django's `Variable("0")` is the INTEGER 0 — it tries the literal
    before any lookup — so a context key literally named `"0"` cannot
    change what `default_if_none:0` means."""

    @pytest.mark.parametrize(
        "src,ctx",
        [
            ("{% if x|default_if_none:0 %}T{% else %}F{% endif %}", lambda: {"x": None, "0": 5}),
            ("{% if x|default_if_none:0 %}T{% else %}F{% endif %}", lambda: {"x": None}),
            ("{{ x|default_if_none:0 }}", lambda: {"x": None, "0": 5}),
            ("{{ x|default:1 }}", lambda: {"x": "", "1": "shadow"}),
            ("{{ p|floatformat:2 }}", lambda: {"p": 1.239, "2": 0}),
            # A NON-numeric bare argument is still a variable, as in Django.
            ("{{ x|default:k }}", lambda: {"x": "", "k": "ctx"}),
        ],
    )
    def test_agrees_with_django(self, template_dir, src, ctx):
        assert_agree(template_dir, src, ctx)


# --------------------------------------------------------------------- #2670


class NeverRaises:
    def __getitem__(self, k):
        return "x"

    def __str__(self):
        return "never-raises"


class TestUnsizedLegacySequenceKeepsALiveHandle2670:
    """`{{ v.0 }}` used to index `str(v)` and render `'n'`."""

    @pytest.mark.parametrize("src", ["{{ v.0 }}", "{{ v }}", "{{ v.foo }}", "{{ v.0 }}{{ v.1 }}"])
    def test_agrees_with_django(self, template_dir, src):
        assert_agree(template_dir, src, lambda: {"v": NeverRaises()})

    def test_the_cited_cell(self):
        assert _rust.render_template("{{ v.0 }}", {"v": NeverRaises()}) == "x"


# --------------------------------------------------------------------- #2678


class Liar:
    """States a huge bound and never raises.

    `10**9` and not `10**7`, and the difference is the whole test: at `10**7`
    the walk still declines (it stops when it has read `len` items) — just
    after ten million wasted `__getitem__` calls — so gating the cap off
    changes only the COST and no assertion here would go red. At `10**9` the
    walk does not return, which is the defect #2678 reports. Measured: with
    the cap removed this render does not finish in 45s; with it, instantly."""

    def __len__(self):
        return 10**9

    def __getitem__(self, i):
        return "x"


class TestAStatedBoundIsTrustedOnlyToTheCap2678:
    """EVERY liar row runs in a bounded CHILD, not in-process.

    The defect is a render that never returns, so an in-process row cannot
    fail — it stalls the runner. That is not a theoretical objection: the
    first version of this class rendered the liar in-process, and gating the
    bound off did not redden it, it hung pytest for the full 900-second
    harness timeout. Every row here that touches the liar therefore compares
    Django (computed in-process, where `str(v)` is instant) against a djust
    render with an exit code and a deadline.
    """

    @staticmethod
    def _normalize(out: str) -> str:
        """A default `__repr__` carries the defining MODULE and the object's
        ADDRESS, and the child defines its own `Liar` — so those two fields
        differ between the processes for reasons that have nothing to do with
        the engines. Everything else must match byte for byte."""
        return re.sub(r"[\w.]*Liar object at 0x[0-9a-f]+", "<Liar>", out)

    @pytest.mark.parametrize(
        "src",
        [
            "{{ v }}",
            "{{ v.0 }}",
            "{{ v|length }}",
            "{% if v %}T{% else %}F{% endif %}",
        ],
    )
    def test_the_liar_agrees_with_django(self, src):
        django = Engine().from_string(src).render(Context({"v": Liar()}))
        djust = _render_in_child(src, "liar")
        assert self._normalize(djust) == self._normalize(django), (
            f"{src!r}: django={django!r} djust={djust!r}"
        )

    @pytest.mark.parametrize(
        "src,ctx",
        [
            # NOT `range(10**9)`, the "honest builtin twin" #2678 mentions in
            # passing: it terminates, so it was not the shape THIS fix bounds
            # and putting it in a context still materialised it. That was
            # closed separately by #2695, which moved the conversion's bound
            # onto the stated length for every sized sequence whose items do
            # not already exist, and gave the SINKS their own termination
            # rule — see
            # `test_sized_sequence_conversion_2695_2693.py`. Its cells live
            # there rather than here, so this table stays the liar's.
            #
            # Still a plain list under the cap — the fix is a shape rule, not
            # a change of carrier for ordinary sequences.
            ("{% for x in v %}{{ x }}{% endfor %}", lambda: {"v": range(5)}),
            ("{{ v|join:',' }}", lambda: {"v": range(5)}),
        ],
    )
    def test_agrees_with_django(self, template_dir, src, ctx):
        assert_agree(template_dir, src, ctx)

    def test_a_render_that_used_to_hang_now_returns(self):
        """Termination is the whole of the bug (the `_render_in_child`
        pattern from `test_value_conversion_crashes_2555_2624_2572.py`: a
        regression comes back as a non-zero exit, which pytest can report)."""
        out = _render_in_child("{{ v }}", "liar", timeout=25)
        assert "Liar object at" in out, out

    def test_the_conversion_bound_is_the_stated_length(self):
        """The scope of the cap, as #2695 left it.

        This fix keyed on TWO conditions — past the cap AND no `__iter__` —
        because its first version keyed on the number alone and turned
        `{% for %}` over a 100,001-item list into a `RuntimeError` (PR #2691
        review). #2695 showed the number really is the conversion's axis, and
        that the earlier version's mistake was applying it at the SINK too:
        a `list` past the cap is carried here and still renders every item,
        because `Encoded::live_walk_terminates` asks the other question
        (`{% for %}` over 100 001 items is pinned by
        `TestATerminatingCollectionPastTheCapIsUntouched` below).

        So the carried set is "anything whose stated length exceeds the cap"
        — the liar, a `range`, a `deque`, a `list` and a `QuerySet` alike.

        A `list` and a `QuerySet` were EXEMPT between the #2695 review and
        #2717, because their declined SPELLING was wrong; #2717 fixed the
        spelling at the sink instead (`Encoded::declined_list_spelling`) and
        dropped the exemption, which was costing 4 GB on a 150 000-row
        table. See `spelling_is_the_items_list_repr` and
        `TestARealQuerySetIsSpelledTheSameOnBothSidesOfTheCap` in
        `test_sized_sequence_conversion_2695_2693.py`, which pins that the
        rendered bytes did not move when the exemption went.
        """
        assert _rust.crosses_as_encoded(Liar()) is True
        assert _rust.crosses_as_encoded(range(10**9)) is True
        # A `deque` past the cap DESCRIBES nothing it has not built, but
        # nothing about `len(deque)` builds it either — it is the ordinary
        # sized-sequence case, and it is carried.
        assert _rust.crosses_as_encoded(collections.deque(range(100_001))) is True
        # A `list` too, since #2717 — no shape is exempt for its LENGTH.
        assert _rust.crosses_as_encoded(list(range(100_001))) is True
        # And under the cap a `list` is NOT carried. That is what makes the
        # line above a statement about the length rather than about `list`,
        # and it is the whole difference between the two rules that now
        # decline a sequence: #2704's is about SPELLING and claims a `deque`
        # at any size, #2717's is about LENGTH and claims a `list` only past
        # the cap. A `list` spells as its items either way, so only the cap
        # can carry one.
        assert _rust.crosses_as_encoded(list(range(3))) is False
        # Under the cap: unchanged, on both the sized and the ordinary shape.
        assert _rust.crosses_as_encoded(list(range(10))) is False
        # A small `range` used to be `False` here — the cap was the only
        # reason a sequence declined. #2704 added the SPELLING reason, which
        # is length-independent: `{{ v }}` over a `range` is `range(0, 10)`
        # in Django and was `[0, 1, …]` here, so every non-`list` sequence
        # now crosses as the carrier at ANY length. The cap question this
        # class measures is unaffected — it only decides whether the ITEMS
        # are read at the conversion or at the sink.
        assert _rust.crosses_as_encoded(range(10)) is True
        # A `deque` one UNDER the cap is carried for the same #2704 reason as
        # a small `range` — the spelling, not the length. What this class
        # measures is unchanged and is asserted directly below: the cap is
        # what decides whether the carrier holds ITEMS.
        assert _rust.crosses_as_encoded(collections.deque(range(100_000))) is True
        assert _rust.crosses_as_encoded(list(range(100_000))) is False
        # The cap's own effect — whether the carrier holds ITEMS — is not
        # visible through `crosses_as_encoded` for a `deque` any more, and it
        # is not this assertion's job: it is measured by
        # `TestATerminatingCollectionPastTheCapIsUntouched` and by the liar
        # rows above, which is where the two sides of the cap answer
        # differently.


#: A collection ONE past `OPAQUE_ITEM_CAP` that genuinely terminates. This is
#: the variant 18,021 green tests never exercised: every earlier case was
#: either well under the cap or an endless iterator, so a cap that claimed
#: honest collections looked exactly like a cap that did not (v1.0.0rc4
#: finding #1 — a suite must enumerate every variant of the surface, and
#: "just past the boundary" is a variant of every boundary).
CAP = 100_000


class _FakeQuerySet:
    """`__len__` + `__iter__` and no `__getitem__` — the QuerySet shape, which
    is the one a real project hits with more than 100,000 rows."""

    def __init__(self, rows):
        self._rows = list(rows)

    def __len__(self):
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)


#: One row per sink. The expected value is computed rather than written into
#: a parametrize id — `"." * 100_001` as an id makes the report unreadable.
PAST_CAP_SINKS = {
    "for": ("{% for x in v %}.{% endfor %}", lambda n: "." * n),
    "join": ('{{ v|join:"" }}', lambda n: "".join(str(i) for i in range(n))),
    "length": ("{{ v|length }}", lambda n: str(n)),
    "index": ("{{ v.0 }}", lambda n: "0"),
    "first": ("{{ v|first }}", lambda n: "0"),
    "in-hit": ("{% if 5 in v %}T{% else %}F{% endif %}", lambda n: "T"),
    "in-miss": ("{% if -1 in v %}T{% else %}F{% endif %}", lambda n: "F"),
}

#: A QuerySet-shaped object has no `__getitem__`, so the two subscript sinks
#: are not its axis.
NOT_SUBSCRIPTABLE = {"index", "first"}


@pytest.mark.parametrize("lazy", [True, False], ids=["lazy", "eager"])
@pytest.mark.parametrize("sink", sorted(PAST_CAP_SINKS))
@pytest.mark.parametrize("shape", ["list", "queryset"])
class TestATerminatingCollectionPastTheCapIsUntouched:
    """Every sink, both shapes, both flag settings, for a collection just past
    the cap.

    Both settings, because the first version of the #2678 fix was wrong in
    DIFFERENT ways on each: under the shipped default it raised
    (`'list' object yielded more than 100000 items`), and on the eager hatch
    it answered from the REPR — `{% for %}` rendered 688,898 dots for a
    100,001-item list, `{{ v.0 }}` rendered `[`, `{% if 5 in v %}` was False.
    A test on one setting could not tell those two apart from correct.
    """

    def test_agrees_with_django(self, template_dir, lazy, sink, shape):
        if shape == "queryset" and sink in NOT_SUBSCRIPTABLE:
            pytest.skip("a QuerySet-shaped object has no __getitem__")
        src, expected_for = PAST_CAP_SINKS[sink]
        factory = list if shape == "list" else _FakeQuerySet
        with resolve_lazy(lazy):
            django, djust = render_both(template_dir, src, lambda: {"v": factory(range(CAP + 1))})
        assert djust == django, (
            f"{sink} on {shape} (lazy={lazy}): django={django[:40]!r}... djust={djust[:40]!r}..."
        )
        assert django == expected_for(CAP + 1), "the Django reference itself moved"


class TestInFailsSoftLikeDjangosSmartIf:
    """`smartif`'s `infix.eval` wraps the operator in `except Exception:
    return False`, so `{% if x in y %}` never 500s a page. The first version
    of the live-handle `in` arm propagated both the cap and a raising
    `__next__` (PR #2691 review)."""

    def test_a_raising_next_is_false_not_a_500(self, template_dir):
        def boom():
            yield 1
            raise ValueError("boom")

        # Needle 9, not 1: `in` short-circuits, so a needle that matches the
        # first element never reaches the raise and the case is vacuous.
        assert_agree(template_dir, "{% if 9 in g %}T{% else %}F{% endif %}", lambda: {"g": boom()})

    def test_an_unbounded_iterable_is_false_not_a_500(self):
        """Asked of djust alone: `-1 in itertools.count()` never returns in
        Django, so there is no reference answer — only the requirement that
        djust neither hangs nor raises."""
        out = _rust.render_template(
            "{% if -1 in g %}T{% else %}F{% endif %}", {"g": itertools.count()}
        )
        assert out == "F", out


# --------------------------------------------------------------------- #2657


class TestCycleStateIsPerIncludedRender2657:
    """Django's `Template.render` pushes a fresh `render_context` frame for
    each included render and `RenderContext` reads only `dicts[-1]`, so an
    included `{% cycle %}` restarts on every execution — plain or `only` —
    while the PARENT's own cycles carry on untouched."""

    @pytest.mark.parametrize(
        "src",
        [
            "{% for x in v %}{% include 'cyc.html' %}{% endfor %}",
            "{% for x in v %}{% include 'cyc.html' only %}{% endfor %}",
            "{% for x in v %}{% include 'cyc.html' with y=x %}{% endfor %}",
            # The parent's cycle must NOT be reset by the include.
            "{% for x in v %}{% cycle 'p' 'q' %}{% include 'cyc.html' %}{% endfor %}",
            "{% for x in v %}{% cycle 'p' 'q' %}{% include 'cyc.html' only %}{% endfor %}",
            # Two cycles inside one included render still advance together.
            "{% for x in v %}{% include 'cyc2.html' %}{% endfor %}",
            # Outside a loop: two includes of the same template.
            "{% include 'cyc.html' %}{% include 'cyc.html' %}",
            "{% include 'cyc.html' only %}{% include 'cyc.html' only %}",
        ],
    )
    def test_agrees_with_django(self, template_dir, src):
        assert_agree(template_dir, src, lambda: {"v": [1, 2, 3]})

    def test_the_cited_table(self, template_dir):
        """`aaa`, both forms — the issue's measurement against Django 5.2."""
        for src in (
            "{% for x in v %}{% include 'cyc.html' %}{% endfor %}",
            "{% for x in v %}{% include 'cyc.html' only %}{% endfor %}",
        ):
            out = _rust.render_template_with_dirs(src, {"v": [1, 2, 3]}, [str(template_dir)])
            assert out == "aaa", (src, out)

    def test_a_top_level_cycle_still_advances_across_a_loop(self, template_dir):
        """The gate-off canary for the frame key: a cycle in the OUTER
        template is one render frame and must still advance 'abc'."""
        assert_agree(
            template_dir,
            "{% for x in v %}{% cycle 'a' 'b' 'c' %}{% endfor %}",
            lambda: {"v": [1, 2, 3]},
        )
        assert (
            _rust.render_template(
                "{% for x in v %}{% cycle 'a' 'b' 'c' %}{% endfor %}", {"v": [1, 2, 3]}
            )
            == "abc"
        )

    def test_resetcycle_still_reaches_the_frame_it_is_in(self, template_dir):
        assert_agree(
            template_dir,
            "{% for x in v %}{% cycle 'a' 'b' 'c' %}{% resetcycle %}{% endfor %}",
            lambda: {"v": [1, 2, 3]},
        )

    def test_the_false_comment_is_gone(self):
        """#2657's actionable half: the `only`-include branch asserted that
        Django's `context.new()` makes a `{% cycle %}` advance the PARENT
        render's iterator. It does not, and that false claim motivated an
        equally wrong `{% ifchanged %}` share during #2650's review."""
        src = (
            Path(__file__).resolve().parents[2] / "crates/djust_templates/src/renderer.rs"
        ).read_text()
        assert "advances the parent render's iterator" not in src
        assert "share_cycle_state_from" not in src
