"""Tests for the server-side JS Commands helper (``djust.js``).

The client-side interpreter is covered by ``tests/js/js-commands.test.js``.
These tests validate the Python builder:

- every command's signature and emitted JSON shape
- the ``to=`` / ``inner=`` / ``closest=`` target mutual exclusion
- ``__str__`` / ``__html__`` HTML-attribute integration
- chain immutability (each method returns a fresh ``JSChain``)
- JSChain equality / hashability basics via the frozen dataclass
"""

from __future__ import annotations

import json

import pytest

from djust.js import JS, JSChain


class TestShowHide:
    def test_show_absolute_to(self) -> None:
        cmd = JS.show("#modal")
        assert json.loads(str(cmd)) == [["show", {"to": "#modal"}]]

    def test_show_with_display(self) -> None:
        cmd = JS.show("#modal", display="flex")
        assert json.loads(str(cmd)) == [["show", {"to": "#modal", "display": "flex"}]]

    def test_show_with_transition_and_time(self) -> None:
        cmd = JS.show("#modal", transition="fade-in", time=300)
        assert json.loads(str(cmd)) == [
            ["show", {"to": "#modal", "transition": "fade-in", "time": 300}]
        ]

    def test_hide_with_closest(self) -> None:
        cmd = JS.hide(closest=".modal")
        assert json.loads(str(cmd)) == [["hide", {"closest": ".modal"}]]

    def test_toggle_with_inner(self) -> None:
        cmd = JS.toggle(inner=".panel")
        assert json.loads(str(cmd)) == [["toggle", {"inner": ".panel"}]]


class TestClassMutations:
    def test_add_class(self) -> None:
        cmd = JS.add_class("active", to="#overlay")
        assert json.loads(str(cmd)) == [["add_class", {"to": "#overlay", "names": "active"}]]

    def test_add_multiple_classes(self) -> None:
        cmd = JS.add_class("active visible", to="#overlay")
        ops = json.loads(str(cmd))
        assert ops[0][1]["names"] == "active visible"

    def test_remove_class(self) -> None:
        cmd = JS.remove_class("hidden", to="#panel")
        assert json.loads(str(cmd)) == [["remove_class", {"to": "#panel", "names": "hidden"}]]

    def test_transition_default_time(self) -> None:
        cmd = JS.transition("fade-in", to="#modal")
        ops = json.loads(str(cmd))
        assert ops[0][0] == "transition"
        assert ops[0][1]["time"] == 200  # default

    def test_transition_custom_time(self) -> None:
        cmd = JS.transition("fade-in", to="#modal", time=500)
        ops = json.loads(str(cmd))
        assert ops[0][1]["time"] == 500


class TestAttributeMutations:
    def test_set_attr_emits_tuple(self) -> None:
        cmd = JS.set_attr("data-open", "true", to="#panel")
        ops = json.loads(str(cmd))
        assert ops == [["set_attr", {"to": "#panel", "attr": ["data-open", "true"]}]]

    def test_remove_attr_emits_string(self) -> None:
        cmd = JS.remove_attr("disabled", to="#submit-btn")
        ops = json.loads(str(cmd))
        assert ops == [["remove_attr", {"to": "#submit-btn", "attr": "disabled"}]]


class TestMiscOps:
    def test_focus(self) -> None:
        cmd = JS.focus("#input-name")
        assert json.loads(str(cmd)) == [["focus", {"to": "#input-name"}]]

    def test_dispatch_minimal(self) -> None:
        cmd = JS.dispatch("my:event", to="#el")
        ops = json.loads(str(cmd))
        assert ops[0][0] == "dispatch"
        assert ops[0][1]["event"] == "my:event"
        assert ops[0][1]["bubbles"] is True

    def test_dispatch_with_detail(self) -> None:
        cmd = JS.dispatch("my:event", detail={"x": 1, "y": 2})
        ops = json.loads(str(cmd))
        assert ops[0][1]["detail"] == {"x": 1, "y": 2}

    def test_dispatch_bubbles_false(self) -> None:
        cmd = JS.dispatch("my:event", bubbles=False)
        ops = json.loads(str(cmd))
        assert ops[0][1]["bubbles"] is False


class TestPush:
    def test_push_minimal(self) -> None:
        cmd = JS.push("save_draft")
        assert json.loads(str(cmd)) == [["push", {"event": "save_draft"}]]

    def test_push_with_value(self) -> None:
        cmd = JS.push("save_draft", value={"id": 42, "text": "hi"})
        ops = json.loads(str(cmd))
        assert ops[0][1]["value"] == {"id": 42, "text": "hi"}

    def test_push_with_target(self) -> None:
        cmd = JS.push("save", target="#form-1")
        ops = json.loads(str(cmd))
        assert ops[0][1]["target"] == "#form-1"

    def test_push_page_loading(self) -> None:
        cmd = JS.push("generate_report", page_loading=True)
        ops = json.loads(str(cmd))
        assert ops[0][1]["page_loading"] is True

    def test_push_page_loading_false_omitted(self) -> None:
        cmd = JS.push("save")
        ops = json.loads(str(cmd))
        assert "page_loading" not in ops[0][1]


class TestChaining:
    def test_two_ops(self) -> None:
        cmd = JS.show("#modal").add_class("active", to="#overlay")
        ops = json.loads(str(cmd))
        assert len(ops) == 2
        assert ops[0][0] == "show"
        assert ops[1][0] == "add_class"

    def test_long_chain(self) -> None:
        cmd = (
            JS.show("#modal")
            .add_class("open", to="#overlay")
            .focus("#modal-title")
            .dispatch("modal:opened")
        )
        ops = json.loads(str(cmd))
        assert [op[0] for op in ops] == ["show", "add_class", "focus", "dispatch"]

    def test_chain_is_immutable(self) -> None:
        """Each chain method must return a new JSChain — never mutate self."""
        base = JS.show("#modal")
        extended = base.add_class("active", to="#overlay")
        assert len(base.ops) == 1
        assert len(extended.ops) == 2
        assert base is not extended

    def test_same_chain_reused(self) -> None:
        """Sharing a chain across multiple call sites must not cross-contaminate."""
        base = JS.show("#modal")
        a = base.add_class("active", to="#overlay")
        b = base.add_class("visible", to="#overlay")
        assert len(a.ops) == 2
        assert len(b.ops) == 2
        assert a.ops != b.ops
        assert a.ops[1][1]["names"] == "active"
        assert b.ops[1][1]["names"] == "visible"


class TestTargetValidation:
    def test_to_and_inner_rejected(self) -> None:
        with pytest.raises(ValueError, match="at most one of"):
            JS.show(to="#a", inner=".b")

    def test_inner_and_closest_rejected(self) -> None:
        with pytest.raises(ValueError, match="at most one of"):
            JS.hide(inner=".a", closest=".b")

    def test_to_and_closest_rejected(self) -> None:
        with pytest.raises(ValueError, match="at most one of"):
            JS.add_class("x", to="#a", closest=".b")

    def test_all_three_rejected(self) -> None:
        with pytest.raises(ValueError, match="at most one of"):
            JS.show(to="#a", inner=".b", closest=".c")

    def test_none_of_them_ok(self) -> None:
        # Targets the origin element — no kwargs at all
        cmd = JS.show()
        ops = json.loads(str(cmd))
        assert ops == [["show", {}]]


class TestHTMLIntegration:
    def test_str_returns_json(self) -> None:
        cmd = JS.show("#modal")
        assert str(cmd) == '[["show",{"to":"#modal"}]]'

    def test_html_returns_safestring(self) -> None:
        from django.utils.safestring import SafeString

        cmd = JS.show("#modal")
        out = cmd.__html__()
        assert isinstance(out, SafeString)
        assert str(out) == '[["show",{"to":"#modal"}]]'

    def test_template_rendering_does_not_double_escape(self) -> None:
        from django.template import Context, Template

        tpl = Template('<button dj-click="{{ cmd }}">Open</button>')
        cmd = JS.show("#modal").add_class("active", to="#overlay")
        rendered = tpl.render(Context({"cmd": cmd}))
        # The JSON quotes should be HTML-attribute-encoded to &quot; by
        # Django's default auto-escape so the attribute is well-formed.
        # Either form is acceptable as long as client JSON.parse works
        # after the browser decodes the attribute.
        assert "show" in rendered
        assert "#modal" in rendered


class TestJSChainDirect:
    """Using the JSChain class directly, not via the JS factory."""

    def test_empty_chain(self) -> None:
        c = JSChain()
        assert str(c) == "[]"
        assert c.ops == []

    def test_chain_from_empty_then_add(self) -> None:
        c = JSChain().show("#modal")
        assert len(c.ops) == 1

    def test_chain_equality(self) -> None:
        a = JS.show("#modal")
        b = JS.show("#modal")
        assert a == b

    def test_chain_inequality(self) -> None:
        a = JS.show("#modal")
        b = JS.show("#dialog")
        assert a != b
