"""Tests for the djust MCP server tools.

Tests the static/framework-only tools that don't require Django setup:
- detect_common_issues
- validate_view (enhanced with service pattern detection)
- get_best_practices (expanded content)
"""

import json

import pytest


# ---------------------------------------------------------------------------
# Helpers — call tool functions directly from the server factory
# ---------------------------------------------------------------------------


@pytest.fixture()
def mcp_server():
    """Create a djust MCP server instance for testing."""
    from djust.mcp.server import create_server

    return create_server()


def _call_tool(mcp_server, tool_name, **kwargs):
    """Invoke a tool function registered on the MCP server and return parsed JSON."""
    # FastMCP stores tools by name; access internal registry
    tool_fn = None
    for tool in mcp_server._tool_manager._tools.values():
        if tool.name == tool_name:
            tool_fn = tool.fn
            break
    if tool_fn is None:
        raise ValueError("Tool %r not found on MCP server" % tool_name)
    raw = tool_fn(**kwargs)
    return json.loads(raw)


# ============================================================================
# detect_common_issues
# ============================================================================


class TestDetectCommonIssues:
    """Tests for the detect_common_issues MCP tool."""

    def test_clean_code_no_issues(self, mcp_server):
        code = """
from djust import LiveView
from djust.decorators import event_handler

class MyView(LiveView):
    template_name = 'my/template.html'

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1
"""
        result = _call_tool(mcp_server, "detect_common_issues", code=code)
        assert result["summary"]["total"] == 0
        assert result["issues"] == []

    def test_detects_service_instance_by_name(self, mcp_server):
        code = """
class MyView:
    def mount(self, request, **kwargs):
        self.client = SomeClient()
"""
        result = _call_tool(mcp_server, "detect_common_issues", code=code)
        issues = result["issues"]
        service_issues = [i for i in issues if i["type"] == "service_in_state"]
        assert len(service_issues) >= 1
        assert "client" in service_issues[0]["message"]
        assert service_issues[0]["severity"] == "error"

    def test_detects_boto3_usage(self, mcp_server):
        code = """
class MyView:
    def mount(self, request, **kwargs):
        self.s3 = boto3.client('s3')
"""
        result = _call_tool(mcp_server, "detect_common_issues", code=code)
        issues = result["issues"]
        service_issues = [i for i in issues if i["type"] == "service_in_state"]
        assert len(service_issues) >= 1
        assert "boto3" in service_issues[0]["message"]

    def test_detects_missing_event_handler_decorator(self, mcp_server):
        code = """
class MyView:
    template_name = 'test.html'

    def handle_click(self, **kwargs):
        pass

    def on_submit(self, **kwargs):
        pass
"""
        result = _call_tool(mcp_server, "detect_common_issues", code=code)
        missing = [i for i in result["issues"] if i["type"] == "missing_decorator"]
        assert len(missing) == 2
        names = {i["message"] for i in missing}
        assert any("handle_click" in n for n in names)
        assert any("on_submit" in n for n in names)

    def test_detects_missing_kwargs(self, mcp_server):
        code = """
from djust.decorators import event_handler

class MyView:
    @event_handler()
    def do_thing(self, item_id: int = 0):
        pass
"""
        result = _call_tool(mcp_server, "detect_common_issues", code=code)
        kwargs_issues = [i for i in result["issues"] if i["type"] == "missing_kwargs"]
        assert len(kwargs_issues) == 1
        assert "do_thing" in kwargs_issues[0]["message"]

    def test_detects_public_queryset(self, mcp_server):
        code = """
class MyView:
    def _refresh(self):
        self.items = Item.objects.filter(active=True)
"""
        result = _call_tool(mcp_server, "detect_common_issues", code=code)
        qs_issues = [i for i in result["issues"] if i["type"] == "public_queryset"]
        assert len(qs_issues) == 1
        assert "items" in qs_issues[0]["message"]
        assert "_items" in qs_issues[0]["fix"]

    def test_private_queryset_is_ok(self, mcp_server):
        code = """
class MyView:
    def _refresh(self):
        self._items = Item.objects.filter(active=True)
"""
        result = _call_tool(mcp_server, "detect_common_issues", code=code)
        qs_issues = [i for i in result["issues"] if i["type"] == "public_queryset"]
        assert len(qs_issues) == 0

    def test_syntax_error_handled(self, mcp_server):
        code = "class MyView(\n"
        result = _call_tool(mcp_server, "detect_common_issues", code=code)
        assert result["summary"]["errors"] == 1
        assert result["issues"][0]["type"] == "syntax_error"

    def test_summary_counts(self, mcp_server):
        code = """
from djust.decorators import event_handler

class MyView:
    def mount(self, request, **kwargs):
        self.client = SomeClient()

    @event_handler()
    def do_thing(self, item_id: int = 0):
        pass

    def handle_click(self):
        pass
"""
        result = _call_tool(mcp_server, "detect_common_issues", code=code)
        assert result["summary"]["total"] > 0
        assert result["summary"]["errors"] >= 1  # service_in_state
        assert result["summary"]["warnings"] >= 1  # missing_kwargs or missing_decorator


# ============================================================================
# validate_view (enhanced with service detection)
# ============================================================================


class TestValidateViewEnhanced:
    """Tests for the enhanced validate_view tool with service pattern detection."""

    def test_detects_boto3_in_mount(self, mcp_server):
        code = """
class MyView:
    template_name = 'test.html'

    def mount(self, request, **kwargs):
        self.s3 = boto3.client('s3')
"""
        result = _call_tool(mcp_server, "validate_view", code=code)
        service_issues = [i for i in result if "Service instance" in i.get("message", "")]
        assert len(service_issues) >= 1
        assert "services.md" in service_issues[0]["fix_hint"]

    def test_detects_requests_session(self, mcp_server):
        code = """
class MyView:
    template_name = 'test.html'

    def mount(self, request, **kwargs):
        self.http = requests.Session()
"""
        result = _call_tool(mcp_server, "validate_view", code=code)
        service_issues = [i for i in result if "Service instance" in i.get("message", "")]
        assert len(service_issues) >= 1

    def test_private_service_attr_not_flagged(self, mcp_server):
        """Private attributes (self._client) should not be flagged."""
        code = """
class MyView:
    template_name = 'test.html'

    def mount(self, request, **kwargs):
        self._client = boto3.client('s3')
"""
        result = _call_tool(mcp_server, "validate_view", code=code)
        service_issues = [i for i in result if "Service instance" in i.get("message", "")]
        assert len(service_issues) == 0

    def test_existing_checks_still_work(self, mcp_server):
        """Ensure original validate_view checks are preserved."""
        code = """
class MyView:
    def handle_click(self):
        pass
"""
        result = _call_tool(mcp_server, "validate_view", code=code)
        # Should detect missing template and handler without decorator
        messages = [i["message"] for i in result]
        assert any("template" in m.lower() for m in messages)
        assert any("handler" in m.lower() for m in messages)

    def test_mark_safe_still_detected(self, mcp_server):
        code = """
class MyView:
    template_name = 'test.html'

    def render_content(self):
        return mark_safe(f"<div>{self.user_input}</div>")
"""
        result = _call_tool(mcp_server, "validate_view", code=code)
        security_issues = [i for i in result if "XSS" in i.get("message", "")]
        assert len(security_issues) >= 1


# ============================================================================
# get_best_practices (expanded)
# ============================================================================


class TestGetBestPracticesExpanded:
    """Tests for the expanded BEST_PRACTICES schema."""

    def test_has_state_management_section(self, mcp_server):
        result = _call_tool(mcp_server, "get_best_practices")
        assert "state_management" in result
        assert "serialization" in result["state_management"]
        sm = result["state_management"]["serialization"]
        assert "serializable" in sm
        assert "not_serializable" in sm
        assert "fix_pattern" in sm

    def test_has_templates_section(self, mcp_server):
        result = _call_tool(mcp_server, "get_best_practices")
        assert "templates" in result
        assert "required_attributes" in result["templates"]
        attrs = result["templates"]["required_attributes"]
        assert "data-djust-view" in attrs["attributes"][0]
        assert "data-djust-root" in attrs["attributes"][1]

    def test_has_event_handler_signature_section(self, mcp_server):
        result = _call_tool(mcp_server, "get_best_practices")
        assert "event_handler_signature" in result
        sig = result["event_handler_signature"]
        assert "**kwargs" in sig["correct"]
        assert "**kwargs" not in sig["wrong"] or "Missing" in sig["wrong"]

    def test_common_pitfalls_are_structured(self, mcp_server):
        result = _call_tool(mcp_server, "get_best_practices")
        pitfalls = result["common_pitfalls"]
        assert isinstance(pitfalls, list)
        assert len(pitfalls) == 8

        # Each pitfall should have structured fields
        for pitfall in pitfalls:
            assert "id" in pitfall
            assert "problem" in pitfall
            assert "why" in pitfall
            assert "solution" in pitfall

    def test_pitfall_covers_service_instances(self, mcp_server):
        result = _call_tool(mcp_server, "get_best_practices")
        pitfalls = result["common_pitfalls"]
        service_pitfall = next(p for p in pitfalls if p["id"] == 1)
        assert "service" in service_pitfall["problem"].lower()
        assert "helper method" in service_pitfall["solution"].lower()

    def test_pitfall_covers_missing_root(self, mcp_server):
        result = _call_tool(mcp_server, "get_best_practices")
        pitfalls = result["common_pitfalls"]
        root_pitfall = next(p for p in pitfalls if p["id"] == 2)
        assert "data-djust-root" in root_pitfall["problem"]

    def test_original_sections_preserved(self, mcp_server):
        result = _call_tool(mcp_server, "get_best_practices")
        assert "setup" in result
        assert "lifecycle" in result
        assert "event_handlers" in result
        assert "jit_serialization" in result
        assert "forms" in result
        assert "security" in result
