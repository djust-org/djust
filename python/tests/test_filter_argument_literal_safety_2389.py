"""A quoted literal in a FILTER ARGUMENT is `SafeData` (#2389).

The defect
----------
Django resolves a filter argument through `Variable` too, and
`FilterExpression.resolve` marks a CONSTANT one safe::

    for lookup, arg in args:
        if not lookup:
            arg_vals.append(mark_safe(arg))
        else:
            arg_vals.append(arg.resolve(context))

So `Variable('"<b>"').literal` is `mark_safe(unescape_string_literal(...))`,
`default` hands that object back unchanged, and
`render_value_in_context`'s `conditional_escape` leaves the markup LIVE::

    {{ p|default:"<b>" }}     p = ""
      django  '[<b>]'
      djust   '[&lt;b&gt;]'

Direction: OVER-escaping — a lost capability, never a leak. Which is why
#2389 was filed rather than folded into #2376.

Two premises the issue states that did not survive
--------------------------------------------------
1. *"Threading a grant through it is a signature change across all of
   them."* `apply_filter_full_safe` has taken `arg_was_quoted: bool` since
   #2202, `builtin_produced_safe` already receives it, and the `add` arm
   already reads it. The change is two arms and one `if`, not 57 signatures.

2. *"…filters that RETURN the argument (`default`, `default_if_none`,
   `yesno`, `join`'s separator, …)"*. Running all 57 built-ins against a
   hostile quoted-literal argument — rather than reading the bodies —
   corrects that list on both sides:

   * `yesno` and `pluralize` do NOT: they `str.split(",")` the argument, and
     splitting a `SafeString` yields plain `str`s. Measured: both engines
     escape.
   * `join` already agreed: `conditional_escape(arg)` leaves a `SafeString`
     separator alone, and djust already emitted it raw.
   * `json_script` DOES, and the issue's list does not name it. `_json_script`
     builds its tag with `format_html`, whose interpolation is
     `conditional_escape(element_id)`, so a literal id goes into the `id`
     attribute RAW.

   Three filters, found mechanically: `TestTheEnumerationIsMechanical` re-runs
   that sweep so a Django release that adds a fourth fails here rather than
   drifting.

The gate is the ARGUMENT'S OWN provenance
-----------------------------------------
`arg_was_quoted` is exactly Django's `not lookup`. A VARIABLE argument stays
escaped on both engines — measured across every built-in in
`TestTheVariableChannelIsUntouched`, which is the half that can carry
attacker data. A quoted literal lives in the template source, which its author
controls as completely as any raw HTML they type there.

Every expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.template.defaultfilters import register

from djust import _rust

XSS = "<img src=x onerror=alert(1)>"


def django(tpl: str, ctx: dict) -> str:
    return DjangoTemplate(tpl).render(DjangoContext(dict(ctx)))


def djust(tpl: str, ctx: dict) -> str:
    return _rust.render_template_with_dirs(tpl, dict(ctx), [], None)


def assert_agrees(tpl: str, ctx: dict) -> str:
    d, r = django(tpl, ctx), djust(tpl, ctx)
    assert r == d, f"{tpl!r} over {ctx!r}\n  django={d!r}\n  djust ={r!r}"
    return d


class TestAQuotedArgumentIsSafeData:
    """The three filters that let a constant argument reach the page."""

    @pytest.mark.parametrize("payload", ["<b>", XSS, "<b>x</b>", "&amp;", "'", "a<b"])
    def test_default_returns_the_literal_live(self, payload: str) -> None:
        out = assert_agrees('[{{ p|default:"%s" }}]' % payload, {"p": ""})
        assert out == f"[{payload}]", out

    @pytest.mark.parametrize("payload", ["<b>", XSS])
    def test_default_if_none_returns_the_literal_live(self, payload: str) -> None:
        out = assert_agrees('[{{ p|default_if_none:"%s" }}]' % payload, {"p": None})
        assert out == f"[{payload}]", out

    @pytest.mark.parametrize("payload", ["<b>", XSS])
    def test_json_script_puts_the_literal_id_in_raw(self, payload: str) -> None:
        out = assert_agrees('[{{ p|json_script:"%s" }}]' % payload, {"p": ""})
        assert f'id="{payload}"' in out, out

    def test_both_quote_spellings(self) -> None:
        """Django's `constant_arg` group accepts either quote."""
        assert assert_agrees("""[{{ p|default:'<b>' }}]""", {"p": ""}) == "[<b>]"
        assert assert_agrees('[{{ p|default:"<b>" }}]', {"p": ""}) == "[<b>]"

    def test_the_grant_composes_exactly_as_far_as_djangos_does(self) -> None:
        """`FilterExpression.resolve` re-marks a filter's OUTPUT only when
        that filter is `is_safe=True` AND its input was `SafeData`.

        Both directions measured rather than assumed, because a first draft of
        this test asserted the opposite of each:

        * `upper` is registered `is_safe=False` — uppercasing can turn
          `&amp;` into `&AMP;` — so the grant is LOST and the result escapes.
        * `lower` is `is_safe=True`, so it survives.
        """
        assert assert_agrees('[{{ p|default:"<b>"|upper }}]', {"p": ""}) == "[&lt;B&gt;]"
        assert assert_agrees('[{{ p|default:"<B>"|lower }}]', {"p": ""}) == "[<b>]"

    def test_escape_is_conditional_and_so_leaves_the_grant_alone(self) -> None:
        """Django's `escape` filter is `conditional_escape(value)`, not
        `html.escape` — so it is a NO-OP on `SafeData`. Asserted because the
        opposite is the intuitive reading and it is wrong."""
        assert assert_agrees('[{{ p|default:"<b>"|escape }}]', {"p": ""}) == "[<b>]"

    def test_force_escape_does_escape_it(self) -> None:
        """The filter that is unconditional. `force_escape` is
        `escape(value)`, so the grant does not survive it."""
        assert assert_agrees('[{{ p|default:"<b>"|force_escape }}]', {"p": ""}) == "[&lt;b&gt;]"


class TestTheBranchThatWasAlreadyRight:
    """`default`'s TRUTHY branch returns the INPUT object, not the argument.

    Two branches with two provenances, and the fix must not collapse them: a
    truthy input's safety is the input's, and only the falsy branch reads the
    argument's.
    """

    def test_a_truthy_unmarked_input_is_still_escaped(self) -> None:
        assert assert_agrees('[{{ p|default:"<b>" }}]', {"p": XSS}) == f"[{_esc(XSS)}]"

    def test_a_non_none_input_is_still_escaped_by_default_if_none(self) -> None:
        assert assert_agrees('[{{ p|default_if_none:"<b>" }}]', {"p": XSS}) == f"[{_esc(XSS)}]"


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


class TestTheVariableChannelIsUntouched:
    """A RESOLVED argument is a plain `str` in Django and must stay escaped.

    This is the half that can carry attacker data, and it is the whole reason
    the grant is gated on `arg_was_quoted` rather than on "the argument is a
    string". Swept across every built-in rather than sampled: the claim is
    about the CHANNEL, so one filter proving it would not.
    """

    def test_no_builtin_emits_a_resolved_argument_live(self) -> None:
        live_django, live_djust = [], []
        for name in sorted(register.filters):
            for value in ("", "abc", None, ["a", "b"], 5):
                tpl = "[{{ p|%s:q }}]" % name
                ctx = {"p": value, "q": XSS}
                for out, bucket in ((django, live_django), (djust, live_djust)):
                    try:
                        rendered = out(tpl, ctx)
                    except Exception:  # noqa: BLE001 — a raise is not a leak
                        continue
                    if XSS in rendered:
                        bucket.append((name, value, rendered))
        assert live_django == [], live_django
        assert live_djust == [], live_djust

    @pytest.mark.parametrize(
        "tpl", ["[{{ p|default:q }}]", "[{{ p|default_if_none:q }}]", "[{{ p|json_script:q }}]"]
    )
    def test_the_three_granted_filters_escape_a_variable_argument(self, tpl: str) -> None:
        out = assert_agrees(tpl, {"p": None if "if_none" in tpl else "", "q": "<b>"})
        assert "<b>" not in out, out
        assert "&lt;b&gt;" in out, out


class TestTheEnumerationIsMechanical:
    """Which filters let a constant argument through is DERIVED, not listed.

    A curated list samples one axis and blinds you on the next — that is how
    the issue's own candidate list came to name `yesno` (which does not) and
    miss `json_script` (which does). This re-runs the derivation against the
    LIVE registry, so a Django release that adds a fourth such filter fails
    here rather than drifting silently.
    """

    #: FOUR, not the three #2389 changes. `join` is the one djust ALREADY
    #: agreed on — `conditional_escape(arg)` leaves the separator alone and
    #: djust's `join` arm never escaped it — so it belongs in the derived set
    #: even though nothing about it moved. Leaving it out to match the diff
    #: would make this a transcript of the fix rather than of Django.
    GRANTED = {"default", "default_if_none", "join", "json_script"}

    def test_django_emits_a_literal_argument_live_for_exactly_these(self) -> None:
        emits_live = set()
        for name in sorted(register.filters):
            for value in ("", "abc", None, ["a", "b"], 5, True):
                tpl = '[{{ p|%s:"%s" }}]' % (name, XSS)
                try:
                    rendered = django(tpl, {"p": value})
                except Exception:  # noqa: BLE001
                    continue
                if XSS in rendered:
                    emits_live.add(name)
        assert emits_live == self.GRANTED, (
            f"live Django's set moved: {sorted(emits_live)} vs {sorted(self.GRANTED)}"
        )

    def test_djust_agrees_on_every_one_of_them(self) -> None:
        for name in sorted(register.filters):
            for value in ("", "abc", None, ["a", "b"], 5, True):
                tpl = '[{{ p|%s:"%s" }}]' % (name, XSS)
                try:
                    expected = django(tpl, {"p": value})
                except Exception:  # noqa: BLE001
                    continue
                if XSS not in expected:
                    continue
                assert djust(tpl, {"p": value}) == expected, tpl

    def test_the_named_non_members_really_do_split_their_argument(self) -> None:
        """`yesno` and `pluralize` are in the issue's candidate list and are
        NOT members — `str.split` on a `SafeString` yields plain `str`s."""
        assert assert_agrees('[{{ p|yesno:"<b>,<i>" }}]', {"p": True}) == "[&lt;b&gt;]"
        assert assert_agrees('[{{ p|pluralize:"<b>,<i>" }}]', {"p": 2}) == "[&lt;i&gt;]"

    def test_join_already_agreed_and_still_does(self) -> None:
        """`conditional_escape(arg)` leaves a `SafeString` separator alone, so
        this cell was correct before #2389 and must stay correct."""
        assert assert_agrees('[{{ p|join:"<b>" }}]', {"p": ["a", "b"]}) == "[a<b>b]"
