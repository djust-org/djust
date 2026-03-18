"""
Tests for debug state size visualization functionality.

Tests _debug_state_sizes() method in PostProcessingMixin and its integration
with get_debug_info() and get_debug_update().
"""

import json
import sys
from djust import LiveView


class TestDebugStateSizes:
    """Test _debug_state_sizes() method and state size reporting."""

    def test_debug_state_sizes_basic(self):
        """Test basic state size calculation for simple types."""

        class SimpleView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.count = 42
                self.name = "test"
                self.items = [1, 2, 3]

        view = SimpleView()
        view.mount(None)

        sizes = view._debug_state_sizes()

        # Should include public attributes
        assert "count" in sizes
        assert "name" in sizes
        assert "items" in sizes

        # Should have memory and serialized keys
        assert "memory" in sizes["count"]
        assert "serialized" in sizes["count"]
        assert "memory" in sizes["name"]
        assert "serialized" in sizes["name"]

        # Should be positive integers
        assert sizes["count"]["memory"] > 0
        assert sizes["count"]["serialized"] > 0

    def test_debug_state_sizes_excludes_private(self):
        """Test that private attributes are excluded from size report."""

        class PrivateView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.public_var = "visible"
                self._private_var = "hidden"
                self.__double_private = "also hidden"

        view = PrivateView()
        view.mount(None)

        sizes = view._debug_state_sizes()

        assert "public_var" in sizes
        assert "_private_var" not in sizes
        assert "__double_private" not in sizes
        assert "_PrivateView__double_private" not in sizes  # name-mangled

    def test_debug_state_sizes_excludes_callables(self):
        """Test that methods are excluded from size report."""

        class MethodView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.count = 0
                self.handler = self.increment  # method reference

            def increment(self):
                self.count += 1

        view = MethodView()
        view.mount(None)

        sizes = view._debug_state_sizes()

        assert "count" in sizes
        assert "handler" not in sizes  # callable excluded
        assert "increment" not in sizes  # method excluded
        assert "mount" not in sizes  # inherited method excluded

    def test_debug_state_sizes_handles_nonserializable(self):
        """Test handling of objects that cannot be JSON serialized."""

        class ComplexObject:
            def __str__(self):
                return "<ComplexObject instance>"

        class NonSerializableView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.simple = "test"
                self.complex = ComplexObject()

        view = NonSerializableView()
        view.mount(None)

        sizes = view._debug_state_sizes()

        # Simple should have both sizes
        assert sizes["simple"]["memory"] > 0
        assert sizes["simple"]["serialized"] > 0

        # Complex has default=str fallback, so it serializes to str() repr
        # Memory is still tracked even if repr is used
        assert sizes["complex"]["memory"] > 0
        # default=str fallback is used, so serialized is the str length not None
        assert sizes["complex"]["serialized"] > 0

    def test_debug_state_sizes_sorted_output(self):
        """Test that size report keys are sorted alphabetically."""

        class UnsortedView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.zebra = 1
                self.alpha = 2
                self.middle = 3

        view = UnsortedView()
        view.mount(None)

        sizes = view._debug_state_sizes()
        keys = list(sizes.keys())

        # Should be alphabetically sorted
        assert keys == ["alpha", "middle", "zebra"]

    def test_debug_state_sizes_serialized_byte_accuracy(self):
        """Test that serialized size accurately reflects JSON-encoded byte count."""

        class StringView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.ascii = "test"  # 4 bytes
                self.unicode = "tëst"  # ë becomes \u00eb in JSON (6-char escape)
                self.emoji = "😀"  # 😀 becomes surrogate pair \uD83D\uDE00 in JSON

        view = StringView()
        view.mount(None)

        sizes = view._debug_state_sizes()

        # JSON serialized size includes quotes and uses \u escapes for unicode
        # "test" = 6 bytes ("test" with quotes)
        assert sizes["ascii"]["serialized"] == 6

        # "tëst" = 11 bytes ("t\u00ebst" with quotes — ë escaped as 6-char \u00eb)
        assert sizes["unicode"]["serialized"] == 11

        # "😀" = 14 bytes ("\uD83D\uDE00" with quotes — emoji escaped as 12-char surrogate pair)
        assert sizes["emoji"]["serialized"] == 14

    def test_debug_state_sizes_in_get_debug_info(self):
        """Test that state_sizes is included in get_debug_info() response."""

        class InfoView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.count = 100

        view = InfoView()
        view.mount(None)

        debug_info = view.get_debug_info()

        assert "state_sizes" in debug_info
        assert "count" in debug_info["state_sizes"]
        assert debug_info["state_sizes"]["count"]["memory"] > 0
        assert debug_info["state_sizes"]["count"]["serialized"] > 0

    def test_debug_state_sizes_in_get_debug_update(self):
        """Test that state_sizes is included in get_debug_update() response."""

        class UpdateView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.count = 0

        view = UpdateView()
        view.mount(None)

        view.count = 42  # Update state

        debug_update = view.get_debug_update()

        assert "state_sizes" in debug_update
        assert "count" in debug_update["state_sizes"]

    def test_debug_state_sizes_memory_uses_getsizeof(self):
        """Test that memory size uses sys.getsizeof."""

        class MemoryView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.small = 1
                self.large = [i for i in range(1000)]

        view = MemoryView()
        view.mount(None)

        sizes = view._debug_state_sizes()

        # Large list should have significantly more memory than small int
        assert sizes["large"]["memory"] > sizes["small"]["memory"]

        # Verify memory matches sys.getsizeof
        assert sizes["small"]["memory"] == sys.getsizeof(1)
        assert sizes["large"]["memory"] == sys.getsizeof(view.large)

    def test_debug_state_sizes_with_default_fallback(self):
        """Test that default=str fallback handles edge case objects."""

        from datetime import datetime

        class DateView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.now = datetime(2024, 1, 1, 12, 0, 0)

        view = DateView()
        view.mount(None)

        sizes = view._debug_state_sizes()

        # datetime should be serializable via default=str
        assert "now" in sizes
        assert sizes["now"]["memory"] > 0
        assert sizes["now"]["serialized"] > 0

        # Serialized form should include str() representation
        serialized = json.dumps(view.now, default=str)
        assert sizes["now"]["serialized"] == len(serialized.encode("utf-8"))

    def test_debug_state_sizes_empty_view(self):
        """Test size report for view with no public state variables."""

        class EmptyView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self._private_only = "hidden"

        view = EmptyView()
        view.mount(None)

        sizes = view._debug_state_sizes()

        # Should return empty dict
        assert sizes == {}
