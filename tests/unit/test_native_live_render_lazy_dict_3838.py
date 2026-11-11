"""#3838: a dict ``lazy=`` on ``{% live_render %}`` reaches the native handler as itself.

``{% live_render "app.views.Child" lazy=cfg %}`` with ``cfg = {"placeholder": ..., "trigger": ...}``
renders under the Django engine. Under the native (Rust) engine the handler was handed the dict as
the string ``lazy={"placeholder":"..."}`` (the engine JSON-encodes a structured operand) and
``live_render`` refused it::

    TemplateSyntaxError: {% live_render %} lazy= accepts True, 'visible', or a dict - got '{"placeholder":...}'

so the dict form (``trigger`` / ``on_error`` / ``timeout_s`` / ``placeholder``) could not be used from
a root LiveView template at all.

The fix is the typed-operand channel ``json_viewer`` uses (#3756): ``LiveRenderTagHandler`` declares
``TYPED_ARGS`` and reads ``lazy`` from ``context["__djust_typed_args__"]``, the value the engine
resolved through the floored walk ``{{ }}`` uses. Not the JSON string decoded back: a string cannot
carry ``SafeString``-ness, and #3819 made the placeholder's escaping depend on it (a marked-safe
placeholder is markup, a plain ``str`` is text). The typed value keeps the marker, so the two engines
print the same bytes.

Engine path (CLAUDE.md "engine-path declaration"): BOTH. The Django-engine tag
(``djust.templatetags.live_tags.live_render``) and the native handler
(``djust.template_tags.live_render``) are exercised with one source and one context, and a view
rendered through ``render_full_template`` (the native engine, the path a root LiveView takes).
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template, TemplateSyntaxError
from django.test import override_settings
from django.utils.safestring import mark_safe

from djust import LiveView

pytestmark = [pytest.mark.django_db(transaction=False)]

CHILD = "tests.unit.test_native_live_render_lazy_dict_3838._Child"
_ALLOWED = ["tests.unit.test_native_live_render_lazy_dict_3838"]
SRC = "{% live_render '" + CHILD + "' lazy=cfg %}"


class _Child(LiveView):
    template = "<div>child</div>"

    def mount(self, request, **kwargs):
        pass


class _Parent(LiveView):
    template = "<div dj-root></div>"

    def mount(self, request, **kwargs):
        pass


def _parent(rf):
    request = rf.get("/")
    request.user = AnonymousUser()
    parent = _Parent()
    parent.request = request
    parent.mount(request)
    return parent


def _norm(html: str) -> str:
    """The per-parent ``child_N`` counter is the only thing two parents legitimately differ in."""
    return re.sub(r"child_\d+", "child_X", html)


def _django(src: str, ctx: dict, rf) -> str:
    parent = _parent(rf)
    return _norm(
        Template("{% load live_tags %}" + src).render(
            Context({"view": parent, "request": parent.request, **ctx})
        )
    )


def _native(src: str, ctx: dict, rf) -> str:
    from djust._rust import RustLiveView

    parent = _parent(rf)
    rv = RustLiveView(src)
    rv.update_state(ctx)
    rv.set_raw_py_values({"view": parent, "request": parent.request})
    return _norm(rv.render())


def _both(src: str, ctx: dict, rf) -> tuple[str, str]:
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
        return _django(src, ctx, rf), _native(src, ctx, rf)


class TestDictLazyReachesTheNativeHandler:
    @pytest.mark.parametrize(
        "cfg",
        [
            {"placeholder": mark_safe("<div>Loading...</div>")},
            {"placeholder": mark_safe("<i>wait</i>"), "trigger": "visible"},
            {"trigger": "visible"},
            {"trigger": "visible", "timeout_s": 5},
            {"placeholder": "plain <b>text</b>"},
            {},
        ],
        ids=["safe", "safe+trigger", "trigger only", "timeout", "plain str", "empty"],
    )
    def test_the_dict_renders_the_same_bytes_on_both_engines(self, rf, cfg):
        django, native = _both(SRC, {"cfg": cfg}, rf)
        # An empty dict is falsy: Django's tag renders the child eagerly, and so does the native one.
        assert ("<dj-lazy-slot" in django) == bool(cfg), django
        assert native == django

    def test_the_reported_reproduction(self, rf):
        """The issue's snippet: it raised ``TemplateSyntaxError ... got '{"placeholder":...}'``."""
        from djust._rust import RustLiveView

        parent = _parent(rf)
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
            rv = RustLiveView(SRC)
            rv.update_state({"cfg": {"placeholder": "x"}})
            rv.set_raw_py_values({"view": parent, "request": parent.request})
            out = rv.render()
        assert "<dj-lazy-slot" in out
        assert ">x</dj-lazy-slot>" in out

    def test_a_mark_safe_placeholder_is_markup_and_a_plain_str_is_text(self, rf):
        """The #3819 contract holds on the native engine: the marker crosses with the value."""
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
            safe = _native(SRC, {"cfg": {"placeholder": mark_safe("<b>m</b>")}}, rf)
            plain = _native(SRC, {"cfg": {"placeholder": "<b>t</b>"}}, rf)
        assert "<b>m</b>" in safe
        assert "<b>t</b>" not in plain
        assert "&lt;b&gt;t&lt;/b&gt;" in plain

    def test_a_hostile_plain_placeholder_cannot_inject_markup(self, rf):
        hostile = "<img src=x onerror=alert(1)>"
        _, native = _both(SRC, {"cfg": {"placeholder": hostile}}, rf)
        assert hostile not in native
        assert "&lt;img" in native

    def test_a_view_attribute_dict_renders_through_the_native_view_path(self, rf):
        """The path a root LiveView takes: a ``mount()``-set dict, ``render_full_template`` (the
        native engine), the safe marker surviving the state round trip."""

        placeholders = {"safe": mark_safe("<i>wait</i>"), "plain": "<i>wait</i>"}

        class _CfgParent(LiveView):
            template = "<div dj-root>" + SRC + "</div>"
            kind = "safe"

            def mount(self, request, **kwargs):
                self.cfg = {"placeholder": placeholders[self.kind], "trigger": "visible"}

        def rendered(kind: str) -> str:
            request = rf.get("/")
            request.user = AnonymousUser()
            view = _CfgParent()
            view.kind = kind
            view.request = request
            view.mount(request)
            with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
                return view.render_full_template(request)

        assert (
            '<dj-lazy-slot data-id="child_1" data-trigger="visible"><i>wait</i></dj-lazy-slot>'
            in (rendered("safe"))
        )
        assert "&lt;i&gt;wait&lt;/i&gt;</dj-lazy-slot>" in rendered("plain")


class TestTheOtherLazyForms:
    """What ``lazy=`` accepted before still behaves, and still as Django behaves."""

    @pytest.mark.parametrize(
        "src",
        [
            "{% live_render '" + CHILD + "' lazy=True %}",
            "{% live_render '" + CHILD + "' lazy='visible' %}",
            "{% live_render '" + CHILD + "' lazy=flag %}",
            "{% live_render '" + CHILD + "' lazy=trig %}",
            "{% live_render '" + CHILD + "' %}",
        ],
        ids=["True", "visible", "bool var", "str var", "eager"],
    )
    def test_the_scalar_forms_are_unchanged(self, rf, src):
        django, native = _both(src, {"flag": True, "trig": "visible"}, rf)
        assert native == django

    def test_a_string_variable_holding_a_quoted_word_is_refused_as_django_refuses_it(self, rf):
        """``lazy=trig`` with ``trig = "'visible'"`` (quotes inside the string) is a ``str`` that is
        not ``"visible"`` to Django's tag, which rejects it. The native handler used to strip the
        quotes and accept it; the typed value is the string the context holds."""
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
            with pytest.raises(TemplateSyntaxError) as django_exc:
                _django("{% live_render '" + CHILD + "' lazy=trig %}", {"trig": "'visible'"}, rf)
            with pytest.raises(Exception) as native_exc:  # noqa: PT011 - the bridge wraps it
                _native("{% live_render '" + CHILD + "' lazy=trig %}", {"trig": "'visible'"}, rf)
        assert "accepts True, 'visible', or a dict" in str(django_exc.value)
        assert "accepts True, 'visible', or a dict" in str(native_exc.value)

    def test_an_unknown_key_in_the_dict_is_refused_on_both_engines(self, rf):
        cfg = {"placeholder": "x", "nonsense": 1}
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
            with pytest.raises(TemplateSyntaxError) as django_exc:
                _django(SRC, {"cfg": cfg}, rf)
            with pytest.raises(Exception) as native_exc:  # noqa: PT011 - the bridge wraps it
                _native(SRC, {"cfg": cfg}, rf)
        assert "nonsense" in str(django_exc.value)
        assert "nonsense" in str(native_exc.value)

    def test_the_declaration_is_what_delivers_the_dict(self):
        """The handler declares ``TYPED_ARGS`` (the engine hands a typed operand only to a handler
        that does), and does not read the string it used to."""
        from djust.template_tags import TYPED_ARGS_KEY, get_registered_handlers
        from djust.template_tags.live_render import LiveRenderTagHandler

        assert LiveRenderTagHandler.TYPED_ARGS is True
        assert get_registered_handlers()["live_render"].TYPED_ARGS is True
        assert TYPED_ARGS_KEY == "__djust_typed_args__"
