"""One render path per component tag (S3, decided 2026-11-12).

Every ``djust_components`` tag renders under the Rust engine through the ``{% load %}`` bridge:
the engine runs the Python tag's own node, so the Rust engine and Django's engine give the same
markup by construction. Only the four core layout tags keep a native handler (the root-proof
protocol), pinned here against the Python tags over a spread of inputs.

What each class proves:

* ``TestRegistration``: every tag of the library is registered as a bridge handler of the right
  kind, the four layout tags and nothing else are native, and ``RAW_BODY_TAGS`` is exactly the set
  of block tags whose node reads its children (derived from the tags' source, both directions).
* ``TestLayoutParity``: the native layout handlers render the Python tags' markup for every
  argument value in a spread (hostile, marked safe, missing, ``None``, numbers, flags), and the
  one documented difference (body content outside the child tags is kept) is still the only one.
* ``TestTheParityIssuesAreGone``: each open twin-drift issue's reproduction, through the single
  path, gives Django's output (#3663, #3768, #3849, #3873, #3878, #3882, #3883, #3895).
* ``TestRawBodyNesting``: a raw-body tag nested in itself renders as Django renders it (#3878: the
  raw body used to end at the first end tag).
* ``TestGallery``: every gallery example renders the same under both engines.
"""

from __future__ import annotations

import collections
import inspect
import re
import uuid

import pytest
from django.template import engines
from django.utils.safestring import mark_safe

from djust import _rust
from djust import template_libraries as tl
from djust.components import rust_handlers as rh
from djust.components.templatetags import (
    _advanced,
    _charts,
    _dev_tools,
    _forms,
    djust_components as lib,
)

pytestmark = pytest.mark.components

LAYOUT = {"app_shell", "app_sidebar", "app_header", "app_content"}
_MODULES = (lib, _forms, _advanced, _charts, _dev_tools)


def _django(src: str, ctx: dict) -> str:
    return engines["django"].from_string("{% load djust_components %}" + src).render(dict(ctx))


def _rust_render(src: str, ctx: dict) -> str:
    return str(_rust.render_template(src, dict(ctx)))


@pytest.fixture(autouse=True)
def _registered():
    rh.register_with_rust_engine()


def _uuid_free(html: str) -> str:
    """Tags that draw a random id (``uuid4``) or a process-wide counter differ render to render,
    under either engine."""
    html = re.sub(r"[0-9a-f]{8}(-?[0-9a-f]{4}){0,3}(-?[0-9a-f]{12})?", "<id>", html)
    html = re.sub(r"(?<=[-_])[0-9a-f]{6,12}\b", "<id>", html)
    return re.sub(r"(?<=-tip-)\d+", "<n>", html)


class TestRegistration:
    def test_every_tag_is_bridged_except_the_layout_tags(self):
        native = {name for name, _end, _h in rh.NATIVE_BLOCK_HANDLERS}
        assert native == LAYOUT
        for name, compile_func in lib.register.tags.items():
            kind, handler = rh.bridged_handler(name, compile_func, lib.register)
            if name in LAYOUT:
                assert _rust.has_block_tag_handler(name), name
                continue
            registered = {
                "tag": _rust.has_tag_handler,
                "block": _rust.has_block_tag_handler,
                "raw": _rust.has_raw_block_tag_handler,
            }
            assert registered[kind](name), (name, kind)
            for other, check in registered.items():
                if other != kind:
                    assert not check(name), f"{name} is also registered as a {other} handler"
            assert isinstance(
                handler,
                (tl.LibraryTagHandler, tl.LibraryRawBlockTagHandler),
            ), name
            assert handler.compile_func is compile_func

    def test_a_reregistration_hands_the_engine_the_same_handlers(self):
        first = rh._bridged_handlers(lib.register)
        rh.register_with_rust_engine()
        assert rh._bridged_handlers(lib.register) is first

    def test_raw_body_tags_are_exactly_the_tags_that_read_their_children(self):
        """Derived from the source of each body-consuming tag's node (both directions).

        A node reads its children when it iterates its nodelist (``for n in self.nodelist``),
        renders its body in a context it pushes, reads back a value a child wrote into the context
        while the body rendered, or parses more than one body segment. Rendering the body first
        (the rendered-body route) would lose exactly those children.
        """
        derived = set()
        for name, compile_func in lib.register.tags.items():
            if name in LAYOUT:
                continue
            if not (tl._classify(compile_func) == "raw" and tl._consumes_body(compile_func)):
                continue
            source = inspect.getsource(compile_func)
            if len(re.findall(r"parser\.parse\(", source)) > 1:
                derived.add(name)
                continue
            for node_name in re.findall(r"(\w+Node)\(", source):
                cls = next((getattr(m, node_name) for m in _MODULES if hasattr(m, node_name)), None)
                if cls is None:
                    continue
                node_source = inspect.getsource(cls)
                if (
                    re.search(r"in self\.nodelist\b(?!\.)", node_source)
                    or "context.push" in node_source
                    or re.search(r"context\.get\(_\w+_key", node_source)
                ):
                    derived.add(name)
        assert derived == set(rh.RAW_BODY_TAGS)

    def test_every_other_block_tag_renders_its_body_as_a_wrapper(self):
        """A rendered-body tag (outside ``RAW_BODY_TAGS``) passes the bridge's own wrapper probe
        (ADR-030): one body segment, rendered as it is, in the context it was given. A tag that
        renders its body only when it is open (``modal``, ``sheet``, ``bottom_sheet``, ...) is
        probed open, since the probe otherwise hands every operand ``""``."""
        for name, compile_func in lib.register.tags.items():
            kind, _handler = rh.bridged_handler(name, compile_func, lib.register)
            if kind != "block" or name in LAYOUT:
                continue
            reason = tl._wrapper_refusal(name, compile_func)
            if reason is not None and "does not render its body" in reason:
                # Quoted: the probe's context answers every name (``True`` too) with "".
                reason = tl._wrapper_refusal(name, compile_func, ['open="y"', 'is_open="y"'])
            # A child tag drawn by its parent renders nothing on its own, under either engine.
            if reason is not None and name in _CHILD_TAGS:
                continue
            assert reason is None, (name, reason)


#: Child tags whose own node renders nothing: the parent (a raw-body tag) draws them.
_CHILD_TAGS = {"nav_item", "sidebar_item", "toolbar_overflow"}


# --------------------------------------------------------------------------- #
# The native layout handlers against the Python tags

_EVIL = "<script>x</script>\"'&"
_VALUES = {
    "literal": '"main shell"',
    "hostile": "evil",
    "safe": "safe_attr",
    "missing": "undefined_var",
    "none": "none",
    "number": "num",
    "zero": "zero",
    "true": "True",
    "false": "False",
    "empty": '""',
    "tuple": "pair",
}
_CTX = {
    "evil": _EVIL,
    "safe_attr": mark_safe('ZQ" onmouseover="alert(1)'),
    "none": None,
    "num": 7,
    "zero": 0,
    "pair": (1, 2),
}
_BODY = (
    "{% app_sidebar %}<nav>{{ evil }}</nav>{% endapp_sidebar %}"
    "{% app_header %}<h1>T</h1>{% endapp_header %}"
    "{% app_content %}<p>{{ num }}</p>{% endapp_content %}"
)


class TestLayoutParity:
    @pytest.mark.parametrize("value", sorted(_VALUES))
    @pytest.mark.parametrize("param", ["id", "class", "sidebar_collapsed"])
    def test_app_shell_matches_the_python_tag(self, param, value):
        src = "{% app_shell " + f"{param}={_VALUES[value]}" + " %}" + _BODY + "{% endapp_shell %}"
        assert _rust_render(src, _CTX) == _django(src, _CTX)

    @pytest.mark.parametrize("tag", ["app_sidebar", "app_header", "app_content"])
    @pytest.mark.parametrize("body", ["", "<b>x</b>", "{{ evil }}", "{{ safe_attr }} {{ num }}"])
    def test_the_slots_match_the_python_tags_inside_a_shell(self, tag, body):
        src = "{% app_shell %}{% " + tag + " %}" + body + "{% end" + tag + " %}{% endapp_shell %}"
        assert _rust_render(src, _CTX) == _django(src, _CTX)

    def test_an_attribute_value_is_escaped_whatever_it_is_marked_as(self):
        """#4009 on the kept twin and on the Python tag: a marked-safe ``class``/``id`` cannot
        leave its attribute."""
        for param in ("id", "class"):
            src = "{% app_shell " + param + "=safe_attr %}{% endapp_shell %}"
            for html in (_rust_render(src, _CTX), _django(src, _CTX)):
                assert 'onmouseover="' not in html, html
                assert "&quot; onmouseover=&quot;" in html, html

    def test_the_one_documented_difference_is_body_content_outside_the_slots(self):
        """The native ``app_shell`` keeps body content that is not inside a slot tag, in template
        order (it is what the root-proof protocol copies); the Python tag drops it. Nothing else
        differs: with that content removed the two agree (the parametrized cases above)."""
        src = "{% app_shell %}stray {{ num }}" + _BODY + "{% endapp_shell %}"
        native, python = _rust_render(src, _CTX), _django(src, _CTX)
        assert "stray 7" in native and "stray" not in python
        assert native.replace("stray 7", "") == python


# --------------------------------------------------------------------------- #
# The reproductions of the twin-drift issues, through the single path

_P = collections.namedtuple("P", "x y")


class TestTheParityIssuesAreGone:
    @pytest.mark.parametrize(
        "value",
        [(1, 2), _P(1, 2), uuid.UUID(int=5), b"ab", {3}, frozenset({4})],
        ids=["tuple", "namedtuple", "uuid", "bytes", "set", "frozenset"],
    )
    def test_a_value_reaches_the_tag_as_its_type_3768_3849(self, value):
        for src in ("{% badge label=v %}", "{% dj_tag label=v %}", "{% stat_card value=v %}"):
            assert _rust_render(src, {"v": value}) == _django(src, {"v": value}), src

    @pytest.mark.parametrize("text", ['"x"', "'x'", "false", "007", "1e3", "1_000", "0", "None"])
    def test_a_resolved_string_is_never_read_again_as_a_literal_3883(self, text):
        for src in (
            "{% badge label=v %}",
            "{% dj_button label=v confirm=v %}",
            "{% card title=v %}b{% endcard %}",
        ):
            assert _rust_render(src, {"v": text}) == _django(src, {"v": text}), (src, text)

    def test_a_value_that_names_a_variable_is_not_looked_up_again_3882(self):
        ctx = {"v": "name", "name": "n", "items": [1, 2]}
        for src in (
            "{% dj_button event=v %}",
            "{% nav_menu toggle_event=v %}{% endnav_menu %}",
            "{% dj_button label=w %}",
        ):
            ctx2 = dict(ctx, w="items")
            assert _rust_render(src, ctx2) == _django(src, ctx2), src
        assert 'dj-click="name"' in _rust_render("{% dj_button event=v %}", ctx)

    def test_nav_item_three_levels_deep_is_drawn_as_django_draws_it_3873(self):
        src = (
            '{% nav_menu %}{% nav_item label="A" %}{% nav_item label="B" %}'
            '{% nav_item label="C" event="deep" %}{% endnav_item %}{% endnav_item %}'
            "{% endnav_item %}{% endnav_menu %}"
        )
        html = _rust_render(src, {})
        assert html == _django(src, {})
        assert "deep" not in html  # Django's menu draws one dropdown level

    def test_filter_bar_and_nav_menu_draw_only_their_direct_children_3878(self):
        cases = [
            (
                '{% filter_bar id="f" %}{% filter_search name="a" %}{% if x %}'
                '{% filter_search name="b" %}{% endif %}{% endfilter_bar %}'
            ),
            (
                '{% nav_menu %}{% nav_item event="top" label="T" %}{% if x %}'
                '{% nav_item event="s" label="S" %}{% endnav_item %}{% endif %}'
                "{% endnav_item %}{% endnav_menu %}"
            ),
        ]
        for src in cases:
            assert _rust_render(src, {"x": True}) == _django(src, {"x": True}), src

    def test_data_table_row_url_residuals_are_the_python_tags_3895(self):
        cols = [{"key": "n", "label": "N", "link": "u"}]
        for rows in (
            [{"u": None, "n": 1}, {"n": 2}],
            [{"u": "/a/", "n": 1}],
        ):
            ctx = {"rows": rows, "cols": cols}
            src = '{% data_table rows=rows columns=cols row_url="u" %}'
            assert _rust_render(src, ctx) == _django(src, ctx)

    def test_data_table_renders_the_python_template_only_3869(self):
        """The native handler wired ~13 features the Python template does not render; the single
        path renders the template (whether to add them to it is #3869's open decision)."""
        ctx = {"rows": [{"id": 1, "n": "a"}], "cols": [{"key": "n", "label": "N"}]}
        for flag in ("expandable=True", "exportable=True", "copyable=True", "row_drag=True"):
            src = "{% data_table rows=rows columns=cols " + flag + " %}"
            html = _rust_render(src, ctx)
            assert html == _django(src, ctx)
        assert "on_table_expand" not in _rust_render(
            "{% data_table rows=rows columns=cols expandable=True %}", ctx
        )


class TestRawBodyNesting:
    @pytest.mark.parametrize(
        "src",
        [
            '{% accordion id="o" %}{% accordion_item id="a" title="A" %}{% accordion id="i" %}'
            '{% accordion_item id="b" title="B" %}x{% endaccordion_item %}{% endaccordion %}'
            "{% endaccordion_item %}{% endaccordion %}",
            '{% filter_bar id="o" %}{% filter_bar id="i" %}{% filter_search name="a" %}'
            "{% endfilter_bar %}{% endfilter_bar %}",
            '{% tabs id="o" active="a" %}{% tab id="a" label="A" %}{% tabs id="i" active="b" %}'
            '{% tab id="b" label="B" %}in{% endtab %}{% endtabs %}{% endtab %}{% endtabs %}',
        ],
        ids=["accordion", "filter_bar", "tabs"],
    )
    def test_a_raw_body_tag_nested_in_itself_renders_as_django_does(self, src):
        assert _uuid_free(_rust_render(src, {})) == _uuid_free(_django(src, {}))


# --------------------------------------------------------------------------- #
# The shipped gallery


def _gallery_variants():
    from djust.components.gallery.examples import EXAMPLES

    for tag, example in EXAMPLES.items():
        for variant in example.get("variants", []):
            yield pytest.param(
                variant["template"], dict(variant.get("context", {})), id=f"{tag}-{variant['name']}"
            )


class TestGallery:
    @pytest.mark.parametrize("src, ctx", list(_gallery_variants()))
    def test_every_gallery_example_renders_the_same_under_both_engines(self, src, ctx):
        try:
            expected = _django(src, ctx)
        except Exception as exc:  # noqa: BLE001 - the Rust engine must refuse it too
            with pytest.raises(Exception):
                _rust_render(src, ctx)
            pytest.skip(f"Django refuses this example too: {type(exc).__name__}")
        assert _uuid_free(_rust_render(src, ctx)) == _uuid_free(expected)
