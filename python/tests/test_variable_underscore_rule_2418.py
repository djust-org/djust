"""``Variable.__init__``'s underscore rule, which djust had NOWHERE (#2418).

The rule
--------
Django refuses a variable or attribute name that begins with ``_`` while the
template is being COMPILED::

    if var.find(VARIABLE_ATTRIBUTE_SEPARATOR + "_") > -1 or var[0] == "_":
        raise TemplateSyntaxError(
            "Variables and attributes may not begin with underscores: '%s'" % var
        )

It is a rule about the NAME, not about the value: it fires whether or not the
name resolves. That is why it was invisible to #2411's sweep, whose context
bound no ``_x`` — djust refused those cells for the unrelated "argument does
not resolve" reason, so they never showed as divergent. With ``_x`` BOUND, the
three shapes ``TestTheThreeShapesTheIssueNames`` carries rendered here and
refused on Django.

What this closes, and what it deliberately does not
---------------------------------------------------
Three call sites now run the rule, and between them they cover every place
djust turns a template NAME into a lookup: the ``{{ … }}`` head, every filter
ARGUMENT, and every TAG OPERAND (which reaches ``{% if %}``/``{% elif %}``,
``{% for %}``, ``{% with %}``, ``{% firstof %}``, ``{% widthratio %}``,
``{% cycle %}`` and ``{% include … with %}``).

It does NOT reach a name BINDING, because Django's rule does not either:
``{% for _i in items %}X{% endfor %}`` compiles on Django. You may bind an
underscore name; you may just never read one back. ``TestABindingIsNotALookup``
pins that against live Django, and it is the reason no call site passes a loop
variable, a ``{% with %}`` target or an ``as``-name to the validator.

Every expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.template import TemplateSyntaxError
from django.template.base import Variable

from djust import _rust

#: The message Django raises, read off Django rather than typed out.
DJANGO_MESSAGE = "Variables and attributes may not begin with underscores"


class _Obj:
    """An object with one public and one private attribute."""

    def __init__(self) -> None:
        self.pub = "PUB"
        self._y = "PRIVATE"


#: ``_x`` and ``_items`` are BOUND on purpose. An unbound underscore name is
#: refused by djust for the unrelated "does not resolve" reason, which is
#: exactly the masking that made this look like part of #2411.
CTX: dict[str, object] = {
    "p": "a b c",
    "q": 2,
    "_x": 9,
    "items": [1, 2],
    "_items": [3, 4],
    "pairs": [(1, 2)],
    "obj": _Obj(),
    "d": {"_k": "DICTPRIVATE", "k": "ok"},
}

PARSER_RS = (
    pathlib.Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "parser.rs"
)


def django_compiles(source: str) -> bool:
    """Does Django COMPILE this template — the question the rule answers."""
    try:
        DjangoTemplate(source)
    except TemplateSyntaxError:
        return False
    return True


def django_renders(source: str) -> bool:
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
# The three shapes the issue names — the evidence this was SEPARATE from #2411
# ---------------------------------------------------------------------------


class TestTheThreeShapesTheIssueNames:
    """Neither ``{% for %}`` nor ``{% with %}`` nor ``{{ }}`` swallows anything.

    These are the cells #2411 could not have closed: no unresolvable earlier
    step, no short-circuit, no untaken branch. They rendered here purely
    because the rule did not exist.
    """

    SHAPES = [
        "{{ p|date:_x }}",
        "{% for i in p|date:_x %}Y{% endfor %}",
        "{% with v=p|date:_x %}{{ v }}{% endwith %}",
    ]

    @pytest.mark.parametrize("source", SHAPES)
    def test_django_refuses_at_compile_time(self, source: str) -> None:
        with pytest.raises(TemplateSyntaxError, match=DJANGO_MESSAGE):
            DjangoTemplate(source)

    @pytest.mark.parametrize("source", SHAPES)
    def test_djust_refuses_it_too_and_names_the_same_rule(self, source: str) -> None:
        rendered, out = djust_renders(source)
        assert not rendered, f"still renders what Django refuses: {out!r}"
        assert DJANGO_MESSAGE in out, out
        assert "'_x'" in out, out

    def test_the_shape_that_made_it_look_like_2411(self) -> None:
        """With ``_x`` UNBOUND the same cell refuses for a DIFFERENT reason.

        This is the whole reason #2411's 13,202-template sweep reported zero
        rows on these three shapes: its context bound no ``_x``, so djust
        already refused — for a render-time resolution failure rather than for
        the compile-time rule. The distinction is the issue's premise, so it is
        asserted rather than remembered.
        """
        unbound = dict(CTX)
        del unbound["_x"]
        try:
            _rust.render_template("{{ p|date:_x }}", unbound)
        except Exception as exc:  # noqa: BLE001
            before = str(exc)
        else:
            pytest.fail("expected a refusal with _x unbound")
        # It is now the COMPILE-time rule at both bindings, which is the fix:
        # the refusal no longer depends on whether the name resolves.
        assert DJANGO_MESSAGE in before, before


# ---------------------------------------------------------------------------
# Every channel that turns a NAME into a lookup
# ---------------------------------------------------------------------------

#: One row per parse site the rule now runs at. `reason` is the substring of
#: Django's own refusal that djust must now also report.
CHANNELS = [
    # ({{ }}) the head, bare and dotted
    ("{{ _x }}", "'_x'"),
    ("{{ _items }}", "'_items'"),
    ("{{ obj._y }}", "'obj._y'"),
    ("{{ d._k }}", "'d._k'"),
    ("{{ p.__class__ }}", "'p.__class__'"),
    ("{{ _x.real }}", "'_x.real'"),
    ("{{ _x|upper }}", "'_x'"),
    # filter ARGUMENTS
    ("{{ p|cut:_x }}", "'_x'"),
    ("{{ p|default:_x }}", "'_x'"),
    ("{{ p|cut:obj._y }}", "'obj._y'"),
    # {% if %} / {% elif %}, including the shapes that never evaluate
    ("{% if _x %}Y{% else %}N{% endif %}", "'_x'"),
    ("{% if not _x %}Y{% else %}N{% endif %}", "'_x'"),
    ("{% if _x == 9 %}Y{% else %}N{% endif %}", "'_x'"),
    ("{% if obj._y %}Y{% else %}N{% endif %}", "'obj._y'"),
    ("{% if p|date:_x %}Y{% else %}N{% endif %}", "'_x'"),
    ("{% if 0 and _x %}Y{% else %}N{% endif %}", "'_x'"),
    ("{% if 1 or _x %}Y{% else %}N{% endif %}", "'_x'"),
    ("{% if 0 %}A{% elif _x %}B{% else %}C{% endif %}", "'_x'"),
    ("{% if 0 %}{% if _x %}Y{% endif %}{% endif %}", "'_x'"),
    # {% for %} / {% with %}
    ("{% for i in _items %}[{{ i }}]{% endfor %}", "'_items'"),
    ("{% for i in obj._y %}[{{ i }}]{% endfor %}", "'obj._y'"),
    ("{% with v=_x %}{{ v }}{% endwith %}", "'_x'"),
    ("{% with v=p|date:_x %}{{ v }}{% endwith %}", "'_x'"),
    # the three operand tags #2411 left unwired, found by grepping the SINK
    ("{% firstof _x %}", "'_x'"),
    ('{% firstof _x "fb" %}', "'_x'"),
    ("{% firstof q _x %}", "'_x'"),
    ("{% firstof p|date:_x %}", "'_x'"),
    ("{% widthratio _x 10 100 %}", "'_x'"),
    ("{% widthratio q _x 100 %}", "'_x'"),
    ("{% widthratio q 10 _x %}", "'_x'"),
    ("{% cycle _x q %}", "'_x'"),
    ("{% cycle q _x %}", "'_x'"),
]


class TestEveryChannelThatTurnsANameIntoALookup:
    """N similar sites need N tests (#1104), and the sites were found by
    grepping the SINK rather than by listing the tags anyone remembered —
    ``{% firstof %}``, ``{% widthratio %}`` and ``{% cycle %}`` each reach a
    name and none of them was wired by #2411."""

    @pytest.mark.parametrize(("source", "named"), CHANNELS)
    def test_django_refuses_it(self, source: str, named: str) -> None:
        assert not django_renders(source), f"premise: Django must refuse {source!r}"

    @pytest.mark.parametrize(("source", "named"), CHANNELS)
    def test_djust_refuses_it_and_names_the_offending_atom(self, source: str, named: str) -> None:
        rendered, out = djust_renders(source)
        assert not rendered, f"still renders what Django refuses: {out!r}"
        assert DJANGO_MESSAGE in out, out
        assert named in out, out


class TestIncludeWithIsTheSameShapeAsWith:
    """``{% include "x" with a=_y %}`` is ``{% with %}``'s RHS one tag over.

    It needs a real loader to reach, which is the only reason it is not a row
    in ``CHANNELS``.
    """

    def test_django_refuses_it(self) -> None:
        assert not django_compiles('{% include "x.html" with a=_x %}')

    def test_djust_refuses_it_at_parse_time(self) -> None:
        rendered, out = djust_renders('{% include "x.html" with a=_x %}')
        assert not rendered, out
        assert DJANGO_MESSAGE in out, out

    def test_a_quoted_value_still_compiles(self) -> None:
        """Not stricter: the literal arm lets ``a="_x"`` through, so the
        refusal above is about the NAME and not about the tag."""
        assert django_compiles('{% include "x.html" with a="_x" %}')
        _, out = djust_renders('{% include "x.html" with a="_x" %}')
        assert DJANGO_MESSAGE not in out, out


# ---------------------------------------------------------------------------
# Django's ORDER — the three arms that run BEFORE the underscore check
# ---------------------------------------------------------------------------

#: Every one of these COMPILES on Django. A check placed before the literal
#: arms refuses them, which is the sharpest way this fix could be wrong.
MUST_STILL_COMPILE = [
    '{{ p|default:"_x" }}',
    "{{ p|default:'_x' }}",
    '{{ p|default:_("_x") }}',
    "{{ p|default:_('_x') }}",
    '{{ "_x" }}',
    "{{ p|cut:'a._b' }}",
    '{{ p|cut:"a._b" }}',
    '{% if "_x" %}Y{% else %}N{% endif %}',
    "{% if '_x' %}Y{% else %}N{% endif %}",
    '{% for i in items %}{{ i|default:"_x" }}{% endfor %}',
    '{% with v=p|default:"_x" %}{{ v }}{% endwith %}',
    '{% firstof p "_fb" %}',
    '{% cycle "_a" "_b" %}',
    "{% widthratio q 10 100 %}",
    "{{ obj.pub }}",
    "{{ d.k }}",
    "{% for i in items %}[{{ i }}]{% endfor %}",
]


class TestTheArmsThatRunFirst:
    @pytest.mark.parametrize("source", MUST_STILL_COMPILE)
    def test_django_compiles_it(self, source: str) -> None:
        assert django_compiles(source), "premise: Django must accept this"

    @pytest.mark.parametrize("source", MUST_STILL_COMPILE)
    def test_djust_does_not_refuse_it(self, source: str) -> None:
        rendered, out = djust_renders(source)
        assert rendered, f"over-refused: {out}"
        assert DJANGO_MESSAGE not in out, out

    def test_the_i18n_wrapper_arm_is_load_bearing(self) -> None:
        """``_("_x")`` starts with ``_``. Only Django's translate arm — which
        STRIPS ``_( … )`` before the literal and underscore checks — saves it,
        so a fix without that arm refuses a template Django compiles."""
        assert django_compiles('{{ p|default:_("_x") }}')
        rendered, out = djust_renders('{{ p|default:_("_x") }}')
        assert rendered, out

    def test_a_dotted_name_inside_the_i18n_wrapper_is_still_refused(self) -> None:
        """The strip is a strip, not an exemption: Django runs the underscore
        check on what is left, so ``_(a._b)`` still refuses."""
        assert not django_compiles("{{ _(a._b) }}")
        rendered, out = djust_renders("{{ _(a._b) }}")
        assert not rendered, out
        assert DJANGO_MESSAGE in out, out


class TestNoNumericSpellingIsRefused:
    """Django tries the NUMERIC arm before the underscore check, and this fix
    does not reproduce that arm. It does not need to, and this is the check
    rather than the claim: Python rejects a leading ``_`` in a numeric literal
    and no numeric spelling contains ``._``, so the numeric arm can never be
    what saves a name from the rule. A numeric pre-check would be a second
    mechanism with nothing to do (#2233)."""

    SPELLINGS = [
        "0",
        "1",
        "-3",
        "+5",
        "2.7",
        "-2.7",
        "1e5",
        "1E5",
        "1e-5",
        "7.",
        ".5",
        "1_0",
        "10_000",
        "0x10",
        "99999999999999999999",
    ]

    @pytest.mark.parametrize("spelling", SPELLINGS)
    def test_django_never_reaches_the_underscore_check_for_a_number(self, spelling: str) -> None:
        """Whatever Django does with the spelling, it is never THIS refusal."""
        try:
            Variable(spelling)
        except TemplateSyntaxError as exc:
            assert DJANGO_MESSAGE not in str(exc), spelling

    @pytest.mark.parametrize("spelling", SPELLINGS)
    def test_no_numeric_spelling_begins_with_underscore_or_carries_a_dotted_one(
        self, spelling: str
    ) -> None:
        """The structural reason the arm is unnecessary, stated as an
        assertion so a future spelling that breaks it fails here."""
        assert not spelling.startswith("_")
        assert "._" not in spelling

    @pytest.mark.parametrize("spelling", ["_1", "_0", "_1.5", "_", "__"])
    def test_python_refuses_a_leading_underscore_in_a_numeric_literal(self, spelling: str) -> None:
        """The premise, read off live Python. ``int("_1")`` raising is what
        makes the numeric arm unreachable for an underscore-leading name."""
        with pytest.raises(ValueError):
            int(spelling)
        with pytest.raises(ValueError):
            float(spelling)


class TestDjangosErrorOrderIsReproduced:
    """The head before any filter; within a filter, the argument's name before
    that filter's arity; and an EARLIER filter's arity before a LATER filter's
    name. Measured off Django, then asserted of djust."""

    CASES = [
        ("{{ _x|cut }}", DJANGO_MESSAGE),
        ("{{ p|upper:_x }}", DJANGO_MESSAGE),
        ('{{ p|upper:"a"|cut:_x }}', "upper requires 1 arguments"),
        ("{{ _x|nosuchfilter }}", DJANGO_MESSAGE),
    ]

    @pytest.mark.parametrize(("source", "reason"), CASES)
    def test_django_reports_this_one(self, source: str, reason: str) -> None:
        with pytest.raises(TemplateSyntaxError, match=re.escape(reason)):
            DjangoTemplate(source)

    @pytest.mark.parametrize(("source", "reason"), CASES)
    def test_djust_reports_the_same_one(self, source: str, reason: str) -> None:
        rendered, out = djust_renders(source)
        assert not rendered, out
        assert reason in out, out


# ---------------------------------------------------------------------------
# A BINDING is not a lookup — the over-refusal this fix must not commit
# ---------------------------------------------------------------------------


class TestABindingIsNotALookup:
    """Django's rule reaches a name being READ, never a name being BOUND.

    Every row here compiles on live Django, and the body deliberately never
    mentions the bound name — the first version of this measurement did, so
    what refused was the ``{{ }}`` channel and the binding looked refused when
    it is not.
    """

    BINDINGS = [
        "{% for _i in items %}X{% endfor %}",
        "{% for _a, _b in pairs %}X{% endfor %}",
        "{% with _v=q %}X{% endwith %}",
        "{% firstof q 1 as _n %}X",
        "{% widthratio q 10 100 as _n %}X",
        '{% cycle "a" "b" as _n %}X',
        "{% block _b %}X{% endblock %}",
    ]

    @pytest.mark.parametrize("source", BINDINGS)
    def test_django_compiles_the_binding(self, source: str) -> None:
        assert django_compiles(source), "premise: Django binds underscore names"

    @pytest.mark.parametrize("source", BINDINGS)
    def test_djust_still_compiles_it(self, source: str) -> None:
        rendered, out = djust_renders(source)
        assert rendered, f"over-refused a BINDING: {out}"

    def test_the_asymmetry_is_django_s_own(self) -> None:
        """You may bind an underscore name and never read it back. Stated
        because it looks like a bug until you measure it."""
        assert django_compiles("{% with _v=q %}X{% endwith %}")
        assert not django_compiles("{% with _v=q %}{{ _v }}{% endwith %}")
        assert djust_renders("{% with _v=q %}X{% endwith %}")[0]
        assert not djust_renders("{% with _v=q %}{{ _v }}{% endwith %}")[0]


class TestNoIfOperatorWordIsRefusable:
    """``validate_if_operands`` still carries no operator set, and this is why.

    The #2411 doc-comment predicted a name check here "would need the operator
    set reinstated, because ``==`` is not a variable". Measured against live
    ``smartif.OPERATORS`` that is wrong: no operator token begins with ``_`` or
    contains ``._``, so the rule is a no-op on every one of them. Reinstating
    the set would be a second mechanism that changes no behaviour (#2233).
    """

    def test_no_operator_word_trips_the_rule(self) -> None:
        from django.template.smartif import OPERATORS  # noqa: PLC0415

        words = {w for key in OPERATORS for w in key.split()}
        assert words, "premise: smartif exposes its operator words"
        for word in words:
            assert not word.startswith("_"), word
            assert "._" not in word, word
            source = "{%% if p %s p %%}Y{%% else %%}N{%% endif %%}" % word
            rendered, out = djust_renders(source)
            assert rendered, f"operator {word!r} was refused as an operand: {out}"


# ---------------------------------------------------------------------------
# The caller SET, not a floor
# ---------------------------------------------------------------------------


class TestTheCallerSetIsPinned:
    """One rule, three call sites — pinned as a SET so the next channel that
    forgets the call fails here rather than shipping (#1125/#2233)."""

    def test_the_rule_runs_at_exactly_these_three_places(self) -> None:
        source = PARSER_RS.read_text()
        calls = re.findall(r"validate_variable_name\([^)]*\)\?", source)
        assert sorted(calls) == sorted(
            [
                "validate_variable_name(expr_part)?",  # the `{{ … }}` head
                "validate_variable_name(arg)?",  # every filter ARGUMENT
                "validate_variable_name(&parts[0])?",  # every TAG OPERAND's head
            ]
        ), calls

    def test_the_tag_operand_caller_set_grew_by_the_four_sites_2411_missed(self) -> None:
        """``validate_tag_operand`` is how the rule reaches a tag, so its own
        caller set is the second half of the pin. #2411 wired four sites;
        grepping the SINK for "what resolves a name" found four more."""
        source = PARSER_RS.read_text()
        calls = re.findall(r"validate_(?:tag_operand|if_operands)\([^)]*\)\?", source)
        assert sorted(calls) == sorted(
            [
                "validate_if_operands(args)?",  # the `"if"` arm of parse_token
                "validate_if_operands(args)?",  # the `{% elif %}` arm
                "validate_tag_operand(&iterable)?",  # the `"for"` arm
                "validate_tag_operand(&expression)?",  # the `"with"` arm
                "validate_tag_operand(arg)?",  # validate_if_operands' delegation
                "validate_tag_operand(parts[1])?",  # `{% include … with k=v %}`
                "validate_tag_operand(operand)?",  # `{% widthratio %}`
                "validate_tag_operand(operand)?",  # `{% firstof %}`
                "validate_tag_operand(value)?",  # `{% cycle %}`
            ]
        ), calls

    def test_the_tag_operand_validator_delegates_rather_than_restating(self) -> None:
        """Not a second copy of the rule (#1646): a mutation that inlines the
        underscore test here instead of calling the shared validator fails."""
        body = PARSER_RS.read_text().split("pub(crate) fn validate_tag_operand", 1)[1]
        body = body.split("\n}\n", 1)[0]
        assert "validate_variable_name(" in body, body


# ---------------------------------------------------------------------------
# The compatibility surface this changes
# ---------------------------------------------------------------------------


class TestTheOneDocumentedPatternThisBreaks:
    """``Component.render`` injected ``_component_key`` and its own docstring
    told authors to read it as ``{{ _component_key }}``. That template never
    compiled on Django's engine — ``_render_template_with_fallback`` falls back
    to ``django.template.Template`` on a Rust error — so the documented shape
    only ever worked on the Rust path, and this rule closes it there too.

    The replacement is the same value under a name the rule admits. Both keys
    are injected; only the non-underscore one is readable, which is exactly
    Django's own asymmetry.
    """

    def test_the_underscore_spelling_is_now_refused_on_both_engines(self) -> None:
        assert not django_compiles("{{ _component_key }}")
        rendered, out = djust_renders("{{ _component_key }}")
        assert not rendered, out
        assert DJANGO_MESSAGE in out, out

    def test_the_replacement_spelling_renders(self) -> None:
        from djust.components.base import Component  # noqa: PLC0415

        class Badge(Component):
            template = '<span data-component-key="{{ component_key }}">x</span>'

        html = Badge(_component_key="Badge_7").render()
        assert 'data-component-key="Badge_7"' in html, html

    def test_both_keys_are_still_in_the_context(self) -> None:
        """``_component_key`` is kept — a Python-side reader
        (``get_context_data`` override, a custom ``_render_custom``) is
        unaffected; only the TEMPLATE spelling changed."""
        from djust.components.base import Component  # noqa: PLC0415

        class Badge(Component):
            template = "<span>x</span>"

            def get_context_data(self):  # type: ignore[no-untyped-def]
                return {}

        badge = Badge(_component_key="Badge_9")
        badge.render()
        assert badge._component_key == "Badge_9"
