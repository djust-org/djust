"""A ``Component``'s hybrid ``template`` follows the attribute rule of the other two paths (#3900).

``Component.render`` draws a component by the crate instance, else by its ``template`` (the hybrid
path), else by ``_render_custom()``. The crate constructor takes plain text only, so a value it
refuses (a lazy string is one) leaves a class that has a ``template`` to be drawn by it, and a
class with no crate counterpart is drawn by it always. That path wrote ``{{ src }}`` into ``src=``
with no URL check and only autoescape.

* ``TestTheIssue``: the cells of the issue, on ``Avatar``.
* ``TestValueKinds``: every kind of value that can reach the engine, in a URL and in a plain slot.
* ``TestWhitespaceAroundEquals``: the attribute name is followed through HTML's own states.
* ``TestWholeValue``: where in a URL value a slot is, and what is judged for each position.
* ``TestTheRule``: what the rewrite does and does not touch.
* ``TestTheScanner``: where an attribute slot is.
* ``TestBlocksInAValue`` / ``TestLiteralsABrowserNormalises``: a literal that may be skipped or
  that a browser reads differently never fixes the value.
* ``TestNamesBuiltByTags`` / ``TestWhatIsNotJudged`` / ``TestTheWarning``: the URL values that
  are escaped but not judged, and that they say so (class and attribute names only).
* ``TestDivergencesFromTheEngine``: where a slot prints something other than the engine alone.
* ``TestEveryHybridTemplate``: derived from the code, not listed: every class with a template.

The sweep in ``test_marked_safe_attribute_sweep_3898.py`` drives the same path across every
class, claim, payload and parameter.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from django.utils.functional import lazy
from django.utils.safestring import SafeString, mark_safe

import djust.components as components_package
from djust.components import _hybrid_attrs as hybrid
from djust.components.base import Component
from djust.components.ui.avatar_simple import Avatar
from djust.components.ui.button_group_simple import ButtonGroup
from djust.components.ui.input_simple import Input

pytestmark = pytest.mark.components


def _lazy(text: str, *, safe: bool = True) -> object:
    return lazy((lambda: mark_safe(text)) if safe else (lambda: text), str)()


class _HtmlStr(str):
    """A ``str`` with ``__html__`` (what ``markupsafe.Markup`` is): not ``SafeData``."""

    def __html__(self) -> str:
        return str(self)


class _HtmlObject:
    """Not a ``str`` at all: it only has ``__html__``, which ``conditional_escape`` honours."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __html__(self) -> str:
        return self.text

    def __str__(self) -> str:
        return self.text


class _TextFormMarkedSafe:
    """Not text and not marked safe itself, but the text it gives (``str(x)``) is."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __str__(self) -> str:
        return mark_safe(self.text)


def _callable(text: str):
    return lambda: mark_safe(text)


def _callable_of_object(text: str):
    return lambda: _TextFormMarkedSafe(text)


#: Every kind of value that can reach the engine with text in it. Plain text is the baseline.
KINDS = {
    "plain": lambda t: t,
    "mark_safe": mark_safe,
    "safestring-subclass": type("_SafeSub", (SafeString,), {}),
    "html-str": _HtmlStr,
    "html-object": _HtmlObject,
    "lazy-safe": _lazy,
    "lazy-plain": lambda t: _lazy(t, safe=False),
    "text-form-marked-safe": _TextFormMarkedSafe,
    "callable-marked-safe": _callable,
    "callable-of-text-form": _callable_of_object,
}


def _attrs(markup: str, tag: str) -> dict[str, str]:
    found: dict[str, str] = {}

    class P(HTMLParser):
        def handle_starttag(self, t, attrs):
            if t == tag and not found:
                found.update({k: (v or "") for k, v in attrs})

    P(convert_charrefs=True).feed(markup)
    return found


def _hybrid(cls: type[Component], **kwargs):
    """``cls`` drawn through its template, whatever the crate would have done."""
    obj = cls(**kwargs)
    obj._rust_instance = None
    return obj


def _draw(template: str, **context):
    """A one-off hybrid component with ``template`` and a fixed context."""

    class Box(Component):
        pass

    Box.template = template
    Box.get_context_data = lambda self: dict(context)  # type: ignore[method-assign]
    return str(Box().render())


JS = "javascript:alert(1)"
BREAKOUT = 'md" onclick="alert(1)" x="'


@pytest.fixture(autouse=True)
def _bootstrap(settings, monkeypatch):
    settings.DJUST_CONFIG = {"css_framework": "bootstrap5"}
    # Auto-call is the project's setting, read by the engine call and by `slot_text`; another test
    # that leaves the global config switched off must not decide what these tests exercise.
    monkeypatch.setattr("djust.config.template_auto_call_enabled", lambda: True)


class TestTheIssue:
    """The cells of the issue: ``Avatar`` with ``src`` / ``size`` / ``status`` a lazy marked-safe string."""

    def test_a_lazy_script_url_is_neutralised_like_the_plain_string(self) -> None:
        plain = Avatar(src=JS, alt="x").render()
        lazy_out = Avatar(src=_lazy(JS), alt="x").render()
        assert _attrs(plain, "img")["src"] == "#"
        assert _attrs(lazy_out, "img")["src"] == "#"
        assert "javascript:" not in lazy_out

    def test_a_lazy_script_url_is_drawn_by_the_template_path(self) -> None:
        """The premise: the crate refuses it, so the template is the path under test."""
        if Avatar._rust_impl_class is None:
            pytest.skip("the Rust extension is not built")
        assert Avatar(src=JS)._rust_instance is not None
        assert Avatar(src=_lazy(JS))._rust_instance is None

    @pytest.mark.parametrize("param", ["size", "status"])
    def test_a_lazy_marked_safe_attribute_value_stays_inside_its_attribute(
        self, param: str
    ) -> None:
        out = Avatar(src="/a.png", alt="x", **{param: _lazy(BREAKOUT)}).render()
        assert "onclick" not in _attrs(out, "div"), out
        assert "&quot;" in out

    def test_a_rustless_class_is_covered_without_any_lazy_value(self) -> None:
        """``Input`` has no crate counterpart: its template draws it for a plain string too."""
        assert Input._rust_impl_class is None
        out = Input(name="n", placeholder=mark_safe(BREAKOUT), value=mark_safe(BREAKOUT)).render()
        assert "onclick" not in _attrs(out, "input"), out

    def test_a_loop_variable_in_an_attribute_is_escaped(self) -> None:
        assert ButtonGroup._rust_impl_class is None
        out = ButtonGroup(buttons=[{"label": "a", "variant": mark_safe(BREAKOUT)}]).render()
        assert "onclick" not in _attrs(out, "button"), out


class TestValueKinds:
    """A value reaches the engine in many shapes. Whatever the shape, an attribute slot holds
    plain text and a URL slot is judged on that text."""

    @pytest.mark.parametrize("kind", sorted(KINDS))
    def test_a_url_slot_is_judged_whatever_the_value_is(self, kind: str) -> None:
        out = _hybrid(Avatar, src=KINDS[kind](JS), alt="x").render()
        assert _attrs(out, "img")["src"] == "#", (kind, out)

    @pytest.mark.parametrize("kind", sorted(KINDS))
    def test_a_plain_slot_is_escaped_whatever_the_value_is(self, kind: str) -> None:
        out = _hybrid(
            Avatar, src="/a.png", size=KINDS[kind](BREAKOUT), status=KINDS[kind](BREAKOUT)
        ).render()
        assert "onclick" not in _attrs(out, "div"), (kind, out)
        assert "&quot;" in out

    @pytest.mark.parametrize("kind", sorted(KINDS))
    def test_a_good_value_prints_the_same_whatever_it_is(self, kind: str) -> None:
        """A kind of value is not what changes the output: its text is."""
        out = _hybrid(Avatar, src=KINDS[kind]("/a?x=1&y=2"), alt="x", size=KINDS[kind]("lg"))
        assert _attrs(out.render(), "img")["src"] == "/a?x=1&y=2"
        assert "avatar-lg" in out.render()

    @pytest.mark.parametrize(
        "value",
        [
            b"bytes",
            ["a", "b"],
            {"k": "v"},
            {1, 2},
            (1, 2),
            object(),
            Decimal("1.50"),
            True,
            7,
            3.5,
            None,
            range(3),
            _TextFormMarkedSafe,
        ],
        ids=lambda v: type(v).__name__,
    )
    def test_everything_else_becomes_the_exact_str_type(self, value) -> None:
        """No value kind is waved through: the text of anything is taken as an exact ``str``."""
        text = hybrid.slot_text(value)
        assert type(text) is str

    def test_a_value_whose_text_form_is_marked_safe_comes_out_plain(self) -> None:
        text = hybrid.slot_text(_TextFormMarkedSafe("<b>"))
        assert type(text) is str and text == "<b>"
        assert type(hybrid.slot_text(_callable("<b>"))) is str

    def test_a_callable_is_called_as_the_engine_calls_it(self) -> None:
        assert hybrid.slot_text(lambda: "x") == "x"

    def test_a_callable_that_opts_out_is_not_called(self) -> None:
        def f():
            raise AssertionError("called")

        f.alters_data = True  # type: ignore[attr-defined]
        assert hybrid.slot_text(f) == ""


class TestWhitespaceAroundEquals:
    """HTML allows whitespace between an attribute name and ``=`` and between ``=`` and the value."""

    FORMS = [
        '<a href="{{ u }}">',
        '<a href ="{{ u }}">',
        '<a href= "{{ u }}">',
        '<a href = "{{ u }}">',
        '<a href\n="{{ u }}">',
        '<a href=\n"{{ u }}">',
        '<a href\r\n=\r\n"{{ u }}">',
        '<a\thref\t=\t"{{ u }}">',
        "<a href ='{{ u }}'>",
        "<a href = {{ u }}>",
        '<a title="t" href \t = "{{ u }}" id="i">',
        '<a {% if x %}href ="{{ u }}"{% endif %}>',
    ]

    @pytest.mark.parametrize("template", FORMS)
    @pytest.mark.parametrize("kind", ["plain", "mark_safe", "text-form-marked-safe"])
    def test_the_url_is_judged(self, template: str, kind: str) -> None:
        out = _draw(template, u=KINDS[kind](JS), x=True)
        assert "javascript:" not in out, out
        assert "#" in out

    @pytest.mark.parametrize("template", FORMS)
    def test_a_good_url_is_kept(self, template: str) -> None:
        out = _draw(template, u="/ok?a=1", x=True)
        assert "/ok?a=1" in out

    @pytest.mark.parametrize(
        "template",
        ['<img src\t=\t"{{ u }}">', "<img src\n= '{{ u }}'>", '<img src ="{{ u }}">'],
    )
    def test_an_image_source_with_whitespace_keeps_a_data_image(self, template: str) -> None:
        uri = "data:image/png;base64,AAAA"
        assert uri in _draw(template, u=uri)
        assert "data:text" not in _draw(template, u="data:text/html,x")

    @pytest.mark.parametrize("template", FORMS)
    def test_the_scanner_names_the_attribute(self, template: str) -> None:
        (slot,) = hybrid.slots(template)
        assert (slot.tag, slot.attr) in {("a", "href"), ("img", "src")}


class TestWholeValue:
    """The scheme belongs to the value as it is assembled, never to one slot of it.

    Decided per position: a slot that can still choose the scheme (it is first, or only characters
    a scheme is made of come before it) is judged together with everything that follows it and
    every other slot of the value; a literal character that cannot be part of a scheme before
    the slot fixes the value, and the slot is escaped, not judged.
    """

    @pytest.mark.parametrize(
        ("template", "context", "kept"),
        [
            ('<a href="/search?q={{ q }}">', {"q": "foo:bar"}, "/search?q=foo:bar"),
            ('<a href="/u/{{ name }}">', {"name": "a:b"}, "/u/a:b"),
            (
                '<a href="http://{{ host }}/">',
                {"host": "example.com:8080"},
                "http://example.com:8080/",
            ),
            ('<a href="?next={{ n }}">', {"n": "javascript:x"}, "?next=javascript:x"),
            ('<a href="#{{ frag }}">', {"frag": "a:b"}, "#a:b"),
            ('<a href="/a?x={{ a }}&y={{ b }}">', {"a": "1:2", "b": "3:4"}, "/a?x=1:2&y=3:4"),
            ('<a href="1{{ x }}">', {"x": "a:b"}, "1a:b"),
            ('<a href="  /p/{{ x }}">', {"x": "a:b"}, "/p/a:b"),
        ],
    )
    def test_a_slot_after_a_literal_that_fixes_the_value_is_not_judged(
        self, template: str, context: dict, kept: str
    ) -> None:
        for kind in ("plain", "mark_safe"):
            ctx = {k: KINDS[kind](v) for k, v in context.items()}
            assert kept in _draw(template, **ctx)

    @pytest.mark.parametrize(
        ("template", "context"),
        [
            ('<a href="{{ u }}">', {"u": JS}),
            ('<a href="{{ s }}:alert(1)">', {"s": "javascript"}),
            ('<a href="{{ a }}{{ b }}">', {"a": "java", "b": "script:alert(1)"}),
            ('<a href="java{{ b }}">', {"b": "script:alert(1)"}),
            ('<a href="{{ a }}s{{ b }}">', {"a": "java", "b": "cript:alert(1)"}),
            ('<a href="  {{ u }}">', {"u": JS}),
            ('<a href="{% if x %}{{ u }}{% endif %}">', {"u": JS, "x": True}),
            ('<a href="{{ a }}{{ b }}{{ c }}">', {"a": "ja", "b": "va", "c": "script:alert(1)"}),
        ],
    )
    def test_a_slot_that_can_still_choose_the_scheme_is_judged_as_a_whole(
        self, template: str, context: dict
    ) -> None:
        for kind in ("plain", "mark_safe", "text-form-marked-safe"):
            ctx = {k: KINDS[kind](v) if isinstance(v, str) else v for k, v in context.items()}
            out = _draw(template, **ctx)
            assert "script:" not in out and "javascript" not in out.lower().replace("#", ""), out

    def test_a_slot_after_a_fixing_literal_needs_no_judging_even_if_it_is_a_dotted_path(
        self,
    ) -> None:
        """The literal fixed the value, so there is nothing a warning would be about."""
        for template in (
            '<a href="/search?q={{ item.q }}">',
            '{% for l in ls %}<a href="/x/{{ l }}">{% endfor %}',
            '<a href="http://{{ host|lower }}/">',
        ):
            plan = hybrid.plan(template)
            assert plan.unchecked_urls == () and plan.groups == (), template
            assert "force_escape" in plan.template

    def test_the_whole_value_decides_not_the_first_slot(self) -> None:
        """Each slot alone is harmless; together they are a script URL."""
        out = _draw('<a href="{{ a }}{{ b }}">', a="java", b="script:alert(1)")
        assert out == '<a href="#">'

    def test_a_good_whole_value_is_kept_in_pieces(self) -> None:
        out = _draw('<a href="{{ a }}{{ b }}">', a="https://", b="example.com/x?y=1")
        assert out == '<a href="https://example.com/x?y=1">'

    def test_a_good_scheme_across_slots_is_kept(self) -> None:
        assert _draw('<a href="{{ s }}://h/">', s="https") == '<a href="https://h/">'
        assert _draw('<a href="{{ s }}:x">', s="mailto") == '<a href="mailto:x">'

    def test_a_refused_value_is_replaced_once(self) -> None:
        out = _draw('<a href="{{ a }}{{ b }}">', a="javascript:", b="alert(1)")
        assert out == '<a href="#">'

    def test_the_noop_href_is_kept(self) -> None:
        assert (
            _draw('<a href="{{ u }}">', u="javascript:void(0)") == '<a href="javascript:void(0)">'
        )
        assert _draw('<img src="{{ u }}">', u="javascript:void(0)") == '<img src="#">'

    def test_a_scheme_outside_the_allowlist_is_replaced_not_only_script(self) -> None:
        for value in ("magnet:?x=1", "sms:123", "blob:x", "data:text/html,x"):
            assert _draw('<a href="{{ u }}">', u=value) == '<a href="#">'

    def test_a_missing_name_prints_nothing_and_judges_nothing(self) -> None:
        assert _draw('<a href="/a/{{ nope }}">') == '<a href="/a/">'
        assert _draw('<a href="{{ nope }}">') == '<a href="">'

    @pytest.mark.parametrize("directive", ["dj-navigate", "dj-patch", "dj-patch-reload"])
    def test_the_framework_url_directives_are_url_attributes(self, directive: str) -> None:
        assert _draw(f'<a {directive}="{{{{ u }}}}">', u=JS) == f'<a {directive}="#">'
        assert _draw(f'<a {directive}="{{{{ u }}}}">', u="/p/?a=1") == (
            f'<a {directive}="/p/?a=1">'
        )
        assert _draw(f'<a {directive}="?page={{{{ n }}}}">', n=2) == f'<a {directive}="?page=2">'

    def test_srcset_is_judged_only_when_the_slot_is_the_whole_value(self) -> None:
        whole = _draw('<img srcset="{{ s }}">', s="/a.png 1x, javascript:alert(1) 2x")
        assert "javascript:" not in whole
        assert (
            _attrs(_draw('<img srcset="{{ s }}">', s="/a.png 1x, /b.png 2x"), "img")["srcset"]
            == "/a.png 1x, /b.png 2x"
        )
        plan = hybrid.plan('<img srcset="/a.png 1x, {{ s }} 2x">')
        assert plan.groups == ()
        assert [t[:2] for t in plan.unchecked_urls] == [("img", "srcset")]


class TestTheRule:
    def test_a_plain_value_renders_as_before(self) -> None:
        """No double escaping for what was already right."""
        out = _hybrid(Avatar, src="/a?x=1&y=2", alt="a & b", size="lg", status="online").render()
        assert _attrs(out, "img") == {"src": "/a?x=1&y=2", "alt": "a & b", "class": "avatar-img"}
        assert "avatar-lg" in out and "avatar-online" in out
        assert "&amp;amp;" not in out

    def test_a_pre_escaped_marked_safe_value_is_escaped_again(self) -> None:
        """It is the rule, not a side effect: a marked-safe value is not exempt, so its ``&amp;``
        is text and prints as ``&amp;amp;`` (read by a browser as ``&amp;``)."""
        out = _draw('<i title="{{ t }}">', t=mark_safe("a &amp; b"))
        assert out == '<i title="a &amp;amp; b">'
        assert _draw('<i title="{{ t|safe }}">', t=mark_safe("a &amp; b")) == out

    def test_a_safe_url_with_an_ampersand_is_escaped_once(self) -> None:
        out = _hybrid(Avatar, src=mark_safe("/a?x=1&y=2")).render()
        assert 'src="/a?x=1&amp;y=2"' in out

    def test_a_data_image_is_still_an_image(self) -> None:
        uri = "data:image/png;base64,AAAA"
        assert _attrs(_hybrid(Avatar, src=uri).render(), "img")["src"] == uri
        assert _attrs(_hybrid(Avatar, src=_lazy(uri)).render(), "img")["src"] == uri

    def test_a_data_document_is_not(self) -> None:
        for value in ("data:text/html,<script>1</script>", _lazy("data:text/html,x")):
            assert _attrs(_hybrid(Avatar, src=value).render(), "img")["src"] == "#"

    def test_a_text_slot_keeps_its_markup(self) -> None:
        """The rule is about attributes. Text slots are not touched."""
        template = hybrid.plan(Avatar.template).template
        assert "{{ initials }}" in template
        assert "{{ initials|" not in template

    def test_a_number_prints_the_same_in_every_kind_of_slot(self) -> None:
        """A plain name, a dotted path and a filtered expression all print a number in its
        canonical form (attributes want ``33.5``, not a localized ``33,5``)."""
        out = _draw(
            '<i data-a="{{ n }}" data-b="{{ o.n }}" data-c="{{ n|default:0 }}" data-d="{{ m }}"'
            ' data-e="{{ flag }}" data-f="{{ missing }}">',
            n=33.5,
            o={"n": 33.5},
            m=Decimal("1.50"),
            flag=None,
        )
        assert (
            out
            == '<i data-a="33.5" data-b="33.5" data-c="33.5" data-d="1.50" data-e="None" data-f="">'
        )

    def test_the_context_dict_the_class_returned_gets_no_synthetic_key(self) -> None:
        """``get_context_data`` may hand back a dict the class keeps: the rewrite works on a copy."""
        value = _lazy(JS)
        shared = {"src": value, "alt": "x", "size": "md", "shape": "circle", "status": None}

        class Fixed(Avatar):
            def get_context_data(self):
                return shared

        obj = Fixed(src="/ok")
        obj._rust_instance = None
        assert _attrs(obj.render(), "img")["src"] == "#"
        assert shared["src"] is value
        assert not any(k.startswith("djust_attr_") for k in shared)

    def test_an_escape_filter_is_made_a_force_escape(self) -> None:
        """``|escape`` leaves a marked-safe value as it is; the slot is escaped once, not twice."""
        out = _draw('<i title="{{ t|escape }}" data-x="{{ t|force_escape }}">', t=mark_safe('a"b'))
        assert out == '<i title="a&quot;b" data-x="a&quot;b">'
        template = hybrid.plan('<i title="{{ t|escape }}">').template
        assert "|force_escape" in template and "|escape" not in template.replace("force_escape", "")

    def test_a_dotted_expression_and_a_filter_are_escaped_too(self) -> None:
        out = _draw(
            '<a data-a="{{ item.k }}" data-b="{{ v|upper }}"></a>',
            item={"k": mark_safe('"x')},
            v=_lazy('"y'),
        )
        assert out == '<a data-a="&quot;x" data-b="&quot;Y"></a>'

    def test_autoescape_off_does_not_switch_the_rule_off(self) -> None:
        out = _draw(
            '{% autoescape off %}<i title="{{ t }}">{% endautoescape %}', t=mark_safe('a"b')
        )
        assert out == '<i title="a&quot;b">'

    def test_a_name_position_is_not_a_slot(self) -> None:
        """``<a {{ attrs }}>`` is the author's own markup, not a value; it is left as written."""
        out = _draw('<a {{ attrs }} id="{{ i }}">', attrs=mark_safe('class="x"'), i="n")
        assert out == '<a class="x" id="n">'

    def test_verbatim_and_comment_blocks_are_left_alone(self) -> None:
        assert _draw('{% verbatim %}<a href="{{ u }}">{% endverbatim %}', u=JS) == (
            '<a href="{{ u }}">'
        )
        template = '{% comment %}<a href="{{ u }}">{% endcomment %}<a title="{{ u }}">'
        assert "djust_attr" not in hybrid.plan(template).template
        assert hybrid.plan(template).template.count("force_escape") == 1
        assert _draw(template, u="t") == '<a title="t">'

    def test_a_srcset_is_judged_candidate_by_candidate(self) -> None:
        """No shipped template has a ``srcset`` slot, so the sweep cannot reach it: this is its
        test (the sweep says so too)."""
        for value in (
            "/a.png 1x, javascript:alert(1) 2x",
            _lazy("/a.png 1x, javascript:alert(1) 2x"),
            _TextFormMarkedSafe("/a.png 1x, javascript:alert(1) 2x"),
        ):
            out = _draw('<img srcset="{{ s }}" src="/ok.png">', s=value)
            assert "javascript:" not in out, out
        good = _draw('<img srcset="{{ s }}" src="/ok.png">', s=_lazy("/a.png 1x, /b.png 2x"))
        assert _attrs(good, "img")["srcset"] == "/a.png 1x, /b.png 2x"

    def test_django_fallback_gets_the_same_template(self, monkeypatch) -> None:
        """The rewrite is plain Django template syntax: the fallback engine draws it alike."""
        import djust._rust as rust

        monkeypatch.setattr(
            rust, "render_template", lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
        )
        out = _hybrid(Avatar, src=_lazy(JS), size=_lazy(BREAKOUT)).render()
        assert _attrs(out, "img")["src"] == "#"
        assert "onclick" not in _attrs(out, "div")
        assert _draw('<a href="{{ a }}{{ b }}">', a="java", b="script:alert(1)") == '<a href="#">'


class TestTheScanner:
    @pytest.mark.parametrize(
        ("template", "expected"),
        [
            ('<a href="{{ u }}">{{ t }}</a>', [("a", "href", "u")]),
            ("<a href={{ u }}>{{ t }}</a>", [("a", "href", "u")]),
            ('<p>{{ t }}</p><img src="{{ s }}">', [("img", "src", "s")]),
            ('<a {% if x > 1 %}class="{{ c }}"{% endif %}>{{ t }}</a>', [("a", "class", "c")]),
            ('<a title="a > b {{ c }}">x</a>', [("a", "title", "c")]),
            ('<!-- <a href="{{ u }}"> --><b>{{ t }}</b>', []),
            ('<script>if (a<b) { x = "{{ t }}" }</script><i id="{{ i }}"></i>', [("i", "id", "i")]),
            ('<textarea>{{ v }}</textarea><b a="{{ w }}">', [("b", "a", "w")]),
            ("a < b {{ t }} <3", []),
            ('<img srcset="{{ s }}" src="{{ u }}">', [("img", "srcset", "s"), ("img", "src", "u")]),
            ('<a {{ attrs }} id="{{ i }}">', [("a", "id", "i")]),
            ('<a data-{{ n }}="{{ v }}">', [("a", "data-", "v")]),
            ('</a href="{{ u }}"><i id="{{ i }}">', [("i", "id", "i")]),
            ('{% verbatim %}<a href="{{ u }}">{% endverbatim %}<i id="{{ i }}">', [("i", "id", "i")]),
            ('<a href ="{{ u }}" title\n=\n\'{{ t }}\' id = {{ i }}>', [
                ("a", "href", "u"), ("a", "title", "t"), ("a", "id", "i"),
            ]),
            ('<a disabled href="{{ u }}">', [("a", "href", "u")]),
            ('<a href="x"id="{{ i }}">', [("a", "id", "i")]),
            ('<a/href="{{ u }}">', [("a", "href", "u")]),
        ],
    )  # fmt: skip
    def test_where_the_attribute_slots_are(self, template: str, expected: list) -> None:
        found = [(s.tag, s.attr, s.expr.strip()) for s in hybrid.slots(template)]
        assert found == expected

    def test_a_loop_variable_is_not_judged_from_the_context(self) -> None:
        plan = hybrid.plan('{% for c in cols %}<i class="{{ c }}" href="{{ c }}">{% endfor %}')
        assert plan.groups == ()
        assert plan.template.count("{{ c|force_escape }}") == 2

    def test_a_with_variable_is_not_judged_from_the_context(self) -> None:
        plan = hybrid.plan('{% with x=top %}<a href="{{ x }}">{% endwith %}')
        assert plan.groups == ()
        assert [t[:2] for t in plan.unchecked_urls] == [("a", "href")]

    def test_a_template_without_an_attribute_slot_is_returned_as_it_is(self) -> None:
        template = "<b>{{ t }}</b>"
        assert hybrid.plan(template).template is template
        context = {"t": 1}
        assert hybrid.harden_context(template, context) == (template, context)

    def test_the_url_attribute_list_covers_the_url_sweeps(self) -> None:
        from scripts.lib.template_library_parity import url_sweep

        assert set(url_sweep.URL_ATTRS) - {"srcset"} <= set(hybrid.URL_ATTRS)


class TestBlocksInAValue:
    """A literal inside a block that may be skipped never fixes the value; a slot inside one is
    judged on every way the value can come out."""

    @pytest.mark.parametrize("kind", ["plain", "mark_safe", "text-form-marked-safe"])
    @pytest.mark.parametrize("rel", [False, True])
    def test_a_conditional_literal_is_skipped_or_taken(self, kind: str, rel: bool) -> None:
        template = '<a href="{% if rel %}/{% endif %}{{ s }}">'
        out = _draw(template, rel=rel, s=KINDS[kind](JS))
        assert "javascript:" not in out, (rel, out)
        # skipped: the value is the slot alone, refused. taken: judged as if skipped (fail closed)
        assert out == ('<a href="/#">' if rel else '<a href="#">')

    def test_a_good_value_after_a_conditional_literal_is_kept(self) -> None:
        template = '<a href="{% if rel %}/{% endif %}{{ s }}">'
        assert _draw(template, rel=True, s="ok") == '<a href="/ok">'
        assert _draw(template, rel=False, s="/ok") == '<a href="/ok">'

    @pytest.mark.parametrize("rel", [False, True])
    def test_an_else_branch_literal(self, rel: bool) -> None:
        template = '<a href="{% if rel %}http://x/{% else %}{% endif %}{{ s }}">'
        assert "javascript:" not in _draw(template, rel=rel, s=JS)

    @pytest.mark.parametrize("a", [False, True])
    @pytest.mark.parametrize("b", [False, True])
    def test_a_nested_block(self, a: bool, b: bool) -> None:
        template = '<a href="{% if a %}{% if b %}/{% endif %}{% endif %}{{ s }}">'
        assert "javascript:" not in _draw(template, a=a, b=b, s=JS)

    @pytest.mark.parametrize("items", [[], [1], [1, 2, 3]])
    def test_a_loop_body_literal(self, items: list) -> None:
        template = '<a href="{% for i in items %}/{% endfor %}{{ s }}">'
        assert "javascript:" not in _draw(template, items=items, s=JS)

    def test_a_with_block_is_always_there(self) -> None:
        """The content of ``{% with %}`` is not conditional, so its literal does fix the value."""
        template = '<a href="{% with x=1 %}/{% endwith %}{{ s }}">'
        assert _draw(template, s="a:b") == '<a href="/a:b">'

    def test_a_top_level_literal_still_fixes_the_value(self) -> None:
        template = '<a href="/x/{% if a %}y{% endif %}{{ s }}">'
        assert hybrid.plan(template).groups == ()
        assert _draw(template, a=False, s="a:b") == '<a href="/x/a:b">'

    @pytest.mark.parametrize("a", [False, True])
    def test_alternative_slots_are_judged_each_on_its_own(self, a: bool) -> None:
        """Two slots that are alternatives are never joined into one value to be judged."""
        template = '<a href="{% if a %}{{ x }}{% else %}{{ y }}{% endif %}">'
        out = _draw(template, a=a, x="/ok", y=JS)
        assert out == ('<a href="/ok">' if a else '<a href="#">')
        out = _draw(template, a=a, x=JS, y="/ok")
        assert out == ('<a href="#">' if a else '<a href="/ok">')

    def test_an_elif_chain(self) -> None:
        template = '<a href="{% if a %}{{ x }}{% elif b %}{{ y }}{% else %}{{ z }}{% endif %}">'
        for a, b, expected in [(True, False, "/x"), (False, True, "/y"), (False, False, "/z")]:
            assert _draw(template, a=a, b=b, x="/x", y="/y", z="/z") == f'<a href="{expected}">'
            out = _draw(template, a=a, b=b, x="/x", y=JS, z="/z")
            assert "javascript:" not in out

    def test_a_comment_before_the_slot_is_silent(self) -> None:
        plan = hybrid.plan('<a href="{# note #}{{ s }}">')
        assert plan.unchecked_urls == () and len(plan.groups) == 1
        assert _draw('<a href="{# note #}{{ s }}">', s=JS) == '<a href="#">'

    def test_an_assignment_tag_before_the_slot_is_silent(self) -> None:
        for template in (
            '<a href="{% url "x" as u %}{{ s }}">',
            '<a href="{% static "x" as u %}{{ s }}">',
            '<a href="{% firstof a b as u %}{{ s }}">',
        ):
            plan = hybrid.plan(template)
            assert plan.unchecked_urls == () and len(plan.groups) == 1, template
        assert _draw('<a href="{% url "x" as u %}{{ s }}">', s=JS) == '<a href="#">'

    def test_a_url_or_static_tag_first_fixes_the_value(self) -> None:
        for template in (
            "<a href=\"{% url 'x' %}?q={{ q }}\">",
            "<img src=\"{% static 'a/' %}{{ name }}\">",
        ):
            plan = hybrid.plan(template)
            assert plan.groups == () and plan.unchecked_urls == (), template

    def test_a_url_tag_inside_a_block_does_not_fix_the_value(self) -> None:
        plan = hybrid.plan("<a href=\"{% if a %}{% url 'x' %}{% endif %}{{ s }}\">")
        assert len(plan.groups) == 1

    def test_the_paths_are_every_way_the_value_comes_out(self) -> None:
        (group,) = hybrid.plan('<a href="{% if a %}/{% endif %}{{ s }}">').groups
        texts = sorted("".join(v if k == "lit" else "<s>" for k, v in p) for p in group.paths)
        assert texts == ["/<s>", "<s>"]

    def test_a_value_with_too_many_paths_is_listed_not_judged_loosely(self) -> None:
        many = "".join("{% if a" + str(i) + " %}/{% endif %}" for i in range(8)) + "{{ s }}"
        plan = hybrid.plan(f'<a href="{many}">')
        assert plan.groups == ()
        assert [t[:2] for t in plan.unchecked_urls] == [("a", "href")]


class TestLiteralsABrowserNormalises:
    """The browser decodes character references and drops tab / newline / carriage return in a
    URL before it reads the scheme, so a literal is judged as the browser reads it."""

    @pytest.mark.parametrize(
        ("template", "value"),
        [
            ('<a href="&#106;ava{{ s }}">', "script:alert(1)"),
            ('<a href="&#x6A;avascript{{ s }}">', ":alert(1)"),
            ('<a href="java&Tab;script{{ s }}">', ":alert(1)"),
            ('<a href="java&NewLine;script{{ s }}">', ":alert(1)"),
            ('<a href="java\tscript{{ s }}">', ":alert(1)"),
            ('<a href="java\nscript{{ s }}">', ":alert(1)"),
            ('<a href="java\r\nscript{{ s }}">', ":alert(1)"),
            ('<a href="&#9;&#106;ava{{ s }}">', "script:alert(1)"),
        ],
    )
    @pytest.mark.parametrize("kind", ["plain", "mark_safe"])
    def test_a_normalised_literal_does_not_fix_the_value(
        self, template: str, value: str, kind: str
    ) -> None:
        plan = hybrid.plan(template)
        assert len(plan.groups) == 1 and plan.unchecked_urls == ()
        out = _draw(template, s=KINDS[kind](value))
        assert "script:" not in out and "cript:" not in out, out

    def test_a_literal_with_an_ampersand_after_a_fixing_character_still_fixes(self) -> None:
        template = '<a href="/a?x=1&amp;q={{ s }}">'
        assert hybrid.plan(template).groups == ()
        assert _draw(template, s="a:b") == '<a href="/a?x=1&amp;q=a:b">'

    def test_a_character_reference_that_is_not_a_scheme_character_fixes_it(self) -> None:
        assert hybrid.plan('<a href="&#47;x/{{ s }}">').groups == ()


class TestNamesBuiltByTags:
    TEMPLATES = {
        "if-else": '<a {% if x %}href{% else %}title{% endif %}="{{ u }}">',
        "var": '<a {{ n }}="{{ u }}">',
        "var-suffix": '<a data-{{ n }}="{{ u }}">',
        "tag-suffix": '<a href{% if x %}-x{% endif %}="{{ u }}">',
    }

    @pytest.mark.parametrize("name", sorted(TEMPLATES))
    def test_it_is_escaped_and_listed(self, name: str) -> None:
        plan = hybrid.plan(self.TEMPLATES[name])
        assert plan.groups == ()
        assert [t[0] for t in plan.unchecked_urls] == ["a"], plan
        out = _draw(self.TEMPLATES[name], x=True, n="href", u=mark_safe('a" onclick="x'))
        assert "onclick" not in _attrs(out, "a"), out

    def test_a_wrapping_tag_around_a_whole_attribute_is_not_a_name(self) -> None:
        template = '<a {% if x %}href="{{ u }}"{% endif %}>'
        assert len(hybrid.plan(template).groups) == 1
        assert _draw(template, x=True, u=JS) == '<a href="#">'


class TestWhatIsNotJudged:
    """Stated, not silent: these URL values are escaped and listed, and a warning says so once."""

    TEMPLATES = {
        "dotted": '<a href="{{ item.url }}">',
        "filtered": '<a href="{{ u|default:"/x" }}">',
        "loop": '{% for l in links %}<a href="{{ l }}">{% endfor %}',
        "with": '{% with x=u %}<a href="{{ x }}">{% endwith %}',
        "printing-tag": '<a href="{% now "Y" %}{{ u }}">',
        "printing-tag-in-block": '<a href="{% if a %}{% trans "x" %}{% endif %}{{ u.k }}">',
    }

    @pytest.mark.parametrize("name", sorted(TEMPLATES))
    def test_it_is_listed(self, name: str) -> None:
        plan = hybrid.plan(self.TEMPLATES[name])
        assert plan.groups == ()
        assert [t[:2] for t in plan.unchecked_urls] == [("a", "href")]

    def test_it_is_escaped(self) -> None:
        out = _draw('<a href="{{ item.url }}">', item={"url": mark_safe('/x" onclick="y')})
        assert out == '<a href="/x&quot; onclick=&quot;y">'

    def test_a_value_after_a_fixing_literal_is_not_listed(self) -> None:
        for template in (
            '<a href="/search?q={{ item.q }}">',
            '<a href="http://{{ host|lower }}/">',
            '{% for l in ls %}<a href="/x/{{ l }}">{% endfor %}',
        ):
            assert hybrid.plan(template).unchecked_urls == (), template

    def test_an_unquoted_value_is_escaped_and_listed(self) -> None:
        template = "<a title={{ t }}>"
        plan = hybrid.plan(template)
        assert [t[:2] for t in plan.unquoted] == [("a", "title")]
        assert _draw(template, t=mark_safe('a" onclick="x')) == "<a title=a&quot; onclick=&quot;x>"


class TestTheWarning:
    """Once per class and set of attributes; the class name and the tag / attribute names only."""

    LOGGER = "djust.components._hybrid_attrs"

    @pytest.fixture(autouse=True)
    def _fresh(self):
        hybrid._WARNED.clear()
        yield
        hybrid._WARNED.clear()

    def _component(self, template: str, name: str = "Linkish"):
        return type(name, (Component,), {"template": template, "get_context_data": lambda s: {}})

    def test_it_names_the_class_and_the_attribute_once(self, caplog) -> None:
        cls = self._component('<a href="{{ item.url }}" title="{{ t }}">')
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            cls().render()
            cls().render()
        (record,) = [r for r in caplog.records if "not scheme-checked" in r.getMessage()]
        assert "Linkish" in record.getMessage() and "a[href]" in record.getMessage()

    def test_it_never_carries_template_text(self, caplog) -> None:
        cls = self._component('<a href="{{ item.RUNTIMEVALUE }}" data-x="{{ RUNTIMEVALUE }}">')
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            cls().render()
        assert caplog.records
        assert "RUNTIMEVALUE" not in caplog.text and "item." not in caplog.text

    def test_an_odd_attribute_name_is_not_logged(self, caplog) -> None:
        cls = self._component('<a {{ n }}x"y="{{ u.k }}">')
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            cls().render()
        assert 'x"y' not in caplog.text

    def test_another_class_with_the_same_template_warns_once_more(self, caplog) -> None:
        template = '<a href="{{ item.url }}">'
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            self._component(template, "One")().render()
            self._component(template, "Two")().render()
        assert len([r for r in caplog.records if "not scheme-checked" in r.getMessage()]) == 2

    def test_a_quoted_plain_template_warns_nothing(self, caplog) -> None:
        cls = self._component('<a href="{{ u }}" title="{{ t }}">')
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            cls().render()
        assert not caplog.records

    def test_the_set_of_warnings_given_is_bounded(self) -> None:
        for i in range(hybrid._MAX_WARNED + 50):
            hybrid._warn_once(f"Cls{i}", hybrid.plan('<a href="{{ item.url }}">'))
        assert len(hybrid._WARNED) <= hybrid._MAX_WARNED

    def test_the_plan_cache_is_a_bounded_lru(self) -> None:
        assert hybrid.plan.cache_info().maxsize == 512
        hybrid.plan.cache_clear()
        for i in range(700):
            hybrid.plan(f'<a title="{{{{ t }}}}" data-n="{i}">')
        assert hybrid.plan.cache_info().currsize <= 512


class TestDivergencesFromTheEngine:
    """Where a slot differs from what the engine alone prints, and why."""

    def test_a_callable_that_needs_arguments_prints_nothing_in_every_slot(self) -> None:
        def needs_args(x):
            return "never"

        assert _draw('<a href="{{ f }}" title="{{ f }}">', f=needs_args) == '<a href="" title="">'

    def test_a_type_error_the_callable_raises_itself_goes_on(self) -> None:
        def raises():
            raise TypeError("its own")

        with pytest.raises(TypeError, match="its own"):
            _draw('<a href="{{ f }}">', f=raises)

    def test_a_date_prints_in_its_iso_form_in_an_attribute(self) -> None:
        import datetime

        out = _draw(
            '<i data-d="{{ d }}" data-t="{{ t }}">',
            d=datetime.date(2026, 1, 2),
            t=datetime.datetime(2026, 1, 2, 3, 4),
        )
        assert out == '<i data-d="2026-01-02" data-t="2026-01-02 03:04:00">'

    def test_a_value_whose_str_raises_is_not_printed_raw(self) -> None:
        """A URL slot raises, as the engine does. In a plain slot the Rust engine's
        ``force_escape`` falls back to the escaped default repr where Django's raises: that is
        the crate's filter, not changed here; the text is escaped either way."""

        class Bad:
            def __str__(self) -> str:
                raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            _draw('<a href="{{ b }}">', b=Bad())
        try:
            out = _draw('<a title="{{ b }}">', b=Bad())
        except ValueError:
            return  # the Django fallback
        assert "boom" not in out and "&lt;" in out


def _component_classes() -> dict[str, type[Component]]:
    """Every ``Component`` subclass with a ``template`` the package defines. A module that does
    not import is an error, not a class that quietly drops out of the derived set."""
    found: dict[str, type[Component]] = {}
    failures: list[str] = []
    for info in pkgutil.walk_packages(
        components_package.__path__,
        "djust.components.",
        onerror=lambda name: failures.append(name),
    ):
        try:
            module = importlib.import_module(info.name)
        except ImportError as exc:
            failures.append(f"{info.name}: {exc}")
            continue
        for _name, cls in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(cls, Component)
                and cls is not Component
                and cls.__module__ == module.__name__
                and getattr(cls, "template", None) is not None
            ):
                found[f"{cls.__module__}.{cls.__name__}"] = cls
    if failures:
        raise ImportError(f"modules of djust.components that do not import: {failures}")
    return found


TEMPLATE_CLASSES = _component_classes()


class TestEveryHybridTemplate:
    """Derived: every ``Component`` subclass with a ``template`` that the package defines."""

    def test_the_classes_are_found(self) -> None:
        assert len(TEMPLATE_CLASSES) == 19, sorted(TEMPLATE_CLASSES)
        assert "djust.components.ui.avatar_simple.Avatar" in TEMPLATE_CLASSES

    def test_a_module_that_does_not_import_fails_loudly(self, monkeypatch) -> None:
        real = importlib.import_module

        def broken(name, *args, **kwargs):
            if name.endswith("avatar_simple"):
                raise ImportError("an optional dependency is missing")
            return real(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", broken)
        with pytest.raises(ImportError, match="avatar_simple"):
            _component_classes()

    @pytest.mark.parametrize("key", sorted(TEMPLATE_CLASSES))
    def test_every_attribute_slot_is_rewritten_and_no_url_is_left_unchecked(self, key: str) -> None:
        template = TEMPLATE_CLASSES[key].template
        plan = hybrid.plan(template)
        assert plan.unchecked_urls == () and plan.unquoted == (), plan
        for slot in hybrid.slots(plan.template):
            assert "force_escape" in slot.expr or slot.expr.strip().endswith("|safe"), (key, slot)

    def test_a_url_attribute_of_a_shipped_template_is_judged(self) -> None:
        groups = {
            key: {g.kind for g in hybrid.plan(cls.template).groups}
            for key, cls in TEMPLATE_CLASSES.items()
        }
        assert "image" in groups["djust.components.ui.avatar_simple.Avatar"]

    def test_no_shipped_template_has_a_srcset_slot_for_the_sweep_to_reach(self) -> None:
        """The sweep drives shipped classes, so it cannot reach a ``srcset`` slot: only
        ``test_a_srcset_is_judged_candidate_by_candidate`` does. When a shipped template gains
        one, this fails and says to extend the sweep."""
        with_srcset = [
            key
            for key, cls in TEMPLATE_CLASSES.items()
            if any(v.attr == "srcset" for v in hybrid.values(cls.template))
        ]
        assert not with_srcset, f"a shipped srcset slot: the sweep must drive it ({with_srcset})"
