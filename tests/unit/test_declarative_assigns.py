"""Tests for declarative component assigns & slots (Phase 1)."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from djust import Assign, AssignValidationError, LiveComponent, Slot
from djust.components.assigns import (
    merge_assign_declarations,
    merge_slot_declarations,
    validate_assigns,
    validate_slots,
)


# ---------------------------------------------------------------------------
# validate_assigns — the core helper
# ---------------------------------------------------------------------------


class TestValidateAssigns:
    def test_required_missing_raises(self):
        decl = [Assign("name", type=str, required=True)]
        with pytest.raises(AssignValidationError, match="Required assign 'name'"):
            validate_assigns(decl, {})

    def test_default_applied_when_missing(self):
        decl = [Assign("variant", type=str, default="default")]
        result = validate_assigns(decl, {})
        assert result == {"variant": "default"}

    def test_str_to_int_coercion(self):
        decl = [Assign("count", type=int, default=0)]
        result = validate_assigns(decl, {"count": "42"})
        assert result == {"count": 42}
        assert isinstance(result["count"], int)

    def test_str_to_bool_coercion_truthy(self):
        decl = [Assign("active", type=bool, default=False)]
        for value in ("true", "yes", "1", "on", "TRUE"):
            result = validate_assigns(decl, {"active": value})
            assert result["active"] is True, value

    def test_str_to_bool_coercion_falsy(self):
        decl = [Assign("active", type=bool, default=True)]
        for value in ("false", "no", "0", "off"):
            result = validate_assigns(decl, {"active": value})
            assert result["active"] is False, value

    def test_str_to_float_coercion(self):
        decl = [Assign("ratio", type=float, default=0.0)]
        result = validate_assigns(decl, {"ratio": "0.25"})
        assert result["ratio"] == 0.25

    def test_enum_violation_raises(self):
        decl = [Assign("variant", type=str, values=["primary", "danger"], default="primary")]
        with pytest.raises(AssignValidationError, match="not in allowed set"):
            validate_assigns(decl, {"variant": "warning"})

    def test_enum_valid_value_passes(self):
        decl = [Assign("variant", type=str, values=["primary", "danger"], default="primary")]
        result = validate_assigns(decl, {"variant": "danger"})
        assert result["variant"] == "danger"

    def test_wrong_type_non_coercible_raises(self):
        decl = [Assign("count", type=int, default=0)]
        with pytest.raises(AssignValidationError, match="Cannot coerce"):
            validate_assigns(decl, {"count": "not-a-number"})

    def test_unknown_kwargs_preserved(self):
        decl = [Assign("name", type=str, required=True)]
        result = validate_assigns(decl, {"name": "x", "extra": "passthrough"})
        assert result == {"name": "x", "extra": "passthrough"}

    def test_matching_type_passthrough(self):
        decl = [Assign("count", type=int, default=0)]
        result = validate_assigns(decl, {"count": 7})
        assert result == {"count": 7}


# ---------------------------------------------------------------------------
# validate_slots
# ---------------------------------------------------------------------------


class TestValidateSlots:
    def test_required_slot_missing_raises(self):
        decl = [Slot("inner_block", required=True)]
        with pytest.raises(AssignValidationError, match="Required slot 'inner_block'"):
            validate_slots(decl, {})

    def test_multiple_true_allows_list(self):
        decl = [Slot("col", multiple=True)]
        provided = {"col": [{"content": "a"}, {"content": "b"}]}
        result = validate_slots(decl, provided)
        assert result is provided

    def test_multiple_false_rejects_multiple_entries(self):
        decl = [Slot("header")]
        with pytest.raises(AssignValidationError, match="multiple=False"):
            validate_slots(decl, {"header": [{"content": "a"}, {"content": "b"}]})

    def test_optional_slot_missing_is_ok(self):
        decl = [Slot("footer", required=False)]
        result = validate_slots(decl, {})
        assert result == {}


# ---------------------------------------------------------------------------
# Inheritance merging
# ---------------------------------------------------------------------------


class TestInheritanceMerging:
    def test_child_extends_parent_assigns(self):
        class Parent(LiveComponent):
            assigns = [Assign("a", type=str, default="pa")]

        class Child(Parent):
            assigns = [Assign("b", type=str, default="cb")]

        merged = merge_assign_declarations(Child)
        names = {d.name for d in merged}
        assert names == {"a", "b"}

    def test_child_overrides_parent_by_name(self):
        class Parent(LiveComponent):
            assigns = [Assign("variant", type=str, default="parent")]

        class Child(Parent):
            assigns = [Assign("variant", type=str, default="child")]

        merged = merge_assign_declarations(Child)
        assert len(merged) == 1
        assert merged[0].default == "child"

    def test_slot_shorthand_string(self):
        class Widget(LiveComponent):
            slots = ["inner_block"]

        merged = merge_slot_declarations(Widget)
        assert len(merged) == 1
        assert merged[0].name == "inner_block"
        assert merged[0].required is True


# ---------------------------------------------------------------------------
# LiveComponent integration
# ---------------------------------------------------------------------------


class ButtonComponent(LiveComponent):
    """Test component with declarative assigns."""

    template = "<button>{{ label }}</button>"

    assigns = [
        Assign("label", type=str, required=True),
        Assign("variant", type=str, default="default", values=["default", "primary"]),
        Assign("count", type=int, default=0),
    ]

    def mount(self, label="", variant="default", count=0, **kwargs):
        self.label = label
        self.variant = variant
        self.count = count

    def get_context_data(self):
        return {"label": self.label}


class TestLiveComponentIntegration:
    def test_debug_mode_raises_on_missing_required(self):
        from djust.components import base as base_mod

        # Force DEBUG=True for this test — demo settings may be DEBUG=False.
        with patch.object(base_mod, "_is_debug_mode", return_value=True):
            with pytest.raises(AssignValidationError, match="Required assign 'label'"):
                ButtonComponent()

    def test_validation_applies_coercion(self):
        comp = ButtonComponent(label="OK", count="5")
        assert comp.count == 5
        assert isinstance(comp.count, int)

    def test_validation_applies_default(self):
        comp = ButtonComponent(label="OK")
        assert comp.variant == "default"
        assert comp.count == 0

    def test_validation_stored_on_instance(self):
        comp = ButtonComponent(label="OK", count="3")
        assert comp._validated_assigns["label"] == "OK"
        assert comp._validated_assigns["count"] == 3
        assert comp._validated_assigns["variant"] == "default"

    def test_non_debug_warns_instead_of_raises(self, caplog):
        # Force DEBUG=False path by patching the helper's import target.
        from djust.components import base as base_mod

        with patch.object(base_mod, "_is_debug_mode", return_value=False):
            with caplog.at_level(logging.WARNING):
                # Missing required -> should warn, not raise.
                comp = ButtonComponent()
            assert any("assign validation failed" in rec.message.lower() for rec in caplog.records)
            assert comp is not None
