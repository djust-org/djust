"""`{% for a,b in x %}` — the comma WITHOUT a space — is a two-name loop (#2377).

The defect
----------
Django's ``do_for`` re-joins the tokens before ``in`` and splits the result on
``re.split(r" *, *", …)``, so ``a,b``, ``a, b`` and ``a ,b`` are one
three-name loop and every spelling is legal. djust split on WHITESPACE and
trimmed a trailing comma off each token — which handles ``a, b`` and nothing
else. ``{% for a,b in p %}`` therefore produced ONE loop variable literally
spelled ``a,b``; nothing ever resolves that, so every ``{{ a }}`` and
``{{ b }}`` in the body rendered empty and the whole region disappeared with
no error.

That is the worst failure shape a template engine has, and the third of its
kind in this area: ``{% for x in p|slice %}`` (#2325) and ``{% for k in d %}``
(#2334) were the same silence with a different cause.

Why the differential could not see it
-------------------------------------
Every loop the corpus builds — in ``PATH_SHAPES``, in ``TAG_SHAPES``, in
``BUILTIN_SHAPES`` — is written with the SPACED spelling. A corpus gap is
silent by construction, so
``TestTheCorpusGapThatHidThisFromTheDifferential`` pins that the unspaced
shapes are on the axis.

Every expectation here is LIVE Django, never a transcription.
"""

import pathlib
import re

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.template import TemplateSyntaxError
from django.utils.safestring import mark_safe

from djust import _rust
from djust.mixins.rust_bridge import _collect_safe_keys

REPO = pathlib.Path(__file__).resolve().parents[2]

MARKED = mark_safe("<b>ok</b>")


def _safe_keys(ctx: dict) -> list[str]:
    keys: list[str] = []
    for key, value in ctx.items():
        keys.extend(_collect_safe_keys(value, key))
    return keys


def render_both(tpl: str, ctx: dict) -> tuple[str, str]:
    django_out = DjangoTemplate(tpl).render(DjangoContext(ctx))
    safe_keys = _safe_keys(ctx)
    djust_out = _rust.render_template_with_dirs(tpl, ctx, [], safe_keys or None)
    return django_out, djust_out


def assert_agrees(tpl: str, ctx: dict) -> str:
    django_out, djust_out = render_both(tpl, ctx)
    assert djust_out == django_out, (
        f"{tpl!r} over {ctx!r}\n  django {django_out!r}\n  djust  {djust_out!r}"
    )
    return djust_out


class TestEverySpellingOfTheUnpackList:
    """The three spellings Django's `re.split(r" *, *", …)` reads as one."""

    @pytest.mark.parametrize(
        "tpl",
        [
            "{% for a,b in p %}[{{ a }}={{ b }}]{% endfor %}",
            "{% for a, b in p %}[{{ a }}={{ b }}]{% endfor %}",
            "{% for a ,b in p %}[{{ a }}={{ b }}]{% endfor %}",
            "{% for a , b in p %}[{{ a }}={{ b }}]{% endfor %}",
        ],
        ids=["unspaced", "space-after", "space-before", "space-both"],
    )
    def test_a_two_name_unpack_agrees_whatever_the_spacing(self, tpl):
        out = assert_agrees(tpl, {"p": [("x", "y")]})
        # Not just "agrees": the values must actually REACH the page. Both
        # engines rendering nothing would agree too, which is the state this
        # issue is about.
        assert out == "[x=y]", out

    def test_three_names_unspaced(self):
        assert (
            assert_agrees(
                "{% for a,b,c in p %}[{{ a }}{{ b }}{{ c }}]{% endfor %}",
                {"p": [("x", "y", "z")]},
            )
            == "[xyz]"
        )

    def test_the_mixed_spelling_django_documents_for_dict_items(self):
        # `{% for key,value in dict.items %}` is the spelling in Django's OWN
        # `do_for` docstring, and it is the unspaced one.
        assert (
            assert_agrees(
                "{% for k,v in p.items %}[{{ k }}={{ v }}]{% endfor %}",
                {"p": {"a": 1, "b": 2}},
            )
            == "[a=1][b=2]"
        )

    def test_unspaced_composes_with_reversed(self):
        assert (
            assert_agrees(
                "{% for a,b in p reversed %}[{{ a }}{{ b }}]{% endfor %}",
                {"p": [("1", "2"), ("3", "4")]},
            )
            == "[34][12]"
        )

    def test_unspaced_still_reaches_the_empty_branch(self):
        assert (
            assert_agrees(
                "{% for a,b in p %}[{{ a }}]{% empty %}E{% endfor %}",
                {"p": []},
            )
            == "E"
        )

    def test_a_single_name_is_untouched(self):
        assert assert_agrees("{% for x in p %}[{{ x }}]{% endfor %}", {"p": ["a", "b"]}) == "[a][b]"


class TestTheGrantStillTravelsUnderTheNewSpelling:
    """The unpack's per-component safety mapping is keyed on the NAMES.

    Splitting on the comma changes what those names ARE, so the #2361/#2363
    positional grant (`<expr>.<index>.<i>`) has to keep resolving. If it did
    not, this fix would trade a silent empty region for a silent over-escape.
    """

    def test_a_marked_component_comes_through_live_unspaced(self):
        assert (
            assert_agrees(
                "{% for a,b in p %}[{{ b }}]{% endfor %}",
                {"p": [("x", MARKED)]},
            )
            == "[<b>ok</b>]"
        )

    def test_an_unmarked_component_is_still_escaped_unspaced(self):
        assert (
            assert_agrees(
                "{% for a,b in p %}[{{ b }}]{% endfor %}",
                {"p": [("x", "<img src=x onerror=alert(1)>")]},
            )
            == "[&lt;img src=x onerror=alert(1)&gt;]"
        )


class TestTheInvalidArgumentRuleIsDjangos:
    """`do_for`'s `invalid_chars` frozenset, verbatim — not `isidentifier()`.

    Splitting on the comma CREATES the empty-name case (`{% for a, in p %}` is
    `["a", ""]`), which the whitespace split could not produce. Django raises
    `TemplateSyntaxError` for it, and for any name carrying a space, either
    quote, or the filter separator. Raising is both parity and the
    less-permissive direction, which is the only direction this engine may
    move in.
    """

    @pytest.mark.parametrize(
        "tpl",
        [
            "{% for a, in p %}[{{ a }}]{% endfor %}",
            "{% for ,a in p %}[{{ a }}]{% endfor %}",
            "{% for a,,b in p %}[{{ a }}]{% endfor %}",
            "{% for a b in p %}[{{ a }}]{% endfor %}",
            "{% for a|upper in p %}[{{ a }}]{% endfor %}",
            "{% for 'a' in p %}[{{ a }}]{% endfor %}",
        ],
        ids=["trailing", "leading", "double", "space", "pipe", "quoted"],
    )
    def test_django_rejects_it_and_so_do_we(self, tpl):
        ctx = {"p": [("x", "y")]}
        with pytest.raises(TemplateSyntaxError):
            DjangoTemplate(tpl).render(DjangoContext(ctx))
        # djust raises its own error type across the PyO3 boundary; the
        # load-bearing claim is that it REFUSES rather than rendering
        # something Django never would.
        with pytest.raises(Exception) as excinfo:  # noqa: PT011 — RuntimeError from PyO3
            _rust.render_template_with_dirs(tpl, ctx, [], None)
        assert "invalid argument" in str(excinfo.value), str(excinfo.value)

    def test_a_name_django_accepts_that_is_not_an_identifier_still_works(self):
        # `a-b` is not a Python identifier, and Django accepts it — it only
        # refuses the four `invalid_chars`. A fix that reached for
        # `isidentifier()` would be STRICTER than Django here, which is a
        # different bug in the other direction.
        assert_agrees("{% for a-b in p %}[{{ a }}]{% endfor %}", {"p": ["x"]})


class TestTheUnpackArityDivergenceMoved:
    """The arity divergence this file pinned is CLOSED, in #2387.

    What used to stand here — `TestTheUnpackArityDivergenceIsNamedNotFixed` —
    asserted that Django raises where djust renders, and said it "goes red the
    day this is closed, and names itself as the thing to move". It moved to
    `test_for_unpack_arity_2387.py`, whose first three parametrized rows ARE
    its three cells, now asserting that both engines refuse.

    What stays here is the part that belongs to #2377: both comma spellings
    reach the same code, so both raise the same message. If the split ever
    regresses, one spelling would stop raising while the other kept doing so.
    """

    @pytest.mark.parametrize(
        "tpl",
        [
            "{% for a,b in p %}[{{ a }}={{ b }}]{% endfor %}",
            "{% for a, b in p %}[{{ a }}={{ b }}]{% endfor %}",
            "{% for a ,b in p %}[{{ a }}={{ b }}]{% endfor %}",
        ],
        ids=["unspaced", "spaced", "space-before"],
    )
    def test_every_comma_spelling_refuses_the_same_arity(self, tpl):
        ctx = {"p": "abc"}
        expected = "Need 2 values to unpack in for loop; got 1. "
        with pytest.raises(ValueError) as django_exc:
            DjangoTemplate(tpl).render(DjangoContext(dict(ctx)))
        assert str(django_exc.value) == expected
        # `RuntimeError`, not `ValueError`: every djust render error crosses
        # the PyO3 boundary as one. The message is Django's, verbatim.
        with pytest.raises(RuntimeError) as djust_exc:
            _rust.render_template_with_dirs(tpl, dict(ctx), [], None)
        assert str(djust_exc.value).endswith(expected), djust_exc.value


class TestTheCorpusGapThatHidThisFromTheDifferential:
    """A corpus gap is silent by construction — this is the fourth one.

    #2281, #2325, #2334 and now this. The manifest reports `0 MISSING` on
    every mechanical axis and still cannot see a spelling no shape spells, so
    the only protection is a test that reads the corpus and asserts the shape
    is there.
    """

    def test_the_unspaced_spellings_are_on_the_path_axis(self):
        src = (REPO / "scripts" / "filter-parity-differential.py").read_text(encoding="utf-8")
        block = src.split("PATH_SHAPES = {", 1)[1].split("\n}", 1)[0]
        # Read the SHAPE SOURCES rather than the keys: a key can be renamed
        # without changing what is rendered, and what is rendered is the
        # claim. `for\s+\w+,\w+` is a `{% for %}` whose unpack list carries a
        # comma with NO space after it — the exact spelling that was broken.
        unspaced = re.findall(r"\{%\s*for\s+\w+,\w+", block)
        assert unspaced, (
            "PATH_SHAPES builds no `{% for a,b in … %}` cell, so the "
            "differential cannot see #2377 and will report clean over it."
        )
