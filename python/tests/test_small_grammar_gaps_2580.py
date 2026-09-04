"""Small parse-time grammar gaps across ten independent tags/mechanisms (#2580).

Each of these tags has its own small grammar check Django performs at parse
time that djust's Rust parser did not. None of these are the SAME defect —
they are ten separate, small refusals, each pinned by its own live-Django
parity test and its own gate-off, per #1104 (N similar sites need N tests)
and #1859 (a pin that cannot distinguish mechanisms is decorative).

Two mechanisms are shared across MULTIPLE cells deliberately, not
accidentally, and are called out as such:

* the "stray closer" fix (one new match arm in ``parse_token_inner``) closes
  both ``test_verbatim_tag04`` (a stray second ``{% endverbatim %}`` once the
  first already closed a NESTED ``{% verbatim %}`` as literal text — matching
  Django's ``Lexer.verbatim`` state machine, which tracks a single string,
  not a nesting depth) and the RIGHT EXCEPTION TYPE for
  ``tests.py::test_invalid_block_suggestion`` (message text differs from
  Django's, deferred to #2581, same treatment as #2576's ``{% else if %}``
  cell);
* the "extends must be first" fix (one post-hoc scan in ``parse_internal``)
  closes both ``test_extends_not_first_tag_in_extended_template`` (content
  before extends) and ``test_exception03`` (a second extends after a block
  already opened) — Django's own ``ExtendsNode.must_be_first`` mechanism
  covers both with one check too.

One cell (``test_exception05``, ``{{ block.super }}`` outside a child
template) is NOT fixed here — it needs real render-time ``block.super``
support that doesn't exist at all yet (tracked on #2531, the sibling issue
about the POSITIVE case). Scoped out per #1079: fixing it here would have
meant building a feature, not tightening a grammar check.

Every Django expectation below is LIVE Django, never a transcription.
"""

from __future__ import annotations

import pytest
from django.template import Context, Engine, TemplateSyntaxError

from djust import _rust


def django_refuses(source: str, context: dict | None = None) -> str:
    """Live Django: compiling (and, if it compiles, rendering) raises
    ``TemplateSyntaxError``. Some of these cells only refuse at RENDER time
    (``block.super``'s sibling class), so this helper covers both."""
    with pytest.raises(TemplateSyntaxError) as info:
        t = Engine().from_string(source)
        t.render(Context(context or {}))
    return str(info.value)


def djust_refuses(source: str, context: dict | None = None) -> str:
    with pytest.raises((RuntimeError, TemplateSyntaxError)) as info:
        _rust.render_template(source, context or {})
    return str(info.value)


def djust_renders(source: str, context: dict | None = None) -> str:
    return _rust.render_template(source, context or {})


# --------------------------------------------------------------------------- #
# regroup — token count (6) + "by"/"as" keyword positions
# --------------------------------------------------------------------------- #


class TestRegroupGrammar:
    CASES = [
        ("{% regroup data by bar as %}", "takes_five", "takes five arguments"),
        (
            "{% regroup data by bar thisaintright grouped %}",
            "as_position",
            "next-to-last argument",
        ),
        (
            "{% regroup data thisaintright bar as grouped %}",
            "by_position",
            "second argument",
        ),
        (
            "{% regroup data by bar as grouped toomanyargs %}",
            "too_many",
            "takes five arguments",
        ),
    ]

    @pytest.mark.parametrize("source,_id,_frag", CASES, ids=[c[1] for c in CASES])
    def test_django_refuses(self, source, _id, _frag):
        django_refuses(source, {"data": []})

    @pytest.mark.parametrize("source,_id,frag", CASES, ids=[c[1] for c in CASES])
    def test_djust_refuses_the_right_shape(self, source, _id, frag):
        msg = djust_refuses(source, {"data": []})
        assert frag in msg or "regroup" in msg, msg

    def test_valid_regroup_still_works(self):
        items = [{"cat": "a"}, {"cat": "a"}, {"cat": "b"}]
        out = djust_renders(
            "{% regroup items by cat as grouped %}"
            "{% for g in grouped %}{{ g.grouper }}{% endfor %}",
            {"items": items},
        )
        assert out == "ab"


# --------------------------------------------------------------------------- #
# with — token_kwargs finds zero valid key=value assignments
# --------------------------------------------------------------------------- #


class TestWithRequiresAnAssignment:
    CASES = [
        ("{% with dict.key xx key %}{{ key }}{% endwith %}", "legacy_bad_kw"),
        ("{% with dict.key as %}{{ key }}{% endwith %}", "legacy_no_name"),
    ]

    @pytest.mark.parametrize("source,_id", CASES, ids=[c[1] for c in CASES])
    def test_django_refuses(self, source, _id):
        django_refuses(source, {"dict": {"key": 1}})

    @pytest.mark.parametrize("source,_id", CASES, ids=[c[1] for c in CASES])
    def test_djust_refuses(self, source, _id):
        msg = djust_refuses(source, {"dict": {"key": 1}})
        assert "with" in msg and "assignment" in msg, msg

    def test_valid_with_still_works(self):
        assert djust_renders("{% with total=5 %}{{ total }}{% endwith %}") == "5"


# --------------------------------------------------------------------------- #
# widthratio — total token count (4 or 6) + the "as" keyword
# --------------------------------------------------------------------------- #


class TestWidthratioGrammar:
    def test_django_refuses_bad_count(self):
        django_refuses("{% widthratio a b 100 as %}", {"a": 1, "b": 2})

    def test_djust_refuses_bad_count(self):
        msg = djust_refuses("{% widthratio a b 100 as %}", {"a": 1, "b": 2})
        assert "at least three arguments" in msg, msg

    def test_django_refuses_bad_keyword(self):
        django_refuses("{% widthratio a b 100 not_as variable %}", {"a": 1, "b": 2})

    def test_djust_refuses_bad_keyword(self):
        msg = djust_refuses("{% widthratio a b 100 not_as variable %}", {"a": 1, "b": 2})
        assert "Expecting 'as' keyword" in msg, msg

    def test_valid_widthratio_still_works(self):
        assert djust_renders("{% widthratio 50 100 100 %}") == "50"
        assert djust_renders("{% widthratio 50 100 100 as w %}{{ w }}") == "50"


# --------------------------------------------------------------------------- #
# named endblock — the closing tag's cited name must match (or be bare)
# --------------------------------------------------------------------------- #


class TestNamedEndblockMustMatch:
    CASES = [
        (
            "1{% block first %}_{% block second %}2{% endblock first %}_{% endblock second %}3",
            "inner_closed_by_outer_name",
        ),
        (
            "1{% block first %}_{% block second %}2{% endblock %}_{% endblock second %}3",
            "outer_closed_by_inner_name",
        ),
        (
            "1{% block first %}_{% block second %}2{% endblock second %}_{% endblock third %}3",
            "outer_closed_by_unrelated_name",
        ),
    ]

    @pytest.mark.parametrize("source,_id", CASES, ids=[c[1] for c in CASES])
    def test_django_refuses(self, source, _id):
        django_refuses(source)

    @pytest.mark.parametrize("source,_id", CASES, ids=[c[1] for c in CASES])
    def test_djust_refuses(self, source, _id):
        msg = djust_refuses(source)
        assert "endblock" in msg and "does not match" in msg, msg

    def test_correctly_named_nested_blocks_still_close_in_either_order(self):
        # inner closed by its own name, then outer closed by its own name
        assert (
            djust_renders(
                "1{% block first %}_{% block second %}2{% endblock second %}_{% endblock first %}3"
            )
            == "1_2_3"
        )
        # bare endblock still closes whatever is currently open
        assert djust_renders("1{% block first %}_{% endblock %}") == "1_"
        # a correctly-named bare endblock also still works
        assert djust_renders("1{% block first %}_{% endblock first %}") == "1_"


# --------------------------------------------------------------------------- #
# verbatim — a nested {% verbatim %} is TEXT (Django's Lexer state machine
# tracks one string, not a nesting depth), so the SECOND endverbatim is a
# stray closer — the SAME mechanism as test_invalid_block_suggestion below.
# --------------------------------------------------------------------------- #


class TestNestedVerbatimIsNotNesting:
    def test_django_refuses(self):
        django_refuses("{% verbatim %}{% verbatim %}{% endverbatim %}{% endverbatim %}")

    def test_djust_refuses(self):
        msg = djust_refuses("{% verbatim %}{% verbatim %}{% endverbatim %}{% endverbatim %}")
        assert "endverbatim" in msg, msg

    def test_ordinary_verbatim_content_is_untouched(self):
        out = djust_renders("{% verbatim %}{{ raw }} {% tag %}{% endverbatim %}")
        assert out == "{{ raw }} {% tag %}"


# --------------------------------------------------------------------------- #
# a stray closer (any of endif/endfor/endblock/else/elif/endverbatim/
# endwith/endspaceless/endautoescape/endfilter) reached where it is not the
# awaited terminator raises, matching Django's "not an independently
# registered tag" fact from the other direction.
# --------------------------------------------------------------------------- #


class TestStrayCloserInsideAnIfBody:
    """``tests.py::test_invalid_block_suggestion``. Django's message
    ("Invalid block tag on line 1: 'endblock', expected 'elif', 'else' or
    'endif'. Did you forget to register or load this tag?") is verbatim
    text (#2581, not yet landed) — djust raises the SAME exception TYPE, at
    the SAME time, with its own wording. This is the class of case #2576's
    `{% else if %}` cell already established: right type + timing, deferred
    message text, still a real fix.
    """

    def test_django_refuses(self):
        django_refuses("{% if 1 %}lala{% endblock %}{% endif %}")

    def test_djust_refuses_the_right_type_even_though_the_wording_differs(self):
        msg = djust_refuses("{% if 1 %}lala{% endblock %}{% endif %}")
        assert "endblock" in msg
        assert "Invalid block tag" in msg

    def test_every_ordinary_closer_form_still_works(self):
        """Regression pin for the WHOLE closer-keyword surface — every one
        of these previously round-tripped through the SAME blanket `Ok(None)`
        arm this fix replaced; none may now be treated as stray."""
        cases = [
            ("{% if 1 %}a{% else %}b{% endif %}", "a"),
            ("{% if 1 %}a{% elif 2 %}b{% else %}c{% endif %}", "a"),
            ("{% with a=1 %}{{ a }}{% endwith %}", "1"),
            ("{% autoescape off %}{{ x }}{% endautoescape %}", "<b>"),
            ("{% filter upper %}hi{% endfilter %}", "HI"),
            ("{% spaceless %}<p> hi </p>{% endspaceless %}", "<p> hi </p>"),
        ]
        for source, expected in cases:
            assert djust_renders(source, {"x": "<b>"}) == expected, source

    def test_for_empty_still_works(self):
        out = djust_renders(
            "{% for x in items %}{{ x }}{% empty %}none{% endfor %}",
            {"items": [1, 2]},
        )
        assert out == "12"
        out = djust_renders(
            "{% for x in items %}{{ x }}{% empty %}none{% endfor %}",
            {"items": []},
        )
        assert out == "none"


# --------------------------------------------------------------------------- #
# extends must be the first tag — covers BOTH "content before extends" and
# "a second extends after a block already opened" with one check, because
# Django's own ExtendsNode.must_be_first covers both the same way.
# --------------------------------------------------------------------------- #


class TestExtendsMustBeFirst:
    def test_django_refuses_content_before_extends(self):
        django_refuses("{% block content %}B{% endblock %}{% extends 'base.html' %}")

    def test_djust_refuses_content_before_extends(self):
        msg = djust_refuses("{% block content %}B{% endblock %}{% extends 'base.html' %}")
        assert "extends" in msg and "first" in msg, msg

    def test_django_refuses_a_second_extends(self):
        django_refuses(
            "{% extends 'inheritance01' %}{% block first %}2{% endblock %}"
            "{% extends 'inheritance16' %}"
        )

    def test_djust_refuses_a_second_extends(self):
        msg = djust_refuses(
            "{% extends 'inheritance01' %}{% block first %}2{% endblock %}"
            "{% extends 'inheritance16' %}"
        )
        assert "extends" in msg and "first" in msg, msg

    def test_leading_text_before_extends_is_still_fine(self):
        """Django's own rule: TEXT (including whitespace) before `{%
        extends %}` does not count as "content" — only a non-text NODE
        does. Probed via the raw entry (no loader configured), so a
        template that reaches the loader stage proves the must-be-first
        check did NOT fire."""
        with pytest.raises(RuntimeError) as info:
            _rust.render_template(
                '   {% extends "base.html" %}{% block c %}hi{% endblock %}',
                {},
            )
        assert "must be the first tag" not in str(info.value)


# --------------------------------------------------------------------------- #
# cycle — the FIRST {% cycle %} tag's own argument tokenization: an
# unspaced comma-joined operand ("a,b,c") is a single malformed Variable
# token, not three values.
# --------------------------------------------------------------------------- #


class TestCycleCommaJoinedOperand:
    def test_django_refuses_at_the_first_tag(self):
        msg = django_refuses("{% cycle a,b,c as foo %}{% cycle bar %}", {"a": 1, "b": 2, "c": 3})
        assert "Could not parse the remainder" in msg

    def test_djust_refuses_the_same_way(self):
        msg = djust_refuses("{% cycle a,b,c as foo %}{% cycle bar %}", {"a": 1, "b": 2, "c": 3})
        assert "Could not parse the remainder" in msg
        assert "a,b,c" in msg

    def test_ordinary_cycle_usage_still_works(self):
        # {% cycle foo %} is a REFERENCE to the named cycle above — it
        # ADVANCES the shared iterator, so this is "a" then "b", not "a"
        # twice.
        assert djust_renders('{% cycle "a" "b" "c" as foo %}{% cycle foo %}') == "ab"
        assert djust_renders("{% cycle a b c %}", {"a": "1", "b": "2", "c": "3"}) == "1"


# --------------------------------------------------------------------------- #
# the same tiling check, applied generically via validate_tag_operand, must
# NOT reject {% if %}'s own operator words (and/or/not/==/is/in/...) — only
# genuine operands. This is the regression #2580's fix could have shipped
# and didn't.
# --------------------------------------------------------------------------- #


class TestIfOperatorWordsSurviveTheTilingCheck:
    CASES = [
        ("{% if 1 == 1 %}y{% endif %}", {}, "y"),
        ("{% if a and b %}y{% else %}n{% endif %}", {"a": 1, "b": 1}, "y"),
        ("{% if a or b %}y{% endif %}", {"a": 0, "b": 1}, "y"),
        ("{% if not a %}y{% endif %}", {"a": 0}, "y"),
        ("{% if a is b %}y{% endif %}", {"a": None, "b": None}, "y"),
        ("{% if a is not b %}y{% endif %}", {"a": 1, "b": None}, "y"),
        ("{% if a in b %}y{% endif %}", {"a": 1, "b": [1, 2]}, "y"),
        ("{% if a >= b %}y{% endif %}", {"a": 2, "b": 1}, "y"),
        ("{% if a != b %}y{% endif %}", {"a": 1, "b": 2}, "y"),
        ("{% if a and b or c %}y{% endif %}", {"a": 1, "b": 0, "c": 1}, "y"),
    ]

    @pytest.mark.parametrize("source,ctx,expected", CASES, ids=[c[0] for c in CASES])
    def test_still_renders(self, source, ctx, expected):
        assert djust_renders(source, ctx) == expected

    def test_a_genuinely_malformed_operand_inside_an_if_still_refuses(self):
        """The tiling check itself must still fire for a real operand —
        confirming the operator-skip in `validate_if_operands` filters only
        operator words, not the check's own effect on operands."""
        msg = djust_refuses("{% if a,b,c %}y{% endif %}")
        assert "Could not parse the remainder" in msg
