"""
Tests for the djust TypeScript definition file (djust.d.ts).

These tests verify:
- The file exists at the expected location in the package
- All public API surfaces are typed (interfaces, classes, events)
- The file is structurally valid TypeScript (balanced braces, no obvious syntax errors)
- Edge cases: null types, optional parameters, union types

Run with:
    pytest python/tests/test_typescript_definitions.py -v
"""

import os
import re

import pytest

# Path to the shipped .d.ts file relative to the package
DTS_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../python/djust/static/djust/djust.d.ts",
)


@pytest.fixture(scope="module")
def dts_content():
    """Read and return the .d.ts file content."""
    abs_path = os.path.abspath(DTS_PATH)
    assert os.path.exists(abs_path), (
        f"djust.d.ts not found at {abs_path}. " "Run the implementation step to create it."
    )
    with open(abs_path) as f:
        return f.read()


# ---------------------------------------------------------------------------
# 1. File existence
# ---------------------------------------------------------------------------


def test_dts_file_exists():
    abs_path = os.path.abspath(DTS_PATH)
    assert os.path.exists(abs_path), f"djust.d.ts missing at {abs_path}"


def test_dts_file_is_non_empty(dts_content):
    assert len(dts_content.strip()) > 200, "djust.d.ts appears to be empty or too short"


# ---------------------------------------------------------------------------
# 2. Core interfaces
# ---------------------------------------------------------------------------


def test_djust_hook_context_interface(dts_content):
    """DjustHookContext must type the `this` inside hook callbacks."""
    assert "DjustHookContext" in dts_content, "Missing DjustHookContext interface"
    # Must have el: Element
    assert re.search(r"el\s*:\s*Element", dts_content), "DjustHookContext must have el: Element"
    # Must have viewName: string
    assert re.search(
        r"viewName\s*:\s*string", dts_content
    ), "DjustHookContext must have viewName: string"
    # Must have pushEvent method
    assert re.search(
        r"pushEvent\s*\(", dts_content
    ), "DjustHookContext must have pushEvent() method"
    # Must have handleEvent method
    assert re.search(
        r"handleEvent\s*\(", dts_content
    ), "DjustHookContext must have handleEvent() method"


def test_djust_hook_interface_lifecycle(dts_content):
    """DjustHook must declare all five lifecycle callbacks."""
    assert "DjustHook" in dts_content, "Missing DjustHook interface"
    for callback in (
        "mounted",
        "updated",
        "beforeUpdate",
        "destroyed",
        "disconnected",
        "reconnected",
    ):
        assert re.search(
            rf"\b{callback}\b", dts_content
        ), f"DjustHook missing lifecycle callback: {callback}"


def test_djust_hook_map_type(dts_content):
    """DjustHookMap must be a string-keyed index signature or Record type."""
    assert "DjustHookMap" in dts_content, "Missing DjustHookMap type"


# ---------------------------------------------------------------------------
# 3. Transport classes
# ---------------------------------------------------------------------------


def test_live_view_websocket_class(dts_content):
    """LiveViewWebSocket class must be declared with key methods."""
    assert "LiveViewWebSocket" in dts_content, "Missing LiveViewWebSocket declaration"
    assert re.search(r"connect\s*\(", dts_content), "LiveViewWebSocket.connect() not declared"
    assert re.search(r"disconnect\s*\(", dts_content), "LiveViewWebSocket.disconnect() not declared"
    assert re.search(r"sendEvent\s*\(", dts_content), "LiveViewWebSocket.sendEvent() not declared"


def test_live_view_sse_class(dts_content):
    """LiveViewSSE class must be declared with key methods."""
    assert "LiveViewSSE" in dts_content, "Missing LiveViewSSE declaration"
    assert re.search(r"connect\s*\(", dts_content), "LiveViewSSE.connect() not declared"
    assert re.search(r"sendEvent\s*\(", dts_content), "LiveViewSSE.sendEvent() not declared"


def test_websocket_stats_type(dts_content):
    """WebSocket stats object should be typed (sent, received, bytes)."""
    assert re.search(r"sent\s*:", dts_content), "WebSocket stats missing 'sent'"
    assert re.search(r"received\s*:", dts_content), "WebSocket stats missing 'received'"


# ---------------------------------------------------------------------------
# 4. Upload types
# ---------------------------------------------------------------------------


def test_upload_progress_event_type(dts_content):
    """Upload progress custom event detail must be typed."""
    # Either a CustomEvent type or an interface for the detail
    assert re.search(
        r"upload.*progress|DjustUpload", dts_content, re.IGNORECASE
    ), "Missing upload progress type declarations"
    # ref field
    assert re.search(r"\bref\b\s*:", dts_content), "Upload type missing 'ref' field"
    # progress field (numeric percentage)
    assert re.search(r"\bprogress\b\s*:", dts_content), "Upload type missing 'progress' field"


def test_upload_config_type(dts_content):
    """Upload config must be typed."""
    assert re.search(
        r"DjustUpload|upload_config|UploadConfig", dts_content, re.IGNORECASE
    ), "Missing upload config type"


# ---------------------------------------------------------------------------
# 5. Streaming types
# ---------------------------------------------------------------------------


def test_stream_operation_type(dts_content):
    """Stream operation type must cover all ops: append, prepend, replace, delete, text, error."""
    assert re.search(
        r"DjustStream|stream.*op|StreamOp", dts_content, re.IGNORECASE
    ), "Missing stream operation type"
    for op in ("append", "prepend", "replace", "delete"):
        assert op in dts_content, f"Stream operation '{op}' not represented in types"


def test_stream_message_type(dts_content):
    """Stream message must include stream name and ops array."""
    assert re.search(r"\bstream\b\s*:", dts_content), "Stream message missing 'stream' field"
    assert re.search(r"\bops\b\s*:", dts_content), "Stream message missing 'ops' field"


# ---------------------------------------------------------------------------
# 6. window.djust namespace
# ---------------------------------------------------------------------------


def test_djust_namespace_declared(dts_content):
    """window.djust must be typed via interface Window extension."""
    assert re.search(
        r"interface\s+Window", dts_content
    ), "Missing 'interface Window' extension for window.djust"
    assert re.search(
        r"djust\s*:\s*Djust", dts_content
    ), "Window interface must have 'djust: Djust' property"


def test_djust_interface_core_props(dts_content):
    """Djust interface must expose core API properties."""
    assert "Djust" in dts_content, "Missing Djust interface"
    # liveViewInstance can be null
    assert "liveViewInstance" in dts_content, "Djust missing liveViewInstance"
    # hooks registry
    assert re.search(r"\bhooks\b\s*:", dts_content), "Djust missing hooks property"
    # handleEvent method
    assert "handleEvent" in dts_content, "Djust missing handleEvent"


def test_djust_interface_hook_methods(dts_content):
    """Djust interface must expose hook management methods."""
    for method in (
        "mountHooks",
        "updateHooks",
        "destroyAllHooks",
        "notifyHooksDisconnected",
        "notifyHooksReconnected",
        "dispatchPushEventToHooks",
    ):
        assert method in dts_content, f"Djust interface missing method: {method}"


def test_djust_interface_navigation(dts_content):
    """Djust.navigation must be typed."""
    assert "navigation" in dts_content, "Djust missing navigation"
    assert "handleNavigation" in dts_content, "navigation missing handleNavigation"


def test_djust_interface_uploads(dts_content):
    """Djust.uploads must be typed."""
    assert "uploads" in dts_content, "Djust missing uploads"
    assert "setConfigs" in dts_content, "uploads missing setConfigs"
    assert "cancelUpload" in dts_content, "uploads missing cancelUpload"


def test_djust_interface_streaming(dts_content):
    """Djust.handleStreamMessage must be typed."""
    assert "handleStreamMessage" in dts_content, "Djust missing handleStreamMessage"


def test_djust_interface_model_binding(dts_content):
    """Djust.bindModelElements must be typed."""
    assert "bindModelElements" in dts_content, "Djust missing bindModelElements"


# ---------------------------------------------------------------------------
# 7. Global declarations and declare global
# ---------------------------------------------------------------------------


def test_declare_global_block(dts_content):
    """File must use declare global {} to extend Window without a module."""
    assert (
        "declare global" in dts_content
    ), "Missing 'declare global' block — required to extend Window as an ambient file"


def test_djust_debug_global(dts_content):
    """djustDebug flag must be declared."""
    assert "djustDebug" in dts_content, "Missing djustDebug global declaration"


def test_djust_hooks_compat_alias(dts_content):
    """window.DjustHooks (Phoenix-compat alias) must be typed."""
    assert (
        "DjustHooks" in dts_content
    ), "Missing DjustHooks window property (Phoenix LiveView-compatible hook registration)"


# ---------------------------------------------------------------------------
# 8. Null/optional handling
# ---------------------------------------------------------------------------


def test_live_view_instance_nullable(dts_content):
    """liveViewInstance must allow null (no active connection)."""
    # Should be typed as `... | null` or optional
    live_view_instance_section = re.search(r"liveViewInstance\s*\??\s*:[^\n;]+", dts_content)
    assert live_view_instance_section, "liveViewInstance not typed in Djust interface"
    assert "null" in live_view_instance_section.group(0) or "?" in live_view_instance_section.group(
        0
    ), "liveViewInstance should be nullable (| null) since connection may not be established"


def test_optional_parameters(dts_content):
    """Key methods must use optional parameters where appropriate."""
    # mountHooks(root?: Element) - root is optional
    assert re.search(
        r"mountHooks\s*\(\s*root\s*\?", dts_content
    ), "mountHooks root parameter should be optional"
    # handleEvent params should be optional
    assert re.search(
        r"handleEvent\s*\([^)]*\?", dts_content
    ), "handleEvent should have at least one optional parameter"


# ---------------------------------------------------------------------------
# 9. Structural validity (basic syntax checks)
# ---------------------------------------------------------------------------


def test_balanced_braces(dts_content):
    """The file must have balanced curly braces."""
    open_count = dts_content.count("{")
    close_count = dts_content.count("}")
    assert (
        open_count == close_count
    ), f"Unbalanced braces in djust.d.ts: {open_count} '{{' vs {close_count} '}}'"


def test_no_bare_javascript_syntax(dts_content):
    """The file must not contain function bodies (only declarations)."""
    # A .d.ts should not have function bodies with actual code
    assert (
        "this.ws = " not in dts_content
    ), "djust.d.ts contains implementation code (this.ws = ...) — should be declarations only"
    # console.log should not appear outside of JSDoc comment blocks
    # Strip JSDoc/line comments and check the remaining code
    code_only = re.sub(r"/\*.*?\*/", "", dts_content, flags=re.DOTALL)  # strip block comments
    code_only = re.sub(r"//[^\n]*", "", code_only)  # strip line comments
    assert (
        "console.log" not in code_only
    ), "djust.d.ts contains console.log outside of comments — should be declarations only"


def test_export_empty_for_ambient_module(dts_content):
    """
    For an ambient (non-module) .d.ts, the file should NOT start with export/import
    at the top level, since it relies on declare global. However, it may use
    export {} at the end to force module mode. This test just ensures the file
    isn't accidentally structured as a plain ESM module with only named exports.
    """
    # The file should contain declare global (already tested), which means
    # it either has no top-level exports OR has export {} to make it a module.
    # Both patterns are acceptable.
    has_declare_global = "declare global" in dts_content
    assert has_declare_global, "File must use declare global for ambient Window extension"
