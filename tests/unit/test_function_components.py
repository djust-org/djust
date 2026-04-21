"""Tests for function components + {% call %} tag (Phase 2 & 4)."""

from __future__ import annotations

import pytest

from djust import Assign, LiveComponent, clear_components, component
from djust.components.function_component import (
    CallTagHandler,
    _COMPONENT_REGISTRY,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Keep each test's component registry isolated."""

    saved = dict(_COMPONENT_REGISTRY)
    clear_components()
    yield
    clear_components()
    _COMPONENT_REGISTRY.update(saved)


@pytest.fixture
def ensure_rust_handlers():
    """Ensure {% call %} / {% component %} / {% slot %} / {% render_slot %} are registered."""

    try:
        from djust.components.rust_handlers import register_with_rust_engine
    except ImportError:
        pytest.skip("Rust engine not available")
    register_with_rust_engine()


def render(source: str, context: dict | None = None) -> str:
    """Render ``source`` via the Rust template engine."""

    from djust._rust import render_template

    return render_template(source, context or {})


# ---------------------------------------------------------------------------
# Decorator & registry
# ---------------------------------------------------------------------------


class TestComponentDecorator:
    def test_bare_decorator_registers_function(self):
        @component
        def my_button(assigns):
            return "<button>x</button>"

        assert _COMPONENT_REGISTRY["my_button"] is my_button

    def test_decorator_with_name_override(self):
        @component(name="fancy_button")
        def btn(assigns):
            return "<button class='fancy'>x</button>"

        assert "fancy_button" in _COMPONENT_REGISTRY
        assert "btn" not in _COMPONENT_REGISTRY

    def test_clear_components(self):
        @component
        def temp(assigns):
            return "x"

        assert "temp" in _COMPONENT_REGISTRY
        clear_components()
        assert "temp" not in _COMPONENT_REGISTRY

    def test_decorator_attaches_assigns(self):
        @component(assigns=[Assign("variant", type=str, default="default")])
        def styled(assigns):
            return f"<i>{assigns['variant']}</i>"

        assert len(styled._djust_assigns) == 1
        assert styled._djust_assigns[0].name == "variant"


# ---------------------------------------------------------------------------
# Direct handler rendering (no Rust)
# ---------------------------------------------------------------------------


class TestCallTagHandlerDirect:
    def test_simple_render(self):
        @component
        def button(assigns):
            return f"<button>{assigns['children']}</button>"

        handler = CallTagHandler()
        out = handler.render(["'button'"], "Click me", {})
        assert out == "<button>Click me</button>"

    def test_kwargs_passed_through(self):
        @component
        def badge(assigns):
            return f"<span class='{assigns.get('variant', 'x')}'>{assigns['children']}</span>"

        handler = CallTagHandler()
        out = handler.render(["'badge'", "variant='warning'"], "Hi", {})
        assert out == "<span class='warning'>Hi</span>"

    def test_children_and_inner_block_both_present(self):
        captured = {}

        @component
        def probe(assigns):
            captured["children"] = assigns.get("children")
            captured["inner_block"] = assigns.get("inner_block")
            return ""

        handler = CallTagHandler()
        handler.render(["'probe'"], "body", {})
        assert captured["children"] == "body"
        assert captured["inner_block"] == "body"

    def test_body_wins_over_children_kwarg(self):
        """Phoenix convention: the block body is authoritative. A caller
        passing ``children="..."`` or ``inner_block="..."`` via tag args
        must not shadow the actual body content."""
        captured = {}

        @component
        def probe(assigns):
            captured["children"] = assigns.get("children")
            captured["inner_block"] = assigns.get("inner_block")
            return ""

        handler = CallTagHandler()
        handler.render(
            ["'probe'", 'children="attempted-override"', 'inner_block="also-attempted"'],
            "actual-body-content",
            {},
        )
        assert captured["children"] == "actual-body-content"
        assert captured["inner_block"] == "actual-body-content"

    def test_non_livecomponent_class_raises_clear_error(self):
        """A class target that isn't a LiveComponent subclass must produce
        a clear error rather than silently instantiating and failing on
        .render()."""

        class NotAComponent:
            def __init__(self, **kwargs):
                pass

        from djust.components.function_component import _COMPONENT_REGISTRY

        _COMPONENT_REGISTRY["bad"] = NotAComponent
        handler = CallTagHandler()
        with pytest.raises(RuntimeError, match="not a LiveComponent subclass"):
            handler.render(["'bad'"], "", {})

    def test_unregistered_component_raises(self):
        handler = CallTagHandler()
        with pytest.raises(RuntimeError, match="not registered"):
            handler.render(["'nope'"], "", {})

    def test_required_assign_missing_raises(self):
        @component(assigns=[Assign("label", type=str, required=True)])
        def btn(assigns):
            return f"<button>{assigns['label']}</button>"

        handler = CallTagHandler()
        with pytest.raises(RuntimeError, match="validation failed"):
            handler.render(["'btn'"], "", {})

    def test_assign_type_coercion(self):
        captured = {}

        @component(assigns=[Assign("count", type=int, default=0)])
        def counter(assigns):
            captured["count"] = assigns["count"]
            return ""

        handler = CallTagHandler()
        handler.render(["'counter'", "count='7'"], "", {})
        assert captured["count"] == 7
        assert isinstance(captured["count"], int)

    def test_livecomponent_dispatch(self):
        class MyComp(LiveComponent):
            template = "<div>{{ label }}</div>"

            def mount(self, label=""):
                self.label = label

            def get_context_data(self):
                return {"label": self.label}

        _COMPONENT_REGISTRY["mycomp"] = MyComp

        handler = CallTagHandler()
        out = handler.render(["'mycomp'", "label='Hello'"], "", {})
        assert "Hello" in out


# ---------------------------------------------------------------------------
# End-to-end via the Rust renderer
# ---------------------------------------------------------------------------


class TestRustEngineIntegration:
    def test_call_via_rust(self, ensure_rust_handlers):
        @component
        def button(assigns):
            variant = assigns.get("variant", "default")
            return f'<button class="btn-{variant}">{assigns["children"]}</button>'

        html = render('{% call "button" variant="primary" %}Go{% endcall %}')
        assert '<button class="btn-primary">Go</button>' == html

    def test_component_alias(self, ensure_rust_handlers):
        @component
        def alert_box(assigns):
            return f"<div class='alert'>{assigns['children']}</div>"

        html = render('{% component "alert_box" %}Danger!{% endcomponent %}')
        assert "<div class='alert'>Danger!</div>" == html

    def test_function_component_with_assigns_validation(self, ensure_rust_handlers):
        @component(
            assigns=[
                Assign(
                    "variant",
                    type=str,
                    required=True,
                    values=["primary", "danger"],
                )
            ]
        )
        def tag(assigns):
            return f"<tag v={assigns['variant']}>{assigns['children']}</tag>"

        html = render('{% call "tag" variant="danger" %}x{% endcall %}')
        assert "<tag v=danger>x</tag>" == html

    def test_nested_call(self, ensure_rust_handlers):
        @component
        def outer(assigns):
            return f"<outer>{assigns['children']}</outer>"

        @component
        def inner(assigns):
            return f"<inner>{assigns['children']}</inner>"

        html = render('{% call "outer" %}{% call "inner" %}x{% endcall %}{% endcall %}')
        assert html == "<outer><inner>x</inner></outer>"

    def test_livecomponent_via_call(self, ensure_rust_handlers):
        class Greeting(LiveComponent):
            template = "<span>Hi {{ name }}</span>"

            def mount(self, name=""):
                self.name = name

            def get_context_data(self):
                return {"name": self.name}

        _COMPONENT_REGISTRY["greeting"] = Greeting

        html = render('{% call "greeting" name="World" %}{% endcall %}')
        assert "Hi World" in html
