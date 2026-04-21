"""Tests for named slots via {% slot %} / {% render_slot %} (Phase 3)."""

from __future__ import annotations

import pytest

from djust import clear_components, component
from djust.components.function_component import (
    _COMPONENT_REGISTRY,
    CallTagHandler,
    SlotTagHandler,
    _extract_slots,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    saved = dict(_COMPONENT_REGISTRY)
    clear_components()
    yield
    clear_components()
    _COMPONENT_REGISTRY.update(saved)


@pytest.fixture
def ensure_rust_handlers():
    try:
        from djust.components.rust_handlers import register_with_rust_engine
    except ImportError:
        pytest.skip("Rust engine not available")
    register_with_rust_engine()


def render(source: str, context: dict | None = None) -> str:
    from djust._rust import render_template

    return render_template(source, context or {})


# ---------------------------------------------------------------------------
# Direct handler tests
# ---------------------------------------------------------------------------


class TestSlotExtraction:
    def test_single_named_slot(self):
        slot_handler = SlotTagHandler()
        sentinel = slot_handler.render(["header"], "Title", {})

        slots, remainder = _extract_slots(sentinel)
        assert "header" in slots
        assert slots["header"][0]["content"] == "Title"
        assert remainder.strip() == ""

    def test_multiple_same_name_slots(self):
        slot = SlotTagHandler()
        content = (
            slot.render(["col", "label='A'"], "cellA", {})
            + slot.render(["col", "label='B'"], "cellB", {})
            + slot.render(["col", "label='C'"], "cellC", {})
        )

        slots, _ = _extract_slots(content)
        assert len(slots["col"]) == 3
        assert slots["col"][0]["attrs"]["label"] == "A"
        assert slots["col"][2]["attrs"]["label"] == "C"

    def test_slot_no_args_defaults_to_default(self):
        slot = SlotTagHandler()
        sentinel = slot.render([], "body", {})

        slots, _ = _extract_slots(sentinel)
        assert "default" in slots

    def test_default_slot_from_non_slot_content(self):
        slot = SlotTagHandler()
        # Mix header slot with free content.
        content = slot.render(["header"], "Title", {}) + "freeform body"

        slots, remainder = _extract_slots(content)
        assert slots["header"][0]["content"] == "Title"
        assert remainder == "freeform body"

    def test_attrs_resolved_from_context(self):
        slot = SlotTagHandler()
        sentinel = slot.render(
            ["col", "label=page_title"],
            "cell",
            {"page_title": "Dashboard"},
        )
        slots, _ = _extract_slots(sentinel)
        assert slots["col"][0]["attrs"]["label"] == "Dashboard"

    def test_empty_slot_handling(self):
        slot = SlotTagHandler()
        sentinel = slot.render(["header"], "", {})
        slots, remainder = _extract_slots(sentinel)
        assert slots["header"][0]["content"] == ""

    def test_user_comment_not_misinterpreted(self):
        # A user writing "<!--DJUST_SLOT:fake-->" in their template must not
        # be treated as a sentinel. Our format is V1 with JSON — bare text
        # won't parse.
        slots, remainder = _extract_slots("before <!--DJUST_SLOT:fake--> after")
        assert slots == {}
        # The old-style comment passes through verbatim.
        assert "<!--DJUST_SLOT:fake-->" in remainder


# ---------------------------------------------------------------------------
# Integration through CallTagHandler
# ---------------------------------------------------------------------------


class TestSlotsViaCallHandler:
    def test_named_slot_available_to_livecomponent(self):
        from djust.components.base import LiveComponent

        class Card(LiveComponent):
            template = (
                "<div class='card'>"
                "<header>{{ slots.header.0.content|safe }}</header>"
                "<section>{{ children|safe }}</section>"
                "</div>"
            )

            def mount(self, **kwargs):
                pass

            def get_context_data(self):
                return {"slots": self._slots, "children": self._children}

        _COMPONENT_REGISTRY["card"] = Card

        slot = SlotTagHandler()
        body = slot.render(["header"], "Title", {}) + "Body content"

        call_handler = CallTagHandler()
        out = call_handler.render(["'card'"], body, {})
        assert "Title" in out
        assert "Body content" in out

    def test_slots_passed_to_function_component(self):
        captured = {}

        @component
        def listing(assigns):
            captured["slots"] = assigns["slots"]
            captured["children"] = assigns["children"]
            return ""

        slot = SlotTagHandler()
        body = (
            slot.render(["header"], "Header!", {})
            + slot.render(["footer"], "Footer!", {})
            + "main body"
        )

        CallTagHandler().render(["'listing'"], body, {})

        assert captured["slots"]["header"][0]["content"] == "Header!"
        assert captured["slots"]["footer"][0]["content"] == "Footer!"
        assert captured["children"] == "main body"


# ---------------------------------------------------------------------------
# End-to-end via the Rust engine
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_render_slot_resolves_path(self, ensure_rust_handlers):
        # {% render_slot slots.header.0 %} emits the header slot's content.
        @component
        def card(assigns):
            return (
                "<div>"
                + assigns["slots"].get("header", [{"content": ""}])[0]["content"]
                + "|"
                + assigns["children"]
                + "</div>"
            )

        out = render('{% call "card" %}{% slot header %}H{% endslot %}B{% endcall %}')
        assert out == "<div>H|B</div>"
