"""`{% regroup %}` over a STRING source builds one group, not zero (#2385, #2394).

The defect
----------
Django's ``RegroupNode.render`` runs ``groupby(obj_list, …)`` over whatever
``self.target.resolve`` returned, so the source is iterated with **Python's own
semantics**: a ``str`` yields its characters, a ``dict`` yields its keys, a
missing variable yields nothing. djust's handler matched ``list``/``tuple`` and
answered ``[]`` for everything else, so::

    {% regroup s by k as g %}[{{ g|length }}]     s = "ab"
      django  '[1]'
      djust   '[0]'

Silently — no exception, no warning, the region simply empty.

#2394 and #2385 are ONE defect, not two
---------------------------------------
#2394 measured three operand spellings (bare ``s``, ``p.0``, ``p.a``) and
concluded the source being a string "is the whole of it", localising the gap to
the handler rather than the operand channel. #2385 measured the same cell on
the corpus (8,505 cells) and said djust's regroup "reaches only ``List``/
``Tuple``". Same sentence, two vocabularies: one `_decode_source` returning
``[]`` for a ``str``. Fixed once, here.

Two mechanisms, and why the second is needed
--------------------------------------------
Iterating the string in the handler is only HALF the fix, and the missing half
is the reason this is not a one-liner. The assign-tag arg channel's contract is
"unresolved ⇒ the caller keeps the raw token", so before #2385 a resolved
string and an unresolved bare name arrived as the SAME bytes::

    {% regroup s by k as g %}     s = "ab"   ->  handler receives  ab
    {% regroup nope by k as g %}  (missing)  ->  handler receives  nope

Django's answer for the second is ZERO groups (``RegroupNode`` fails silently
on an unresolvable target). Teaching the handler to iterate its text would have
made ``nope`` group into four characters — trading one divergence for a new one
in the MORE-permissive direction. So the renderer now JSON-encodes a resolved
``Value::String`` at a declared ``RESOLVE_ARG_POSITIONS`` position, and the
quoting is the type tag that tells the two apart.

That ambiguity had a live second symptom, pinned below: the handler's
"is it a bare name?" fallback looked the text up as a context key, so
``s = "q"`` grouped over the UNRELATED variable ``q`` whenever one existed.

Every expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import pathlib

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import _rust
from djust.mixins.rust_bridge import _collect_safe_keys
from djust.template_tags.regroup import RegroupTagHandler

REPO = pathlib.Path(__file__).resolve().parents[2]
RENDERER = REPO / "crates" / "djust_templates" / "src" / "renderer.rs"

#: `[{{ g|length }}]` plus one `("grouper":len)` per group — enough to tell
#: "no groups" from "one group of N" from "N groups".
SHAPE = (
    "{% regroup OPERAND by k as g %}"
    "[{{ g|length }}]"
    '{% for x in g %}("{{ x.grouper }}":{{ x.list|length }}){% endfor %}'
)


def _safe_keys(ctx: dict) -> list[str]:
    keys: list[str] = []
    for key, value in ctx.items():
        keys.extend(_collect_safe_keys(value, key))
    return keys


def render_both(tpl: str, ctx: dict) -> tuple[str, str]:
    """(django, djust) for one template — Django gets a fresh context dict.

    `Template.render` writes the regroup result INTO the context it is given,
    so the two engines must not share one.
    """
    django_out = DjangoTemplate(tpl).render(DjangoContext(dict(ctx)))
    djust_ctx = dict(ctx)
    djust_out = _rust.render_template_with_dirs(tpl, djust_ctx, [], _safe_keys(djust_ctx) or None)
    return django_out, djust_out


def assert_agrees(operand: str, ctx: dict) -> str:
    tpl = SHAPE.replace("OPERAND", operand)
    django_out, djust_out = render_both(tpl, ctx)
    assert djust_out == django_out, (
        f"{tpl!r} over {ctx!r}\n  django={django_out!r}\n  djust ={djust_out!r}"
    )
    return django_out


class TestAStringSourceBuildsDjangosGroup:
    """The cell both issues cite, and every operand spelling that reaches it.

    #2394's table is the bare-variable / dotted / index spellings; the filter
    spellings are the ones #2385's corpus is made of (`p|<expr>` is how its tag
    axis writes every operand). They were all the same defect: whatever the
    channel, the operand resolved to a string and the handler dropped it.
    """

    @pytest.mark.parametrize(
        "operand,ctx",
        [
            # #2394's cited cell, and #2385's.
            ("s", {"s": "ab"}),
            ("p", {"p": "abc"}),
            # A one-character string: the "[1]" answer is a real group, not a
            # coincidence of length.
            ("p", {"p": "1"}),
            # #2394's other two spellings — an index and a dict key.
            ("p.0", {"p": ["ab"]}),
            ("p.a", {"p": {"a": "ab"}}),
            # The filter spellings #2385's corpus is built from. `upper`
            # changes the characters, so a green here is not the operand
            # falling back to the raw token by luck.
            ("p|first", {"p": ["ab", "cd"]}),
            ("p|upper", {"p": "ab"}),
            ("p|slice:':2'", {"p": "abcd"}),
            # Both quote spellings of a literal. Django resolves them to the
            # identical `str`; before #2385 the single-quoted one was not even
            # valid JSON.
            ('"abc"', {}),
            ("'abc'", {}),
        ],
    )
    def test_string_source_groups_its_characters(self, operand: str, ctx: dict) -> None:
        out = assert_agrees(operand, ctx)
        assert out.startswith("[1]"), (
            f"live Django built {out!r} for {operand!r} over {ctx!r} — this "
            f"class is supposed to be 'one group of characters'"
        )

    def test_the_string_that_names_a_context_key_is_not_that_key(self) -> None:
        """`s = "q"` groups over the characters of `"q"`, not over `q`.

        The live second symptom of the ambiguity the JSON quoting removes: the
        handler's bare-name fallback looked its text up in the context, so a
        string source whose TEXT happened to be a variable name silently
        grouped over that variable's value instead. Django groups the one
        character.
        """
        out = assert_agrees("s", {"s": "q", "q": [{"k": 9}]})
        assert '"None":1' in out, out
        assert '"9"' not in out, (
            f"grouped over the unrelated variable `q` rather than the string "
            f'"q" — the #2385 shadow symptom: {out!r}'
        )

    def test_a_string_that_looks_like_json_is_still_characters(self) -> None:
        """`s = "[1, 2]"` is six characters, not a two-element list.

        The handler decodes its arg as JSON, so a string whose TEXT is JSON was
        decoded into the structure it spells. Django iterates the string.
        """
        out = assert_agrees("p", {"p": "[1, 2]"})
        assert '"None":6' in out, out

    def test_a_dict_source_groups_its_keys(self) -> None:
        """The same `List`/`Tuple`-only match, one type over (#2385's "check
        the other shapes in the same pass").

        `for obj in {"a": 1, "b": 2}` yields the KEYS in Python, so Django
        builds one group of two. djust answered `[0]`.
        """
        out = assert_agrees("p", {"p": {"a": 1, "b": 2}})
        assert '"None":2' in out, out


class TestTheAnswersThatMustNotMove:
    """The fix may not buy the string case by breaking a case that agreed.

    Each of these agreed with Django BEFORE the fix. The first is the one a
    handler-only fix breaks: it is the whole reason the renderer half exists.
    """

    def test_an_unresolvable_source_still_builds_no_groups(self) -> None:
        """`{% regroup nope … %}` is zero groups, not four characters.

        `RegroupNode.render` resolves with `ignore_failures=True` and returns
        `[]` when the target is missing. A handler that iterated its raw token
        would build a group per character of the NAME.
        """
        out = assert_agrees("nope", {"p": "abc"})
        assert out == "[0]", out

    @pytest.mark.parametrize(
        "operand,ctx",
        [
            ("p", {"p": ""}),  # empty string: Django's groupby yields nothing
            ("p", {"p": None}),  # None: RegroupNode's fail-silently branch
            ("p", {"p": ["a", "b"]}),  # the list source, unchanged
            ("p", {"p": ("a", "b")}),  # a tuple
            ("p", {"p": [{"k": 1}, {"k": 1}, {"k": 2}]}),  # real grouping
            ("p.items", {"p": {"a": 1}}),  # a dict view
        ],
    )
    def test_unaffected_sources_still_agree(self, operand: str, ctx: dict) -> None:
        assert_agrees(operand, ctx)

    def test_iteration_itself_was_never_the_bug(self) -> None:
        """`{% for c in "abc" %}` agreed before and agrees now — the control
        both issues cite to localise the gap to regroup."""
        tpl = "{% for c in p %}[{{ c }}]{% endfor %}"
        django_out, djust_out = render_both(tpl, {"p": "abc"})
        assert django_out == djust_out == "[a][b][c]"


class TestTheDivergenceThatIsNotClosedHere:
    """A non-iterable source: Django RAISES, djust renders an empty region.

    Pinned rather than fixed (CLAUDE.md #1079 — fix what the issue cites).
    Neither issue asks for it, and it is not in the direction the fix must not
    move: djust renders LESS than Django, which errors. This test goes red the
    day someone makes regroup raise, and names itself as the thing to move.
    """

    @pytest.mark.parametrize("value", [5, True, 1.5])
    def test_a_non_iterable_source_renders_nothing_where_django_raises(self, value: object) -> None:
        tpl = SHAPE.replace("OPERAND", "p")
        with pytest.raises(TypeError, match="not iterable"):
            DjangoTemplate(tpl).render(DjangoContext({"p": value}))
        ctx = {"p": value}
        assert _rust.render_template_with_dirs(tpl, ctx, [], None) == "[0]"


class TestBothMechanismsAreReachable:
    """One test per mechanism, at the seam it owns (#1468, #2129/#2135).

    The fix has two halves in series, and each is separately load-bearing:

    * the RENDERER half — a resolved `Value::String` is JSON-encoded at a
      declared `RESOLVE_ARG_POSITIONS` position, so the handler can tell it
      from an unresolved raw token;
    * the HANDLER half — the decoded value is iterated with `list()` rather
      than matched against `list`/`tuple`.

    Exercised here below the template layer so a failure names which half
    moved, rather than only reporting that a cell diverged.
    """

    def test_the_renderer_hands_a_string_source_over_quoted(self) -> None:
        """The renderer half, observed at the handler's own boundary.

        `RegroupTagHandler` is registered with `RESOLVE_ARG_POSITIONS = {0}`,
        so the engine resolves position 0 and — since #2385 — JSON-encodes a
        string there. Asserted through a real render by giving the handler's
        `by` operand no match: what reaches `_decode_source` is what the
        grouping is built from.
        """
        captured: list[str] = []
        original = RegroupTagHandler._decode_source

        @classmethod  # type: ignore[misc]
        def spy(cls, expr, context):  # type: ignore[no-untyped-def]
            captured.append(expr)
            return original.__func__(cls, expr, context)  # type: ignore[attr-defined]

        RegroupTagHandler._decode_source = spy  # type: ignore[assignment]
        try:
            _rust.render_template_with_dirs(
                "{% regroup s by k as g %}[{{ g|length }}]", {"s": "ab"}, [], None
            )
            _rust.render_template_with_dirs(
                "{% regroup nope by k as g %}[{{ g|length }}]", {"s": "ab"}, [], None
            )
        finally:
            RegroupTagHandler._decode_source = original  # type: ignore[assignment]

        assert captured == ['"ab"', "nope"], (
            "the resolved string must arrive JSON-quoted and the unresolved "
            f"token bare — that difference IS the fix's renderer half: {captured!r}"
        )

    @pytest.mark.parametrize(
        "expr,expected",
        [
            ('"ab"', ["a", "b"]),  # a string iterates as characters
            ('{"a": 1, "b": 2}', ["a", "b"]),  # a dict iterates as keys
            ("[1, 2]", [1, 2]),  # a list is unchanged
            ("null", []),  # None is not iterable
            ("5", []),  # nor an int
            ("nope", []),  # nor a name that resolves to nothing
        ],
    )
    def test_the_handler_iterates_with_pythons_own_semantics(
        self, expr: str, expected: list
    ) -> None:
        """The handler half, called directly."""
        assert RegroupTagHandler._decode_source(expr, {}) == expected


class TestTheWiringIsLoadBearing:
    """The renderer half is reached through the declared-position branch.

    A pin on a `match` arm rather than on a count: `resolve_assign_tag_args`
    must route a DECLARED position through the value channel and an
    undeclared-policy handler through the historical one. If the two arms ever
    collapse back into one, every `RESOLVE_ARG_POSITIONS` handler silently
    loses the type tag again — the failure that has no symptom until a string
    source appears.
    """

    def test_a_declared_position_routes_through_the_value_channel(self) -> None:
        src = RENDERER.read_text()
        start = src.index("fn resolve_assign_tag_args(")
        body = src[start : src.index("\n}\n", start)]
        assert "Some(_) => resolve_tag_value_arg(arg, context)," in body, body
        assert "None => resolve_tag_arg(arg, context)," in body, body

    def test_only_a_string_is_re_encoded_by_the_value_channel(self) -> None:
        """`Decimal` and `BigInt` also serialize as JSON strings.

        Routing them through the string arm would tell the handler a `Decimal`
        is a sequence of characters, where Python raises. The arm is written
        against `Value::String` alone and this pins that it stays that way.
        """
        src = RENDERER.read_text()
        start = src.index("fn value_channel_arg_string(")
        body = src[start : src.index("\n}\n", start)]
        assert "Value::String(s) => serde_json::to_string(s)" in body, body
        for never in ("Value::Decimal", "Value::BigInt"):
            assert never not in body, (
                f"{never} must fall through to value_to_arg_string — its "
                f"JSON form is a string and would be iterated: {body}"
            )
