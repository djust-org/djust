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

    def test_user_comment_without_v1_prefix_not_misinterpreted(self):
        # A user writing "<!--DJUST_SLOT:fake-->" (without _V1) in their
        # template must not be treated as a sentinel. The regex requires
        # the _V1 prefix.
        slots, remainder = _extract_slots("before <!--DJUST_SLOT:fake--> after")
        assert slots == {}
        # The non-matching comment passes through verbatim.
        assert "<!--DJUST_SLOT:fake-->" in remainder

    def test_malformed_v1_sentinel_falls_through(self):
        # A sentinel that matches our V1 prefix but has an invalid JSON
        # payload (e.g. user authored the comment by mistake, or it was
        # truncated in transit) must fall through verbatim rather than
        # crashing the renderer.
        malformed = "before <!--DJUST_SLOT_V1:not-json-payload--> after"
        slots, remainder = _extract_slots(malformed)
        assert slots == {}
        # The malformed sentinel passes through literally (defensive
        # branch at function_component.py::_extract_slots).
        assert "<!--DJUST_SLOT_V1:not-json-payload-->" in remainder


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

    def test_render_slot_end_to_end_via_rust_engine(self, ensure_rust_handlers):
        """Issue #861 regression: `{% render_slot %}` via the Rust template
        engine used to return empty string for every input because the Rust
        engine pre-resolves args (so the handler saw a JSON-encoded dict
        rather than the literal path it expected). Handler now dual-dispatches
        based on arg shape.
        """
        from djust._rust import render_template

        ctx = {
            "slots": {
                "col": [
                    {"name": "col", "attrs": {}, "content": "first-col-content"},
                    {"name": "col", "attrs": {}, "content": "second-col-content"},
                ],
            },
        }
        # slots.col.0 → first entry's content (Rust resolves to dict, JSON-encoded)
        assert render_template("{% render_slot slots.col.0 %}", ctx) == "first-col-content"
        # slots.col.1 → second entry's content
        assert render_template("{% render_slot slots.col.1 %}", ctx) == "second-col-content"
        # slots.col.0.content → the string directly (Rust resolves to string scalar)
        assert render_template("{% render_slot slots.col.0.content %}", ctx) == "first-col-content"
        # Missing path → empty (Rust passes the unresolved path through; handler resolves against
        # context, finds nothing, returns empty).
        assert render_template("{% render_slot slots.missing.0 %}", ctx) == ""

    def test_render_slot_dotted_path_resolves_slot_entry(self):
        """Issue #790: `RenderSlotTagHandler` resolves `slots.col.0` to the first col slot.

        Tests the dotted-path-with-list-index semantics at the handler level.
        The full Rust-engine end-to-end path (``{% render_slot slots.col.0 %}``
        inside a ``render_template`` call) has a separate registration gap
        tracked under a new tech-debt issue — the handler's own logic is
        exercised directly here.
        """
        from djust.components.function_component import RenderSlotTagHandler

        ctx = {
            "slots": {
                "col": [
                    {"name": "col", "attrs": {}, "content": "first-col-content"},
                    {"name": "col", "attrs": {}, "content": "second-col-content"},
                ],
            }
        }
        handler = RenderSlotTagHandler()
        # slots.col.0 → first entry's .content
        assert handler.render(["slots.col.0"], ctx) == "first-col-content"
        # slots.col.1 → second entry
        assert handler.render(["slots.col.1"], ctx) == "second-col-content"
        # slots.col.0.content → same as slots.col.0 (content fallback)
        assert handler.render(["slots.col.0.content"], ctx) == "first-col-content"
        # slots.missing.0 → empty (None value short-circuits)
        assert handler.render(["slots.missing.0"], ctx) == ""
        # slots.col.9 → empty (out-of-bounds index)
        assert handler.render(["slots.col.9"], ctx) == ""

    def test_slot_inside_for_loop_preserves_row_context(self, ensure_rust_handlers):
        """Issue #789 (Risk 1 from PR #788's plan): slot sentinels emitted per loop iteration.

        When `{% slot col %}` appears inside `{% for row in rows %}`, the sentinel
        scanner at ``CallTagHandler.render`` sees the sentinels flat in the
        body and groups them into `slots['col'] = [...]`. The iteration's loop
        variable (``row.name``) must resolve to the per-iteration value on each
        sentinel, not the last row's value.
        """

        captured = []

        @component
        def rowlist(assigns):
            for s in assigns["slots"].get("col", []):
                captured.append(s["content"])
            return "<ol></ol>"

        render(
            '{% call "rowlist" %}'
            "{% for row in rows %}"
            "{% slot col %}row-{{ row.name }}{% endslot %}"
            "{% endfor %}"
            "{% endcall %}",
            {"rows": [{"name": "A"}, {"name": "B"}, {"name": "C"}]},
        )

        # Exactly three col-slot sentinels, each carrying its own row's name.
        assert captured == ["row-A", "row-B", "row-C"]
