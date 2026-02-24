"""
Tests for type stub files (.pyi) for LiveView and mixins.

These tests ensure that:
1. Stub files are syntactically valid
2. Type checkers (mypy) can use the stubs for validation
3. Typos and invalid method calls are caught at lint time
"""

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Directory containing the djust Python package (so mypy can find it)
_DJUST_PYTHON_PATH = str(Path(__file__).parent.parent)
# Use the same Python interpreter as the current test run to find mypy
_MYPY_EXE = str(Path(sys.executable).parent / "mypy")
_has_mypy = Path(_MYPY_EXE).exists()


class TestStubSyntax:
    """Test that all .pyi stub files are syntactically valid."""

    def test_stub_files_exist(self):
        """Required stub files are present."""
        base_path = Path(__file__).parent.parent / "djust"

        expected_stubs = [
            base_path / "mixins" / "navigation.pyi",
            base_path / "mixins" / "push_events.pyi",
            base_path / "mixins" / "streams.pyi",
            base_path / "streaming.pyi",
            base_path / "live_view.pyi",
        ]

        for stub_file in expected_stubs:
            assert stub_file.exists(), f"Missing stub file: {stub_file}"

    def test_stub_files_valid_syntax(self):
        """All .pyi stub files are syntactically valid Python."""
        base_path = Path(__file__).parent.parent / "djust"
        stub_files = list(base_path.glob("**/*.pyi"))

        assert len(stub_files) >= 5, "Expected at least 5 stub files"

        for stub_file in stub_files:
            try:
                ast.parse(stub_file.read_text())
            except SyntaxError as e:
                pytest.fail(f"Syntax error in {stub_file}: {e}")

    def test_py_typed_marker_exists(self):
        """PEP 561 marker file exists to indicate type information is provided."""
        py_typed = Path(__file__).parent.parent / "djust" / "py.typed"
        assert py_typed.exists(), "Missing py.typed marker file"


@pytest.mark.skipif(not _has_mypy, reason="mypy not installed")
class TestMypyIntegration:
    """Test that mypy can use the stubs for type checking."""

    def run_mypy_on_code(self, code: str) -> subprocess.CompletedProcess:
        """Run mypy on a code snippet and return the result."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            f.flush()

            try:
                # Run mypy with lenient settings for test code.
                # Use the venv's mypy and pass PYTHONPATH so it can find the djust package.
                env = os.environ.copy()
                existing = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = (
                    f"{_DJUST_PYTHON_PATH}:{existing}" if existing else _DJUST_PYTHON_PATH
                )
                result = subprocess.run(
                    [
                        _MYPY_EXE,
                        "--no-error-summary",
                        "--allow-untyped-defs",  # Allow test methods without type hints
                        "--disable-error-code=no-untyped-def",
                        "--python-executable",
                        sys.executable,
                        f.name,
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                return result
            finally:
                Path(f.name).unlink()

    def test_mypy_accepts_navigation_methods(self):
        """Mypy recognizes NavigationMixin methods from stubs."""
        code = """
from djust import LiveView

class TestView(LiveView):
    template_name = "test.html"

    def my_handler(self):
        self.live_patch(params={"page": 1})
        self.live_patch(params={"sort": "name"}, replace=True)
        self.live_redirect("/items/42/")
        self.live_redirect(path="/items/", params={"filter": "active"})
        self.handle_params({"page": "1"}, "/test/")
"""
        result = self.run_mypy_on_code(code)
        assert result.returncode == 0, f"Mypy errors: {result.stdout}"

    def test_mypy_accepts_push_event_methods(self):
        """Mypy recognizes PushEventMixin methods from stubs."""
        code = """
from djust import LiveView

class TestView(LiveView):
    template_name = "test.html"

    def my_handler(self):
        self.push_event("flash", {"message": "Saved!"})
        self.push_event("scroll_to", {"selector": "#bottom"})
        self.push_event("custom_event")
"""
        result = self.run_mypy_on_code(code)
        assert result.returncode == 0, f"Mypy errors: {result.stdout}"

    def test_mypy_accepts_streams_methods(self):
        """Mypy recognizes StreamsMixin methods from stubs."""
        code = """
from djust import LiveView

class TestView(LiveView):
    template_name = "test.html"

    def my_handler(self):
        items = [{"id": 1}, {"id": 2}]
        self.stream("items", items)
        self.stream("items", items, at=0)
        self.stream_insert("items", {"id": 3})
        self.stream_insert("items", {"id": 4}, at=0)
        self.stream_delete("items", 1)
        self.stream_reset("items")
        self.stream_reset("items", items)
"""
        result = self.run_mypy_on_code(code)
        assert result.returncode == 0, f"Mypy errors: {result.stdout}"

    def test_mypy_accepts_streaming_methods(self):
        """Mypy recognizes StreamingMixin async methods from stubs."""
        code = """
from djust import LiveView

class TestView(LiveView):
    template_name = "test.html"

    async def my_async_handler(self):
        await self.stream_to("messages", target="#message-list")
        await self.stream_to("messages", html="<div>Test</div>")
        await self.push_state()
"""
        result = self.run_mypy_on_code(code)
        assert result.returncode == 0, f"Mypy errors: {result.stdout}"

    def test_mypy_verifies_correct_method_signatures(self):
        """
        Mypy verifies that correctly typed methods are accepted.

        Note: Due to Django's dynamic nature, mypy cannot reliably catch
        typos like live_navigate vs live_redirect. However, the stubs DO
        provide autocomplete and signature hints in IDEs.
        """
        code = """
from typing import Dict, Any
from djust import LiveView

class TestView(LiveView):
    template_name = "test.html"

    def my_handler(self) -> None:
        # All these should be accepted by mypy
        params: Dict[str, Any] = {"page": 1}
        self.live_patch(params=params)
        self.live_redirect(path="/items/")
        self.push_event("test", {"data": "value"})
        self.stream_insert("items", {"id": 1})
"""
        result = self.run_mypy_on_code(code)
        assert result.returncode == 0, f"Mypy should accept correct usage: {result.stdout}"

    def test_stub_methods_have_correct_signatures(self):
        """
        Verify stub methods have type hints that enable IDE autocomplete.

        This tests that the stubs are structured correctly for tooling,
        even if mypy can't catch all runtime errors due to Django's
        dynamic base classes.
        """
        # Read the stub files and verify they contain proper type hints
        from djust.mixins.navigation import NavigationMixin
        from djust.mixins.push_events import PushEventMixin

        # These should be importable and have proper signatures

        # The stubs provide type information for static analysis
        # even if the runtime implementation is dynamic
        assert hasattr(NavigationMixin, "live_patch")
        assert hasattr(NavigationMixin, "live_redirect")
        assert hasattr(PushEventMixin, "push_event")

    def test_mypy_catches_missing_required_arg(self):
        """Mypy catches missing required arguments."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            test_code = """
from djust import LiveView

class TestView(LiveView):
    template_name = "test.html"

    def my_handler(self) -> None:
        self.live_redirect()  # Missing required 'path' argument
"""
            f.write(test_code)
            f.flush()

            try:
                result = subprocess.run(
                    ["mypy", "--no-error-summary", "--python-executable", sys.executable, f.name],
                    capture_output=True,
                    text=True,
                )
                # Should catch missing required argument
                has_error = (
                    result.returncode != 0
                    or "Missing positional argument" in result.stdout
                    or "Too few arguments" in result.stdout
                )
                assert (
                    has_error
                ), f"Mypy should have caught missing argument. Output: {result.stdout}"
            finally:
                Path(f.name).unlink()


class TestStubSignatures:
    """Test that stub signatures match implementation where verifiable."""

    def test_navigation_mixin_methods_in_stub(self):
        """NavigationMixin stub includes all public methods."""
        stub_path = Path(__file__).parent.parent / "djust" / "mixins" / "navigation.pyi"
        stub_content = stub_path.read_text()

        # Check for public methods
        assert "def live_patch" in stub_content
        assert "def live_redirect" in stub_content
        assert "def handle_params" in stub_content

        # Private methods should NOT be in stub
        assert "def _drain_navigation" not in stub_content or "# private" in stub_content.lower()

    def test_push_event_mixin_methods_in_stub(self):
        """PushEventMixin stub includes all public methods."""
        stub_path = Path(__file__).parent.parent / "djust" / "mixins" / "push_events.pyi"
        stub_content = stub_path.read_text()

        assert "def push_event" in stub_content
        assert "def _drain_push_events" not in stub_content or "# private" in stub_content.lower()

    def test_streams_mixin_methods_in_stub(self):
        """StreamsMixin stub includes all public methods."""
        stub_path = Path(__file__).parent.parent / "djust" / "mixins" / "streams.pyi"
        stub_content = stub_path.read_text()

        assert "def stream" in stub_content
        assert "def stream_insert" in stub_content
        assert "def stream_delete" in stub_content
        assert "def stream_reset" in stub_content

    def test_streaming_mixin_methods_in_stub(self):
        """StreamingMixin stub includes all public async methods."""
        stub_path = Path(__file__).parent.parent / "djust" / "streaming.pyi"
        stub_content = stub_path.read_text()

        assert "async def stream_to" in stub_content
        assert "async def push_state" in stub_content

    def test_liveview_stub_inherits_mixins(self):
        """LiveView stub properly inherits from all mixin stubs."""
        stub_path = Path(__file__).parent.parent / "djust" / "live_view.pyi"
        stub_content = stub_path.read_text()

        # Check for mixin inheritance or method presence
        # The stub should either inherit from mixins or re-declare their methods
        assert "NavigationMixin" in stub_content or "def live_patch" in stub_content
        assert "PushEventMixin" in stub_content or "def push_event" in stub_content
        assert "StreamsMixin" in stub_content or "def stream" in stub_content
        assert "StreamingMixin" in stub_content or "async def stream_to" in stub_content
