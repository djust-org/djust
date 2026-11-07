"""Raw-text elements of a hybrid template (#3914) and the inline templates of a ``LiveComponent``
and a ``BoundComponent`` (#3913) follow the rule the hybrid ``Component`` template follows (#3900).

#3914. A value written into the content of ``<textarea>`` or ``<title>`` (RCDATA: text in which
character references are decoded, ended by the first matching end tag) or into an HTML comment
is escaped even when it is marked safe, because the text there can end the element. HTML
escaping is the WRONG encoding in ``<script>``, ``<style>``, ``<xmp>``, ``<iframe>``,
``<noembed>``, ``<noframes>``, ``<plaintext>`` and ``<noscript>`` (entities are not decoded, JSON
written with ``|safe`` would stop parsing): those keep the author's markup, as they always did.
Every OTHER text slot keeps a marked-safe value's markup (the #3898 rule).

#3913. ``LiveComponent.render`` and ``BoundComponent.render`` draw their inline ``template``
through the same helper as ``Component.render``: the attribute slots are escaped whatever the
value is marked as and a URL slot is checked against the scheme allowlist. One call, in one
place (``_render_inline_template``); a census below fails when another site renders a component's
inline template.

What each class proves:

* ``TestEscapedText``: ``<textarea>`` / ``<title>`` / comments x every slot spelling x every
  value kind. The text a value yields never ends the element it sits in.
* ``TestOtherRawTextElementsKeepTheAuthorsMarkup``: the other raw-text elements print what they
  printed before, byte for byte; the scanner still reads them as raw text.
* ``TestOrdinaryTextIsUnchanged``: ordinary values (strings, numbers, dates, ``None`` ...) print
  as they did, byte for byte; every other text slot keeps its markup.
* ``TestLazyEvaluation``: a callable in a branch that never renders is not called.
* ``TestTranslationBlocks``: ``{% blocktranslate %}`` in an attribute and in escaped text.
* ``TestTheScanner`` / ``TestTheWarning``: where the escaped text starts and ends, the shapes the
  scanner cannot follow, and what the warning says.
* ``TestInlineTemplateAttributes`` / ``TestLiveComponentTemplate`` / ``TestBoundComponentTemplate``:
  the rule for the other two inline templates, including what it changes in app-authored markup.
* ``TestOneSink``: an AST census of every site that renders a component's inline template.
"""

from __future__ import annotations

import ast
import datetime
import logging
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.utils.safestring import mark_safe

from djust import LiveView
from djust.components import _hybrid_attrs as hybrid
from djust.components import base as components_base
from djust.components.base import Component, LiveComponent
from djust.components.descriptors.base import LiveComponent as DescriptorLiveComponent
from djust.components.descriptors.base import TypedState
from djust.tests.test_component_hybrid_template_attrs_3900 import (
    BREAKOUT,
    JS,
    KINDS,
    _attrs,
    _draw,
)

pytestmark = pytest.mark.components

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _bootstrap(settings, monkeypatch):
    settings.DJUST_CONFIG = {"css_framework": "bootstrap5"}
    # the engine call and `_called` read the project's auto-call setting; another test that
    # leaves it switched off must not decide what these tests exercise
    monkeypatch.setattr("djust.config.template_auto_call_enabled", lambda: True)
    hybrid._WARNED.clear()
    yield
    hybrid._WARNED.clear()


#: The elements whose text is escaped (RCDATA), and the raw-text elements that are NOT: the
#: content ends at the first matching end tag (``<plaintext>`` has none), but entities are not
#: decoded, so the author's markup stays.
ESCAPED_ELEMENTS = ("textarea", "title")
OTHER_RAW_ELEMENTS = ("script", "style", "xmp", "plaintext", "iframe", "noembed", "noframes")
RAW_ELEMENTS = ESCAPED_ELEMENTS + OTHER_RAW_ELEMENTS

#: The ways a slot can be spelled: a plain name, a dotted path, a filtered expression, ``|safe``
#: (the template author's own claim), a loop variable, under ``{% autoescape off %}``.
SPELLINGS = {
    "name": ("{{ v }}", {}),
    "dotted": ("{{ o.v }}", {}),
    "filtered": ('{{ v|default:"-" }}', {}),
    "safe-filter": ("{{ v|safe }}", {}),
    "escape-filter": ("{{ v|escape }}", {}),
    "loop": ("{% for x in xs %}{{ x }}{% endfor %}", {}),
    "autoescape-off": ("{% autoescape off %}{{ v }}{% endautoescape %}", {}),
    "if": ("{% if v %}{{ v }}{% endif %}", {}),
}


class _Obj:
    def __init__(self, v: object) -> None:
        self.v = v


def _context(value: object) -> dict[str, Any]:
    return {"v": value, "o": _Obj(value), "xs": [value]}


def _template(tag: str, slot: str) -> str:
    if tag == "plaintext":
        return f"<plaintext>{slot}"
    return f'<{tag} data-k="1">{slot}</{tag}>'


def _end_tags(markup: str, tag: str) -> int:
    """How many times a browser would end a ``<tag>`` element in ``markup`` (ASCII case and
    whitespace, as the HTML tokenizer reads an end tag)."""
    return len(re.findall(rf"</{tag}(?=[ \t\n\f\r/>])", markup, re.I | re.A))


def _breakout(tag: str) -> str:
    """Text that ends ``tag`` and starts a live element with a handler."""
    return f"</{tag}><b onclick=x>"


def _unhardened(template: str, context: dict[str, Any]) -> str:
    """What the engine prints for ``template`` with none of this module's rewriting."""
    return components_base._render_template_with_fallback(template, context)


@pytest.fixture
def django_engine(monkeypatch):
    """Draw through Django's engine (the Rust engine refuses, as it does for a tag it lacks)."""
    import djust._rust as rust

    def refuse(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("force the Django engine")

    monkeypatch.setattr(rust, "render_template", refuse)


class TestEscapedText:
    """The text a value yields never ends the element it sits in, whatever the value is."""

    @pytest.mark.parametrize("kind", sorted(KINDS))
    @pytest.mark.parametrize("spelling", sorted(SPELLINGS))
    @pytest.mark.parametrize("tag", ESCAPED_ELEMENTS)
    def test_a_value_does_not_end_the_element(self, tag: str, spelling: str, kind: str) -> None:
        slot, _ = SPELLINGS[spelling]
        out = _draw(_template(tag, slot), **_context(KINDS[kind](_breakout(tag))))
        assert _end_tags(out, tag) == 1, (tag, spelling, kind, out)
        assert "<b onclick" not in out, (tag, spelling, kind, out)
        assert "&lt;/" in out, (tag, spelling, kind, out)

    @pytest.mark.parametrize("kind", sorted(KINDS))
    @pytest.mark.parametrize("spelling", ["name", "dotted", "filtered", "safe-filter"])
    @pytest.mark.parametrize("tag", ESCAPED_ELEMENTS)
    def test_the_django_engine_agrees(
        self, tag: str, spelling: str, kind: str, django_engine
    ) -> None:
        slot, _ = SPELLINGS[spelling]
        out = _draw(_template(tag, slot), **_context(KINDS[kind](_breakout(tag))))
        assert _end_tags(out, tag) == 1 and "<b onclick" not in out, (tag, spelling, kind, out)

    @pytest.mark.parametrize("tag", ESCAPED_ELEMENTS)
    def test_the_gate_off_the_unfixed_template_does_end_it(self, tag: str) -> None:
        """The test above can fail: the same template, rendered without the rewrite, is broken
        out of by a marked-safe value (not a tautology)."""
        out = _unhardened(_template(tag, "{{ v }}"), {"v": mark_safe(_breakout(tag))})
        assert _end_tags(out, tag) == 2, out

    @pytest.mark.parametrize("kind", sorted(KINDS))
    def test_textarea_value_of_the_reported_class(self, kind: str) -> None:
        """``TextArea.value`` is the reported case: the class component the issue names."""
        from djust.components.ui.textarea_simple import TextArea

        obj = TextArea(name="n", value=KINDS[kind](_breakout("textarea")))
        obj._rust_instance = None
        out = str(obj.render())
        assert _end_tags(out, "textarea") == 1, out
        assert "<b onclick" not in out, out

    @pytest.mark.parametrize("tag", ["TEXTAREA", "TextArea", "tItLe"])
    def test_the_element_name_is_read_in_any_case(self, tag: str) -> None:
        out = _draw(f"<{tag}>{{{{ v }}}}</{tag}>", v=mark_safe(f"</{tag.lower()}><i>"))
        assert "<i>" not in out, out

    def test_an_end_tag_the_browser_does_not_recognise_is_not_the_end(self) -> None:
        """``</textarea-x>`` does not end a textarea, so the slot after it is still inside it."""
        out = _draw(
            "<textarea>a</textarea-x>{{ v }}</textarea>",
            v=mark_safe("</textarea><i>"),
        )
        assert "<i>" not in out, out

    def test_whitespace_in_the_start_tag_and_attributes_with_slots(self) -> None:
        out = _draw(
            "<textarea\n  name=\"{{ n }}\"\n  placeholder = '{{ p }}'\n>{{ v }}</textarea\n>",
            n="a",
            p="b",
            v=mark_safe("</textarea><i>"),
        )
        assert "<i>" not in out and 'name="a"' in out, out

    def test_the_element_after_the_raw_one_is_ordinary_again(self) -> None:
        out = _draw(
            "<textarea>{{ a }}</textarea><span>{{ b }}</span><title>{{ c }}</title>"
            "<span>{{ d }}</span>",
            a=mark_safe("<i>a</i>"),
            b=mark_safe("<b>b</b>"),
            c=mark_safe("<i>c</i>"),
            d=mark_safe("<b>d</b>"),
        )
        assert "&lt;i&gt;a" in out and "&lt;i&gt;c" in out, out
        assert "<span><b>b</b></span>" in out and "<span><b>d</b></span>" in out, out

    @pytest.mark.parametrize(
        "end", ["</TEXTAREA>", "</TextArea >", "</textarea\n>", "</textarea/>", "</textarea\t>"]
    )
    def test_an_end_tag_in_any_ascii_spelling_ends_the_element_for_the_scanner(
        self, end: str
    ) -> None:
        """The slot after the element is an ordinary text slot again, whatever the end tag looks
        like: a scanner that missed the end would escape it (the case dropped from the search
        flag ``re.I`` is the upper-case one)."""
        out = _draw(f"<textarea>a{end}<span>{{{{ b }}}}</span>", b=mark_safe("<b>b</b>"))
        assert "<span><b>b</b></span>" in out, out

    def test_a_non_ascii_space_after_the_name_is_not_an_end_tag(self) -> None:
        """HTML ends a tag name at ASCII whitespace only: ``</textarea >`` does not end the
        element, so the slot after it is still inside it (and is escaped). The docstring spells
        the character as U+00A0 on purpose."""
        nbsp = chr(0xA0)
        out = _draw(
            f"<textarea>a</textarea{nbsp}><i>{{{{ v }}}}</i></textarea>", v=mark_safe("<b>")
        )
        assert "&lt;b&gt;" in out, out

    def test_a_non_ascii_lookalike_in_an_end_tag_name_is_not_an_end_tag(self) -> None:
        """HTML folds ASCII case only: a dotless i (U+0131) in ``</title>`` does not end a
        ``<title>``, so the slot after it is still inside it. Python's ``re.I`` alone folds the
        dotless i to ``i``; ``re.A`` is what keeps the scanner with the browser."""
        dotless = chr(0x131)
        out = _draw(f"<title>a</t{dotless}tle><i>{{{{ v }}}}</i></title>", v=mark_safe("<b>"))
        assert "&lt;b&gt;" in out, out

    def test_an_end_tag_whose_name_is_followed_by_a_token_is_not_read_as_the_end(self) -> None:
        """``</textarea{{ x }}>`` ends the element only if ``x`` is empty: the scanner cannot
        know, so it keeps reading inside the element. That fails SAFE (the slot after it is
        escaped, as if inside), and the real end tag after it ends the element for both."""
        out = _draw(
            "<textarea>{{ a }}</textarea{{ x }}>{{ b }}</textarea>{{ c }}",
            a=mark_safe("<i>"),
            x="",
            b=mark_safe("<b>"),
            c=mark_safe("<u>"),
        )
        assert out == "<textarea>&lt;i&gt;</textarea>&lt;b&gt;</textarea><u>", out

    def test_a_textarea_opened_inside_verbatim_is_followed(self) -> None:
        """``{% verbatim %}`` prints as written, so the browser is inside the textarea after it."""
        out = _draw(
            "{% verbatim %}<textarea>{% endverbatim %}{{ v }}</textarea>", v=mark_safe("<b>")
        )
        assert "&lt;b&gt;" in out, out

    def test_a_script_url_in_an_attribute_next_to_a_raw_slot_is_still_judged(self) -> None:
        out = _draw(
            '<a href="{{ u }}">x</a><textarea>{{ v }}</textarea>',
            u=mark_safe(JS),
            v=mark_safe("</textarea>"),
        )
        assert _attrs(out, "a")["href"] == "#" and _end_tags(out, "textarea") == 1, out

    def test_an_attribute_slot_of_a_raw_element_is_an_attribute_slot(self) -> None:
        out = _draw('<textarea name="{{ n }}"></textarea>', n=mark_safe(BREAKOUT))
        assert "onclick" not in _attrs(out, "textarea"), out

    @pytest.mark.parametrize("kind", sorted(KINDS))
    @pytest.mark.parametrize("spelling", ["name", "dotted", "filtered", "safe-filter"])
    def test_an_html_comment_is_ended_only_by_its_own_end(self, spelling: str, kind: str) -> None:
        """``-->`` ends a comment as an end tag ends an element: the same invariant, folded in."""
        slot, _ = SPELLINGS[spelling]
        out = _draw(f"<!-- {slot} --><i>after</i>", **_context(KINDS[kind]("--><b onclick=x>")))
        assert out.count("-->") == 1 and "<b onclick" not in out, (spelling, kind, out)

    def test_the_text_after_a_comment_is_ordinary_again(self) -> None:
        out = _draw("<!-- {{ a }} --><span>{{ b }}</span>", a=mark_safe("<i>"), b=mark_safe("<b>"))
        assert "<span><b></span>" in out and "&lt;i&gt;" in out, out


class TestOtherRawTextElementsKeepTheAuthorsMarkup:
    """Entity escaping is the wrong encoding in ``<script>``, ``<style>``, ``<xmp>`` ...: what they
    printed before, they print now, byte for byte, for every value kind and every spelling."""

    @pytest.mark.parametrize("kind", sorted(KINDS))
    @pytest.mark.parametrize("spelling", sorted(SPELLINGS))
    @pytest.mark.parametrize("tag", OTHER_RAW_ELEMENTS + ("noscript",))
    def test_the_output_is_what_the_engine_alone_prints(
        self, tag: str, spelling: str, kind: str
    ) -> None:
        slot, _ = SPELLINGS[spelling]
        template = _template(tag, slot)
        context = _context(KINDS[kind]('{"a": "b & c"}'))
        assert _draw(template, **context) == _unhardened(template, context), (tag, spelling, kind)

    def test_json_written_with_the_safe_filter_stays_json(self) -> None:
        out = _draw("<script>var cfg = {{ cfg|safe }};</script>", cfg='{"a": "b"}')
        assert out == '<script>var cfg = {"a": "b"};</script>'
        out = _draw("<style>.x{{ css|safe }}</style>", css="{color: red}")
        assert out == "<style>.x{color: red}</style>"

    @pytest.mark.parametrize("tag", ["script", "style", "xmp", "iframe", "noembed", "noframes"])
    def test_the_scanner_still_reads_them_as_raw_text(self, tag: str) -> None:
        """Their content is not markup, so an ``<a href="{{ u }}">`` inside is not an attribute
        slot (and is not rewritten); the slot after the element is ordinary again."""
        template = f'<{tag}><a href="{{{{ u }}}}"></{tag}><a href="{{{{ u }}}}">x</a>'
        assert [(s.tag, s.attr) for s in hybrid.slots(template)] == [("a", "href")]
        assert hybrid.plan(template).template.count("{{ u }}") == 1  # the one inside is untouched

    def test_plaintext_swallows_the_rest_of_the_template(self) -> None:
        assert hybrid.slots('<plaintext><a href="{{ u }}">') == []

    def test_noscript_is_scanned_as_markup(self) -> None:
        """With scripting off ``<noscript>`` holds markup, and it always has been scanned: its
        attribute slots are judged."""
        out = _draw('<noscript><a href="{{ u }}">x</a></noscript>', u=mark_safe(JS))
        assert _attrs(out, "a")["href"] == "#", out


class TestOrdinaryTextIsUnchanged:
    """Only a value that carries a claim of being markup changes. Every ordinary value prints as it
    did, in a raw-text element, and every other text slot keeps its markup."""

    ORDINARY = {
        "str": "a < b & 'c' \"d\"",
        "word": "hello",
        "unicode": "café ☃",
        "empty": "",
        "int": 1234567,
        "float": 33.5,
        "decimal": Decimal("1234.50"),
        "date": datetime.date(2026, 1, 2),
        "datetime": datetime.datetime(2026, 1, 2, 3, 4, 5),
        "none": None,
        "true": True,
        "list": ["a<b", "c"],
        "dict": {"k": "v&w"},
        "lazy-plain": KINDS["lazy-plain"]("a < b"),
    }

    @pytest.mark.parametrize(
        "template", ["<textarea>{{ v }}</textarea>", "<title>{{ v }}</title>", "<!-- {{ v }} -->"]
    )
    @pytest.mark.parametrize("name", sorted(ORDINARY))
    def test_an_ordinary_value_in_escaped_text_prints_as_before(
        self, template: str, name: str
    ) -> None:
        value = self.ORDINARY[name]
        assert _draw(template, v=value) == _unhardened(template, {"v": value})

    @pytest.mark.parametrize("name", ["str", "word", "unicode", "empty", "lazy-plain"])
    def test_a_plain_string_in_a_dotted_or_filtered_slot_prints_as_before(self, name: str) -> None:
        """Text keeps its exact form in the other spellings (a number or date does not: see
        the next test)."""
        value = self.ORDINARY[name]
        for slot in ("{{ o.v }}", '{{ v|default:"-" }}'):
            template = _template("textarea", slot)
            ctx = {"v": value, "o": _Obj(value)}
            before = components_base._render_template_with_fallback(template, ctx)
            assert _draw(template, **ctx) == before, slot

    def test_a_number_or_date_in_a_dotted_slot_prints_in_its_canonical_form(self) -> None:
        """Documented: an expression this module cannot resolve ends in ``|force_escape``, which
        takes the value's text form, so a date is not localised there. A plain name keeps it."""
        value = datetime.date(2026, 1, 2)
        plain = _draw("<textarea>{{ v }}</textarea>", v=value)
        dotted = _draw("<textarea>{{ o.v }}</textarea>", o=_Obj(value))
        assert plain == "<textarea>Jan. 2, 2026</textarea>"
        assert dotted == "<textarea>2026-01-02</textarea>"

    @pytest.mark.parametrize("kind", ["mark_safe", "safestring-subclass", "lazy-safe"])
    def test_an_ordinary_text_slot_keeps_a_marked_safe_values_markup(self, kind: str) -> None:
        """Pinned, not changed: the difference to the Rust and custom paths is documented and
        filed separately (the maintainers' decision on #3914)."""
        marked = KINDS[kind]("<b>x</b>")
        for template in (
            "<span>{{ v }}</span>",
            "<p><i>{{ v }}</i></p>",
            "<div>{{ o.v }}</div>",
            "<td>{{ v|default:'-' }}</td>",
            "{{ v }}",
        ):
            out = _draw(template, v=marked, o=_Obj(marked))
            assert "<b>x</b>" in out, (template, out)

    def test_a_plain_string_still_escapes_in_a_text_slot(self) -> None:
        assert _draw("<span>{{ v }}</span>", v="<b>x</b>") == "<span>&lt;b&gt;x&lt;/b&gt;</span>"

    @pytest.mark.parametrize("bound", [True, False])
    def test_a_name_the_template_binds_prints_where_the_context_has_none(
        self, bound: bool, monkeypatch
    ) -> None:
        """A name set by ``{% with %}`` / ``{% ... as x %}`` is not in the context. The scanner
        knows the binders it knows; for one it does not, the slot still prints, escaped (the
        fallback of every plain-name slot), instead of printing nothing."""
        if not bound:
            monkeypatch.setattr(hybrid, "_bound_names", lambda template: frozenset())
            hybrid.plan.cache_clear()
        template = "{% with v=raw %}<textarea>{{ v }}</textarea>{% endwith %}"
        assert _draw(template, raw=mark_safe("<b>")) == "<textarea>&lt;b&gt;</textarea>"

    def test_a_missing_name_prints_nothing(self) -> None:
        assert _draw("<textarea>{{ nope }}</textarea>") == "<textarea></textarea>"

    def test_the_context_the_caller_gave_is_not_modified(self) -> None:
        ctx = {"v": mark_safe("<b>")}
        template, hardened = hybrid.harden_context("<textarea>{{ v }}</textarea>", ctx, "X")
        assert ctx == {"v": mark_safe("<b>")} and "v" in hardened and hardened is not ctx
        assert "djust_raw" not in "".join(ctx)

    @pytest.mark.parametrize("kind", ["html-str", "html-object"])
    def test_the_django_fallback_engine_honours_html_objects_so_they_are_unmarked_too(
        self, kind: str, monkeypatch
    ) -> None:
        """An object with ``__html__`` (what ``markupsafe.Markup`` is) is written as it is by
        Django's engine, which a template falls back to when the Rust engine refuses it. The
        Rust engine escapes it anyway, so only this path tells the rule apart."""
        import djust._rust as rust

        def refuse(*args: Any, **kwargs: Any) -> str:
            raise RuntimeError("force the Django engine")

        monkeypatch.setattr(rust, "render_template", refuse)
        out = _draw("<textarea>{{ v }}</textarea>", v=KINDS[kind]("</textarea><i>"))
        assert "<i>" not in out and _end_tags(out, "textarea") == 1, out

    def test_the_text_form_of_an_ordinary_value_is_not_evaluated(self) -> None:
        """A list or dict is formatted by the engine once; ``unmarked`` must not format it a
        second time just to look for a claim (its repr can be large or have side effects)."""
        calls: list[int] = []

        class Loud:
            def __repr__(self) -> str:
                calls.append(1)
                return "loud"

        for value in ([Loud()], {"k": Loud()}, (Loud(),)):
            deferred, shown = hybrid.unmarked(value)
            assert shown is value and not deferred
        assert calls == []

    def test_unmarked_hands_over_what_it_can_and_defers_the_rest(self) -> None:
        """The three outcomes: as it is, as the exact ``str``, deferred to the template."""
        assert hybrid.unmarked("plain") == (False, "plain")
        assert hybrid.unmarked(5) == (False, 5)
        assert hybrid.unmarked(None) == (False, None)
        deferred, shown = hybrid.unmarked(mark_safe("<b>"))
        assert not deferred and type(shown) is str and shown == "<b>"
        for value in (
            KINDS["lazy-safe"]("<b>"),
            lambda: "x",
            KINDS["text-form-marked-safe"]("<b>"),
        ):
            assert hybrid.unmarked(value) == (True, value)


class TestTheScanner:
    def test_the_slots_of_a_raw_text_element_are_found(self) -> None:
        template = '<textarea name="{{ n }}">{{ a }}</textarea><b>{{ b }}</b><title>{{ c }}</title>'
        assert [(s.tag, s.expr.strip()) for s in hybrid.raw_slots(template)] == [
            ("textarea", "a"),
            ("title", "c"),
        ]
        assert [(s.tag, s.attr) for s in hybrid.slots(template)] == [("textarea", "name")]

    def test_verbatim_and_comment_are_not_templates(self) -> None:
        template = "<textarea>{% verbatim %}{{ a }}{% endverbatim %}{% comment %}{{ b }}{% endcomment %}{{ c }}</textarea>"
        assert [s.expr.strip() for s in hybrid.raw_slots(template)] == ["c"]

    def test_plaintext_has_no_end(self) -> None:
        """Nothing after ``<plaintext>`` is scanned for escaped text or attributes, and its own
        content is not escaped."""
        template = '<plaintext>{{ a }}</plaintext><textarea>{{ b }}</textarea><a href="{{ u }}">'
        assert hybrid.raw_slots(template) == [] and hybrid.slots(template) == []

    def test_an_html_comment_is_not_an_element(self) -> None:
        template = "<!-- <textarea> -->{{ a }}"
        assert hybrid.raw_slots(template) == []

    def test_a_slot_in_an_html_comment_is_a_raw_slot_of_the_comment(self) -> None:
        template = "<!-- {{ a }} --><b>{{ b }}</b>"
        assert [(s.tag, s.expr.strip()) for s in hybrid.raw_slots(template)] == [("comment", "a")]

    def test_the_end_tag_needs_a_delimiter(self) -> None:
        template = "<textarea>{{ a }}</textarea-x>{{ b }}</textarea>{{ c }}"
        assert [s.expr.strip() for s in hybrid.raw_slots(template)] == ["a", "b"]

    def test_the_other_raw_text_elements_hold_no_escaped_slot(self) -> None:
        for tag in OTHER_RAW_ELEMENTS + ("noscript",):
            assert hybrid.raw_slots(_template(tag, "{{ a }}")) == [], tag

    def test_a_tag_that_prints_text_inside_a_raw_element_is_listed(self) -> None:
        plan = hybrid.plan(
            '<textarea>{% firstof a b %}{% if c %}x{% endif %}{% url "x" %}</textarea>'
        )
        assert plan.raw_tags == (("textarea", "firstof"),)

    def test_a_url_assignment_is_silent_inside_escaped_text(self) -> None:
        """``{% url "x" as u %}`` prints nothing, so it is not a tag that prints text."""
        assert hybrid.plan('<textarea>{% url "x" as u %}{{ v }}</textarea>').raw_tags == ()
        assert hybrid.plan("<textarea>{% firstof a as u %}{{ v }}</textarea>").raw_tags == ()

    def test_an_include_is_counted_and_not_a_tag_that_prints_text(self) -> None:
        plan = hybrid.plan('<textarea>{% include "a.html" %}{{ v }}</textarea>')
        assert plan.includes == 1 and plan.include_names == ("a.html",) and plan.raw_tags == ()
        assert hybrid.plan('<p>{% include "a.html" %}</p>').includes == 1
        assert hybrid.plan('{% verbatim %}{% include "a.html" %}{% endverbatim %}').includes == 0
        dynamic = hybrid.plan("<p>{% include tpl %}{% include 'b.html' %}</p>")
        assert dynamic.includes == 2 and dynamic.include_names == ("b.html",)

    def test_a_template_without_any_of_this_is_returned_as_it_is(self) -> None:
        plan = hybrid.plan("<p>{{ a }}</p>")
        assert plan.template == "<p>{{ a }}</p>" and plan.raw == () and plan.shapes == ()

    @pytest.mark.parametrize("tag", ESCAPED_ELEMENTS)
    def test_every_escaped_element_is_a_scanner_state(self, tag: str) -> None:
        assert [s.tag for s in hybrid.raw_slots(_template(tag, "{{ a }}"))] == [tag]

    @pytest.mark.parametrize(
        "template",
        [
            "<{{ tag }}>{{ v }}",
            "<{% if m %}textarea{% else %}div{% endif %}>{{ v }}",
        ],
    )
    def test_an_element_name_built_by_a_tag_is_a_shape_it_cannot_follow(
        self, template: str
    ) -> None:
        assert hybrid.plan(template).shapes == ("element name built by a tag",)

    def test_a_less_than_sign_before_text_is_not_such_a_shape(self) -> None:
        assert hybrid.plan("a < {{ v }} and <b>{{ w }}</b>").shapes == ()

    def test_a_conditional_end_tag_is_a_shape_it_cannot_follow(self) -> None:
        plan = hybrid.plan("<textarea>{{ v }}{% if closed %}</textarea>{% endif %}")
        assert plan.shapes == ("conditional end tag",)
        assert hybrid.plan("{% if a %}<textarea>{{ v }}</textarea>{% endif %}").shapes == ()


class TestTheWarning:
    LOGGER = "djust.components._hybrid_attrs"

    def _component(self, template: str, name: str = "Rawish"):
        return type(name, (Component,), {"template": template, "get_context_data": lambda s: {}})

    def test_a_tag_that_prints_text_in_a_raw_element_is_named(self, caplog) -> None:
        cls = self._component("<textarea>{% firstof RUNTIME other %}</textarea>")
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            cls().render()
            cls().render()
        (record,) = [r for r in caplog.records if "Rawish" in r.getMessage()]
        assert "textarea:firstof" in record.getMessage()
        assert "RUNTIME" not in caplog.text

    def _warned(self, caplog, template: str, sources: dict[str, str] | None, monkeypatch) -> str:
        monkeypatch.setattr(hybrid, "_include_source", lambda name: (sources or {}).get(name))
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            hybrid._warn_once("Includer", hybrid.plan(template))
        return caplog.text

    def test_an_include_that_cannot_be_found_is_counted_never_named(
        self, caplog, monkeypatch
    ) -> None:
        text = self._warned(
            caplog, '<p>{% include "secret/RUNTIMEPART.html" %}</p>', {}, monkeypatch
        )
        assert "Includer" in text and "1 {% include %}" in text
        assert "RUNTIMEPART" not in text and "secret" not in text

    def test_an_include_of_a_dynamic_name_is_counted(self, caplog, monkeypatch) -> None:
        assert "1 {% include %}" in self._warned(
            caplog, "<p>{% include tpl %}</p>", {}, monkeypatch
        )

    def test_an_include_that_holds_no_slot_to_harden_is_not_reported(
        self, caplog, monkeypatch
    ) -> None:
        text = self._warned(
            caplog,
            '<p>{% include "static.html" %}</p>',
            {"static.html": "<p class='x'>just markup {{ plain_text }}</p>"},
            monkeypatch,
        )
        assert text == ""

    @pytest.mark.parametrize(
        "source",
        ['<a href="{{ u }}">x</a>', "<p title='{{ t }}'>x</p>", "<textarea>{{ v }}</textarea>"],
    )
    def test_an_include_that_holds_a_slot_is_reported(self, caplog, monkeypatch, source) -> None:
        text = self._warned(
            caplog, '<p>{% include "part.html" %}</p>', {"part.html": source}, monkeypatch
        )
        assert "1 {% include %}" in text and "part.html" not in text

    def test_the_advice_follows_what_was_found(self, caplog, monkeypatch) -> None:
        text = self._warned(caplog, '<p>{% include "a.html" %}</p>', {}, monkeypatch)
        assert "quote an attribute value" not in text and "URL" not in text
        assert "write the attribute and escaped-text slots in the template itself" in text

    def test_a_shape_the_scanner_cannot_follow_is_named(self, caplog, monkeypatch) -> None:
        text = self._warned(caplog, "<{{ tag }}>{{ v }}", None, monkeypatch)
        assert "element name built by a tag" in text

    def test_only_names_are_logged_and_an_attribute_name_is_template_text(self, caplog) -> None:
        """What the warning carries: the class, tag / attribute / element names the template
        spells, shapes and a count. Never a value; a static attribute name is template text and
        is logged, which is why a template built per instance should keep names static."""
        cls = self._component('<a data-visible-name="{{ item.u }}" href="{{ item.RUNTIMEVALUE }}">')
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            cls().render()
        assert "a[data-visible-name]" not in caplog.text  # not a URL attribute: not listed
        assert "a[href]" in caplog.text and "RUNTIMEVALUE" not in caplog.text

    def test_a_template_with_nothing_to_report_warns_nothing(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            self._component('<textarea>{{ v }}</textarea><a href="{{ u }}">').__call__().render()
        assert not caplog.records


# -- #3913 -----------------------------------------------------------------------------------------


def _live(template: str, **context: Any) -> str:
    """An inline-template ``LiveComponent`` drawn with a fixed context."""

    class Box(LiveComponent):
        pass

    Box.template = template
    Box.get_context_data = lambda self: dict(context)  # type: ignore[method-assign]
    box = Box()
    box._mounted = True
    return str(box.render())


def _bound(template: str, **state: Any) -> str:
    """The same template drawn as a ``BoundComponent`` (a class-level component, ADR-031)."""

    state_cls = type("State", (TypedState,), {"__annotations__": {k: Any for k in state}})
    box_cls = type("Box", (DescriptorLiveComponent,), {"State": state_cls, "template": template})
    page_cls = type("Page", (LiveView,), {"box": box_cls(**state)})
    return str(page_cls().box)


DRAWERS = {"LiveComponent": _live, "BoundComponent": _bound}
#: ... and the hybrid ``Component`` itself, for the tests about the one helper they share.
ALL_DRAWERS = {"Component": _draw, **DRAWERS}


def _body(markup: str) -> str:
    """``markup`` without the wrapper a ``LiveComponent`` / ``BoundComponent`` adds."""
    return re.sub(r'<div data-component-id="[^"]*">|</div>$', "", markup)


@pytest.mark.parametrize("drawer", sorted(ALL_DRAWERS))
class TestLazyEvaluation:
    """A value is looked at where the engine reaches its slot, not when the context is prepared:
    a callable in a branch that never renders is not called. (A ``BoundComponent`` hashes its
    state as JSON with ``default=str`` to cache the render, which asks any value for its text
    form: that is the cache, not this module, so the value-evaluation tests skip it.)"""

    def test_a_callable_in_a_branch_that_never_renders_is_not_called(self, drawer: str) -> None:
        def get_draft() -> str:
            raise ValueError("called although its branch never renders")

        out = ALL_DRAWERS[drawer](
            "{% if flag %}<textarea>{{ get_draft }}</textarea>{% endif %}ok",
            flag=False,
            get_draft=get_draft,
        )
        assert _body(out).endswith("ok") and "textarea" not in out

    def test_the_same_callable_is_called_once_where_its_branch_renders(self, drawer: str) -> None:
        calls: list[int] = []

        def get_draft() -> object:
            calls.append(1)
            return mark_safe("</textarea><b onclick=x>")

        out = ALL_DRAWERS[drawer](
            "{% if flag %}<textarea>{{ get_draft }}</textarea>{% endif %}",
            flag=True,
            get_draft=get_draft,
        )
        assert calls == [1]
        assert _end_tags(out, "textarea") == 1 and "<b onclick" not in out, out

    def test_a_lazy_value_is_not_evaluated_in_a_branch_that_never_renders(
        self, drawer: str
    ) -> None:
        from django.utils.functional import lazy

        if drawer == "BoundComponent":
            pytest.skip("the state hash of a BoundComponent evaluates its values")

        def explode() -> str:
            raise ValueError("evaluated")

        out = ALL_DRAWERS[drawer](
            "{% if flag %}<title>{{ t }}</title>{% endif %}ok", flag=False, t=lazy(explode, str)()
        )
        assert _body(out).endswith("ok")

    def test_an_object_with_its_own_text_form_is_not_asked_for_it_when_unused(
        self, drawer: str
    ) -> None:
        if drawer == "BoundComponent":
            pytest.skip("the state hash of a BoundComponent evaluates its values")

        class Costly:
            def __str__(self) -> str:
                raise ValueError("text form asked for")

        out = ALL_DRAWERS[drawer](
            "{% if flag %}<textarea>{{ obj }}</textarea>{% endif %}ok", flag=False, obj=Costly()
        )
        assert _body(out).endswith("ok")


@pytest.mark.parametrize("engine", ["rust", "django"])
@pytest.mark.parametrize("drawer", sorted(ALL_DRAWERS))
class TestTranslationBlocks:
    """The variables of ``{% blocktranslate %}`` are keyed by the translation catalog: the body is
    never rewritten, and what the block prints is escaped once. Main printed ``Delete `` for an
    attribute (the variable was renamed to something that does not exist)."""

    LOAD = "{% load i18n %}"

    @pytest.fixture(autouse=True)
    def _engine(self, request, monkeypatch):
        if request.node.callspec.params["engine"] == "django":
            request.getfixturevalue("django_engine")

    def _draw(self, drawer: str, template: str, **context: Any) -> str:
        return _body(ALL_DRAWERS[drawer](self.LOAD + template, **context))

    @pytest.mark.parametrize(
        ("block", "context", "want"),
        [
            (
                "{% blocktranslate %}Delete {{ item }}{% endblocktranslate %}",
                {"item": "Rep & x"},
                "Delete Rep &amp; x",
            ),
            (
                "{% blocktranslate trimmed %}  Delete\n   {{ item }}  {% endblocktranslate %}",
                {"item": "R"},
                "Delete R",
            ),
            (
                "{% blocktranslate with n=item %}Hi {{ n }}{% endblocktranslate %}",
                {"item": "a<b"},
                "Hi a&lt;b",
            ),
            (
                '{% blocktranslate context "ctx" %}Open {{ item }}{% endblocktranslate %}',
                {"item": "X"},
                "Open X",
            ),
            (
                "{% blocktranslate count c=n %}one {{ item }}{% plural %}{{ c }} {{ item }}s{% endblocktranslate %}",
                {"item": "doc", "n": 1},
                "one doc",
            ),
            (
                "{% blocktranslate count c=n %}one {{ item }}{% plural %}{{ c }} {{ item }}s{% endblocktranslate %}",
                {"item": "doc", "n": 3},
                "3 docs",
            ),
        ],
        ids=["plain", "trimmed", "with", "context", "count-one", "count-many"],
    )
    @pytest.mark.parametrize("place", ["attribute", "textarea", "title", "comment"])
    def test_an_ordinary_value_prints_as_it_always_did(
        self, drawer: str, engine: str, block: str, context: dict, want: str, place: str
    ) -> None:
        template = {
            "attribute": f'<p aria-label="{block}">x</p>',
            "textarea": f"<textarea>{block}</textarea>",
            "title": f"<title>{block}</title>",
            "comment": f"<!-- {block} -->",
        }[place]
        out = self._draw(drawer, template, **context)
        assert want in out, (place, out)
        # byte for byte what the engine alone prints
        alone = _body(components_base._render_template_with_fallback(self.LOAD + template, context))
        assert out == alone, (place, out, alone)

    @pytest.mark.parametrize("kind", sorted(KINDS))
    @pytest.mark.parametrize("place", ["attribute", "textarea"])
    def test_a_marked_safe_value_stays_inside(
        self, drawer: str, engine: str, kind: str, place: str
    ) -> None:
        block = "{% blocktranslate with n=item %}Hi {{ n }} {{ item }}{% endblocktranslate %}"
        template = (
            f'<p title="{block}">x</p>' if place == "attribute" else f"<textarea>{block}</textarea>"
        )
        out = self._draw(drawer, template, item=KINDS[kind](_breakout("textarea") + '" onclick="y'))
        assert "<b onclick" not in out and "onclick" not in _attrs(out, "p"), out
        assert _end_tags(out, "textarea") <= 1, out

    def test_the_msgid_is_the_authors_so_a_catalog_entry_still_matches(
        self, drawer: str, engine: str, monkeypatch
    ) -> None:
        """The variable keeps its name, so ``Hello %(name)s`` is the key the catalog knows."""
        from django.utils import translation

        monkeypatch.setattr(
            translation,
            "gettext",
            lambda msgid: {"Hello %(name)s": "Hallo %(name)s"}.get(msgid, msgid),
        )
        for template in (
            '<p aria-label="{% blocktranslate %}Hello {{ name }}{% endblocktranslate %}">x</p>',
            "<textarea>{% blocktranslate %}Hello {{ name }}{% endblocktranslate %}</textarea>",
        ):
            assert "Hallo Ann" in self._draw(drawer, template, name="Ann"), template

    def test_a_block_that_stores_its_result_prints_nothing_and_is_left_alone(
        self, drawer: str, engine: str
    ) -> None:
        """``asvar`` sets a variable (a marked-safe string, escaped again where an attribute
        prints it: the rule for any pre-escaped string, documented)."""
        template = (
            "{% blocktranslate asvar lab %}Del {{ item }}{% endblocktranslate %}"
            '<p aria-label="{{ lab }}">x</p><textarea>{{ lab }}</textarea>'
        )
        out = self._draw(drawer, template, item="Rep")
        assert 'aria-label="Del Rep"' in out and "<textarea>Del Rep</textarea>" in out, out
        out = self._draw(drawer, template, item=mark_safe('"><b>x</b>'))
        assert "<b>" not in out and "&lt;b&gt;x" in out, out

    @pytest.mark.parametrize("kind", sorted(KINDS))
    def test_a_with_argument_that_is_an_expression_is_escaped(
        self, drawer: str, engine: str, kind: str
    ) -> None:
        """``with n=obj.v``: the argument is not a name the block reads from the context, so the
        value is escaped where the argument is bound."""
        block = "{% blocktranslate with n=o.v %}Hi {{ n }}{% endblocktranslate %}"
        out = self._draw(
            drawer, f'<p title="{block}">x</p>', o=_Obj(KINDS[kind]('"><b onclick=x>'))
        )
        assert "<b onclick" not in out and "onclick" not in _attrs(out, "p"), (kind, out)

    def test_the_name_a_block_stores_is_a_name_the_template_binds(
        self, drawer: str, engine: str
    ) -> None:
        """``asvar u`` makes ``u`` a template variable: a later URL slot reads it as such (it is
        escaped and named, not looked up in a context that does not hold it)."""
        template = (
            '{% blocktranslate asvar u %}/p/{{ x }}{% endblocktranslate %}<a href="{{ u }}">x</a>'
        )
        assert _attrs(self._draw(drawer, template, x="7"), "a")["href"] == "/p/7"
        assert "u" in hybrid._bound_names(self.LOAD + template)

    def test_a_block_that_stores_its_result_is_not_rewritten_inside_an_attribute(
        self, drawer: str, engine: str
    ) -> None:
        """Even placed in an attribute (where it prints nothing) it stays as written: the
        variable it sets keeps the author's markup for a text slot that prints it."""
        template = (
            '<i title="{% blocktranslate asvar lab %}Del {{ item }}{% endblocktranslate %}"></i>'
            "<p>{{ lab }}</p>"
        )
        out = self._draw(drawer, template, item=mark_safe("<b>x</b>"))
        assert "<p>Del <b>x</b></p>" in out, out
        assert hybrid.plan(self.LOAD + template).template == self.LOAD + template

    def test_the_legacy_form_is_left_alone_and_named(
        self, drawer: str, engine: str, caplog
    ) -> None:
        template = (
            '<p title="{% blocktranslate with item as y %}Del {{ y }}{% endblocktranslate %}">x</p>'
        )
        assert hybrid.plan(self.LOAD + template).shapes == ("translation block it cannot rewrite",)

    def test_a_block_in_a_text_slot_is_not_touched(self, drawer: str, engine: str) -> None:
        out = self._draw(
            drawer,
            "<p>{% blocktranslate %}Hi {{ item }}{% endblocktranslate %}</p>",
            item=mark_safe("<b>x</b>"),
        )
        assert "<p>Hi <b>x</b></p>" in out, out

    def test_a_block_in_a_url_attribute_is_escaped_and_named(
        self, drawer: str, engine: str, caplog
    ) -> None:
        template = '<a href="{% blocktranslate %}/p/{{ item }}{% endblocktranslate %}">x</a>'
        plan = hybrid.plan(self.LOAD + template)
        assert [t[:2] for t in plan.unchecked_urls] == [("a", "href")]
        out = self._draw(drawer, template, item=mark_safe('x" onclick="y'))
        assert "onclick" not in _attrs(out, "a"), out


@pytest.mark.parametrize("drawer", sorted(DRAWERS))
class TestInlineTemplateAttributes:
    """The attribute rule of the hybrid ``Component`` template, for the other two inline templates."""

    @pytest.mark.parametrize("kind", sorted(KINDS))
    def test_a_script_url_is_judged_whatever_the_value_is(self, drawer: str, kind: str) -> None:
        out = DRAWERS[drawer]('<a href="{{ url }}">x</a>', url=KINDS[kind](JS))
        assert _attrs(out, "a")["href"] == "#", (kind, out)

    @pytest.mark.parametrize("kind", sorted(KINDS))
    def test_a_value_stays_inside_its_attribute(self, drawer: str, kind: str) -> None:
        out = DRAWERS[drawer]('<a title="{{ t }}">x</a>', t=KINDS[kind](BREAKOUT))
        assert "onclick" not in _attrs(out, "a"), (kind, out)
        assert "&quot;" in out

    @pytest.mark.parametrize("kind", sorted(KINDS))
    def test_a_single_quoted_value_stays_inside_its_attribute(self, drawer: str, kind: str) -> None:
        out = DRAWERS[drawer]("<a title='{{ t }}'>x</a>", t=KINDS[kind]("x' onclick='y"))
        assert "onclick" not in _attrs(out, "a"), (kind, out)

    @pytest.mark.parametrize("attr", ["dj-navigate", "dj-patch", "dj-patch-reload"])
    @pytest.mark.parametrize("kind", ["plain", "mark_safe", "lazy-safe", "callable-marked-safe"])
    def test_the_frameworks_url_directives_are_judged(
        self, drawer: str, attr: str, kind: str
    ) -> None:
        out = DRAWERS[drawer](f'<a {attr}="{{{{ url }}}}">x</a>', url=KINDS[kind](JS))
        assert _attrs(out, "a")[attr] == "#", (attr, kind, out)

    @pytest.mark.parametrize("kind", ["plain", "mark_safe", "lazy-safe", "text-form-marked-safe"])
    def test_a_raw_text_element_is_escaped_too(self, drawer: str, kind: str) -> None:
        out = DRAWERS[drawer]("<textarea>{{ v }}</textarea>", v=KINDS[kind](_breakout("textarea")))
        assert _end_tags(out, "textarea") == 1 and "<b onclick" not in out, out

    @pytest.mark.parametrize(
        "template",
        [
            "<script>var cfg = {{ cfg|safe }};</script>",
            "<script>var cfg = {{ cfg }};</script>",
            "<style>.x{{ cfg|safe }}</style>",
            "<noscript>{{ cfg|safe }}</noscript>",
            "<xmp>{{ cfg|safe }}</xmp>",
        ],
    )
    @pytest.mark.parametrize("kind", ["plain", "mark_safe"])
    def test_script_style_and_the_like_keep_the_authors_markup(
        self, drawer: str, template: str, kind: str
    ) -> None:
        """The case a review found: JSON written with the safe filter into a script must stay
        JSON. These elements print what they printed before the change."""
        value = KINDS[kind]('{"a": "b"}')
        out = DRAWERS[drawer](template, cfg=value)
        before = _unhardened(template, {"cfg": value})
        body = lambda s: re.sub(r'<div data-component-id="[^"]*">|</div>', "", s)  # noqa: E731
        assert body(out) == body(before), (template, kind, out, before)

    def test_json_script_is_the_way_to_write_data_into_a_page(self, drawer: str) -> None:
        out = DRAWERS[drawer]('<p>{{ cfg|json_script:"cfg" }}</p>', cfg={"a": "b"})
        assert '<script id="cfg" type="application/json">{"a": "b"}</script>' in out, out

    def test_an_html_comment_is_ended_only_by_its_own_end(self, drawer: str) -> None:
        out = DRAWERS[drawer]("<!-- {{ v }} --><i>x</i>", v=mark_safe("--><b onclick=x>"))
        assert out.count("-->") == 1 and "<b onclick" not in out, out

    def test_verbatim_and_comment_are_left_alone(self, drawer: str) -> None:
        out = DRAWERS[drawer](
            '{% verbatim %}<a href="{{ url }}">{% endverbatim %}{% comment %}<a href="{{ url }}">{% endcomment %}',
            url=JS,
        )
        assert '<a href="{{ url }}">' in out and "javascript" not in out.replace("{{ url }}", "")

    @pytest.mark.parametrize(
        "url",
        [
            "/a/b",
            "https://example.com/p?q=1&r=2",
            "mailto:a@b.c",
            "tel:+15551234",
            "#top",
            "?page=2",
            "relative/path",
            "javascript:void(0)",
            "",
        ],
    )
    def test_an_ordinary_url_prints_as_before(self, drawer: str, url: str) -> None:
        template = '<a href="{{ url }}" title="{{ t }}">x</a>'
        before = components_base._render_template_with_fallback(
            template, {"url": url, "t": "a & b"}
        )
        out = DRAWERS[drawer](template, url=url, t="a & b")
        assert _attrs(out, "a") == _attrs(before, "a"), (url, out, before)

    def test_a_url_after_a_fixing_literal_is_escaped_not_judged(self, drawer: str) -> None:
        out = DRAWERS[drawer]('<a href="/search?q={{ q }}">x</a>', q="foo:bar&x=1")
        assert _attrs(out, "a")["href"] == "/search?q=foo:bar&x=1"

    def test_a_script_url_in_a_list_item_is_not_judged_but_is_escaped_and_named(
        self, drawer: str, caplog
    ) -> None:
        template = '<ul>{% for u in urls %}<li><a href="{{ u }}">x</a></li>{% endfor %}</ul>'
        with caplog.at_level(logging.WARNING, logger="djust.components._hybrid_attrs"):
            out = DRAWERS[drawer](template, urls=[mark_safe(BREAKOUT)])
        assert "onclick" not in _attrs(out, "a"), out
        assert "not scheme-checked" in caplog.text and "a[href]" in caplog.text
        assert BREAKOUT not in caplog.text


class TestLiveComponentTemplate:
    def test_the_component_wrapper_is_unchanged(self) -> None:
        out = _live("<b>{{ x }}</b>", x="1")
        assert re.fullmatch(r'<div data-component-id="[^"]+"><b>1</b></div>', out), out

    def test_a_component_id_in_an_attribute_is_unchanged(self) -> None:
        out = _live('<i id="{{ component_id }}">x</i>')
        assert re.fullmatch(r'<div data-component-id="([^"]+)"><i id="\1">x</i></div>', out), out

    def test_a_text_slot_keeps_a_marked_safe_values_markup(self) -> None:
        assert "<b>x</b>" in _live("<p>{{ v }}</p>", v=mark_safe("<b>x</b>"))

    def test_a_real_component_class_with_get_context_data(self) -> None:
        class Link(LiveComponent):
            template = '<a href="{{ url }}">{{ label }}</a>'

            def get_context_data(self) -> dict[str, Any]:
                return {"url": mark_safe(JS), "label": "go"}

        link = Link()
        link._mounted = True
        assert _attrs(str(link.render()), "a")["href"] == "#"

    def test_an_include_is_named_in_the_warning(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="djust.components._hybrid_attrs"):
            try:
                _live('<p title="{{ t }}">{% include "no/such/fragment.html" %}</p>', t="x")
            except Exception:  # noqa: BLE001 - the fragment does not exist; the warning is the subject
                pass
        assert "1 {% include %}" in caplog.text and "no/such" not in caplog.text


class TestBoundComponentTemplate:
    def test_the_wrapper_and_the_cache_are_unchanged(self) -> None:
        out = _bound("<b>{{ x }}</b>", x="1")
        assert out == '<div data-component-id="box"><b>1</b></div>', out

    def test_a_state_change_is_drawn_through_the_rule(self) -> None:
        class Box(DescriptorLiveComponent):
            class State(TypedState):
                url: str = "/ok"

            template = '<a href="{{ url }}">x</a>'

        class Page(LiveView):
            box = Box()

        page = Page()
        assert _attrs(str(page.box), "a")["href"] == "/ok"
        page.box.url = JS
        assert _attrs(str(page.box), "a")["href"] == "#"


#: Names that render a TEMPLATE STRING: the raw renderer of this package, Django's ``Template``,
#: an engine's ``from_string`` and the Rust entry ``render_template``.
SINK_NAMES = frozenset(
    {"_render_template_with_fallback", "Template", "from_string", "render_template"}
)
#: ... and those that render a template FILE (``template_name``), which the rule does not read (#3933).
FILE_NAMES = frozenset({"render_to_string", "get_template", "select_template"})


def _final(node: ast.AST) -> str:
    """The last name of ``x``, ``a.x`` or ``a.b.x``; empty for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def census(source: str, rel: str, names: frozenset[str]) -> set[tuple[str, str, str]]:
    """``(file, enclosing function, name)`` of every place ``source`` reads one of ``names``, under
    ANY spelling: called, passed on, read through a module (``base.f``), imported under another
    name (``from .base import f as g``) or assigned to one (``g = f``). Reading, not only calling,
    so a reference that is called later is a site too."""
    tree = ast.parse(source)
    watch = set(names)
    grew = True
    while grew:  # aliases of aliases
        grew = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for item in node.names:
                    if (
                        item.asname
                        and item.name.split(".")[-1] in watch
                        and item.asname not in watch
                    ):
                        watch.add(item.asname)
                        grew = True
            elif isinstance(node, ast.Assign) and _final(node.value) in watch:
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in watch:
                        watch.add(target.id)
                        grew = True
    found: set[tuple[str, str, str]] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def _seen(self, node: ast.AST) -> None:
            if _final(node) in watch:
                found.add((rel, self.stack[-1] if self.stack else "<module>", _final(node)))

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Load):
                self._seen(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            self._seen(node)
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


def _tree_census(roots: tuple[str, ...], names: frozenset[str]) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    base = ROOT / "python/djust"
    for root in roots:
        path = base / root
        files = [path] if path.suffix == ".py" else sorted(path.rglob("*.py"))
        for file in files:
            posix = file.as_posix()
            if "/tests/" in posix or "/test_" in posix or "/static/" in posix:
                continue
            found |= census(file.read_text(), file.relative_to(base).as_posix(), names)
    return found


#: Everything under these is scanned for a read of a sink name.
CENSUS_ROOTS = ("components", "mixins/components.py")

#: Every site that renders a template STRING under ``CENSUS_ROOTS``, and why it is allowed. A new
#: site is a decision: route it through ``_render_inline_template`` or list it here with a reason.
RENDER_SITES = {
    (
        "components/base.py",
        "_render_template_with_fallback",
        "render_template",
    ): "the raw renderer itself (Rust entry)",
    (
        "components/base.py",
        "_render_template_with_fallback",
        "Template",
    ): "the raw renderer itself (Django fallback)",
    (
        "components/base.py",
        "_render_inline_template",
        "_render_template_with_fallback",
    ): "THE caller: hardens the template first",
    (
        "components/gallery/views.py",
        "_render_component_cards",
        "from_string",
    ): "framework-owned gallery demo of a template TAG, not a component's template",
}

#: Every site that renders a template FILE, all outside the rule (#3933).
FILE_SITES = {
    ("components/base.py", "_load_template_source", "get_template"),
    ("components/base.py", "render", "render_to_string"),
    ("components/suspense.py", "render", "render_to_string"),
}


class TestOneSink:
    """The one place a component's inline template string is rendered (#3913). A site that renders
    one without ``harden_context`` is the parallel-path drift this change closes, so the SET of
    sites is pinned (not a floor), under every spelling: an alias, another module, a direct
    ``Template(...).render(...)``."""

    def test_every_site_that_renders_a_template_string_is_the_helper_or_listed(self) -> None:
        found = _tree_census(CENSUS_ROOTS, SINK_NAMES)
        listed = set(RENDER_SITES)
        assert found == listed, (
            f"new sites (route through _render_inline_template or list with a reason): "
            f"{sorted(found - listed)}\nlisted but gone: {sorted(listed - found)}"
        )

    def test_the_file_based_sites_are_the_listed_ones(self) -> None:
        found = _tree_census(CENSUS_ROOTS, FILE_NAMES)
        # the function that renders a template_name differs per class; compare file and name
        assert {(f, n) for f, _fn, n in found} == {(f, n) for f, _fn, n in FILE_SITES}, found

    def test_no_other_module_of_the_package_reads_the_raw_renderer(self) -> None:
        """A LiveView's own templates (``live_tags``, ``streaming``, ``websocket``) are rendered
        by their own paths; none of them borrows the component renderer."""
        found = _tree_census((".",), frozenset({"_render_template_with_fallback"}))
        assert found == {
            ("components/base.py", "_render_inline_template", "_render_template_with_fallback")
        }

    def test_the_rule_is_applied_in_that_one_place(self) -> None:
        found = _tree_census((".",), frozenset({"harden_context"}))
        assert found == {("components/base.py", "_render_inline_template", "harden_context")}

    def test_every_inline_template_renderer_calls_the_helper(self) -> None:
        found = _tree_census((".",), frozenset({"_render_inline_template"}))
        assert {(f, n) for f, _fn, n in found} == {
            ("components/base.py", "_render_inline_template")
        }
        assert sorted(fn for _f, fn, _n in found if fn != "<module>") == ["render"]
        calls = [
            node
            for node in ast.walk(ast.parse((ROOT / "python/djust/components/base.py").read_text()))
            if isinstance(node, ast.Call) and _final(node.func) == "_render_inline_template"
        ]
        assert len(calls) == 3  # Component.render, BoundComponent.render, LiveComponent.render

    def test_the_helper_takes_the_owner_for_the_warning(self) -> None:
        import inspect

        sig = inspect.signature(components_base._render_inline_template)
        assert list(sig.parameters) == ["template_str", "context", "owner"]

    # -- the census itself can fail ----------------------------------------------------------

    @pytest.mark.parametrize(
        "source",
        [
            "def f(t, c):\n    return _render_template_with_fallback(t, c)\n",
            "from .base import _render_template_with_fallback as r\n\ndef f(t, c):\n    return r(t, c)\n",
            "from .base import _render_template_with_fallback\n\n_alias = _render_template_with_fallback\n\ndef f(t, c):\n    return _alias(t, c)\n",
            "from . import base\n\ndef f(t, c):\n    return base._render_template_with_fallback(t, c)\n",
            "from django.template import Template\n\ndef f(t, c):\n    return Template(t).render(c)\n",
            "def f(engine, t):\n    return engine.from_string(t)\n",
            "from djust._rust import render_template as rt\n\ndef f(t, c):\n    return rt(t, c, True)\n",
            "def f(t, c):\n    return list(map(_render_template_with_fallback, [t], [c]))\n",
        ],
        ids=[
            "name",
            "import-alias",
            "assigned-alias",
            "module-attr",
            "Template",
            "from_string",
            "rust-alias",
            "passed-on",
        ],
    )
    def test_the_census_sees_every_spelling_of_a_render_site(self, source: str) -> None:
        assert census(source, "x.py", SINK_NAMES), source

    def test_the_census_ignores_a_name_that_is_not_read(self) -> None:
        source = '"""_render_template_with_fallback"""\n\ndef f():\n    return 1  # Template\n'
        assert census(source, "x.py", SINK_NAMES) == set()

    def test_the_census_names_the_enclosing_function(self) -> None:
        source = (
            "class C:\n    def render(self):\n        return Template(self.template).render(c)\n"
        )
        assert census(source, "x.py", SINK_NAMES) == {("x.py", "render", "Template")}
