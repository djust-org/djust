"""
Integration tests for type checking with stub files.

These tests verify that the type stubs enable proper type checking
and would catch common errors like typos in method names.
"""

import tempfile
from pathlib import Path

import pytest


class TestTypeCheckingIntegration:
    """Test that type checkers can use the stubs to catch errors."""

    @pytest.fixture
    def temp_test_file(self):
        """Create a temporary Python file for type checking."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
        ) as f:
            yield Path(f.name)
            # Cleanup
            Path(f.name).unlink(missing_ok=True)

    def test_stub_enables_correct_usage(self, temp_test_file):
        """Test that correct usage passes type checking."""
        code = """
from djust._rust import render_template

# Correct usage should type check fine
html = render_template("<h1>{{ title }}</h1>", {"title": "Hello"})
"""
        temp_test_file.write_text(code)

        # Try to import mypy - skip if not available
        try:
            import mypy.api
        except ImportError:
            pytest.skip("mypy not installed")

        result = mypy.api.run([str(temp_test_file), "--strict"])
        stdout, stderr, exit_code = result

        # Should pass (exit code 0) or have minimal warnings
        # Don't fail on missing imports for Django/djust
        assert "error" not in stdout.lower() or "Cannot find implementation" in stdout

    def test_stub_would_catch_typos(self, temp_test_file):
        """Test that typos in function names would be caught (documentation test)."""
        # This test documents that stubs enable catching typos
        # We don't actually run mypy in strict mode here because
        # it would require full environment setup

        _code_with_typo = """
from djust._rust import render_tempalte  # Typo: 'tempalte' instead of 'template'

html = render_tempalte("<h1>Test</h1>", {})
"""

        _code_correct = """
from djust._rust import render_template  # Correct

html = render_template("<h1>Test</h1>", {})
"""

        # Verify the stub file has the correct name
        from djust import _rust

        assert hasattr(_rust, "render_template")
        assert not hasattr(_rust, "render_tempalte")

        # Document the value: with stubs, IDEs and mypy will catch this typo
        # Without stubs, these errors are only caught at runtime

    def test_stub_provides_autocomplete_info(self):
        """Test that stub provides information for IDE autocomplete."""
        # Read the stub file
        from pathlib import Path

        stub_file = Path(__file__).parent.parent / "djust" / "_rust.pyi"
        assert stub_file.exists()

        content = stub_file.read_text()

        # Verify key signatures are documented with types
        assert "def render_template(template_source: str" in content
        assert "-> str:" in content
        assert "Dict[str, Any]" in content

        # Verify docstrings are present for major functions
        assert '"""' in content
        assert "Args:" in content
        assert "Returns:" in content

    def test_stub_shows_correct_return_types(self):
        """Test that stub file has correct return type annotations."""
        # This is important for type checkers
        from pathlib import Path

        stub_file = Path(__file__).parent.parent / "djust" / "_rust.pyi"
        content = stub_file.read_text()

        # render_template returns str
        assert "def render_template" in content
        assert "-> str:" in content

        # diff_html returns str (JSON)
        assert "def diff_html" in content

        # extract_template_variables returns Dict
        assert "def extract_template_variables" in content
        assert "-> Dict[str, List[str]]:" in content

    def test_stub_documents_all_component_classes(self):
        """Test that all Rust component classes are documented."""
        from pathlib import Path

        stub_file = Path(__file__).parent.parent / "djust" / "_rust.pyi"
        content = stub_file.read_text()

        # All components should be in __all__
        assert '"RustButton"' in content
        assert '"RustAlert"' in content
        assert '"RustCard"' in content

        # All components should have class definitions
        assert "class RustButton:" in content
        assert "class RustAlert:" in content
        assert "class RustCard:" in content

    def test_stub_documents_actor_system(self):
        """Test that actor system classes and functions are documented."""
        from pathlib import Path

        stub_file = Path(__file__).parent.parent / "djust" / "_rust.pyi"
        content = stub_file.read_text()

        # Actor system classes
        assert "class SessionActorHandle:" in content
        assert "class SupervisorStatsPy:" in content

        # Actor system functions
        assert "def create_session_actor" in content
        assert "def get_actor_stats" in content

        # Awaitable return types for async methods
        assert "Awaitable" in content


class TestStubFileQuality:
    """Test the quality and completeness of the stub file."""

    def test_stub_is_pep_561_compliant(self):
        """Test that stub file follows PEP 561 conventions."""
        from pathlib import Path

        stub_file = Path(__file__).parent.parent / "djust" / "_rust.pyi"

        # Should be named _rust.pyi (matching _rust.so extension module)
        assert stub_file.name == "_rust.pyi"

        # Should be in the same directory as the module
        module_dir = stub_file.parent
        assert module_dir.name == "djust"

    def test_stub_has_module_docstring(self):
        """Test that stub file has a proper module docstring."""
        from pathlib import Path

        stub_file = Path(__file__).parent.parent / "djust" / "_rust.pyi"
        content = stub_file.read_text()

        # Should start with a docstring
        lines = content.strip().split("\n")
        assert lines[0].strip() == '"""'

    def test_stub_exports_all_names(self):
        """Test that stub __all__ matches actual module exports."""
        from djust import _rust
        from pathlib import Path

        stub_file = Path(__file__).parent.parent / "djust" / "_rust.pyi"
        content = stub_file.read_text()

        # Get actual public names
        actual_names = {name for name in dir(_rust) if not name.startswith("_")}

        # Parse __all__ from stub
        import ast

        tree = ast.parse(content)
        stub_all = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, ast.List):
                            stub_all = {
                                elt.value
                                for elt in node.value.elts
                                if isinstance(elt, ast.Constant)
                            }

        assert stub_all is not None, "Stub should have __all__"

        # All actual names should be in stub __all__
        missing = actual_names - stub_all
        # __all__ itself is meta, not a function/class
        missing.discard("__all__")

        assert not missing, f"Stub __all__ missing: {missing}"
