"""Raw-text elements of a hybrid template (#3914) and the inline templates of a ``LiveComponent``
and a ``BoundComponent`` (#3913) follow the rule the hybrid ``Component`` template follows (#3900).

#3914. A value written into the content of a raw-text element (``<textarea>``, ``<title>``,
``<script>``, ``<style>``, ``<xmp>``, ``<plaintext>``, ``<noscript>``, ``<iframe>``, ``<noembed>``,
``<noframes>``) is escaped even when it is marked safe, because the text there can end the
element. Every OTHER text slot keeps a marked-safe value's markup (the #3898 rule): that part is
not changed and is pinned here.

#3913. ``LiveComponent.render`` and ``BoundComponent.render`` draw their inline ``template``
through the same helper as ``Component.render``: the attribute slots are escaped whatever the
value is marked as and a URL slot is checked against the scheme allowlist. One call, in one
place (``_render_inline_template``); a pin below fails when another caller appears.

What each class proves:

* ``TestRawTextElements``: every element x every slot spelling x every value kind. The text a
  value yields never ends the element it sits in.
* ``TestOrdinaryTextIsUnchanged``: ordinary values (strings, numbers, dates, ``None`` ...) print
  as they did, byte for byte, in a raw-text element; every other text slot keeps its markup.
* ``TestTheScanner``: where the raw-text content starts and ends, and what is listed as not
  escaped (a tag that prints text, an ``{% include %}``).
* ``TestLiveComponentTemplate`` / ``TestBoundComponentTemplate``: the attribute rule, for each
  value kind, the framework's URL directives, ``{% verbatim %}`` / ``{% comment %}``, and the
  ordinary values that must not change.
* ``TestOneSink``: the one place a component's inline template string is rendered.
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


#: Every raw-text element, the way a browser reads it (the content ends at the first matching end
#: tag; ``<plaintext>`` has none).
RAW_ELEMENTS = (
    "textarea",
    "title",
    "script",
    "style",
    "xmp",
    "plaintext",
    "noscript",
    "iframe",
    "noembed",
    "noframes",
)

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
    """How many times a browser would end a ``<tag>`` element in ``markup``."""
    return len(re.findall(rf"</{tag}(?=[\s/>])", markup, re.I))


def _breakout(tag: str) -> str:
    """Text that ends ``tag`` and starts a live element with a handler."""
    return f"</{tag}><b onclick=x>"


class TestRawTextElements:
    """The text a value yields never ends the element it sits in, whatever the value is."""

    @pytest.mark.parametrize("kind", sorted(KINDS))
    @pytest.mark.parametrize("spelling", sorted(SPELLINGS))
    @pytest.mark.parametrize("tag", RAW_ELEMENTS)
    def test_a_value_does_not_end_the_element(self, tag: str, spelling: str, kind: str) -> None:
        slot, _ = SPELLINGS[spelling]
        out = _draw(_template(tag, slot), **_context(KINDS[kind](_breakout(tag))))
        want_ends = 0 if tag == "plaintext" else 1
        assert _end_tags(out, tag) == want_ends, (tag, spelling, kind, out)
        assert "<b onclick" not in out, (tag, spelling, kind, out)
        assert "&lt;/" in out, (tag, spelling, kind, out)

    @pytest.mark.parametrize("tag", RAW_ELEMENTS)
    def test_the_gate_off_the_unfixed_template_does_end_it(self, tag: str) -> None:
        """The test above can fail: the same template, rendered without the rewrite, is broken
        out of by a marked-safe value (not a tautology)."""
        if tag == "plaintext":
            pytest.skip("<plaintext> has no end tag to break out of")
        template = _template(tag, "{{ v }}")
        out = components_base._render_template_with_fallback(
            template, {"v": mark_safe(_breakout(tag))}
        )
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

    def test_a_slot_in_a_nested_element_of_noscript_is_escaped(self) -> None:
        out = _draw("<noscript><p>{{ v }}</p></noscript>", v=mark_safe("</noscript><i>"))
        assert _end_tags(out, "noscript") == 1 and "<i>" not in out, out

    def test_the_element_after_the_raw_one_is_ordinary_again(self) -> None:
        out = _draw(
            "<textarea>{{ a }}</textarea><span>{{ b }}</span><noscript>{{ c }}</noscript>"
            "<span>{{ d }}</span>",
            a=mark_safe("<i>a</i>"),
            b=mark_safe("<b>b</b>"),
            c=mark_safe("<i>c</i>"),
            d=mark_safe("<b>d</b>"),
        )
        assert "&lt;i&gt;a" in out and "&lt;i&gt;c" in out, out
        assert "<span><b>b</b></span>" in out and "<span><b>d</b></span>" in out, out

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

    @pytest.mark.parametrize("tag", ["textarea", "title", "script", "noscript"])
    @pytest.mark.parametrize("name", sorted(ORDINARY))
    def test_an_ordinary_value_in_a_raw_text_element_prints_as_before(
        self, tag: str, name: str
    ) -> None:
        value = self.ORDINARY[name]
        template = _template(tag, "{{ v }}")
        before = components_base._render_template_with_fallback(template, {"v": value})
        assert _draw(template, v=value) == before

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
            assert hybrid.unmarked(value) is value
        assert calls == []


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
        template = "<plaintext>{{ a }}</plaintext>{{ b }}"
        assert [s.expr.strip() for s in hybrid.raw_slots(template)] == ["a", "b"]

    def test_an_html_comment_is_not_an_element(self) -> None:
        template = "<!-- <textarea> -->{{ a }}"
        assert hybrid.raw_slots(template) == []

    def test_a_slot_in_an_html_comment_is_a_raw_slot_of_the_comment(self) -> None:
        template = "<!-- {{ a }} --><b>{{ b }}</b>"
        assert [(s.tag, s.expr.strip()) for s in hybrid.raw_slots(template)] == [("comment", "a")]

    def test_the_end_tag_needs_a_delimiter(self) -> None:
        template = "<textarea>{{ a }}</textarea-x>{{ b }}</textarea>{{ c }}"
        assert [s.expr.strip() for s in hybrid.raw_slots(template)] == ["a", "b"]

    def test_a_slot_is_not_a_raw_slot_after_noscript_ends(self) -> None:
        template = "<noscript>{{ a }}</noscript>{{ b }}"
        assert [s.expr.strip() for s in hybrid.raw_slots(template)] == ["a"]

    def test_a_tag_that_prints_text_inside_a_raw_element_is_listed(self) -> None:
        plan = hybrid.plan(
            '<textarea>{% firstof a b %}{% if c %}x{% endif %}{% url "x" %}</textarea>'
        )
        assert plan.raw_tags == (("textarea", "firstof"),)

    def test_an_include_is_counted(self) -> None:
        assert hybrid.plan('<p>{% include "a.html" %}</p>').includes == 1
        assert hybrid.plan('{% verbatim %}{% include "a.html" %}{% endverbatim %}').includes == 0

    def test_a_template_without_any_of_this_is_returned_as_it_is(self) -> None:
        plan = hybrid.plan("<p>{{ a }}</p>")
        assert plan.template == "<p>{{ a }}</p>" and plan.raw == ()

    def test_every_raw_element_is_a_scanner_state(self) -> None:
        for tag in RAW_ELEMENTS:
            assert [s.tag for s in hybrid.raw_slots(_template(tag, "{{ a }}"))] == [tag], tag


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

    def test_an_include_is_named_by_count_and_never_by_template(self, caplog) -> None:
        plan = hybrid.plan('<p>{% include "secret/RUNTIMEPART.html" %}</p>')
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            hybrid._warn_once("Includer", plan)
        assert "Includer" in caplog.text and "included" in caplog.text
        assert "RUNTIMEPART" not in caplog.text and "secret" not in caplog.text

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
        assert "included" in caplog.text and "no/such" not in caplog.text


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


class TestOneSink:
    """The one place a component's inline template string is rendered (#3913). A fourth caller of
    ``_render_template_with_fallback`` that skips ``harden_context`` is the parallel-path drift
    this change closes, so the caller SET is pinned (not a floor)."""

    SOURCE = (ROOT / "python/djust/components/base.py").read_text()

    def _calls(self, name: str) -> list[tuple[str, str]]:
        """``(enclosing function, callee)`` of every call to ``name`` in ``base.py``."""
        tree = ast.parse(self.SOURCE)
        found: list[tuple[str, str]] = []

        class V(ast.NodeVisitor):
            def __init__(self) -> None:
                self.stack: list[str] = []

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

            def visit_Call(self, node: ast.Call) -> None:
                func = node.func
                callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if callee == name:
                    found.append((self.stack[-1] if self.stack else "<module>", callee))
                self.generic_visit(node)

        V().visit(tree)
        return found

    def test_the_raw_renderer_has_exactly_one_caller(self) -> None:
        assert self._calls("_render_template_with_fallback") == [
            ("_render_inline_template", "_render_template_with_fallback")
        ]

    def test_the_rule_is_applied_in_that_one_place(self) -> None:
        assert self._calls("harden_context") == [("_render_inline_template", "harden_context")]

    def test_every_class_that_draws_an_inline_template_calls_it(self) -> None:
        assert sorted(f for f, _ in self._calls("_render_inline_template")) == [
            "render",
            "render",
            "render",
        ]

    def test_the_helper_takes_the_owner_for_the_warning(self) -> None:
        import inspect

        sig = inspect.signature(components_base._render_inline_template)
        assert list(sig.parameters) == ["template_str", "context", "owner"]
