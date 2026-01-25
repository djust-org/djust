"""
Benchmarks for the Tag Handler Registry.

These tests measure the Python-side overhead of custom template tag handlers,
which is part of the overall ~15-50µs per tag invocation (as per ADR-001).

Note: The Rust-side registry overhead is measured separately in Rust benchmarks.
These benchmarks focus on the Python handler execution and context passing.
"""

import pytest


class MockTagHandler:
    """Mock tag handler for benchmarking."""

    def __init__(self, name):
        self.name = name

    def render(self, args, context):
        """Render method that mimics real tag handlers."""
        # Simple rendering - just format output
        output = f"<{self.name}"
        for i, arg in enumerate(args):
            output += f' arg{i}="{arg}"'
        output += " />"
        return output


class UrlTagHandler:
    """Mock URL tag handler (simulates {% url %})."""

    def render(self, args, context):
        """Simulate URL resolution."""
        if not args:
            return ""
        url_name = args[0].strip("'\"")
        # Simple mock - in real usage this calls Django's reverse()
        return f"/mock/{url_name}/"


class StaticTagHandler:
    """Mock static tag handler (simulates {% static %})."""

    STATIC_URL = "/static/"

    def render(self, args, context):
        """Simulate static file URL generation."""
        if not args:
            return ""
        path = args[0].strip("'\"")
        return f"{self.STATIC_URL}{path}"


class TestTagHandlerExecution:
    """Benchmarks for tag handler execution."""

    @pytest.mark.benchmark(group="tag_handler_exec")
    def test_simple_handler(self, benchmark):
        """Benchmark simple tag handler execution."""
        handler = MockTagHandler("button")
        args = ["class='btn'", "type='submit'"]
        context = {}

        result = benchmark(handler.render, args, context)
        assert "<button" in result

    @pytest.mark.benchmark(group="tag_handler_exec")
    def test_url_handler(self, benchmark):
        """Benchmark URL tag handler."""
        handler = UrlTagHandler()
        args = ["'home'"]
        context = {}

        result = benchmark(handler.render, args, context)
        assert "/mock/home/" == result

    @pytest.mark.benchmark(group="tag_handler_exec")
    def test_static_handler(self, benchmark):
        """Benchmark static tag handler."""
        handler = StaticTagHandler()
        args = ["'css/style.css'"]
        context = {}

        result = benchmark(handler.render, args, context)
        assert "/static/css/style.css" == result


class TestTagHandlerWithContext:
    """Benchmarks for tag handlers that use context."""

    @pytest.mark.benchmark(group="tag_handler_context")
    def test_handler_with_simple_context(self, benchmark, simple_context):
        """Benchmark handler with simple context lookup."""

        class ContextAwareHandler:
            def render(self, args, context):
                name = context.get("name", "")
                return f"<span>{name}</span>"

        handler = ContextAwareHandler()
        args = []

        result = benchmark(handler.render, args, simple_context)
        assert "World" in result

    @pytest.mark.benchmark(group="tag_handler_context")
    def test_handler_with_nested_context(self, benchmark, nested_context):
        """Benchmark handler with nested context lookup."""

        class DeepContextHandler:
            def render(self, args, context):
                user = context.get("user", {})
                profile = user.get("profile", {})
                settings = profile.get("settings", {})
                theme = settings.get("theme", "default")
                return f'<div data-theme="{theme}"></div>'

        handler = DeepContextHandler()
        args = []

        result = benchmark(handler.render, args, nested_context)
        assert 'data-theme="dark"' in result


class TestTagHandlerRegistration:
    """Benchmarks for tag handler registration operations."""

    @pytest.mark.benchmark(group="tag_registry_ops")
    def test_handler_instantiation(self, benchmark):
        """Benchmark handler object creation."""

        def create_handler():
            return MockTagHandler("test")

        handler = benchmark(create_handler)
        assert handler.name == "test"

    @pytest.mark.benchmark(group="tag_registry_ops")
    def test_registry_simulation(self, benchmark):
        """Benchmark dictionary-based registry lookup (simulates Rust registry)."""
        # Simulate the Python-side registry lookup
        registry = {f"tag-{i}": MockTagHandler(f"tag-{i}") for i in range(50)}

        def lookup_and_render():
            handler = registry.get("tag-25")
            if handler:
                return handler.render(["arg1"], {})
            return ""

        result = benchmark(lookup_and_render)
        assert "<tag-25" in result


class TestMultipleTagHandlers:
    """Benchmarks for multiple tag handler invocations."""

    @pytest.mark.benchmark(group="tag_handler_multi")
    def test_sequence_of_handlers(self, benchmark):
        """Benchmark sequence of tag handler calls."""
        handlers = [
            MockTagHandler("btn"),
            UrlTagHandler(),
            StaticTagHandler(),
            MockTagHandler("div"),
            MockTagHandler("span"),
        ]

        def render_all():
            results = []
            for handler in handlers:
                results.append(handler.render(["'test'"], {}))
            return results

        results = benchmark(render_all)
        assert len(results) == 5

    @pytest.mark.benchmark(group="tag_handler_multi")
    def test_repeated_handler_calls(self, benchmark):
        """Benchmark repeated calls to same handler."""
        handler = MockTagHandler("li")
        items = [f"item-{i}" for i in range(20)]

        def render_list():
            return [handler.render([item], {}) for item in items]

        results = benchmark(render_list)
        assert len(results) == 20


class TestRustPythonInterop:
    """Benchmarks for Rust-Python interop (when available)."""

    @pytest.mark.benchmark(group="rust_python_interop")
    def test_register_handler(self, benchmark):
        """Benchmark registering a handler with the Rust registry."""
        try:
            from djust._rust import (
                register_tag_handler,
                unregister_tag_handler,
                clear_tag_handlers,
            )

            # Clear first
            clear_tag_handlers()

            handler = MockTagHandler("bench-tag")

            def register_and_unregister():
                register_tag_handler("bench-tag", handler)
                unregister_tag_handler("bench-tag")

            benchmark(register_and_unregister)

        except ImportError:
            pytest.skip("Rust extension not available")

    @pytest.mark.benchmark(group="rust_python_interop")
    def test_check_handler_exists(self, benchmark):
        """Benchmark checking if handler exists in Rust registry."""
        try:
            from djust._rust import (
                register_tag_handler,
                has_tag_handler,
                clear_tag_handlers,
            )

            clear_tag_handlers()
            handler = MockTagHandler("exists-tag")
            register_tag_handler("exists-tag", handler)

            result = benchmark(has_tag_handler, "exists-tag")
            assert result is True

            clear_tag_handlers()

        except ImportError:
            pytest.skip("Rust extension not available")

    @pytest.mark.benchmark(group="rust_python_interop")
    def test_get_registered_tags(self, benchmark):
        """Benchmark getting list of registered tags."""
        try:
            from djust._rust import (
                register_tag_handler,
                get_registered_tags,
                clear_tag_handlers,
            )

            clear_tag_handlers()

            # Register several handlers
            for i in range(10):
                handler = MockTagHandler(f"tag-{i}")
                register_tag_handler(f"tag-{i}", handler)

            result = benchmark(get_registered_tags)
            assert len(result) == 10

            clear_tag_handlers()

        except ImportError:
            pytest.skip("Rust extension not available")
