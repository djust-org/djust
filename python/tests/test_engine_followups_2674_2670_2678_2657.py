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

import itertools
import shutil
import tempfile
from pathlib import Path

import pytest
from django.template import Context, Engine

from djust import _rust

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
        truncated (silently wrong) join."""
        with pytest.raises(Exception, match="more than"):
            _rust.render_template('{{ g|join:"," }}', {"g": itertools.count()})
        # A needle that is never found — `1 in count()` short-circuits at the
        # second element in Python too, and must NOT raise.
        with pytest.raises(Exception, match="more than"):
            _rust.render_template("{% if -1 in g %}T{% endif %}", {"g": itertools.count()})
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


#: ONE instance for both engines: `{{ v }}` renders `str(v)`, which for a
#: default `__repr__` includes the object's ADDRESS — two instances differ in
#: their rendering for a reason that has nothing to do with the engines.
_LIAR = Liar()


class TestAStatedBoundIsTrustedOnlyToTheCap2678:
    @pytest.mark.parametrize(
        "src,ctx",
        [
            ("{{ v }}", lambda: {"v": _LIAR}),
            ("{{ v.0 }}", lambda: {"v": _LIAR}),
            ("{{ v|length }}", lambda: {"v": _LIAR}),
            ("{% if v %}T{% else %}F{% endif %}", lambda: {"v": _LIAR}),
            # The honest builtin twin the issue names.
            ("{{ v.0 }}", lambda: {"v": range(10**9)}),
            ("{{ v|length }}", lambda: {"v": range(10**9)}),
            ("{{ v.999999999 }}", lambda: {"v": range(10**9)}),
            # Still a plain list under the cap — the fix is a ceiling, not a
            # change of carrier for ordinary sequences.
            ("{% for x in v %}{{ x }}{% endfor %}", lambda: {"v": range(5)}),
            ("{{ v|join:',' }}", lambda: {"v": range(5)}),
        ],
    )
    def test_agrees_with_django(self, template_dir, src, ctx):
        assert_agree(template_dir, src, ctx)

    def test_a_render_that_used_to_hang_now_returns(self):
        """Termination is the whole of the bug. The render is `str(v)`, as
        Django's is."""
        out = _rust.render_template("{{ v }}", {"v": Liar()})
        assert "Liar object at" in out

    def test_an_over_cap_sequence_is_not_materialised_by_for(self):
        """`{% for %}` over it raises at the cap rather than building a
        hundred-million-element list."""
        with pytest.raises(Exception, match="more than"):
            _rust.render_template("{% for x in v %}{{ x }}{% endfor %}", {"v": range(10**9)})

    def test_a_sequence_at_the_cap_still_crosses_as_a_list(self):
        """The boundary itself: `OPAQUE_ITEM_CAP` items is a list, one past
        it is the carrier."""
        assert _rust.crosses_as_encoded(range(100_000)) is False
        assert _rust.crosses_as_encoded(range(100_001)) is True


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
