"""Django-verbatim `TemplateSyntaxError` message text (#2581).

After #2549 (parse at construction), each of these cells raised
`DjustTemplateSyntaxError` at the right time — a genuine `TemplateSyntaxError`
subclass — but with djust's OWN wording rather than Django's. The cell fails
only on `assertRaisesMessage`, never on exception type or timing.

Two independent mechanisms:

* **`Unclosed tag on line N: 'x'. Looking for one of: y, z.`** — needs the
  OPENING tag's own line number (`token.lineno` in Django), not the point
  where the token stream ran out. All three sites (`parse_if_block`,
  `parse_for_block`, `parse_block`) already receive `spans`/`source` as
  parameters — the #2557 infrastructure this issue named as its own
  blocker — so this is a formatting fix, not new plumbing. One shared
  helper (`unclosed_tag_error`), not a new match arm — these are separate
  parse FUNCTIONS, not match ARMS in the same match statement, so the
  #2580 scanner-fragility hazard (a multi-name arm sitting immediately
  before a match's `_ => {}` wildcard) does not apply here; confirmed by
  running the differential-reachability manifest tests unchanged.
* **Six independent one-line message swaps** — `extends`, `for` (×2),
  `now`, `static` — each verified against LIVE Django, not transcribed
  from the issue body (which paraphrased some of these).

**Not fixed here, and explicitly not part of the 8-cell scope this PR
closes:**

* `test_static_prefixtag_without_as` (`{% get_media_prefix %}`) — the tag
  is not implemented AT ALL (confirmed: no `get_media_prefix` /
  `get_static_prefix` anywhere in the crate or `python/djust/`), so djust
  currently answers "Unsupported template tag", not a wrong message for a
  supported one. Building the tag is a #2556-adjacent feature gap, not a
  message-text fix.
* `tests.DebugTemplateTests.test_compile_tag_error` — the issue's own text
  excludes this from its 32-cell count. Different failure shape entirely
  (`RuntimeError not raised`), needs the `{% load %}` bridge to import a
  test-only library whose compile function deliberately raises.

Every Django expectation below is LIVE Django, never a transcription.
"""

from __future__ import annotations

import pytest
from django.template import Context, Engine, TemplateSyntaxError

from djust import _rust


def django_message(source: str, context: dict | None = None) -> str:
    with pytest.raises(TemplateSyntaxError) as info:
        t = Engine().from_string(source)
        t.render(Context(context or {}))
    return str(info.value)


def djust_message(source: str, context: dict | None = None) -> str:
    with pytest.raises(RuntimeError) as info:
        _rust.render_template(source, context or {})
    return str(info.value)


def djust_renders(source: str, context: dict | None = None) -> str:
    return _rust.render_template(source, context or {})


# --------------------------------------------------------------------------- #
# the shared "Unclosed tag" mechanism
# --------------------------------------------------------------------------- #


class TestUnclosedTagCarriesTheOpeningLine:
    CASES = [
        ("{% block a %}", "block", "endblock"),
        ("{% if a %}", "if", "elif"),  # 'elif' substring is enough to confirm the list
    ]

    @pytest.mark.parametrize("source,_name,_frag", CASES, ids=[c[1] for c in CASES])
    def test_django_message(self, source, _name, _frag):
        msg = django_message(source, {"a": 1})
        assert "Unclosed tag on line" in msg

    def test_djust_block_matches_django_exactly(self):
        assert djust_message("{% block a %}") == (
            "Template error: Unclosed tag on line 1: 'block'. Looking for one of: endblock."
        )
        assert django_message("{% block a %}") == (
            "Unclosed tag on line 1: 'block'. Looking for one of: endblock."
        )

    def test_djust_if_matches_django_exactly(self):
        assert djust_message("{% if a %}", {"a": 1}) == (
            "Template error: Unclosed tag on line 1: 'if'. Looking for one of: elif, else, endif."
        )
        assert django_message("{% if a %}", {"a": 1}) == (
            "Unclosed tag on line 1: 'if'. Looking for one of: elif, else, endif."
        )

    def test_the_line_number_is_the_opening_tags_own_line(self):
        """A multi-line template: the unclosed {% block %} opens on line 3,
        not line 1 (parse start) or the end of the file."""
        source = "line1\nline2\n{% block a %}\nline4"
        msg = djust_message(source)
        assert "line 3" in msg, msg
        assert django_message(source) == msg.removeprefix("Template error: ")

    def test_ordinary_closed_if_and_block_still_render(self):
        assert djust_renders("{% block a %}x{% endblock %}") == "x"
        assert djust_renders("{% if a %}y{% endif %}", {"a": 1}) == "y"
        assert (
            djust_renders("{% if a %}y{% elif b %}z{% else %}n{% endif %}", {"a": 0, "b": 1}) == "z"
        )


# --------------------------------------------------------------------------- #
# extends
# --------------------------------------------------------------------------- #


class TestExtendsTakesOneArgument:
    def test_django_message(self):
        assert django_message("{% extends %}") == "'extends' takes one argument"

    def test_djust_matches(self):
        msg = djust_message("{% extends %}")
        assert "'extends' takes one argument" in msg

    def test_valid_extends_is_unaffected(self):
        # No loader configured on the raw entry — confirms the arg-count
        # check does not fire (it reaches the loader stage instead), not
        # that the whole tag "works" in this harness.
        with pytest.raises(RuntimeError) as info:
            _rust.render_template('{% extends "base.html" %}{% block a %}x{% endblock %}', {})
        assert "takes one argument" not in str(info.value)


# --------------------------------------------------------------------------- #
# for
# --------------------------------------------------------------------------- #


class TestForStatementMessages:
    def test_django_too_few_words(self):
        assert django_message("{% for x items %}{% endfor %}", {"items": (1, 2)}) == (
            "'for' statements should have at least four words: for x items"
        )

    def test_djust_too_few_words_matches(self):
        msg = djust_message("{% for x items %}{% endfor %}", {"items": (1, 2)})
        assert "'for' statements should have at least four words: for x items" in msg

    def test_django_missing_in_keyword(self):
        assert django_message("{% for x from items %}{% endfor %}", {"items": (1, 2)}) == (
            "'for' statements should use the format 'for x in y': for x from items"
        )

    def test_djust_missing_in_keyword_matches(self):
        msg = djust_message("{% for x from items %}{% endfor %}", {"items": (1, 2)})
        assert "'for' statements should use the format 'for x in y': for x from items" in msg

    def test_ordinary_for_loops_still_work(self):
        assert djust_renders("{% for x in items %}{{ x }}{% endfor %}", {"items": [1, 2]}) == "12"
        assert (
            djust_renders("{% for a, b in items %}{{ a }}{{ b }}{% endfor %}", {"items": [(1, 2)]})
            == "12"
        )


# --------------------------------------------------------------------------- #
# now
# --------------------------------------------------------------------------- #


class TestNowStatementTakesOneArgument:
    def test_django_message(self):
        assert django_message("{% now %}") == "'now' statement takes one argument"

    def test_djust_matches(self):
        msg = djust_message("{% now %}")
        assert "'now' statement takes one argument" in msg

    def test_valid_now_still_renders(self):
        assert djust_renders('{% now "Y" %}').isdigit()


# --------------------------------------------------------------------------- #
# static
# --------------------------------------------------------------------------- #


class TestStaticTakesAtLeastOneArgument:
    def test_django_message(self):
        # Django's own `template_tests` engine has the `static` library
        # pre-registered; a bare `Engine()` does not, so `{% load static %}`
        # itself fails first with an unrelated "not a registered tag
        # library" error unless it's registered explicitly here too.
        engine = Engine(libraries={"static": "django.templatetags.static"})
        with pytest.raises(TemplateSyntaxError) as info:
            t = engine.from_string("{% load static %}{% static %}")
            t.render(Context({}))
        assert str(info.value) == "'static' takes at least one argument (path to file)"

    def test_djust_matches(self):
        msg = djust_message("{% static %}")
        assert "'static' takes at least one argument (path to file)" in msg


# --------------------------------------------------------------------------- #
# scoped out — confirm the excluded cell is genuinely a different shape,
# not silently fixed or silently made worse by this PR
# --------------------------------------------------------------------------- #


class TestGetMediaPrefixIsStillUnimplemented:
    """`{% get_media_prefix as var %}` is not built — this PR must not
    accidentally start answering something DIFFERENT for it (a crash, a
    wrong render) while leaving the message wrong; "unsupported tag" is
    the correct, unchanged shape until the tag itself exists (#2531-
    adjacent, out of #2581's scope)."""

    def test_still_unsupported(self):
        msg = djust_message("{% get_media_prefix ad media_prefix %}")
        assert "Unsupported template tag" in msg
