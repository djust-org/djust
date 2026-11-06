"""A ``Component``'s hybrid ``template`` follows the attribute rule of the other two paths (#3900).

``Component.render`` draws a component by the crate instance, else by its ``template`` (the hybrid
path), else by ``_render_custom()``. The crate constructor takes plain text only: a lazy string
makes it refuse, and a class that has a ``template`` is then drawn by it; a class with no crate
counterpart is drawn by it always. The template path wrote ``{{ src }}`` into ``src=`` with no URL
check and only autoescape, which leaves a marked-safe value alone.

* ``TestTheIssue``: the measured cells of the issue, on ``Avatar``.
* ``TestTheRule``: what the rewrite does and does not touch (text slots, numbers, ``|safe``).
* ``TestTheScanner``: where an attribute slot is.
* ``TestEveryHybridTemplate``: derived from the code, not listed: every class with a template.

The sweep in ``test_marked_safe_attribute_sweep_3898.py`` drives the same path across every
class, claim, payload and parameter.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
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


JS = "javascript:alert(1)"
BREAKOUT = 'md" onclick="alert(1)" x="'


@pytest.fixture(autouse=True)
def _bootstrap(settings):
    settings.DJUST_CONFIG = {"css_framework": "bootstrap5"}


class TestTheIssue:
    """The measured cells: ``Avatar`` with ``src`` / ``size`` / ``status`` a lazy marked-safe string."""

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

    @pytest.mark.parametrize(
        "make", [mark_safe, SafeString, _HtmlStr, _HtmlObject, _lazy], ids=lambda f: f.__name__
    )
    def test_every_kind_of_marked_safe_value_is_judged_like_plain_text(self, make) -> None:
        out = _hybrid(
            Avatar, src=make(JS), alt="x", size=make(BREAKOUT), status=make(BREAKOUT)
        ).render()
        assert _attrs(out, "img")["src"] == "#"
        assert "onclick" not in _attrs(out, "div"), out

    def test_a_rustless_class_is_covered_without_any_lazy_value(self) -> None:
        """``Input`` has no crate counterpart: its template draws it for a plain string too."""
        assert Input._rust_impl_class is None
        out = Input(name="n", placeholder=mark_safe(BREAKOUT), value=mark_safe(BREAKOUT)).render()
        assert "onclick" not in _attrs(out, "input"), out

    def test_a_loop_variable_in_an_attribute_is_escaped(self) -> None:
        assert ButtonGroup._rust_impl_class is None
        out = ButtonGroup(buttons=[{"label": "a", "variant": mark_safe(BREAKOUT)}]).render()
        assert "onclick" not in _attrs(out, "button"), out


class TestTheRule:
    def test_a_plain_value_renders_exactly_as_before(self) -> None:
        """No double escaping and no reformatting for what was already right."""
        out = _hybrid(Avatar, src="/a?x=1&y=2", alt="a & b", size="lg", status="online").render()
        assert _attrs(out, "img") == {"src": "/a?x=1&y=2", "alt": "a & b", "class": "avatar-img"}
        assert "avatar-lg" in out and "avatar-online" in out
        assert "&amp;amp;" not in out

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
        out = _hybrid(Avatar, initials=mark_safe("<b>")).render()
        assert '<span class="avatar-initials">' in out
        # initials are cut to two characters by the class itself; the point is the slot is text
        template = hybrid.plan(Avatar.template).template
        assert "{{ initials }}" in template

    def test_numbers_and_none_are_not_reformatted(self) -> None:
        """A value that is not text goes through the synthetic key untouched, so whatever the
        engine did with it (localization included) is what it still does: the render equals
        the same template drawn with no rewrite at all."""
        from djust.components.base import _render_template_with_fallback

        class Box(Component):
            template = '<i data-a="{{ n }}" data-b="{{ missing }}" data-c="{{ flag }}" data-d="{{ d }}"></i>'

            def get_context_data(self):
                from decimal import Decimal

                return {"n": 33.5, "flag": None, "d": Decimal("1.50")}

        box = Box()
        context = {**box.get_context_data(), "component_key": box._component_key}
        expected = _render_template_with_fallback(Box.template, dict(context))
        assert hybrid.plan(Box.template).subs  # the rewrite did apply
        assert str(box.render()) == expected
        assert 'data-b=""' in expected and 'data-c="None"' in expected

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

        class Box(Component):
            template = '<i title="{{ t|escape }}" data-x="{{ t|force_escape }}"></i>'

            def get_context_data(self):
                return {"t": mark_safe('a"b')}

        assert Box().render() == '<i title="a&quot;b" data-x="a&quot;b"></i>'
        assert "|force_escape" in hybrid.plan(Box.template).template
        assert "|escape" not in hybrid.plan(Box.template).template.replace("force_escape", "")

    def test_a_dotted_expression_and_a_filter_are_escaped_too(self) -> None:
        class Box(Component):
            template = '<a data-a="{{ item.k }}" data-b="{{ v|upper }}"></a>'

            def get_context_data(self):
                return {"item": {"k": mark_safe('"x')}, "v": _lazy('"y')}

        out = Box().render()
        assert out == '<a data-a="&quot;x" data-b="&quot;Y"></a>'

    def test_a_srcset_is_judged_candidate_by_candidate(self) -> None:
        """No shipped template has one, but a template you write may: ``srcset`` is a list of
        URLs, so it goes through ``srcset_attr``, not the single-URL check."""

        class Pic(Component):
            template = '<img srcset="{{ s }}" src="/ok.png">'

            def __init__(self, s):
                super().__init__(s=s)
                self.s = s

            def get_context_data(self):
                return {"s": self.s}

        for value in (
            "/a.png 1x, javascript:alert(1) 2x",
            _lazy("/a.png 1x, javascript:alert(1) 2x"),
        ):
            out = Pic(s=value).render()
            assert "javascript:" not in out, out
        good = Pic(s=_lazy("/a.png 1x, /b.png 2x")).render()
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
        ],
    )
    def test_where_the_attribute_slots_are(self, template: str, expected: list) -> None:
        found = [(s.tag, s.attr, s.expr.strip()) for s in hybrid.slots(template)]
        assert found == expected

    def test_a_loop_variable_is_not_substituted_from_the_context(self) -> None:
        plan = hybrid.plan('{% for c in cols %}<i class="{{ c }}" id="{{ top }}">{% endfor %}')
        assert [(s.name, s.kind) for s in plan.subs] == [("top", "text")]
        assert "{{ c|force_escape }}" in plan.template

    def test_a_with_variable_is_not_substituted_from_the_context(self) -> None:
        plan = hybrid.plan('{% with x=top %}<i class="{{ x }}" id="{{ top }}">{% endwith %}')
        assert [s.name for s in plan.subs] == ["top"]

    def test_a_url_expression_that_cannot_be_judged_is_listed(self) -> None:
        plan = hybrid.plan('{% for u in us %}<a href="{{ u.href }}">x</a>{% endfor %}')
        assert plan.unchecked_urls == (("a", "href", "u.href"),)

    def test_a_template_without_an_attribute_slot_is_returned_as_it_is(self) -> None:
        template = "<b>{{ t }}</b>"
        assert hybrid.plan(template).template is template
        context = {"t": 1}
        assert hybrid.harden_context(template, context) == (template, context)

    def test_the_url_attribute_list_covers_the_url_sweeps(self) -> None:
        from scripts.lib.template_library_parity import url_sweep

        assert set(url_sweep.URL_ATTRS) - {"srcset"} <= set(hybrid.URL_ATTRS)


def _component_classes() -> dict[str, type[Component]]:
    found: dict[str, type[Component]] = {}
    for info in pkgutil.walk_packages(components_package.__path__, "djust.components."):
        try:
            module = importlib.import_module(info.name)
        except Exception:  # noqa: BLE001 - an optional dependency a module needs is not installed
            continue
        for _name, cls in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(cls, Component)
                and cls is not Component
                and cls.__module__ == module.__name__
                and getattr(cls, "template", None) is not None
            ):
                found[f"{cls.__module__}.{cls.__name__}"] = cls
    return found


TEMPLATE_CLASSES = _component_classes()


class TestEveryHybridTemplate:
    """Derived: every ``Component`` subclass with a ``template`` that the package defines."""

    def test_the_classes_are_found(self) -> None:
        assert len(TEMPLATE_CLASSES) >= 15, sorted(TEMPLATE_CLASSES)
        assert "djust.components.ui.avatar_simple.Avatar" in TEMPLATE_CLASSES

    @pytest.mark.parametrize("key", sorted(TEMPLATE_CLASSES))
    def test_every_attribute_slot_is_rewritten_and_no_url_is_left_unchecked(self, key: str) -> None:
        template = TEMPLATE_CLASSES[key].template
        plan = hybrid.plan(template)
        assert plan.unchecked_urls == (), plan.unchecked_urls
        for slot in hybrid.slots(plan.template):
            expr = slot.expr.strip()
            assert expr.startswith("djust_attr_") or "force_escape" in expr, (key, slot)

    def test_a_url_attribute_of_a_shipped_template_is_checked(self) -> None:
        kinds = {
            key: {s.kind for s in hybrid.plan(cls.template).subs}
            for key, cls in TEMPLATE_CLASSES.items()
        }
        assert "image" in kinds["djust.components.ui.avatar_simple.Avatar"]
