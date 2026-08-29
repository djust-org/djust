"""A quoted or numeric LITERAL resolves, and a quoted one is `SafeData` (#2376).

The defect
----------
djust had two resolvers that could see a bare token, and only one of them knew
what a literal is:

* ``renderer::get_value_safe`` — the ``{% if %}`` / ``{% with %}`` /
  ``{% firstof %}`` / ``{% cycle %}`` operand channel — had an int arm, a float
  arm and a quote-strip arm;
* ``Node::Variable`` / ``Node::InlineIf`` — the EMIT arms — had none at all.

So ``{% if "<b>" %}`` was right and ``{{ "<b>" }}`` rendered the **empty
string**: the text vanished rather than appearing escaped. The same was true
of ``{{ 5 }}`` and ``{{ 5.5 }}``, which is the half the issue title does not
name — the defect is the whole literal surface, not the quoted spelling.
Exactly #2347's two-resolvers-one-blind split, three literal kinds over
(#1646).

And the half that survived also had a defect: Django's ``Variable.__init__``
ends its quoted branch with
``self.literal = mark_safe(unescape_string_literal(var))``, so a quoted
literal is ``SafeData`` and ``{{ "<b>" }}`` renders LIVE markup. Resolving the
literal WITHOUT that grant renders ``&lt;b&gt;`` — a third answer, neither the
bug's nor Django's — which is why both halves are one change and one function
(``django_literal``).

Direction
---------
Marking the literal safe is not a new attack surface: the string is the
TEMPLATE AUTHOR's own source text, never context data. A template assembled
from user input is already an RCE, in Django exactly as here. The
``…|upper``/``…|striptags`` cases below pin that the grant RE-TAINTS through
the filter chain exactly as Django's does, so the seed cannot be read as a
blanket "literals are always live".

Every expectation here is LIVE Django, never a transcription.
"""

import pathlib
import re

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import _rust

REPO = pathlib.Path(__file__).resolve().parents[2]


def render_both(tpl: str, ctx: dict | None = None) -> tuple[str, str]:
    ctx = ctx or {}
    django_out = DjangoTemplate(tpl).render(DjangoContext(dict(ctx)))
    djust_out = _rust.render_template(tpl, ctx)
    return django_out, djust_out


def assert_agrees(tpl: str, ctx: dict | None = None) -> str:
    django_out, djust_out = render_both(tpl, ctx)
    assert djust_out == django_out, (
        f"{tpl!r} over {ctx!r}\n  django {django_out!r}\n  djust  {djust_out!r}"
    )
    return djust_out


class TestTheEmitArmResolvesALiteralAtAll:
    """The correctness half, and it is the whole literal surface."""

    @pytest.mark.parametrize(
        ("tpl", "expected"),
        [
            ('[{{ "hello" }}]', "[hello]"),
            ("[{{ 'hello' }}]", "[hello]"),
            ('[{{ "" }}]', "[]"),
            ("[{{ 5 }}]", "[5]"),
            ("[{{ 5.5 }}]", "[5.5]"),
            ("[{{ -5 }}]", "[-5]"),
            ("[{{ +5 }}]", "[5]"),
            ("[{{ .5 }}]", "[0.5]"),
            ("[{{ 1e3 }}]", "[1000.0]"),
            # Python's `int()` has no width. Past `i64` this is a `Value::BigInt`.
            ("[{{ 99999999999999999999999 }}]", "[99999999999999999999999]"),
        ],
    )
    def test_a_bare_literal_renders_what_django_renders(self, tpl, expected):
        assert assert_agrees(tpl) == expected

    @pytest.mark.parametrize(
        ("tpl", "expected"),
        [
            ('[{{ "a"|upper }}]', "[A]"),
            ("[{{ 5|add:1 }}]", "[6]"),
            ('[{{ "5"|add:1 }}]', "[6]"),
            ('[{{ "<b>"|length }}]', "[3]"),
        ],
    )
    def test_a_literal_composes_with_a_filter(self, tpl, expected):
        # `{{ "a"|upper }}` was EMPTY before this, because the value never
        # resolved — the chain then ran over `Value::Missing`.
        assert assert_agrees(tpl) == expected

    def test_the_escaped_quote_and_backslash_are_unescaped_djangos_way(self):
        # `unescape_string_literal` is `.replace(r"\<quote>", quote)` then
        # `.replace(r"\\", "\\")`.
        assert assert_agrees(r'[{{ "a\"b" }}]') == '[a"b]'
        assert assert_agrees(r'[{{ "a\\b" }}]') == "[a\\b]"

    def test_the_unescape_order_is_unobservable(self):
        """Why there is no test pinning the ORDER of the two replaces.

        Gating the order off SURVIVES the suite, and a surviving mutation is a
        question with three answers — an equivalent mutation, two mechanisms
        shadowing each other, or genuinely missing coverage. This test answers
        it: the order is equivalent for every token Django can build.

        Exhaustively, over every string on ``{a, \\, "}`` up to length 6: the
        two orders differ for some, and Django's own ``FilterExpression``
        parses NONE of those — each distinguishing shape contains ``\\\\"``,
        where ``strdq`` terminates the constant and the remainder fails.
        """
        import itertools

        q = '"'

        def order_a(inner):  # Django's
            return inner.replace("\\" + q, q).replace("\\\\", "\\")

        def order_b(inner):  # the mutation
            return inner.replace("\\\\", "\\").replace("\\" + q, q)

        differ = [
            "".join(t)
            for n in range(7)
            for t in itertools.product("a\\" + q, repeat=n)
            if order_a("".join(t)) != order_b("".join(t))
        ]
        # Non-vacuous: the two orders really are different functions.
        assert differ, "the two orders never differ — this test proves nothing"

        parsed = []
        for inner in differ:
            try:
                DjangoTemplate("[{{ " + q + inner + q + " }}]").render(DjangoContext({}))
            except Exception:  # noqa: BLE001, S110 — an unparsable token is the point
                continue
            parsed.append(inner)
        assert parsed == [], (
            "Django now parses a token whose unescaping depends on the replace "
            f"order ({parsed[:3]!r}); the order has become observable and needs "
            "its own parity test."
        )

    def test_a_number_is_a_literal_before_it_is_a_context_key(self):
        # `Variable.__init__` runs at COMPILE time, so a numeric token never
        # becomes a lookup at all and a context key spelled `5` does NOT
        # shadow it. djust resolved the key's value here — measured against
        # Django 5.2.16, which renders `5`.
        assert assert_agrees("[{{ 5 }}]", {"5": "ctxwins"}) == "[5]"


class TestTheLiteralCarriesDjangosGrant:
    """`mark_safe(unescape_string_literal(var))` — the second half."""

    @pytest.mark.parametrize(
        "tpl",
        ['[{{ "<b>x</b>" }}]', "[{{ '<b>x</b>' }}]"],
        ids=["double", "single"],
    )
    def test_a_quoted_literal_is_live_markup(self, tpl):
        assert assert_agrees(tpl) == "[<b>x</b>]"

    @pytest.mark.parametrize(
        ("tpl", "expected"),
        [
            # `escape` is `conditional_escape`, which returns SafeData
            # UNCHANGED — so the literal comes through live.
            ('[{{ "<b>"|escape }}]', "[<b>]"),
            ('[{{ "<b>"|safe }}]', "[<b>]"),
            ('[{{ "<b>"|default:"x" }}]', "[<b>]"),
            ('[{{ "<b>"|linebreaksbr }}]', "[<b>]"),
        ],
    )
    def test_a_safe_output_filter_keeps_it_live(self, tpl, expected):
        assert assert_agrees(tpl) == expected

    @pytest.mark.parametrize(
        ("tpl", "expected"),
        [
            # `upper` is registered `is_safe=False` in Django, precisely
            # because upper-casing `&lt;` yields `&LT;`.
            ('[{{ "<b>"|upper }}]', "[&lt;B&gt;]"),
            ('[{{ "<b>"|force_escape }}]', "[&lt;b&gt;]"),
        ],
    )
    def test_a_re_tainting_filter_takes_the_grant_away(self, tpl, expected):
        # This is the pin that keeps the seed from being read as "a literal is
        # always live": the grant feeds the CHAIN and the last filter decides.
        assert assert_agrees(tpl) == expected

    def test_a_number_is_not_granted(self):
        # Django `mark_safe`s only the quoted branch. Nothing observable
        # depends on it for a bare number — there is no markup — so the claim
        # is made where it IS observable: `stringformat` produces a plain str
        # from the number and the result is escaped like any other value.
        assert assert_agrees("[{{ 5|stringformat:'s' }}]") == "[5]"


class TestTheTagOperandChannelAgreesWithTheEmitArm:
    """The other resolver, which is why this is ONE function and not two.

    These four already RESOLVED a literal before this change and none of them
    granted it, so each rendered `&lt;b&gt;` where Django renders `<b>`. They
    are the non-regression half AND the half the shared helper fixes for free.
    """

    @pytest.mark.parametrize(
        ("tpl", "expected"),
        [
            ('{% with q="<b>" %}[{{ q }}]{% endwith %}', "[<b>]"),
            ('{% firstof "<b>" %}', "<b>"),
            ("{% cycle '<b>' 'z' %}", "<b>"),
            ('{% if "<b>" %}Y{% else %}N{% endif %}', "Y"),
            ('{% for x in "<b>" %}[{{ x }}]{% endfor %}', "[&lt;][b][&gt;]"),
        ],
    )
    def test_the_operand_channel_matches_django(self, tpl, expected):
        assert assert_agrees(tpl) == expected


class TestTheInlineIfArmAgreesWithTheEmitArm:
    """`{{ a if c else b }}` is a djust EXTENSION — Django raises for it.

    So there is no Django answer to compare against, and the claim has to be
    stated as an internal one: the inline-if arm is the SECOND emit site, it
    reads the same expression grammar, and it must resolve and grant a literal
    exactly as `Node::Variable` does. Before this change it called `get_value`,
    which DISCARDS the safety bool, and seeded from `context.is_safe(expr)` —
    which has nothing to say about a token that is not a name.

    Without this class the seed change would have no test that goes red when
    only that mechanism is removed (#2129/#2135).
    """

    @pytest.mark.parametrize(
        ("tpl", "expected"),
        [
            ('[{{ "<b>x</b>" if p }}]', "[<b>x</b>]"),
            ('[{{ "y" if q else "<i>n</i>" }}]', "[<i>n</i>]"),
            ("[{{ 5 if p }}]", "[5]"),
            # Re-taint, the same way the emit arm does it. The filters bind to
            # the WHOLE inline-if in this grammar (the parser splits on `|`
            # before it looks for `if`), so the chain is spelled at the end.
            ('[{{ "<b>" if p|upper }}]', "[&lt;B&gt;]"),
        ],
    )
    def test_it_resolves_and_grants_the_same_literal(self, tpl, expected):
        assert _rust.render_template(tpl, {"p": 1, "q": 0}) == expected

    def test_a_context_name_is_unchanged_by_the_seed_swap(self):
        # The non-regression half: for every expression that is NOT a literal,
        # `get_value_safe`'s bool IS `context.is_safe(expr)`, so this arm must
        # answer exactly what it answered before.
        assert _rust.render_template("[{{ p if p }}]", {"p": "<b>"}) == "[&lt;b&gt;]"


class TestTheGateDjangoKeepsClosed:
    """A token that only LOOKS numeric stays a variable lookup.

    `inf` and `nan` are the sharp ones: `float()` accepts BOTH, and so does
    Rust's `f64` parser — but Django reaches `float()` only when the token
    carries a `.` or an `e`, and neither does. They take the `int()` arm, fail,
    and resolve as variables. A recognizer that parsed `f64` first would
    silently turn a variable named `inf` into a float.
    """

    @pytest.mark.parametrize(
        "tpl",
        ["[{{ inf }}]", "[{{ nan }}]", "[{{ Infinity }}]", "[{{ 0x10 }}]", "[{{ 5. }}]"],
    )
    def test_it_renders_empty_in_both_engines(self, tpl):
        assert assert_agrees(tpl) == "[]"

    def test_such_a_name_still_resolves_from_the_context(self):
        # The point of the gate: `inf` is a NAME, so a context entry answers.
        assert assert_agrees("[{{ inf }}]", {"inf": "from-context"}) == "[from-context]"

    @pytest.mark.parametrize("name", ["email", "value", "e", "user.email"])
    def test_an_ordinary_name_carrying_an_e_or_a_dot_is_not_a_number(self, name):
        # These all take Django's `float()` branch (they contain `e` or `.`)
        # and fail there. If the branch were skipped or widened they would
        # become literals and stop reading the context.
        assert assert_agrees("[{{ " + name + " }}]", {"email": "x", "value": "y", "e": "z"})


class TestKnownNarrowerThanDjango:
    """Pinned rather than left silent — both are the over-narrow direction.

    A literal recognizer may only fail by refusing to invent a value Django
    would not; each of these renders EMPTY, which is what it rendered before
    `django_literal` existed.
    """

    def test_pythons_digit_separator_is_not_read(self):
        # `float("1_000")` / `int("1_000")` are 1000 in Python; Rust's parsers
        # reject the underscore.
        django_out, djust_out = render_both("[{{ 1_000 }}]")
        assert django_out == "[1000]"
        assert djust_out == "[]"

    def test_a_literal_in_a_FILTER_ARGUMENT_is_not_granted(self):
        # Django resolves a filter argument through `Variable` too, so
        # `"<b>"` arrives at `default` as a `SafeString` and comes back out
        # live. djust's filter-argument channel carries a `&str` with no
        # safety beside it — a THIRD resolver, and a signature change across
        # every filter arm rather than an arm in this one. Over-escaping, so
        # the safe direction. Filed separately per #1079.
        django_out, djust_out = render_both('[{{ p|default:"<b>" }}]', {"p": ""})
        assert django_out == "[<b>]"
        assert djust_out == "[&lt;b&gt;]"


class TestTheCorpusGapThatHidThisFromTheDifferential:
    """Every cell outside the builtin axis writes `p` as the expression.

    So the corpus could not construct a bare literal in the VALUE position at
    all, and the tool reported clean over the whole of this — the fifth time
    (#2281, #2325, #2334, #2377). The `@builtin` axis is where #2347 put
    `True`/`False`/`None` for exactly this reason; the quoted and numeric
    literals belong beside them.
    """

    def test_the_literal_tokens_are_on_the_builtin_axis(self):
        src = (REPO / "scripts" / "filter-parity-differential.py").read_text(encoding="utf-8")
        block = src.split("BUILTIN_NAMES = [", 1)[1].split("]", 1)[0]
        # Read what the axis actually SWEEPS, not the names of the entries.
        assert re.search(r"""["']\s*\\?["'][^"']*<""", block) or any(
            q in block for q in ('"<b>ok</b>"', "'<script>alert(1)</script>'")
        ), "the builtin axis carries no QUOTED literal, so #2376's grant half is unmeasured"
        assert re.search(r"^\s*\"5\.5\"", block, re.M), (
            "the builtin axis carries no NUMERIC literal, so the half of #2376 "
            "that was not in the issue title is unmeasured"
        )
