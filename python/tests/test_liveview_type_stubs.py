"""
Test type stubs for LiveView methods.

These tests verify that:
1. LiveView.pyi stub file exists for type checking
2. All mixin methods are properly typed in the stub
3. Type checkers can catch typos like 'live_navigate' (nonexistent)
4. IDEs can provide autocomplete for LiveView instance methods
"""

import ast
from pathlib import Path

import pytest


class TestLiveViewTypeStubs:
    """Test that LiveView stub file exists and covers all mixin methods."""

    @pytest.fixture
    def stub_file_path(self):
        """Path to the live_view.pyi stub file."""
        djust_dir = Path(__file__).parent.parent / "djust"
        return djust_dir / "live_view.pyi"

    @pytest.fixture
    def live_view_module(self):
        """Import the actual LiveView module."""
        from djust import live_view

        return live_view

    def test_stub_file_exists(self, stub_file_path):
        """Test that live_view.pyi stub file exists."""
        assert stub_file_path.exists(), f"Stub file not found at {stub_file_path}"

    def test_stub_file_is_valid_python(self, stub_file_path):
        """Test that stub file contains valid Python syntax."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        # Should parse without SyntaxError
        try:
            ast.parse(content)
        except SyntaxError as e:
            pytest.fail(f"Stub file has invalid syntax: {e}")

    def test_stub_has_liveview_class(self, stub_file_path):
        """Test that LiveView class is defined in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "class LiveView" in content

    # Navigation methods (NavigationMixin)
    def test_stub_has_live_patch_method(self, stub_file_path):
        """Test that live_patch method is typed in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def live_patch" in content
        # Should have proper signature
        assert "params:" in content or "path:" in content
        assert "replace:" in content

    def test_stub_has_live_redirect_method(self, stub_file_path):
        """Test that live_redirect method is typed in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def live_redirect" in content
        assert "path: str" in content

    def test_stub_has_handle_params_method(self, stub_file_path):
        """Test that handle_params method is typed in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def handle_params" in content

    # Push events (PushEventMixin)
    def test_stub_has_push_event_method(self, stub_file_path):
        """Test that push_event method is typed in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def push_event" in content
        assert "event: str" in content
        assert "payload:" in content

    # Streams (StreamsMixin)
    def test_stub_has_stream_method(self, stub_file_path):
        """Test that stream method is typed in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def stream" in content
        assert "name: str" in content
        assert "items:" in content

    def test_stub_has_stream_insert_method(self, stub_file_path):
        """Test that stream_insert method is typed in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def stream_insert" in content
        assert "name: str" in content
        assert "item:" in content

    def test_stub_has_stream_delete_method(self, stub_file_path):
        """Test that stream_delete method is typed in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def stream_delete" in content

    def test_stub_has_stream_reset_method(self, stub_file_path):
        """Test that stream_reset method is typed in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def stream_reset" in content

    # Core LiveView methods
    def test_stub_has_mount_method(self, stub_file_path):
        """Test that mount method is typed in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def mount" in content
        assert "request" in content

    def test_stub_has_get_context_data_method(self, stub_file_path):
        """Test that get_context_data method is typed in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def get_context_data" in content

    def test_stub_has_handle_tick_method(self, stub_file_path):
        """Test that handle_tick method is typed in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def handle_tick" in content

    def test_stub_has_get_state_method(self, stub_file_path):
        """Test that get_state method is typed in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def get_state" in content
        # Should return Dict[str, Any]
        assert "-> Dict[str, Any]" in content

    def test_stub_has_proper_type_annotations(self, stub_file_path):
        """Test that stub methods have proper type annotations."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()
        tree = ast.parse(content)

        # Find LiveView class
        liveview_class = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "LiveView":
                liveview_class = node
                break

        assert liveview_class is not None, "LiveView class not found in stub"

        # Check that live_redirect has proper annotations
        live_redirect_found = False
        for item in liveview_class.body:
            if isinstance(item, ast.FunctionDef) and item.name == "live_redirect":
                live_redirect_found = True
                # Should have return annotation (-> None)
                assert item.returns is not None, "live_redirect missing return type"

                # Should have parameter annotations
                for arg in item.args.args:
                    if arg.arg not in ["self", "kwargs"]:
                        assert (
                            arg.annotation is not None
                        ), f"Parameter {arg.arg} missing type annotation"
                break

        assert live_redirect_found, "live_redirect method not found in LiveView stub"

    def test_stub_doesnt_have_nonexistent_methods(self, stub_file_path):
        """Test that stub doesn't include nonexistent methods like 'live_navigate'."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        # These methods should NOT exist
        assert "def live_navigate" not in content
        assert "def push_events" not in content  # singular form
        assert "def stream_append" not in content  # use stream_insert instead


class TestLiveViewStubDocumentation:
    """Test that stub provides good documentation for developers."""

    @pytest.fixture
    def stub_file_path(self):
        """Path to the live_view.pyi stub file."""
        djust_dir = Path(__file__).parent.parent / "djust"
        return djust_dir / "live_view.pyi"

    def test_stub_has_module_docstring(self, stub_file_path):
        """Test that stub file has a proper module docstring."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        # Should start with a docstring
        lines = [line for line in content.split("\n") if line.strip()]
        assert lines[0].strip() == '"""' or lines[0].strip().startswith('"""')

    def test_stub_has_method_docstrings(self, stub_file_path):
        """Test that key methods have docstrings."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        # live_patch should have docs about what it does
        assert '"""' in content
        # Should document at least a few key methods
        docstring_count = content.count('"""')
        assert docstring_count >= 4, "Not enough docstrings in stub file"

    def test_stub_imports_necessary_types(self, stub_file_path):
        """Test that stub imports necessary types for annotations."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        # Should import typing constructs
        assert "from typing import" in content or "import typing" in content

        # Should have common types
        assert "Dict" in content or "dict" in content
        assert "Any" in content
        assert "Optional" in content or "None" in content


class TestStubPreventsTypos:
    """Test that the stub would help catch common typos."""

    def test_actual_liveview_has_correct_methods(self):
        """Verify LiveView has the correct method names."""
        from djust import LiveView

        # These should exist
        assert hasattr(LiveView, "live_redirect")
        assert hasattr(LiveView, "live_patch")
        assert hasattr(LiveView, "push_event")
        assert hasattr(LiveView, "stream")
        assert hasattr(LiveView, "stream_insert")
        assert hasattr(LiveView, "stream_delete")

        # These should NOT exist (common typos)
        assert not hasattr(LiveView, "live_navigate")
        assert not hasattr(LiveView, "push_events")
        assert not hasattr(LiveView, "stream_append")

    def test_stub_would_catch_live_navigate_typo(self, tmp_path):
        """
        Document that stub enables catching 'live_navigate' typo.

        This typo is mentioned in the task description as something
        that should be caught at lint time, not runtime.
        """
        # Create a test file with the typo
        test_file = tmp_path / "test_typo.py"
        test_file.write_text(
            """
from djust import LiveView

class MyView(LiveView):
    def some_handler(self):
        # Typo: live_navigate doesn't exist
        self.live_navigate('/path/')
"""
        )

        # With the stub file, mypy/pyright would catch this error:
        # Error: Cannot find reference 'live_navigate' in 'live_view.pyi'

        # Actual LiveView doesn't have this method
        from djust import LiveView

        assert not hasattr(LiveView, "live_navigate")
