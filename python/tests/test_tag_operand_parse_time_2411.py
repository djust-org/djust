"""A tag operand's filter chain is compiled at PARSE time, as Django does (#2411).

The defect
----------
Django compiles every tag operand's filter chain with ``compile_filter`` while
the template is being COMPILED, so a wrong argument count (#2400), a lexer
remainder (#2409) or an unparseable spec refuses the template before any value
is resolved. djust reached the chain only at RENDER time, left to right, in
``renderer::get_value_safe`` — and ``{% if %}`` legitimately absorbs a
``VariableDoesNotExist`` (``evaluate_condition_for_if``). So an EARLIER step
that failed to resolve made the condition falsy before the LATER filter's
refusal was ever reached::

    {% if p|cut %}          django  <<TemplateSyntaxError: cut requires 2 arguments, 1 provided>>
                            djust   <<RuntimeError: cut requires 2 arguments, 1 provided>>  agrees

    {% if p|date:.|cut %}   django  <<TemplateSyntaxError: cut requires 2 arguments, 1 provided>>
                            djust   ''                                                      masked

Narrowing the swallow is NOT the fix, and the issue's own framing said so for
the wrong reason. The measured reason is that **Django swallows the very same
thing**: ``IfNode.render`` wraps the whole ``condition.eval(context)`` — filter
arguments included — in ``except VariableDoesNotExist``, so
``{% if p|date:missingvar %}`` renders the false branch on BOTH engines.
``TestDjangoSwallowsResolutionFailuresToo`` pins that, because it is the
premise the whole fix shape rests on.

Two more shapes have nothing to do with the swallow and are closed by the same
move — which is why the check lives at parse time rather than at the
``{% if %}`` render arm::

    {% if 0 and p|cut %}                            short-circuit: never evaluated
    {% if 0 %}{% for x in p|cut %}{% endfor %}{% endif %}   a branch that never renders

One validator, two times it runs
--------------------------------
``parser::validate_tag_operand`` calls ``parse_filter_specs`` — the SAME
function ``{{ … }}`` has always run at parse time — rather than restating its
rules (#1646). ``TestTheCallerSetIsPinned`` pins the caller SET, not a floor
(#1125/#2233): the next operand-bearing tag that forgets the call fails here.

What is deliberately NOT closed
-------------------------------
``Invalid filter`` (#2419) and ``Variables and attributes may not begin with
underscores`` (#2418) stay masked, and ``TestTheTwoRulesThisDoesNotClose`` pins
them as known-open with the evidence for why each is a SEPARATE defect rather
than part of this one. Measured over a 13,202-template sweep, this change moves 775 cells
from "djust renders what Django refuses" to agreement and moves none the other
way; the ``arity``, ``remainder`` and ``some-characters`` buckets go to zero.

Every expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.template import TemplateSyntaxError

from djust import _rust

#: ``_x`` is BOUND on purpose: it is what separates the underscore rule (a rule
#: djust does not have anywhere) from the masking this issue is about.
CTX: dict[str, object] = {"p": "a b c", "q": 2, "_x": 9}

PARSER_RS = (
    pathlib.Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "parser.rs"
)


def django_renders(source: str) -> bool:
    """Does Django produce output for this template — compile AND render."""
    try:
        DjangoTemplate(source).render(DjangoContext(dict(CTX)))
    except Exception:  # noqa: BLE001 — any refusal is a refusal
        return False
    return True


def djust_renders(source: str) -> tuple[bool, str]:
    try:
        return True, _rust.render_template(source, dict(CTX))
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    except BaseException as exc:  # noqa: BLE001 — a Rust panic is not a refusal
        return False, f"PANIC {exc}"


# ---------------------------------------------------------------------------
# The premise the fix shape rests on, read off Django rather than asserted
# ---------------------------------------------------------------------------


class TestDjangoSwallowsResolutionFailuresToo:
    """``ignore_failures`` is not the difference — TIME is.

    If Django refused ``{% if p|date:missingvar %}``, the fix would be to
    narrow djust's catch. It does not, so the only thing left to move is WHEN
    the refusal happens.
    """

    def test_django_renders_the_false_branch_for_an_unresolvable_argument(self) -> None:
        out = DjangoTemplate("{% if p|date:missingvar %}Y{% else %}N{% endif %}").render(
            DjangoContext(dict(CTX))
        )
        assert out == "N", "premise: Django's IfNode.render absorbs this too"

    def test_djust_agrees_on_that_cell(self) -> None:
        rendered, out = djust_renders("{% if p|date:missingvar %}Y{% else %}N{% endif %}")
        assert (rendered, out) == (True, "N")

    def test_one_more_filter_flips_django_to_a_COMPILE_time_refusal(self) -> None:
        """The whole issue in one assertion: the only difference between the
        two templates is a filter Django never resolves."""
        with pytest.raises(TemplateSyntaxError, match="cut requires 2 arguments"):
            DjangoTemplate("{% if p|date:missingvar|cut %}Y{% endif %}")


# ---------------------------------------------------------------------------
# The cells the issue names
# ---------------------------------------------------------------------------

#: Each carries a DIFFERENT Django compile-time refusal behind the same
#: unresolvable leading argument. `.` is a legal `var_arg` for Django's lexer,
#: so both engines accept the spelling and neither resolves it.
MASKED_CELLS = [
    # (operand, the substring of Django's refusal djust must now also report)
    ("p|date:.|cut", "cut requires 2 arguments"),
    ("p|date:.|add", "add requires 2 arguments"),
    ("p|date:.|default", "default requires 2 arguments"),
    ("p|date:.|join", "join requires 2 arguments"),
    ('p|date:.|cut:"a":"b"', "Could not parse the remainder"),
    ('p|date:missingvar|truncatewords:"1":"2"', "Could not parse the remainder"),
]


class TestAnUnresolvableArgumentNoLongerMasksTheRefusal:
    @pytest.mark.parametrize(("operand", "reason"), MASKED_CELLS)
    def test_django_refuses_at_compile_time(self, operand: str, reason: str) -> None:
        with pytest.raises(TemplateSyntaxError, match=re.escape(reason)):
            DjangoTemplate("{%% if %s %%}Y{%% endif %%}" % operand)

    @pytest.mark.parametrize(("operand", "reason"), MASKED_CELLS)
    def test_djust_refuses_it_too_and_for_the_same_reason(self, operand: str, reason: str) -> None:
        rendered, out = djust_renders("{%% if %s %%}Y{%% endif %%}" % operand)
        assert not rendered, f"still masked — djust rendered {out!r}"
        assert reason in out, out

    @pytest.mark.parametrize(("operand", "reason"), MASKED_CELLS)
    def test_the_same_operand_in_elif(self, operand: str, reason: str) -> None:
        """``{% elif %}`` builds its ``Node::If`` at a DIFFERENT parse site, so
        a fix wired only into the ``"if"`` arm passes every test above and
        leaves this one masked."""
        source = "{%% if 0 %%}A{%% elif %s %%}B{%% endif %%}" % operand
        assert not django_renders(source), "premise"
        rendered, out = djust_renders(source)
        assert not rendered, f"elif still masked — djust rendered {out!r}"
        assert reason in out, out


# ---------------------------------------------------------------------------
# The two shapes that are NOT about the swallow, and that only a parse-time
# check can reach
# ---------------------------------------------------------------------------


class TestShapesNoRenderTimeFixCouldReach:
    """These have no unresolvable argument at all. They were masked because the
    operand is never EVALUATED, so a fix that walked the rest of the chain
    after a resolution failure would leave every one of them open."""

    @pytest.mark.parametrize(
        "source",
        [
            "{% if 0 and p|cut %}Y{% else %}N{% endif %}",
            "{% if 1 or p|cut %}Y{% else %}N{% endif %}",
        ],
    )
    def test_a_short_circuited_operand_is_still_compiled(self, source: str) -> None:
        assert not django_renders(source), "premise"
        rendered, out = djust_renders(source)
        assert not rendered, f"short-circuit still masked — djust rendered {out!r}"
        assert "cut requires 2 arguments" in out, out

    @pytest.mark.parametrize(
        "inner",
        [
            "{% if p|cut %}Y{% endif %}",
            "{% for x in p|cut %}Y{% endfor %}",
            "{% with v=p|cut %}Y{% endwith %}",
            '{% if p|cut:"a":"b" %}Y{% endif %}',
            '{% for x in p|cut:"a":"b" %}Y{% endfor %}',
            '{% with v=p|cut:"a":"b" %}Y{% endwith %}',
        ],
    )
    def test_a_branch_that_never_renders_is_still_compiled(self, inner: str) -> None:
        """Django compiles the whole template; djust reached a tag operand only
        by rendering it. This is the ``{% for %}`` / ``{% with %}`` half — the
        same missing parse-time call, one tag over (#1646)."""
        source = "{%% if 0 %%}%s{%% endif %%}" % inner
        assert not django_renders(source), "premise"
        rendered, out = djust_renders(source)
        assert not rendered, f"dead branch still masked — djust rendered {out!r}"


# ---------------------------------------------------------------------------
# It must not become STRICTER than Django
# ---------------------------------------------------------------------------

#: Everything here compiles on Django and must keep rendering here. The quoted
#: cells are the ones a naive "split on | and :" validator gets wrong.
MUST_STILL_RENDER = [
    "{% if p|upper %}Y{% else %}N{% endif %}",
    "{% if p|date:. %}Y{% else %}N{% endif %}",
    "{% if p|date:missingvar %}Y{% else %}N{% endif %}",
    "{% if p|length > 2 %}Y{% else %}N{% endif %}",
    "{% if p|length >= 2 and q|add:1 %}Y{% else %}N{% endif %}",
    "{% if not p|upper %}Y{% else %}N{% endif %}",
    '{% if p|default:"x" == "a b c" %}Y{% else %}N{% endif %}',
    '{% if "a|b"|upper %}Y{% else %}N{% endif %}',
    '{% if p|cut:"a|b" %}Y{% else %}N{% endif %}',
    '{% if p|cut:":" %}Y{% else %}N{% endif %}',
    '{% if p|date:"H:i" %}Y{% else %}N{% endif %}',
    '{% if p|join:", " %}Y{% else %}N{% endif %}',
    '{% if "x" in p|cut:"a" %}Y{% else %}N{% endif %}',
    "{% if 0 %}A{% elif p|upper %}B{% else %}C{% endif %}",
    '{% for z in p|slice:":2" %}[{{ z }}]{% endfor %}',
    '{% with v=p|cut:"a" %}{{ v }}{% endwith %}',
    '{% with v=p|date:"H:i" %}{{ v }}{% endwith %}',
]


class TestItIsNotStricterThanDjango:
    @pytest.mark.parametrize("source", MUST_STILL_RENDER)
    def test_django_compiles_it(self, source: str) -> None:
        assert django_renders(source), "premise: Django must accept this"

    @pytest.mark.parametrize("source", MUST_STILL_RENDER)
    def test_djust_still_renders_it(self, source: str) -> None:
        rendered, out = djust_renders(source)
        assert rendered, f"over-refused: {out}"

    def test_no_django_if_operator_word_is_refusable_as_an_operand(self) -> None:
        """``validate_if_operands`` runs over EVERY token rather than skipping
        Django's operator words, because it can: an operator carries no
        unquoted ``|``, so the validator is a no-op on it. This is why there is
        no operator-set constant to drift — the claim is checked against
        Django's live ``smartif.OPERATORS`` instead of a transcription."""
        from django.template.smartif import OPERATORS  # noqa: PLC0415

        for word in {w for key in OPERATORS for w in key.split()}:
            source = "{%% if p %s p %%}Y{%% else %%}N{%% endif %%}" % word
            rendered, out = djust_renders(source)
            assert rendered, f"operator {word!r} was refused as an operand: {out}"


# ---------------------------------------------------------------------------
# The caller SET, not a floor
# ---------------------------------------------------------------------------


class TestTheCallerSetIsPinned:
    """Grep the SINK. Enumerating "the tags I know carry an operand" is
    reliably one short — the ``{% for %}`` and ``{% with %}`` sites were found
    by measuring, not by listing (#2233 / the v1.1.1-2 sink rule)."""

    def test_the_validator_is_called_exactly_where_it_should_be(self) -> None:
        source = PARSER_RS.read_text()
        # Call sites only — the definitions and the doc-comment references are
        # excluded by requiring the `?` that makes it a refusal.
        calls = re.findall(r"validate_(?:tag_operand|if_operands)\([^)]*\)\?", source)
        assert sorted(calls) == sorted(
            [
                "validate_if_operands(args)?",  # the `"if"` arm of parse_token
                "validate_if_operands(args)?",  # the `{% elif %}` arm of parse_if_block
                "validate_tag_operand(&iterable)?",  # the `"for"` arm
                "validate_tag_operand(&expression)?",  # the `"with"` arm, per assignment
                "validate_tag_operand(arg)?",  # validate_if_operands' own delegation
            ]
        ), calls

    def test_it_delegates_to_the_shared_parse_time_validator(self) -> None:
        """Not a second copy of the rule: the body must go through
        ``parse_filter_specs``, which is what ``{{ … }}`` already runs (#1646).
        A hand-rolled re-implementation here is the drift this pins."""
        body = PARSER_RS.read_text().split("pub(crate) fn validate_tag_operand", 1)[1]
        body = body.split("\n}\n", 1)[0]
        assert "parse_filter_specs(" in body, body


# ---------------------------------------------------------------------------
# What this deliberately leaves open
# ---------------------------------------------------------------------------


class TestTheTwoRulesThisDoesNotClose:
    """Both are SEPARATE defects, and the evidence is that each is visible in
    shapes that never swallow anything. Kept as executable notes rather than
    prose so they go red the day either is fixed and this file is stale."""

    def test_unknown_filter_is_a_RENDER_time_lookup_on_every_shape(self) -> None:
        """Not masked by ``{% if %}``: ``{{ }}`` renders it too. Moving it to
        parse time for one shape only would be new drift, and would refuse a
        custom filter registered after the template was parsed."""
        source = "{% if 0 %}{{ p|nosuchfilter }}{% endif %}"
        assert not django_renders(source), "premise"
        rendered, _ = djust_renders(source)
        assert rendered, "unknown-filter now refuses at parse time — update this file"

    @pytest.mark.parametrize(
        "source",
        [
            "{{ p|date:_x }}",
            "{% for i in p|date:_x %}Y{% endfor %}",
            "{% with v=p|date:_x %}{{ v }}{% endwith %}",
        ],
    )
    def test_the_underscore_name_rule_is_missing_on_shapes_that_never_swallow(
        self, source: str
    ) -> None:
        """``Variable.__init__``'s rule, which djust has nowhere. With ``_x``
        BOUND, these three render on this engine and refuse on Django — so it
        cannot be the ``{% if %}`` swallow, and a parse-time filter-chain check
        cannot reach it either."""
        assert not django_renders(source), "premise"
        rendered, _ = djust_renders(source)
        assert rendered, "the underscore rule now exists — update this file"
