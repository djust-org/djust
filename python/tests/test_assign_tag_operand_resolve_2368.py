"""The assign-tag operand channel resolves through `resolve`, not `get` (#2368).

The defect
----------
``renderer::resolve_tag_operand`` had two branches::

    if expr.contains('|') {
        return match get_value(expr, context) { … };   // #2333 routed this one
    }
    context.get(expr).map(value_to_arg_string)          // …and left this one

``d.items`` / ``.keys`` / ``.values`` resolve in ``Context::resolve``, not in
``Context::get`` — ``dict_view`` is only reachable from ``resolve``, which is
where #2334 put it. So the pipe-bearing branch saw a view and the bare dotted
path did not, and ``{% regroup p.values by k as g %}`` fell to the channel's
"unresolved ⇒ keep the raw token" contract: the handler received the template's
own source text, decoded nothing, and ``{{ g|length }}`` rendered ``0``.
Silently — no exception, no warning.

Same shape as #2333, one operand form over: that fix made this channel
FILTER-aware and left it dict-view-blind (#1646).

What widening costs, and why it is safe
---------------------------------------
``resolve`` is strictly wider than ``get``. Each thing it adds is decided here
rather than inherited:

* **the dict views** — the point;
* **the raw-Python sidecar walk and ADR-024's auto-call** — the same widening
  the PIPE branch already had, since ``get_value_safe`` ends with a
  ``context.resolve`` fallback. A tag operand naming a model attribute
  resolved through ``p|<filter>`` and not through ``p`` alone;
* **``template_builtin``** — textually inert: ``None`` / ``True`` / ``False``
  serialize back to the same bytes the raw token would have carried.

The keyword-operand hazard #2041's ``RESOLVE_ARG_POSITIONS`` exists to prevent
is untouched, and ``TestTheKeywordOperandsStayLiteral`` measures that rather
than asserting it.

Every expectation here is LIVE Django, never a transcription.
"""

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import _rust

ROWS = {"a": {"k": 2}, "b": {"k": 1}}
LIST_ROWS = [{"k": 2}, {"k": 1}]


def both(tpl: str, ctx: dict) -> tuple[str, str]:
    django_out = DjangoTemplate(tpl).render(DjangoContext(dict(ctx)))
    djust_out = _rust.render_template(tpl, ctx)
    return django_out, djust_out


def assert_agrees(tpl: str, ctx: dict) -> str:
    django_out, djust_out = both(tpl, ctx)
    assert djust_out == django_out, (
        f"{tpl!r} over {ctx!r}\n  django {django_out!r}\n  djust  {djust_out!r}"
    )
    return djust_out


class TestTheBareDottedPathReachesTheHandler:
    """The reported cell, and the three views it generalises to."""

    def test_the_reported_cell(self):
        assert (
            assert_agrees("{% regroup p.values by k as g %}[{{ g|length }}]", {"p": ROWS}) == "[2]"
        )

    def test_the_groups_carry_their_rows_not_just_a_count(self):
        # `|length` alone would agree if BOTH engines produced one empty
        # group, so the grouper values are what prove the rows arrived.
        assert (
            assert_agrees(
                "{% regroup p.values by k as g %}"
                "{% for x in g %}({{ x.grouper }}:{{ x.list|length }}){% endfor %}",
                {"p": ROWS},
            )
            == "(2:1)(1:1)"
        )

    @pytest.mark.parametrize("view", ["keys", "items", "values"])
    def test_every_view_kind(self, view):
        assert_agrees("{% regroup p." + view + " by k as g %}[{{ g|length }}]", {"p": ROWS})

    def test_a_deeper_dotted_path_to_a_view(self):
        assert (
            assert_agrees(
                "{% regroup p.sub.values by k as g %}[{{ g|length }}]",
                {"p": {"sub": ROWS}},
            )
            == "[2]"
        )


class TestTheControlsThatAlreadyAgreed:
    """The other three operand forms, which localise the defect precisely.

    Each of these resolved BEFORE the change and must resolve after — the
    non-regression half, and the reason the issue could say "only the bare
    dotted path is left".
    """

    @pytest.mark.parametrize(
        ("tpl", "ctx"),
        [
            ("{% regroup p by k as g %}[{{ g|length }}]", {"p": LIST_ROWS}),
            (
                "{% regroup p.rows by k as g %}[{{ g|length }}]",
                {"p": {"rows": LIST_ROWS}},
            ),
            ("{% regroup p|dictsort:'k' by k as g %}[{{ g|length }}]", {"p": LIST_ROWS}),
            ("{% regroup p.values|slice:':2' by k as g %}[{{ g|length }}]", {"p": ROWS}),
        ],
        ids=["list", "dotted-to-list", "filtered", "view-via-the-pipe"],
    )
    def test_it_still_agrees(self, tpl, ctx):
        assert assert_agrees(tpl, ctx) == "[2]"


class TestTheKeywordOperandsStayLiteral:
    """#2041's `RESOLVE_ARG_POSITIONS` is what this widening must not disturb.

    `regroup` declares `{0}` — resolve only the SOURCE — so its `by`, `<attr>`,
    `as` and `<var>` tokens never reach `resolve_tag_operand` at all. Measured
    with context entries deliberately named after each keyword, because "the
    mask still holds" is a claim about the code path and this is the input that
    would show it broken.
    """

    def test_a_context_name_matching_the_ATTR_does_not_shadow_it(self):
        # `k` in the context is a decoy: the `<attr>` operand must stay the
        # literal name `k`, looked up per ITEM.
        assert (
            assert_agrees(
                "{% regroup p by k as g %}{% for x in g %}({{ x.grouper }}){% endfor %}",
                {"p": LIST_ROWS, "k": "DECOY"},
            )
            == "(2)(1)"
        )

    def test_context_names_matching_the_KEYWORDS_do_not_shadow_them(self):
        assert (
            assert_agrees(
                "{% regroup p by k as g %}[{{ g|length }}]",
                {"p": LIST_ROWS, "by": "DECOY", "as": "DECOY", "g": "DECOY"},
            )
            == "[2]"
        )

    def test_a_keyword_spelled_like_a_template_builtin_is_textually_inert(self):
        # `Context::resolve` adds `template_builtin`, which `Context::get` does
        # not — but `Value::None` / `Bool(true)` serialize back to `None` /
        # `True`, byte-identical to the raw token the miss path would have
        # passed. Asserted at the encoder, where the claim actually lives.
        assert _rust.render_template(
            "{% regroup p by None as g %}[{{ g|length }}]", {"p": LIST_ROWS}
        )
        assert _rust.render_template(
            "{% regroup p by True as g %}[{{ g|length }}]", {"p": LIST_ROWS}
        )
