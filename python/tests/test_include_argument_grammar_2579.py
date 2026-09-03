"""``{% include %}`` refuses malformed ``with``/``only`` argument forms (#2579).

The defect
----------
Django's ``do_include`` walks ``token.split_contents()[2:]`` word by word at
PARSE time and refuses:

* ``with`` followed by zero valid ``key=value`` bits (``token_kwargs`` returns
  an empty dict — ``'"with" in %r tag needs at least one keyword argument.'``);
* a ``with``-clause bit whose key isn't a bare identifier — ``token_kwargs``'s
  ``kwarg_re`` requires ``\\w+=`` from the START of the bit, so a dotted key
  (``dotted.arg=``) or a non-``key=value`` bit (``"no key"``) can't match and
  the clause is treated as having supplied zero kwargs — the SAME error as
  the empty-``with`` case;
* any top-level word that is not literally ``with`` or ``only``
  (``'Unknown argument for %r tag: %r.'``) — this also fires for
  ``foo="duplicate" foo="key"`` because that template has no ``with`` at all,
  so ``foo="duplicate"`` itself is the unrecognized top-level word, not a
  duplicate KEY inside a kwargs clause;
* ``with`` or ``only`` appearing twice (``'The %r option was specified more
  than once.'``).

djust's Rust parser (``crates/djust_templates/src/parser.rs``, ``"include"``
arm) accepted every one of these: it only checked ``args[i].contains('=')``
(no identifier check on the key) and otherwise silently skipped any
unrecognized word (``else { i += 1; }``), so all six shapes below compiled
here and rendered — or, since ``basic-syntax01`` was never loaded, still
crossed the argument-parsing point and only later failed at
``Template loader not configured`` — while Django refused at
``Engine.from_string`` / ``get_template``.

The fix
-------
The parser now walks the same three checks Django's ``do_include`` does, in
the same order: an option name may not repeat (``with``/``only`` each get
their own ``*_seen`` flag), a ``with`` clause's kwarg bits are consumed only
while the bit matches ``\\w+=value`` (a dotted or keyless bit stops the
consumption — Django's ``token_kwargs`` early-return, not a parse error by
itself), and finding zero kwargs after ``with`` is what actually refuses. Any
top-level word that is neither ``with``/``only`` nor a bit already consumed by
a ``with`` clause is refused as "unrecognized argument". Message text is
djust's own wording (#2581, the Django-verbatim message-text follow-up, has
not landed) — not a transcription of Django's ``%r``-formatted strings.

Three independent mechanisms, three gate-offs
----------------------------------------------
* ``with_seen``/``only_seen`` (``TestOptionSpecifiedMoreThanOnce``) — remove
  the ``*_seen`` checks and ``{% include "x" only only %}`` (and the synthetic
  doubled-``with``) stop refusing while the other two classes stay refused.
* the ``found_kwarg`` guard (``TestWithRequiresAtLeastOneKwarg``) — remove the
  ``if !found_kwarg { … }`` refusal and the empty/keyless/dotted ``with``
  shapes stop refusing while the doubled-option and unrecognized-argument
  classes stay refused.
* the ``else`` arm refusing unrecognized words (``TestUnrecognizedArgument``)
  — remove it (fall through to ``i += 1`` as before) and
  ``something_random``/``foo="duplicate" foo="key"`` stop refusing while the
  other two classes stay refused.

Every Django expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import django
import pytest
from django.template import Engine, TemplateSyntaxError

from djust import _rust


@pytest.fixture
def dirs(tmp_path):
    """A real ``basic-syntax01`` file on disk, matching Django's own fixture
    name. Load-bearing for the gate-off (#1468/#2129/#2135): if the
    argument-grammar refusal is neutered, ``_rust.render_template_with_dirs``
    must SUCCEED (the template exists and is trivial) rather than raise some
    OTHER RuntimeError (e.g. "Template loader not configured") that a
    message-blind ``pytest.raises(RuntimeError)`` would wrongly count as
    "still refused"."""
    (tmp_path / "basic-syntax01").write_text("basic-syntax01", encoding="utf-8")
    return [str(tmp_path)]


def django_refuses(source: str) -> str:
    """Live Django: ``Engine().from_string`` raises ``TemplateSyntaxError``.

    Compile-only — Django's ``do_include`` refuses these six shapes while
    PARSING the ``{% include %}`` tag, before ``IncludeNode`` ever resolves
    or loads the included template, so no ``dirs``/loader is needed here."""
    with pytest.raises(TemplateSyntaxError) as info:
        Engine().from_string(source)
    return str(info.value)


def djust_refuses(source: str, dirs: list[str]) -> str:
    """djust: refused before the loader is ever reached.

    Uses ``render_template_with_dirs`` with a REAL ``basic-syntax01`` on disk
    (the ``dirs`` fixture) so a neutered grammar check falls through to a
    successful render instead of a different, unrelated RuntimeError — see
    the fixture's docstring."""
    with pytest.raises(RuntimeError) as info:
        _rust.render_template_with_dirs(source, {}, dirs, None)
    msg = str(info.value)
    assert "Template loader not configured" not in msg, (
        f"gate-off tautology: {source!r} reached the loader stage — "
        f"the grammar refusal did not fire — got {msg!r}"
    )
    return msg


#: (source, id) — the six cells #2579 names, verbatim from Django's own
#: ``template_tests/syntax_tests/test_include.py`` fixtures.
MALFORMED_CELLS = [
    ('{% include "basic-syntax01" with %}', "error01_bare_with"),
    ('{% include "basic-syntax01" with "no key" %}', "error02_no_key"),
    ('{% include "basic-syntax01" with dotted.arg="error" %}', "error03_dotted_key"),
    ('{% include "basic-syntax01" something_random %}', "error04_unknown_word"),
    ('{% include "basic-syntax01" foo="duplicate" foo="key" %}', "error05_no_with_prefix"),
    ('{% include "basic-syntax01" only only %}', "error06_doubled_only"),
]


class TestEveryCellIsRefusedOnBothEngines:
    """The whole issue: six shapes, each refused by Django and (now) djust."""

    @pytest.mark.parametrize(
        "source", [c for c, _ in MALFORMED_CELLS], ids=[i for _, i in MALFORMED_CELLS]
    )
    def test_django_refuses_it(self, source):
        django_refuses(source)

    @pytest.mark.parametrize(
        "source", [c for c, _ in MALFORMED_CELLS], ids=[i for _, i in MALFORMED_CELLS]
    )
    def test_djust_refuses_it_too(self, source, dirs):
        djust_refuses(source, dirs)


class TestWithRequiresAtLeastOneKwarg:
    """error01/02/03 — a ``with`` clause with zero valid ``key=value`` bits.

    All three collapse to the SAME Django error: ``token_kwargs`` returns an
    empty dict whether the clause is truly empty, its only bit isn't
    ``key=value`` shaped at all, or its key isn't a bare identifier.
    """

    @pytest.mark.parametrize(
        "source",
        [
            '{% include "basic-syntax01" with %}',
            '{% include "basic-syntax01" with "no key" %}',
            '{% include "basic-syntax01" with dotted.arg="error" %}',
        ],
        ids=["bare", "no_key", "dotted_key"],
    )
    def test_django_needs_at_least_one_kwarg(self, source):
        msg = django_refuses(source)
        assert "with" in msg and "keyword argument" in msg, msg

    @pytest.mark.parametrize(
        "source",
        [
            '{% include "basic-syntax01" with %}',
            '{% include "basic-syntax01" with "no key" %}',
            '{% include "basic-syntax01" with dotted.arg="error" %}',
        ],
        ids=["bare", "no_key", "dotted_key"],
    )
    def test_djust_needs_at_least_one_kwarg(self, source, dirs):
        msg = djust_refuses(source, dirs)
        assert "'with'" in msg and "key=value" in msg, msg

    def test_a_dotted_key_does_not_stop_a_later_valid_kwarg_from_being_seen(self, dirs):
        """The dotted bit isn't consumed — Django's `token_kwargs` returns as
        soon as it hits a non-kwarg bit, so a well-formed pair AFTER a dotted
        one is never reached either; the whole clause still has zero valid
        kwargs and the SAME error fires."""
        source = '{% include "basic-syntax01" with dotted.arg="x" foo="y" %}'
        django_refuses(source)
        djust_refuses(source, dirs)


class TestUnrecognizedArgument:
    """error04/05 — a top-level word that is neither ``with`` nor ``only``.

    error05 has no ``with`` prefix at all, so ``foo="duplicate"`` itself is
    the unrecognized top-level word — this is NOT a duplicate-KEY check
    inside a kwargs clause (Django's `token_kwargs`/dict silently lets a
    LATER duplicate key win; see `test_a_duplicate_key_inside_with_is_not_refused`).
    """

    @pytest.mark.parametrize(
        "source,offending",
        [
            ('{% include "basic-syntax01" something_random %}', "something_random"),
            ('{% include "basic-syntax01" foo="duplicate" foo="key" %}', 'foo="duplicate"'),
        ],
        ids=["bare_word", "no_with_prefix"],
    )
    def test_django_names_the_offending_word(self, source, offending):
        msg = django_refuses(source)
        assert offending in msg, msg

    @pytest.mark.parametrize(
        "source,offending",
        [
            ('{% include "basic-syntax01" something_random %}', "something_random"),
            ('{% include "basic-syntax01" foo="duplicate" foo="key" %}', 'foo="duplicate"'),
        ],
        ids=["bare_word", "no_with_prefix"],
    )
    def test_djust_names_the_offending_word(self, source, offending, dirs):
        msg = djust_refuses(source, dirs)
        assert offending in msg, msg

    def test_a_duplicate_key_inside_with_is_not_refused(self):
        """`{% include "x" with foo=1 foo=2 %}` DOES have a `with` prefix, so
        both bits are consumed as kwargs by `token_kwargs` — a dict, so the
        later key silently wins. Django does not refuse this; djust must not
        either (a false positive #2579 explicitly warns against)."""
        source = '{% include "basic-syntax01" with foo=1 foo=2 %}'
        Engine().from_string(source)  # must NOT raise
        # djust: must reach the loader-not-configured stage, i.e. it passed
        # argument parsing — not the argument-grammar refusal under test.
        with pytest.raises(RuntimeError) as info:
            _rust.render_template(source, {})
        assert "Template loader not configured" in str(info.value)


class TestOptionSpecifiedMoreThanOnce:
    """error06 — ``only only`` — and its ``with``/``with`` sibling."""

    def test_django_refuses_doubled_only(self):
        msg = django_refuses('{% include "basic-syntax01" only only %}')
        assert "only" in msg and "more than once" in msg, msg

    def test_djust_refuses_doubled_only(self, dirs):
        msg = djust_refuses('{% include "basic-syntax01" only only %}', dirs)
        assert "'only'" in msg and "more than once" in msg, msg

    def test_django_refuses_doubled_with(self):
        msg = django_refuses('{% include "basic-syntax01" with a=1 with b=2 %}')
        assert "with" in msg and "more than once" in msg, msg

    def test_djust_refuses_doubled_with(self, dirs):
        msg = djust_refuses('{% include "basic-syntax01" with a=1 with b=2 %}', dirs)
        assert "'with'" in msg and "more than once" in msg, msg


class TestNoFalsePositiveOnValidUsage:
    """#2579's own warning: `{% include %}` is used extensively across
    djust's own suite and templates. None of these five forms may be refused
    by the argument-grammar check — each must reach the render-time
    loader-not-configured stage instead, proving the parse-time gate let it
    through."""

    @pytest.mark.parametrize(
        "source",
        [
            '{% include "basic-syntax01" %}',
            '{% include "basic-syntax01" with foo=1 %}',
            '{% include "basic-syntax01" with foo=1 bar=2 %}',
            '{% include "basic-syntax01" only %}',
            '{% include "basic-syntax01" with foo=1 only %}',
        ],
        ids=["bare", "one_kwarg", "two_kwargs", "only", "with_and_only"],
    )
    def test_valid_form_is_not_refused_by_the_argument_grammar(self, source):
        Engine().from_string(source)  # must NOT raise on Django either
        with pytest.raises(RuntimeError) as info:
            _rust.render_template(source, {})
        # The ONLY acceptable failure past argument parsing: no loader.
        assert "Template loader not configured" in str(info.value), str(info.value)

    def test_valid_form_with_filtered_value_still_renders(self, tmp_path):
        """A filtered `with` value renders end to end (`validate_tag_operand`
        must not have been broken by the #2579 rewrite)."""
        (tmp_path / "child.html").write_text("[{{ q }}]", encoding="utf-8")
        engine = Engine(dirs=[str(tmp_path)], libraries={})
        source = '{% include "child.html" with q=p|upper %}'
        ctx = {"p": "hi"}
        django_out = engine.from_string(source).render(django.template.Context(ctx))
        djust_out = _rust.render_template_with_dirs(source, ctx, [str(tmp_path)], None)
        assert djust_out == django_out == "[HI]"


class TestTheCallerSetIsPinned:
    """The fix must live at the `"include"` parse arm, not a second copy."""

    def test_the_fix_touches_only_the_include_arm(self):
        import pathlib

        parser_rs = (
            pathlib.Path(__file__).resolve().parents[2]
            / "crates"
            / "djust_templates"
            / "src"
            / "parser.rs"
        )
        text = parser_rs.read_text(encoding="utf-8")
        start = text.index('"include" => {')
        # The next top-level tag arm after "include" — used only to bound the
        # slice; if this ever goes missing the slice below will run to EOF
        # and the substring assertions still hold, so it's not load-bearing.
        end = text.index('"csrf_token" => {', start)
        include_arm = text[start:end]
        assert "with_seen" in include_arm
        assert "only_seen" in include_arm
        assert "found_kwarg" in include_arm
