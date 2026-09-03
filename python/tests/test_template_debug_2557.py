"""A template error carries Django's ``template_debug``, so the technical-500
page shows the name, the line and the source excerpt (#2557).

The defect
----------
Django's debug view reads a ``template_debug`` dict off the exception
(``django/views/debug.py:329``) and its template renders ``name``, ``line``,
``during`` and a ``source_lines`` excerpt from it. djust raised
``Error rendering template: …`` / ``Template error: …`` with no such dict, so a
developer got a message with no location — on a large template, a bisect by
hand.

The fix, in three movements
---------------------------
1. ``crates/djust_templates/src/lexer.rs`` ``tokenize_spanned`` records each
   token's ``[start, end)`` byte span (Django's ``Token.position``).
2. ``parser.rs`` ``parse_token`` tags a failure with the span of the token it
   was parsing, INNERMOST-first, as ``DjangoRustError::TemplateErrorAt``.
3. every ``Template::new`` in ``djust_live`` hangs the span on the
   ``RuntimeError`` as ``djust_token_span``; ``DjustTemplate._compile`` turns
   it into the dict via ``build_template_debug`` — a port of Django's own
   ``get_exception_info``.

The premise the issue got wrong
-------------------------------
The issue says "the parser already tracks offsets; the work is carrying them
across the PyO3 boundary". It did not: ``lexer::Token`` carried no position at
all, so the offsets had to be produced before they could be carried.

Every Django expectation here is LIVE Django, never a transcription: the
differential test builds the SAME broken source on ``django.template.Engine``
and asserts djust's dict agrees field for field.

Gate-off (#1468 / #2135, each mechanism independently):
* drop the span in the lexer (``out.push(t, tok_start, chars.pos())`` →
  ``(0, 0)``) → the position tests go red;
* stop attaching in ``parse_token`` (``e.at(...)`` → ``e``) →
  ``TestTemplateDebugIsPopulated`` / ``TestDjangoDifferential`` go red;
* stop reading the attribute in ``_compile`` (``span = None``) → the same;
* revert ``find_if_keyword`` to the byte walk → ``TestNonAsciiExpression``
  goes red (it panics);
* remove the empty-variable refusal from ``parse_token_inner``'s
  ``Token::Variable`` arm → ``TestEmptyVariableTag`` goes red;
* remove the empty-BLOCK refusal from the ``Token::Tag`` arm →
  ``TestEmptyBlockTag`` goes red (independently of the one above);
* move either refusal back into ``tokenize_spanned`` →
  ``TestEmptyTagContextAxis`` goes red on the raw-block rows, which is the
  regression the first version of this PR shipped;
* delete the re-locate block in ``build_template_debug`` →
  ``TestMultiLineTokenSpan`` goes red with ``line == 0``;
* drop the ``UNKNOWN_SOURCE`` default → ``TestUnknownSourceName`` goes red.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from django.template import Context, Engine, Origin, TemplateSyntaxError
from django.template import Template as DjangoTemplate

from djust.template import DjustTemplate, DjustTemplateBackend, DjustTemplateSyntaxError
from djust.template.exceptions import UNKNOWN_SOURCE, build_template_debug


def django_unknown_source() -> str:
    """Django's own constant, read live — never transcribed."""
    from django.template.base import UNKNOWN_SOURCE as _DJANGO

    return _DJANGO


#: The keys Django's technical-500 template reads. Taken from
#: ``Template.get_exception_info``'s return, not from memory.
DJANGO_DEBUG_KEYS = {
    "message",
    "source_lines",
    "before",
    "during",
    "after",
    "top",
    "bottom",
    "total",
    "line",
    "name",
    "start",
    "end",
}

#: Structural fields both engines must agree on exactly. ``message`` is
#: excluded because the two engines word their errors differently (djust's
#: text is a published contract, #2549) and ``name`` because it comes from the
#: origin each caller supplies.
POSITIONAL_KEYS = (
    "source_lines",
    "before",
    "during",
    "after",
    "top",
    "bottom",
    "total",
    "line",
    "start",
    "end",
)


@pytest.fixture
def backend(tmp_path: pathlib.Path) -> DjustTemplateBackend:
    return DjustTemplateBackend(
        {"NAME": "djust", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    )


def _debug_for(source: str, backend: DjustTemplateBackend, name: str | None = None) -> dict:
    """Build ``source`` through djust and return the resulting template_debug."""
    origin = Origin(name=name, template_name=name) if name else None
    with pytest.raises(DjustTemplateSyntaxError) as info:
        DjustTemplate(source, backend, origin=origin)
    debug = info.value.template_debug
    assert debug is not None, f"no template_debug for {source!r}"
    return debug


class TestTemplateDebugIsPopulated:
    """The dict exists, has Django's keys, and points at the right token."""

    def test_has_exactly_djangos_key_set(self, backend):
        debug = _debug_for("a\n{% nosuchtag %}\nb", backend)
        assert set(debug) == DJANGO_DEBUG_KEYS

    def test_during_is_the_offending_token(self, backend):
        debug = _debug_for("a\n{% nosuchtag %}\nb", backend)
        assert debug["during"] == "{% nosuchtag %}"

    def test_line_is_the_line_the_token_is_on(self, backend):
        debug = _debug_for("a\nb\nc\n{% nosuchtag %}\nd", backend)
        assert debug["line"] == 4

    def test_source_lines_carry_the_excerpt(self, backend):
        debug = _debug_for("one\ntwo\n{% nosuchtag %}\nfour", backend)
        text = [line for _, line in debug["source_lines"]]
        assert "one\n" in text
        assert "{% nosuchtag %}\n" in text
        assert "four" in text

    def test_name_is_the_origin_name(self, backend):
        debug = _debug_for("{% nosuchtag %}", backend, name="/app/t/broken.html")
        assert debug["name"] == "/app/t/broken.html"

    def test_start_and_end_bracket_the_token_in_the_source(self, backend):
        source = "hello\n{% nosuchtag %}\nworld"
        debug = _debug_for(source, backend)
        assert source[debug["start"] : debug["end"]] == "{% nosuchtag %}"

    def test_before_during_after_reassemble_the_line(self, backend):
        source = "x{% nosuchtag %}y\n"
        debug = _debug_for(source, backend)
        assert debug["before"] + debug["during"] + debug["after"] == "x{% nosuchtag %}y\n"

    def test_an_unknown_filter_is_located_too(self, backend):
        # #2419 already refused an unknown filter at parse time; #2557 is what
        # gives that refusal a position.
        debug = _debug_for("pad\n{{ x|nosuchfilter }}\n", backend)
        assert debug["during"] == "{{ x|nosuchfilter }}"
        assert debug["line"] == 2

    def test_an_unclosed_block_points_at_the_opener(self, backend):
        # Django's `unclosed_block_tag` reports the OPENING token, not EOF.
        debug = _debug_for("a\n{% if x %}\nb\n", backend)
        assert debug["during"] == "{% if x %}"
        assert debug["line"] == 2


class TestInnermostTokenWins:
    """A nested failure points at the offending tag, not its enclosing block.

    This is what ``DjangoRustError::at``'s "already-located wins" rule buys:
    the deepest ``parse_token`` frame attaches first and no outer frame
    overwrites it.
    """

    def test_error_inside_an_if_points_at_the_inner_tag(self, backend):
        debug = _debug_for("{% if x %}\n  {% nosuchtag %}\n{% endif %}", backend)
        assert debug["during"] == "{% nosuchtag %}"
        assert debug["line"] == 2

    def test_error_three_blocks_deep_still_points_at_the_inner_tag(self, backend):
        source = (
            "{% if a %}\n"
            "{% for i in xs %}\n"
            "{% with y=1 %}\n"
            "{% nosuchtag %}\n"
            "{% endwith %}\n"
            "{% endfor %}\n"
            "{% endif %}"
        )
        debug = _debug_for(source, backend)
        assert debug["during"] == "{% nosuchtag %}"
        assert debug["line"] == 4

    def test_the_second_of_two_siblings_is_the_one_reported(self, backend):
        source = "{% if a %}{% endif %}\n{% nosuchtag %}"
        debug = _debug_for(source, backend)
        assert debug["start"] == source.index("{% nosuchtag %}")


class TestDjangoDifferential:
    """djust's dict agrees with Django's own, computed live, field for field.

    A curated expectation samples one axis; a differential against the
    reference implementation does not (v1.1.1-2 canon). Every source here is
    one BOTH engines refuse, so both produce a dict for the same token.
    """

    SOURCES = [
        "{% nosuchtag %}",
        "line one\nline two\n{% nosuchtag %}\nline four\n",
        "prefix {% nosuchtag %} suffix",
        "{% if x %}\n  {% nosuchtag %}\n{% endif %}\n",
        "\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n{% nosuchtag %}\n\n\n\n\n\n\n\n\n\n\n\n\n",
        "{% nosuchtag %}\ntail without a trailing newline",
    ]

    @pytest.mark.parametrize("source", SOURCES)
    def test_positional_fields_match_django(self, source, backend):
        django_engine = Engine(debug=True)
        with pytest.raises(TemplateSyntaxError) as dj_info:
            django_engine.from_string(source)
        django_debug = dj_info.value.template_debug
        assert django_debug is not None, "Django did not locate this source either"

        djust_debug = _debug_for(source, backend)

        assert set(djust_debug) == set(django_debug)
        for key in POSITIONAL_KEYS:
            assert djust_debug[key] == django_debug[key], (
                f"{key}: django={django_debug[key]!r} djust={djust_debug[key]!r}"
            )

    def test_the_long_source_really_exercises_the_context_window(self, backend):
        """Guard against the differential passing vacuously.

        The 15-blank-lines source exists to make ``top``/``bottom`` clip; if it
        did not, the ``top``/``bottom`` comparison above would be comparing
        1 to 1 on every row.
        """
        source = self.SOURCES[4]
        debug = _debug_for(source, backend)
        assert debug["top"] > 1
        assert debug["bottom"] < debug["total"]


class TestTechnicalFiveHundredRender:
    """The dict reaches the page: render Django's real reporter, not a mock."""

    def test_the_debug_page_names_the_template_line_and_token(self, backend, rf, settings):
        settings.DEBUG = True
        from django.views.debug import technical_500_response

        origin = Origin(name="/app/templates/broken.html", template_name="broken.html")
        source = "line one\nline two\n{% badtagname %}\nline four\nline five\n"
        try:
            DjustTemplate(source, backend, origin=origin)
        except TemplateSyntaxError:
            response = technical_500_response(rf.get("/broken/"), *sys.exc_info())
        page = response.content.decode()

        assert response.status_code == 500
        assert "/app/templates/broken.html" in page, "the template name is missing"
        assert "error at line <strong>3</strong>" in page, "the line number is missing"
        assert "{% badtagname %}" in page, "the offending token is missing"
        assert "line four" in page, "the source excerpt is missing"

    def test_the_reporter_still_copes_when_the_engine_cannot_locate_it(self, backend, rf):
        """``template_debug`` stays ``None`` for an error raised after the
        token walk (cycle binding runs on the AST, which carries no spans), and
        Django's reporter renders the plain traceback for a ``None`` — the
        pre-#2557 behaviour, preserved rather than crashed."""
        from django.views.debug import ExceptionReporter

        with pytest.raises(DjustTemplateSyntaxError) as info:
            DjustTemplate("{% cycle nosuchcycle %}", backend)
        assert info.value.template_debug is None

        try:
            raise info.value
        except DjustTemplateSyntaxError:
            data = ExceptionReporter(rf.get("/"), *sys.exc_info()).get_traceback_data()
        assert data["template_info"] is None


class TestSpanCrossesThePyO3Boundary:
    """The span rides on the raw ``RuntimeError``, beside an unchanged message."""

    def test_compile_template_sets_djust_token_span(self):
        from djust._rust import compile_template

        source = "pad\n{% nosuchtag %}"
        with pytest.raises(RuntimeError) as info:
            compile_template(source)
        span = getattr(info.value, "djust_token_span", None)
        assert span is not None, "compile_template did not carry the span across"
        assert source[span[0] : span[1]] == "{% nosuchtag %}"

    def test_the_message_is_unchanged_by_the_span(self):
        """The span is an attribute, never folded into the text — so every
        existing assertion on the message keeps holding."""
        from djust._rust import compile_template

        with pytest.raises(RuntimeError) as info:
            compile_template("{% nosuchtag %}")
        text = str(info.value)
        assert text.startswith("Template error: Unsupported template tag")
        assert "djust_token_span" not in text
        assert "start" not in text


class TestNonAsciiExpression:
    """#2551 / #2552: a non-ASCII byte in a ``{{ }}`` expression panicked.

    ``find_if_keyword`` walked ``expr.as_bytes()`` and sliced ``expr[i..]`` at
    every byte, including a continuation byte, which is not a char boundary.
    Every one of these is a template Django renders.
    """

    @pytest.mark.parametrize(
        "source,context",
        [
            ("{{ café }}", {"café": "ok"}),
            ("{{ x.é }}", {"x": {"é": "ok"}}),
            ("{{ naïve }}", {"naïve": "ok"}),
            ("{{ 日本 }}", {"日本": "ok"}),
            ("{{ x|default:'é' }}", {}),
            ("{% if café %}y{% endif %}", {"café": 1}),
        ],
    )
    def test_matches_django(self, source, context, backend):
        expected = DjangoTemplate(source).render(Context(context))
        assert backend.from_string(source).render(context) == expected

    def test_a_non_ascii_template_does_not_panic_at_construction(self, backend):
        # The panic surfaced from `compile_template` after #2549 moved the
        # parse to construction; assert construction alone is clean.
        DjustTemplate("{{ café }}", backend)


class TestByteOffsetsBecomeCharacterOffsets:
    """Rust spans are byte offsets; Django's dict is in characters.

    A template with a multi-byte character BEFORE the offending token is the
    only place the two differ — and it is exactly where an unconverted offset
    would slice the excerpt off by a few characters.
    """

    def test_the_excerpt_is_still_exactly_the_token(self, backend):
        source = "café ☕ naïve\n{% nosuchtag %}\n"
        debug = _debug_for(source, backend)
        assert debug["during"] == "{% nosuchtag %}"
        assert source[debug["start"] : debug["end"]] == "{% nosuchtag %}"

    def test_it_agrees_with_django_on_a_non_ascii_source(self, backend):
        source = "café ☕ naïve\n{% nosuchtag %}\n"
        with pytest.raises(TemplateSyntaxError) as dj_info:
            Engine(debug=True).from_string(source)
        django_debug = dj_info.value.template_debug
        djust_debug = _debug_for(source, backend)
        for key in POSITIONAL_KEYS:
            assert djust_debug[key] == django_debug[key], key

    def test_the_helper_is_identity_on_ascii(self):
        """The ASCII short-circuit must not change any answer — pin it against
        the general path by feeding the same offsets through a source that is
        ASCII except for one character appended after every offset used."""
        ascii_src = "abcdef\n{% x %}\n"
        non_ascii_src = ascii_src + "é"
        for offset in range(len(ascii_src) + 1):
            a = build_template_debug(ascii_src, None, offset, offset, "m")
            b = build_template_debug(non_ascii_src, None, offset, offset, "m")
            assert a["start"] == b["start"] == offset
            assert a["end"] == b["end"] == offset


class TestEmptyVariableTag:
    """``{{ }}`` and ``{% %}`` are parse-time refusals; both were accepted.

    Django refuses them in ``Parser.parse``
    (``django/template/base.py:483-486`` and ``:497``) — NOT in the lexer.
    ``Lexer.create_token`` returns ``Token(TokenType.VAR, "")`` quite happily;
    the refusal is one layer up, which is what lets a ``{% verbatim %}`` body
    (lexed to TEXT) and a ``{% comment %}`` body (skipped by the parser) hold
    an empty ``{{ }}`` literally. ``TestEmptyTagContextAxis`` below is the
    other half of this surface — the first version of #2557 refused in the
    lexer and regressed both.

    djust built a ``Variable("")`` that rendered as nothing and dropped
    ``{% %}`` on the floor.
    """

    @pytest.mark.parametrize("source", ["{{ }}", "{{        }}", "{{\t}}", "{{}}"])
    def test_refused_at_construction(self, source, backend):
        with pytest.raises(DjustTemplateSyntaxError) as info:
            DjustTemplate(source, backend)
        assert "Empty variable tag on line 1" in str(info.value)

    def test_django_refuses_the_same_sources(self, backend):
        for source in ["{{ }}", "{{        }}", "{{\t}}", "{{}}"]:
            with pytest.raises(TemplateSyntaxError):
                Engine(debug=True).from_string(source)

    def test_the_line_number_is_the_tags_own(self, backend):
        with pytest.raises(DjustTemplateSyntaxError) as info:
            DjustTemplate("a\nb\nc\n{{ }}\n", backend)
        assert "Empty variable tag on line 4" in str(info.value)

    def test_it_is_located_like_every_other_parse_error(self, backend):
        source = "pad\n{{ }}\n"
        debug = _debug_for(source, backend)
        assert debug["during"] == "{{ }}"
        assert debug["line"] == 2

    def test_a_nonempty_variable_is_untouched(self, backend):
        assert backend.from_string("{{ x }}").render({"x": "v"}) == "v"

    def test_a_brace_pair_with_no_closer_is_still_literal_text(self, backend):
        # `has_closer` treats `{{` with no `}}` as plain text (#2558); the new
        # refusal must not reach it.
        assert backend.from_string("a {{ unclosed b").render({}) == "a {{ unclosed b"


class TestEmptyBlockTag:
    """``{% %}`` — the sibling refusal from the same ``Parser.parse`` loop.

    ``django/template/base.py:497`` raises ``Empty block tag on line %d``.
    djust's lexer dropped the token entirely, so an accidentally-empty block
    tag rendered as nothing — the identical silently-invisible bug ``{{ }}``
    had (#2557 review, yellow 4).
    """

    @pytest.mark.parametrize("source", ["{% %}", "{%  %}", "{%\t%}"])
    def test_refused_at_construction(self, source, backend):
        with pytest.raises(DjustTemplateSyntaxError) as info:
            DjustTemplate(source, backend)
        assert "Empty block tag on line 1" in str(info.value)

    @pytest.mark.parametrize("source", ["{% %}", "{%  %}", "{%\t%}"])
    def test_django_refuses_the_same_sources(self, source, backend):
        with pytest.raises(TemplateSyntaxError):
            Engine(debug=True).from_string(source)

    def test_the_line_number_is_the_tags_own(self, backend):
        with pytest.raises(DjustTemplateSyntaxError) as info:
            DjustTemplate("a\nb\nc\n{% %}\n", backend)
        assert "Empty block tag on line 4" in str(info.value)

    def test_it_is_located_like_every_other_parse_error(self, backend):
        debug = _debug_for("pad\n{% %}\n", backend)
        assert debug["during"] == "{% %}"
        assert debug["line"] == 2

    def test_a_nonempty_tag_is_untouched(self, backend):
        assert backend.from_string("{% if x %}y{% endif %}").render({"x": 1}) == "y"


class TestEmptyTagContextAxis:
    """The CONTEXT axis of both refusals, against live Django (#2557 review).

    ``TestEmptyVariableTag`` samples only the bare-source axis. The refusal
    also has a context axis — bare / ``verbatim`` / ``comment`` — and the
    first version of #2557 put the check in ``tokenize_spanned``, BELOW
    djust's ``collect_raw_source``, so every raw-block row here raised where
    Django renders. That is single-variant coverage of a multi-variant
    surface (v1.0.0rc4 finding #1), and it cost working templates:
    ``{% verbatim %}`` exists precisely to show ``{{ }}``-delimited syntax
    literally (Vue, Alpine, Handlebars, djust's own docs pages).

    Every row is compared against Django itself rather than a transcription,
    so RAISES/RENDERS is measured, not asserted from memory.
    """

    #: source -> whether Django refuses it. Verified by
    #: ``test_django_agrees_on_every_row`` rather than trusted.
    ROWS = [
        ("{{ }}", True),
        ("{% %}", True),
        ("{% verbatim %}{{ }}{% endverbatim %}", False),
        ("{% comment %}{{ }}{% endcomment %}", False),
        ("<pre>{% verbatim %}Vue: {{ }} and {{ m }}{% endverbatim %}</pre>", False),
        ("{% verbatim %}{% %}{% endverbatim %}", False),
        ("{% comment %}{% %}{% endcomment %}", False),
        # The exemption is the BODY's, not the template's.
        ("{% verbatim %}{{ }}{% endverbatim %}{{ }}", True),
        ("{{ }}{% verbatim %}{{ }}{% endverbatim %}", True),
        ("{% comment %}{{ }}{% endcomment %}{% %}", True),
    ]

    @pytest.mark.parametrize("source,refused", ROWS)
    def test_django_agrees_on_every_row(self, source, refused):
        """The table is Django's behaviour, measured — not an assumption."""
        engine = Engine(debug=True)
        try:
            engine.from_string(source).render(Context({"m": "x"}))
        except TemplateSyntaxError:
            assert refused, f"Django rendered {source!r}; the table says refused"
        else:
            assert not refused, f"Django refused {source!r}; the table says renders"

    @pytest.mark.parametrize("source,refused", ROWS)
    def test_djust_agrees_with_django(self, source, refused, backend):
        try:
            backend.from_string(source).render({"m": "x"})
        except (DjustTemplateSyntaxError, TemplateSyntaxError):
            assert refused, f"djust refused {source!r}; Django renders it"
        else:
            assert not refused, f"djust rendered {source!r}; Django refuses it"

    def test_a_verbatim_body_still_reaches_the_page(self, backend):
        """The realistic shape: a template teaching `{{ }}` syntax."""
        out = backend.from_string(
            "<pre>{% verbatim %}Vue: {{ }} and {{ m }}{% endverbatim %}</pre>"
        ).render({"m": "ignored"})
        assert "{{ m }}" in out
        assert "Vue:" in out


class TestMultiLineTokenSpan:
    """A token whose span crosses a newline is located, not abandoned.

    ``build_template_debug`` ports ``get_exception_info`` including its
    locating condition ``start >= upto and end <= next_break``. Django can
    rely on that always firing because ``tag_re`` has no ``re.DOTALL`` — a
    Django token never spans a line. **djust's lexer has no such bound** and
    the engine accepts a multi-line tag, so the ported condition never fired
    and the loop fell through with its initial values: ``line: 0``, an empty
    ``during``, and an excerpt clamped to lines 1..11.

    On a 44-line template with the error at line 40 that rendered
    ``error at line 0``, no highlight, and the WRONG ten lines — worse than
    the ``None`` #2557 replaced, because ``None`` at least admits it does not
    know. Reachable two ordinary ways: any multi-line ``{% … %}`` that errors,
    and an unterminated ``{%`` (whose ``has_closer`` scan reaches the next
    ``%}`` anywhere in the file).

    Gate-off: delete the re-locate block in ``build_template_debug`` →
    ``test_a_high_line_number_is_reported`` fails with ``line == 0``.
    """

    #: Far enough down that the wrong window (lines 1..11) is visibly wrong.
    ERROR_LINE = 40

    def _long_source(self) -> str:
        head = "\n".join(f"<p>line {i}</p>" for i in range(1, self.ERROR_LINE))
        tail = "\n".join(f"<p>tail {i}</p>" for i in range(1, 4))
        return head + "\n{% nosuchtag foo\n     bar %}\n" + tail

    def test_a_high_line_number_is_reported(self, backend):
        source = self._long_source()
        debug = _debug_for(source, backend)
        assert debug["line"] == self.ERROR_LINE

    def test_the_excerpt_window_contains_the_error(self, backend):
        source = self._long_source()
        debug = _debug_for(source, backend)
        numbers = [n for n, _ in debug["source_lines"]]
        assert self.ERROR_LINE in numbers, numbers
        # And the old wrong window is genuinely gone.
        assert debug["top"] > 1

    def test_the_offending_tag_is_highlighted(self, backend):
        debug = _debug_for(self._long_source(), backend)
        assert debug["during"].startswith("{% nosuchtag")
        assert "\n" not in debug["during"], "the highlight must stay on one line"

    def test_the_line_reassembles_from_before_during_after(self, backend):
        source = self._long_source()
        debug = _debug_for(source, backend)
        line_text = dict(debug["source_lines"])[debug["line"]]
        assert debug["before"] + debug["during"] + debug["after"] == line_text

    def test_an_unterminated_block_tag_is_located_too(self, backend):
        # `has_closer` scans to the next `%}` ANYWHERE, so this lexes as one
        # token spanning the whole file.
        source = "{% block content }\n<p>hi</p>\n{% endblock %}"
        debug = _debug_for(source, backend)
        assert debug["line"] == 1
        assert debug["during"].startswith("{% block content }")

    def test_a_single_line_span_is_untouched_by_the_relocate(self, backend):
        """The re-locate must fire ONLY when Django's own loop fell through."""
        debug = _debug_for("a\nb\n{% nosuchtag %}\nc\n", backend)
        assert debug["line"] == 3
        assert debug["during"] == "{% nosuchtag %}"

    @pytest.mark.parametrize("span", [(0, 0), (2, 2), (4, 4)])
    def test_a_degenerate_empty_span_keeps_djangos_answer(self, span):
        """The re-locate is guarded on ``end > start``, so a zero-width span
        — which Django's own loop DOES match — keeps Django's answer.

        Measured against live Django rather than asserted: the loop does not
        break, so it keeps overwriting and the answer is the LAST matching
        line, not the first.
        """
        source = "a\nb\n"
        start, end = span
        template = DjangoTemplate("x")
        template.source = source
        template.origin = Origin(name="N")

        class _Tok:
            position = (start, end)

        expected = template.get_exception_info(Exception("m"), _Tok())
        assert build_template_debug(source, "N", start, end, "m") == expected


class TestUnknownSourceName:
    """``from_string`` supplies no origin; the page must not say "None".

    Django's ``Template.__init__`` falls back to ``Origin(UNKNOWN_SOURCE)``,
    so its heading reads ``In template <unknown source>``. djust interpolated
    the raw ``None`` (#2557 review, yellow 5).
    """

    def test_a_none_name_becomes_unknown_source(self):
        got = build_template_debug("{{ }}", None, 0, 5, "m")
        assert got["name"] == UNKNOWN_SOURCE
        assert got["name"] == django_unknown_source()

    def test_a_real_name_is_untouched(self):
        assert build_template_debug("{{ }}", "/a/b.html", 0, 5, "m")["name"] == "/a/b.html"

    def test_the_from_string_path_reports_it(self, backend):
        with pytest.raises(DjustTemplateSyntaxError) as info:
            DjustTemplate("{{ }}", backend)
        assert info.value.template_debug["name"] == UNKNOWN_SOURCE


class TestBuildTemplateDebugPort:
    """``build_template_debug`` reproduces ``get_exception_info`` on the edges.

    Compared against Django's own method rather than a transcription, driven
    through a real ``django.template.Template`` so ``self.source`` is the same
    string and ``token.position`` is the same pair.
    """

    @pytest.mark.parametrize(
        "source",
        [
            "{{ x }}",
            "no newline at all",
            "trailing newline\n",
            "\n\nleading newlines\n",
            "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\neleven\ntwelve\n",
            "",
        ],
    )
    @pytest.mark.parametrize("span", [(0, 0), (0, 1), (1, 3)])
    def test_matches_get_exception_info_for_an_arbitrary_span(self, source, span):
        start, end = span
        if end > len(source):
            pytest.skip("span past the end of this source")
        if "\n" in source[start:end]:
            # Django's contract is DEFINED only for a span inside one line:
            # its ``tag_re`` has no ``re.DOTALL``, so a Django token can never
            # cross a newline and ``get_exception_info`` has no answer for one
            # (it falls through to ``line: 0`` and an empty ``during``). djust
            # DOES produce multi-line tokens, and deliberately diverges there
            # — pinned by ``TestMultiLineTokenSpan`` rather than skipped.
            pytest.skip("multi-line span: Django has no defined answer")
        template = DjangoTemplate("x")  # any template; we drive the method directly
        template.source = source
        template.origin = Origin(name="N")

        class _Tok:
            position = (start, end)

        expected = template.get_exception_info(Exception("m"), _Tok())
        got = build_template_debug(source, "N", start, end, "m")
        assert got == expected
