"""
Test type stubs for Rust extension module.

These tests verify that:
1. Type stub files (.pyi) exist for Rust-exported functions and classes
2. Type checkers can properly validate calls to Rust functions
3. IDEs can provide autocomplete for Rust-exported APIs
"""

import ast
from pathlib import Path

import pytest


class TestRustTypeStubs:
    """Test that _rust.pyi stub file exists and is valid."""

    @pytest.fixture
    def stub_file_path(self):
        """Path to the _rust.pyi stub file."""
        djust_dir = Path(__file__).parent.parent / "djust"
        return djust_dir / "_rust.pyi"

    @pytest.fixture
    def rust_module(self):
        """Import the actual _rust module."""
        try:
            from djust import _rust

            return _rust
        except ImportError:
            pytest.skip("Rust extension not built")

    def test_stub_file_exists(self, stub_file_path):
        """Test that _rust.pyi stub file exists."""
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

    def test_stub_has_rustliveview_class(self, stub_file_path):
        """Test that RustLiveView class is defined in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "class RustLiveView" in content

    def test_stub_has_render_template_function(self, stub_file_path):
        """Test that render_template function is defined in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def render_template" in content

    def test_stub_has_diff_html_function(self, stub_file_path):
        """Test that diff_html function is defined in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def diff_html" in content

    def test_stub_has_session_actor_handle_class(self, stub_file_path):
        """Test that SessionActorHandle class is defined in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "class SessionActorHandle" in content

    def test_stub_has_create_session_actor_function(self, stub_file_path):
        """Test that create_session_actor function is defined in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def create_session_actor" in content

    def test_stub_has_extract_template_variables_function(self, stub_file_path):
        """Test that extract_template_variables function is defined in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        assert "def extract_template_variables" in content

    def test_stub_has_all_rust_components(self, stub_file_path):
        """Test that all Rust component classes are in stub."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()

        # All Rust components from djust_components
        components = [
            "RustAlert",
            "RustAvatar",
            "RustBadge",
            "RustButton",
            "RustCard",
            "RustDivider",
            "RustIcon",
            "RustModal",
            "RustProgress",
            "RustRange",
            "RustSpinner",
            "RustSwitch",
            "RustTextArea",
            "RustToast",
            "RustTooltip",
        ]

        for component in components:
            assert f"class {component}" in content, f"Missing {component} in stub"

    def test_stub_covers_all_exported_names(self, stub_file_path, rust_module):
        """Test that stub file covers all publicly exported names from _rust module."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        # Get all public names from the actual module
        public_names = [name for name in dir(rust_module) if not name.startswith("_")]

        # Parse stub file
        with open(stub_file_path) as f:
            content = f.read()
        tree = ast.parse(content)

        # Extract all defined names from stub
        stub_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                stub_names.add(node.name)
            elif isinstance(node, ast.FunctionDef):
                stub_names.add(node.name)

        # Check that all public names are in stub
        missing = set(public_names) - stub_names
        # __all__ is special and defined as a list, not a function/class
        missing.discard("__all__")

        assert not missing, f"Stub missing definitions for: {missing}"

    def test_stub_has_proper_type_annotations(self, stub_file_path):
        """Test that stub functions have proper type annotations."""
        if not stub_file_path.exists():
            pytest.skip("Stub file does not exist yet")

        with open(stub_file_path) as f:
            content = f.read()
        tree = ast.parse(content)

        # Find render_template function
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "render_template":
                # Should have return annotation
                assert node.returns is not None, "render_template missing return type"

                # Should have parameter annotations
                for arg in node.args.args:
                    if arg.arg != "self":
                        assert (
                            arg.annotation is not None
                        ), f"Parameter {arg.arg} missing type annotation"
                break
        else:
            pytest.fail("render_template not found in stub")


class TestMyPyValidation:
    """Test that mypy can validate code using the stubs."""

    def test_stub_allows_mypy_to_catch_typos(self, tmp_path):
        """Test that mypy catches typos in LiveView method calls when using stubs."""
        # This is an integration test that would run mypy
        # For now, we'll just verify the stub structure supports this
        pytest.skip("Integration test - requires mypy setup")

    def test_stub_provides_autocomplete_info(self):
        """Test that IDEs can use stub for autocomplete."""
        # This would be tested with an IDE integration
        pytest.skip("Integration test - requires IDE setup")
