"""Tests for the djust_audit management command."""

import json

import pytest
from django.core.management import call_command
from io import StringIO

from djust.management.commands.djust_audit import (
    _audit_class,
    _format_decorator_tags,
    _format_handler_params,
    _get_handler_metadata,
    _is_user_class,
    _walk_subclasses,
)


# ---------------------------------------------------------------------------
# Fixtures: minimal LiveView / LiveComponent subclasses for testing
# ---------------------------------------------------------------------------


@pytest.fixture
def make_view_class():
    """Factory for creating test LiveView subclasses."""
    from djust.live_view import LiveView

    def _make(name="TestView", template_name="test.html", handlers=None, **attrs):
        body = {"template_name": template_name, "__module__": "myapp.views"}
        body.update(attrs)

        if handlers:
            for hname, hfunc in handlers.items():
                body[hname] = hfunc

        cls = type(name, (LiveView,), body)
        return cls

    return _make


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestIsUserClass:
    def test_user_class(self, make_view_class):
        cls = make_view_class()
        assert _is_user_class(cls)

    def test_djust_internal_class(self, make_view_class):
        cls = make_view_class(__module__="djust.live_view")
        assert not _is_user_class(cls)

    def test_djust_test_class(self, make_view_class):
        cls = make_view_class(__module__="djust.tests.test_something")
        assert _is_user_class(cls)

    def test_djust_example_class(self, make_view_class):
        cls = make_view_class(__module__="djust.examples.demo")
        assert _is_user_class(cls)


class TestWalkSubclasses:
    def test_finds_subclasses(self, make_view_class):
        parent = make_view_class("ParentView")
        child = type("ChildView", (parent,), {"__module__": "myapp.views"})

        found = list(_walk_subclasses(parent))
        assert child in found

    def test_finds_nested_subclasses(self, make_view_class):
        grandparent = make_view_class("GrandparentView")
        parent = type("ParentView", (grandparent,), {"__module__": "myapp.views"})
        child = type("ChildView", (parent,), {"__module__": "myapp.views"})

        found = list(_walk_subclasses(grandparent))
        assert parent in found
        assert child in found


class TestGetHandlerMetadata:
    def test_finds_event_handlers(self, make_view_class):
        from djust.decorators import event_handler

        @event_handler()
        def increment(self, **kwargs):
            pass

        cls = make_view_class(handlers={"increment": increment})
        handlers = list(_get_handler_metadata(cls))
        names = [h[0] for h in handlers]
        assert "increment" in names

    def test_skips_private_methods(self, make_view_class):
        from djust.decorators import event_handler

        @event_handler()
        def _private_handler(self, **kwargs):
            pass

        cls = make_view_class(handlers={"_private_handler": _private_handler})
        handlers = list(_get_handler_metadata(cls))
        names = [h[0] for h in handlers]
        assert "_private_handler" not in names

    def test_skips_non_handlers(self, make_view_class):
        def regular_method(self):
            pass

        cls = make_view_class(handlers={"regular_method": regular_method})
        handlers = list(_get_handler_metadata(cls))
        names = [h[0] for h in handlers]
        assert "regular_method" not in names


class TestFormatHandlerParams:
    def test_kwargs_only(self):
        meta = {"event_handler": {"params": [], "accepts_kwargs": True}}
        assert _format_handler_params(meta) == "**kwargs"

    def test_typed_param_with_default(self):
        meta = {
            "event_handler": {
                "params": [{"name": "value", "type": "str", "required": False, "default": ""}],
                "accepts_kwargs": False,
            }
        }
        result = _format_handler_params(meta)
        assert "value: str" in result

    def test_required_param(self):
        meta = {
            "event_handler": {
                "params": [{"name": "item_id", "type": "int", "required": True}],
                "accepts_kwargs": False,
            }
        }
        result = _format_handler_params(meta)
        assert result == "item_id: int"


class TestFormatDecoratorTags:
    def test_debounce(self):
        meta = {"debounce": {"wait": 0.3, "max_wait": None}}
        tags = _format_decorator_tags(meta)
        assert any("@debounce" in t for t in tags)
        assert any("wait=0.3" in t for t in tags)

    def test_rate_limit(self):
        meta = {"rate_limit": {"rate": 5, "burst": 3}}
        tags = _format_decorator_tags(meta)
        assert any("@rate_limit" in t for t in tags)

    def test_optimistic_bool(self):
        meta = {"optimistic": True}
        tags = _format_decorator_tags(meta)
        assert "@optimistic" in tags

    def test_no_decorators(self):
        meta = {}
        tags = _format_decorator_tags(meta)
        assert tags == []


class TestAuditClass:
    def test_basic_view_audit(self, make_view_class):
        cls = make_view_class(template_name="counter.html")
        result = _audit_class(cls, "LiveView")

        assert result["type"] == "LiveView"
        assert result["template"] == "counter.html"
        assert "myapp.views" in result["class"]

    def test_inline_template(self, make_view_class):
        cls = make_view_class(template_name=None, template="<div>{{ count }}</div>")
        result = _audit_class(cls, "LiveView")
        assert result["template"] == "(inline)"

    def test_no_template(self, make_view_class):
        cls = make_view_class(template_name=None)
        result = _audit_class(cls, "LiveView")
        assert result["template"] == "(none)"

    def test_config_tick_interval(self, make_view_class):
        cls = make_view_class(tick_interval=1000)
        result = _audit_class(cls, "LiveView")
        assert result["config"]["tick_interval"] == 1000

    def test_config_use_actors(self, make_view_class):
        cls = make_view_class(use_actors=True)
        result = _audit_class(cls, "LiveView")
        assert result["config"]["use_actors"] is True

    def test_handlers_included(self, make_view_class):
        from djust.decorators import event_handler

        @event_handler()
        def do_something(self, value: str = "", **kwargs):
            pass

        cls = make_view_class(handlers={"do_something": do_something})
        result = _audit_class(cls, "LiveView")
        assert len(result["handlers"]) >= 1
        names = [h["name"] for h in result["handlers"]]
        assert "do_something" in names


# ---------------------------------------------------------------------------
# Integration tests: management command execution
# ---------------------------------------------------------------------------


class TestCommandOutput:
    def test_command_runs(self):
        """djust_audit runs without error."""
        out = StringIO()
        call_command("djust_audit", stdout=out)
        # Should complete without exception

    def test_json_output_is_valid(self):
        """--json produces valid JSON with expected structure."""
        out = StringIO()
        call_command("djust_audit", json_output=True, stdout=out)
        data = json.loads(out.getvalue())
        assert "audits" in data
        assert "summary" in data
        assert "views" in data["summary"]
        assert "components" in data["summary"]
        assert "handlers" in data["summary"]

    def test_app_filter(self):
        """--app filters results to the specified app."""
        out = StringIO()
        call_command("djust_audit", json_output=True, app_label="nonexistent_app_xyz", stdout=out)
        data = json.loads(out.getvalue())
        assert data["audits"] == []

    def test_pretty_output_contains_header(self, make_view_class):
        """Pretty output includes the header banner."""
        # Create a test view so there's output; keep reference to prevent GC
        _view_cls = make_view_class(template_name="test.html")
        out = StringIO()
        call_command("djust_audit", stdout=out)
        output = out.getvalue()
        assert "djust audit" in output
        del _view_cls

    def test_verbose_flag(self):
        """--verbose flag doesn't crash even without Rust extension."""
        out = StringIO()
        call_command("djust_audit", verbose=True, stdout=out)
        # Should complete without exception
