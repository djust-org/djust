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

    def test_a_closed_handler_signature_is_not_an_issue(self, mcp_server):
        # ADR-037 retired V007: a closed signature is encouraged, and
        # `manage.py check` (T020) compares bindings with handlers instead.
        code = """
from djust.decorators import event_handler

class MyView:
    @event_handler()
    def do_thing(self, item_id: int = 0):
        pass
"""
        for tool in ("detect_common_issues", "validate_view"):
            result = _call_tool(mcp_server, tool, code=code)
            # detect_common_issues answers {"issues": [...]}; validate_view a list.
            issues = result["issues"] if isinstance(result, dict) else result
            assert not [
                i for i in issues if "kwargs" in json.dumps(i) and "mount" not in json.dumps(i)
            ], tool

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
        assert result["summary"]["warnings"] >= 1  # missing_decorator


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
        assert "dj-view" in attrs["attributes"][0]
        assert "dj-root" in attrs["attributes"][1]

    def test_has_event_handler_signature_section(self, mcp_server):
        result = _call_tool(mcp_server, "get_best_practices")
        sig = result["event_handler_signature"]
        assert "**kwargs" not in sig["correct"]
        assert "T020" in sig["description"]
        rules = " ".join(result["event_handlers"]["rules"])
        assert "MUST accept **kwargs" not in rules and "MUST have default values" not in rules

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
        assert "dj-root" in root_pitfall["problem"]

    def test_original_sections_preserved(self, mcp_server):
        result = _call_tool(mcp_server, "get_best_practices")
        assert "setup" in result
        assert "lifecycle" in result
        assert "event_handlers" in result
        assert "jit_serialization" in result
        assert "forms" in result
        assert "security" in result


def test_ai_events_reference_does_not_require_kwargs():
    import pathlib

    text = pathlib.Path(__file__).resolve().parents[3].joinpath("docs/ai/events.md").read_text()
    assert "require `@event_handler()` decorator and `**kwargs`" not in text


def test_no_documentation_teaches_the_retired_kwargs_rule():
    # #3138 review: ADR-037 retired V007's "always accept **kwargs", but the
    # website guides still taught it (and "default values for all parameters").
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[3]
    retired = re.compile(
        r"(?i)(always (?:accept|include|use)[^\n]{0,40}\*\*kwargs"
        r"|(?:include|accept) `?\*\*kwargs`? (?:in all|for flexibility)"
        r"|default values for all (?:handler )?parameters)"
    )
    files = [*(root / "docs/website").rglob("*.md"), *(root / "docs/ai").rglob("*.md")]
    files.append(root / "docs/BEST_PRACTICES_AI.md")
    found = [
        "%s:%d" % (path.relative_to(root), number)
        for path in files
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if retired.search(line)
    ]
    assert found == [], found


def test_guidance_says_what_legacy_submit_actually_sends():
    # #3138 review: legacy dj-submit sends the form fields plus `_target`, never
    # `field` (09-event-binding.js; _template_bindings DIRECTIVES "dj-submit").
    # Guidance telling an AI to declare `field` on a submit handler gets every
    # submit rejected for a missing required parameter.
    import json
    import pathlib

    from djust._template_bindings import DIRECTIVES
    from djust.schema import BEST_PRACTICES

    submit = DIRECTIVES["dj-submit"]
    assert "field" not in submit.generated + submit.legacy_only
    assert submit.legacy_only == ("_target",)
    root = pathlib.Path(__file__).resolve().parents[3]
    texts = {
        "schema": json.dumps(BEST_PRACTICES),
        "docs/ai/events.md": (root / "docs/ai/events.md").read_text(),
    }
    for name, text in texts.items():
        assert "dj-submit also send" not in text, name
        assert "dj-change and dj-submit also send" not in text, name
        assert "raises TypeError" not in text, name
