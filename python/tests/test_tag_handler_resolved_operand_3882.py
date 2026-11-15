"""A ``TagHandler`` operand the engine resolved is never resolved a second time (#3882, #3883).

``TagHandler._resolve_arg`` is the reader of the ``djust.template_tags`` family
(``djust_markdown``, ``dj_flash``, the PWA tags, ``live_render``). The engine resolves every
operand before the handler runs, and ``_resolve_arg`` used to look the resolved VALUE up in the
context again when it was shaped like a dotted name, walking dicts and attributes. A value that
spelled a variable's name (a user's text ``other_var``, ``user.email``) printed that variable:
``{% djust_markdown comment.body %}`` with a comment reading ``other_var`` rendered the value of
``other_var``. It also read a resolved ``"007"`` as the integer ``7`` (#3883).

Every case renders a template through the real engine. Where a Django tag exists (``dj_flash``),
both engines are compared; ``djust_markdown`` is native only, so its reference is the value the
template names.
"""

from __future__ import annotations

import pytest
from django.template import engines

from djust import LiveView
from djust._rust import RustLiveView
from djust.testing import LiveViewTestClient

PLANTED = "PLANTED-3882"


def _rust(src: str, ctx: dict) -> str:
    from djust.components.rust_handlers import register_with_rust_engine

    register_with_rust_engine()
    rv = RustLiveView("<div dj-root>" + src + "</div>")
    rv.update_state(ctx)
    out = rv.render()
    return out[len("<div dj-root>") : -len("</div>")]


def _django(src: str, ctx: dict) -> str:
    return engines["django"].from_string(src).render(ctx)


class _Holder:
    """An object whose attribute a resolved value could name (``holder.token``)."""

    token = PLANTED


CTX = {
    "other_var": PLANTED,
    "user": {"note": PLANTED},
    "holder": _Holder(),
}


@pytest.mark.parametrize("value", ["other_var", "user.note", "holder.token"])
def test_markdown_source_naming_a_variable_is_the_text_itself(value: str) -> None:
    html = _rust("{% djust_markdown src %}", dict(CTX, src=value))
    assert PLANTED not in html, html
    assert "<p>%s</p>" % value in html, html


def test_markdown_source_with_leading_zeros_keeps_them() -> None:
    """#3883 on this path: a resolved ``"007"`` is the text, not the integer 7."""
    html = _rust("{% djust_markdown src %}", {"src": "007"})
    assert "<p>007</p>" in html, html


@pytest.mark.parametrize("value", ["other_var", "user.note", "holder.token"])
def test_flash_position_naming_a_variable_is_the_value_on_both_engines(value: str) -> None:
    """Both engines build the class from the VALUE. (The native handler also drops characters
    outside ``[A-Za-z0-9-]`` where Django's tag keeps them; that difference predates this.)"""
    ctx = dict(CTX, pos=value)
    rs = _rust("{% dj_flash position=pos %}", ctx)
    dj = _django("{% load djust_flash %}{% dj_flash position=pos %}", ctx)
    for html in (rs, dj):
        assert PLANTED.replace("-", "") not in html.replace("-", ""), html
    assert "dj-flash-" + value in dj, dj
    assert "dj-flash-" + "".join(c for c in value if c.isalnum() or c == "-") in rs, rs


@pytest.mark.parametrize("value", ["other_var", "user.note"])
def test_pwa_head_name_naming_a_variable_is_the_text_itself(value: str) -> None:
    html = _rust("{% djust_pwa_head name=n %}", dict(CTX, n=value))
    assert PLANTED not in html, html
    assert 'content="%s"' % value in html, html


def test_a_literal_integer_is_still_an_integer() -> None:
    """Non-regression: the canonical spelling of an integer is still read as one."""
    rs = _rust("{% dj_flash auto_dismiss=1200 %}", {})
    assert 'data-dj-auto-dismiss="1200"' in rs, rs


@pytest.mark.django_db
def test_an_attribute_the_template_names_still_renders() -> None:
    """Non-regression: ``post.body`` is resolved by the ENGINE, not by the handler, so an
    object's attribute still reaches ``djust_markdown`` on the real LiveView path."""

    class Post:
        body = "**hi** from obj"

    class _V(LiveView):
        exposure_policy = "explicit"
        template = "<div>{% djust_markdown post.body %}</div>"

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx["post"] = Post()
            return ctx

    client = LiveViewTestClient(_V)
    client.mount()
    html, _, _ = client.render_with_patches()
    assert ">hi</strong> from obj" in html, html


@pytest.mark.django_db
def test_a_model_attribute_and_method_still_render() -> None:
    """Non-regression for #2037's premise ("a top-level model object Rust cannot resolve arrives
    as its token and Python resolves it"): the engine resolves a model's field and its method,
    so dropping the handler's own lookup loses neither."""
    from django.contrib.auth.models import User

    user = User.objects.create(username="u3882", first_name="Bobby", last_name="T")

    class _V(LiveView):
        exposure_policy = "explicit"
        template = (
            "<div>{% djust_markdown u.first_name %}|{% djust_markdown u.get_full_name %}"
            "|{% dj_flash position=u.first_name %}</div>"
        )

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx["u"] = user
            return ctx

    client = LiveViewTestClient(_V)
    client.mount()
    html, _, _ = client.render_with_patches()
    assert ">Bobby</p>" in html and ">Bobby T</p>" in html and "dj-flash-Bobby" in html, html
