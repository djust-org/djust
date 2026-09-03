"""A template that does not parse is refused when it is BUILT, not rendered (#2549).

The defect
----------
Django's ``Engine.from_string`` / ``get_template`` compile the source and raise
``TemplateSyntaxError`` before any render. ``DjustTemplate.__init__`` stored the
string and left the Rust parse to the first ``render``, so the same defect
surfaced one call later — and a defect in a branch that never rendered never
surfaced at all::

    {% if False %}{% unknown %}{% endif %}    django   TemplateSyntaxError at from_string
                                              djust    ''  (rendered, silently)

Two render-time refusals were also promoted to parse time, where Django has
them: an unregistered tag (``Unsupported template tag``, the parser used to
build ``Node::UnsupportedTag`` and the RENDERER refused it) and an unknown
``{% templatetag %}`` argument. ``widthratio``'s non-numeric final argument
stays at render — Django raises it from ``WidthRatioNode.render``.

The exception
-------------
``DjustTemplateSyntaxError`` is Django's ``TemplateSyntaxError`` (so Django's
own ``assertRaises`` / loaders / debug view see the type they expect) AND a
``RuntimeError`` (the type every Rust engine failure has crossed to Python as,
so a caller catching ``RuntimeError`` around construction — ``components/base.py``
— keeps catching). The message text is the engine's, unchanged: it is a
published contract. ``template_debug`` exists and is ``None``; the dict is
#2557's job.

Gate-off (#1468 / #2135, each mechanism alone):
* revert the construction-time parse (``DjustTemplate._compile``) →
  ``TestConstructionTimeRefusal`` goes red, ``TestPromotedToParseTime`` stays green;
* revert the parser's one-arm promotion (build ``Node::UnsupportedTag`` again) →
  ``TestPromotedToParseTime`` goes red, ``TestConstructionTimeRefusal`` stays green.

Every Django expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from django.template import Engine, TemplateSyntaxError
from django.template import Template as DjangoTemplate
from djust import _rust
from djust.template import DjustTemplate, DjustTemplateBackend, DjustTemplateSyntaxError

PARSER_RS = (
    pathlib.Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "parser.rs"
)


@pytest.fixture
def backend(tmp_path: pathlib.Path) -> DjustTemplateBackend:
    return DjustTemplateBackend(
        {"NAME": "djust", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    )


def django_refuses_at_construction(source: str) -> str:
    """Live Django: ``Engine.from_string`` raises ``TemplateSyntaxError``."""
    with pytest.raises(TemplateSyntaxError) as info:
        Engine().from_string(source)
    return str(info.value)


#: (source, substring of djust's message) — every class the loop moved. The
#: first four were already parse-time inside the engine; the last two are the
#: render-time refusals promoted by #2549.
PARSE_TIME_CLASSES = [
    ("{% for x in %}", "Invalid for tag syntax"),
    ("{% if x %}", "Unclosed if tag"),
    ("{{ x|nosuchfilter }}", "Unknown filter: nosuchfilter"),
    ("{{ x|upper:1 }}", "upper"),
    ("{% unknowntag a b %}", "Unsupported template tag '{% unknowntag a b %}'"),
    ("{% templatetag bogus %}", "Unknown templatetag argument: 'bogus'"),
]


class TestConstructionTimeRefusal:
    """``from_string`` / ``get_template`` / ``DjustTemplate(...)`` refuse, Django-shaped."""

    @pytest.mark.parametrize(
        "source,needle", PARSE_TIME_CLASSES, ids=[s for s, _ in PARSE_TIME_CLASSES]
    )
    def test_from_string_refuses_where_django_does(self, backend, source, needle):
        django_refuses_at_construction(source)
        with pytest.raises(TemplateSyntaxError) as info:
            backend.from_string(source)
        exc = info.value
        assert isinstance(exc, DjustTemplateSyntaxError)
        assert isinstance(exc, RuntimeError), "callers catching RuntimeError must keep catching"
        assert needle in str(exc), str(exc)

    @pytest.mark.parametrize(
        "source,needle", PARSE_TIME_CLASSES, ids=[s for s, _ in PARSE_TIME_CLASSES]
    )
    def test_get_template_refuses_and_names_the_origin(self, backend, tmp_path, source, needle):
        (tmp_path / "bad.html").write_text(source, encoding="utf-8")
        with pytest.raises(DjustTemplateSyntaxError) as info:
            backend.get_template("bad.html")
        assert needle in str(info.value)
        assert info.value.origin is not None
        assert info.value.origin.template_name == "bad.html"

    def test_untaken_branch_is_refused_like_django(self, backend):
        """The whole point: a defect Django refuses at load and djust used to render."""
        source = "{% if False %}{% unknown %}{% endif %}"
        django_refuses_at_construction(source)
        with pytest.raises(TemplateSyntaxError, match="Unsupported template tag '{% unknown %}'"):
            backend.from_string(source)

    def test_untaken_branch_unknown_filter_is_refused_like_django(self, backend):
        source = "{% if False %}{{ x|nosuchfilter }}{% endif %}"
        django_refuses_at_construction(source)
        with pytest.raises(TemplateSyntaxError, match="Unknown filter: nosuchfilter"):
            backend.from_string(source)

    def test_direct_construction_is_the_one_site(self):
        """The suite adapter and both backend entry points build ``DjustTemplate``
        directly — the parse lives in ``__init__`` so every constructor refuses."""
        with pytest.raises(DjustTemplateSyntaxError):
            DjustTemplate("{% unknown %}", backend=None)

    def test_valid_template_constructs_and_renders(self, backend):
        t = backend.from_string("{% templatetag openblock %} {{ x|upper }}")
        assert t.render({"x": "a"}) == "{% A"

    def test_the_construction_parse_is_cached_for_the_render(self, backend):
        """One parse, not two: the render finds the construction's cache entry."""
        source = "{{ y }} unique-to-2549-cache-test"
        assert _rust.template_cache_contains(source) is False
        backend.from_string(source)
        assert _rust.template_cache_contains(source) is True

    def test_message_is_the_engine_text_unchanged(self, backend):
        """Published contract: the same bytes the render path raised with."""
        with pytest.raises(DjustTemplateSyntaxError) as info:
            backend.from_string("{% ifchanged %}x{% endifchanged %}")
        assert str(info.value) == (
            "Template error: Unsupported template tag '{% ifchanged %}'. "
            "Register a handler via djust._rust.register_tag_handler(), "
            "or use Django's template engine instead."
        )

    def test_render_time_failure_is_unchanged(self, backend):
        """``widthratio``'s non-numeric final argument is a RENDER-time error on
        Django (``WidthRatioNode.render``) and stays one here: construction
        succeeds, render raises the wrapper's bare ``Exception`` as before."""
        source = "{% widthratio a b c %}"
        Engine().from_string(source)  # Django constructs it fine
        t = backend.from_string(source)
        with pytest.raises(Exception, match=r"^Error rendering template: .*widthratio") as info:
            t.render({"a": 1, "b": 2, "c": "notanumber"})
        assert type(info.value) is Exception


class TestPromotedToParseTime:
    """The two render-time classes the parser now refuses itself — pinned on the
    Rust parse directly, so they are red when ONLY the promotion is reverted."""

    def test_unregistered_tag_refuses_at_parse(self):
        with pytest.raises(RuntimeError, match="Unsupported template tag '{% unknowntag %}'"):
            _rust.compile_template("{% if False %}{% unknowntag %}{% endif %}")

    def test_unknown_templatetag_argument_refuses_at_parse(self):
        with pytest.raises(RuntimeError, match="Unknown templatetag argument: 'bogus'"):
            _rust.compile_template("{% if False %}{% templatetag bogus %}{% endif %}")

    @pytest.mark.parametrize(
        "name",
        [
            "openblock",
            "closeblock",
            "openvariable",
            "closevariable",
            "openbrace",
            "closebrace",
            "opencomment",
            "closecomment",
        ],
    )
    def test_every_django_templatetag_name_still_parses(self, name):
        source = "{% templatetag " + name + " %}"
        expected = DjangoTemplate(source).render(__import__("django.template").template.Context())
        _rust.compile_template(source)
        assert _rust.render_template(source, {}) == expected

    def test_late_registration_is_honoured(self):
        """A failed parse is never cached: register the handler, parse again, it works."""
        source = "{% late_2549_tag %}"
        with pytest.raises(RuntimeError, match="Unsupported template tag"):
            _rust.compile_template(source)

        class Late:
            def render(self, args, context):
                return "late"

        _rust.register_tag_handler("late_2549_tag", Late())
        try:
            _rust.compile_template(source)
            assert _rust.render_template(source, {}) == "late"
        finally:
            _rust.unregister_tag_handler("late_2549_tag")

    def test_parser_no_longer_builds_unsupported_tag(self):
        """Structural pin: outside ``#[cfg(test)]`` the only ``UnsupportedTag`` in
        parser.rs is the enum variant — no arm constructs it any more."""
        src = PARSER_RS.read_text(encoding="utf-8")
        production = src.split("#[cfg(test)]", 1)[0]
        assert "Ok(Some(Node::UnsupportedTag" not in production
        assert re.findall(r"Node::UnsupportedTag\s*\{", production) == []
        assert len(re.findall(r"^\s*UnsupportedTag\s*\{", production, re.MULTILINE)) == 1, (
            "expected exactly the enum declaration"
        )


class TestExistingCallersStillCatch:
    def test_except_runtime_error_catches_it(self, backend):
        """``components/base.py`` falls back to Django on ``RuntimeError``."""
        caught = None
        try:
            backend.from_string("{% unknown %}")
        except RuntimeError as e:
            caught = e
        assert isinstance(caught, DjustTemplateSyntaxError)

    def test_template_debug_attribute_exists_and_is_none(self, backend):
        with pytest.raises(DjustTemplateSyntaxError) as info:
            backend.from_string("{% unknown %}")
        assert info.value.template_debug is None

    def test_django_debug_reporter_accepts_it(self, backend, rf):
        """Django's technical-500 page reads ``template_debug`` with ``getattr``
        and renders the plain traceback when it is ``None`` — run the real
        reporter rather than trusting the citation."""
        import sys

        from django.views.debug import ExceptionReporter

        try:
            backend.from_string("{% unknown %}")
        except DjustTemplateSyntaxError:
            reporter = ExceptionReporter(rf.get("/"), *sys.exc_info())
            data = reporter.get_traceback_data()
        assert data["template_info"] is None
        assert "Unsupported template tag" in data["exception_value"]
