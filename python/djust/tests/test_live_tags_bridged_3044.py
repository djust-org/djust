"""``{% live_input %}``, ``{% djust_skeleton %}`` and ``{% djust_track_static %}``
in a root LiveView template (#3044).

The same class as #2958: the three tags had no Rust handler, so a root
LiveView template using them failed with "Invalid block tag … Did you forget
to register or load this tag?" even after ``{% load live_tags %}``. They now
bridge through Django's own nodes (``_DJUST_TAGS_BRIDGED``), and the output is
compared with the real Django engine's.

``djust_skeleton`` dedupes its shimmer ``<style>`` block through
``context.render_context``. A bridged node renders on a fresh Django
``Context`` per call, so without a shared per-render ``render_context`` the
block was emitted once per call instead of once per render.
"""

from __future__ import annotations

import pytest
from django.template import engines

from djust import LiveView

SRC_PREFIX = "{% load live_tags %}<div dj-root>"


def _rust(body: str, context: dict) -> str:
    """A bare Rust render inside the per-render scope every LiveView,
    component and djust-backend render entry opens (``library_render_scope``)."""
    from djust._rust import RustLiveView
    from djust.template_libraries import library_render_scope

    rv = RustLiveView(SRC_PREFIX + body + "</div>")
    rv.update_state(context)
    with library_render_scope():
        return rv.render()


def _django(body: str, context: dict) -> str:
    return engines["django"].from_string(SRC_PREFIX + body + "</div>").render(context)


CASES = [
    '{% live_input "text" handler="search" value=q %}',
    '{% live_input "text" name="q" handler="search" debounce="300" %}',
    '{% live_input "textarea" handler="save" value=q %}',
    '{% live_input "select" handler="pick" value="b" choices=choices %}',
    '{% live_input "checkbox" handler="toggle" checked=True %}',
    '{% live_input "hidden" name="token" value=q %}',
    '{% live_input "bogus" %}',
    "{% djust_skeleton %}",
    '{% djust_skeleton shape="circle" width="48px" height="48px" %}',
    '{% djust_skeleton count=3 %}{% djust_skeleton shape="rect" %}',
    '<script {% djust_track_static %} src="/s/app.js"></script>',
]
CTX = {"q": '<b>"hi"</b>', "choices": [("a", "A"), ("b", "B <i>")]}


@pytest.mark.parametrize("body", CASES)
def test_bridged_tags_render_like_django(body):
    assert _rust(body, CTX) == _django(body, CTX)


def test_skeleton_style_block_is_emitted_once_per_render():
    body = '{% djust_skeleton %}<p>x</p>{% djust_skeleton shape="circle" %}'
    out = _rust(body, {})
    assert out.count("<style") == 1, out
    assert out == _django(body, {})
    # A second render emits it again: the dedupe is per render, not global.
    from djust._rust import RustLiveView
    from djust.template_libraries import library_render_scope

    rv = RustLiveView(SRC_PREFIX + body + "</div>")
    rv.update_state({})
    for _ in range(2):
        with library_render_scope():
            assert rv.render().count("<style") == 1


def test_without_a_scope_each_call_is_its_own_render():
    """Outside any render entry the bridge keeps its per-call ``Context``
    (the pre-#3044 behaviour): nothing is shared between unrelated calls."""
    from djust._rust import RustLiveView

    rv = RustLiveView(SRC_PREFIX + "{% djust_skeleton %}{% djust_skeleton %}</div>")
    rv.update_state({})
    assert rv.render().count("<style") == 2


@pytest.mark.django_db
def test_every_ws_render_emits_the_style_block():
    """``render_with_diff`` (the WS path) opens a fresh scope per render."""

    class V(LiveView):
        template = (
            "{% load live_tags %}<div dj-root>{% djust_skeleton %}{{ n }}{% djust_skeleton %}</div>"
        )

        def mount(self, request, **kwargs):
            self.n = 0

    v = V()
    v.mount(None)
    for i in range(3):
        v.n = i
        html, _patches, _version = v.render_with_diff(None)
        assert html.count("<style") == 1, i


def test_the_three_tags_are_bridged():
    from djust import template_libraries as tl

    allowed = tl._DJUST_TAGS_BRIDGED["djust.templatetags.live_tags"]
    assert {"live_input", "djust_skeleton", "djust_track_static"} <= allowed


@pytest.mark.django_db
def test_root_liveview_template_renders_them():
    """The issue's reproduction, through a real LiveView render (GET shell and
    the WS ``render_with_diff`` path)."""

    class V(LiveView):
        template = (
            "{% load live_tags %}<div dj-root>"
            '{% live_input "text" handler="search" value=q %}'
            "{% djust_skeleton %}{% djust_skeleton %}"
            '<link {% djust_track_static %} rel="stylesheet" href="/a.css">'
            "</div>"
        )

        def mount(self, request, **kwargs):
            self.q = "hello"

    v = V()
    v.mount(None)
    html, _patches, _version = v.render_with_diff(None)
    assert 'dj-input="search"' in html and 'value="hello"' in html
    assert html.count("<style") == 1
    assert "dj-track-static" in html
