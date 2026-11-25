"""
Tests for debug state size visualization functionality.

Tests _debug_state_sizes() method in PostProcessingMixin and its integration
with get_debug_info() and get_debug_update().

An explicit view reports a size only for a field declared ``client=True``: the
debug projection never walks the instance, never reports a ``sys.getsizeof``
memory figure, and reads every other field as ``"[redacted]"`` with no size.
"""

import json

from djust import LiveView
from djust.decorators import state


def _size(value):
    """The projected size: compact JSON, UTF-8 encoded as is."""
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


class TestDebugStateSizes:
    """Test _debug_state_sizes() method and state size reporting."""

    def test_debug_state_sizes_basic(self):
        """A client field reports its serialized size; the memory figure is never reported."""

        class SimpleView(LiveView):
            exposure_policy = "explicit"
            template_name = "test.html"

            count = state(42, client=True)
            name = state("test", client=True)
            items = state(default_factory=lambda: [1, 2, 3], client=True)

        view = SimpleView()

        sizes = view._debug_state_sizes()

        assert set(sizes) == {"count", "name", "items"}
        for field in ("count", "name", "items"):
            assert set(sizes[field]) == {"memory", "serialized"}
            assert sizes[field]["memory"] is None
        assert sizes["count"]["serialized"] == _size(42)
        assert sizes["items"]["serialized"] == _size([1, 2, 3])

    def test_debug_state_sizes_report_only_declared_fields(self):
        """Undeclared and private attributes, and a field with no ``client=True`` grant,
        carry no size: the report is the declared fields, redacted unless granted."""

        class PrivateView(LiveView):
            exposure_policy = "explicit"
            template_name = "test.html"

            shown = state("visible", client=True)
            held = state("SIZE_SENTINEL")

            def mount(self, request, **kwargs):
                self.public_var = "undeclared"
                self._private_var = "hidden"

        view = PrivateView()
        view.mount(None)

        sizes = view._debug_state_sizes()

        assert set(sizes) == {"shown", "held"}
        assert sizes["shown"]["serialized"] > 0
        assert sizes["held"] == {"memory": None, "serialized": None}
        assert "SIZE_SENTINEL" not in json.dumps(sizes)

    def test_debug_state_sizes_follow_declaration_order(self):
        """The size report keys are the declared fields, in declaration order."""

        class OrderedView(LiveView):
            exposure_policy = "explicit"
            template_name = "test.html"

            zebra = state(1, client=True)
            alpha = state(2, client=True)
            middle = state(3, client=True)

        sizes = OrderedView()._debug_state_sizes()

        assert list(sizes) == ["zebra", "alpha", "middle"]

    def test_debug_state_sizes_serialized_byte_accuracy(self):
        """The serialized size is the UTF-8 byte count of the projected JSON."""

        class StringView(LiveView):
            exposure_policy = "explicit"
            template_name = "test.html"

            ascii = state("test", client=True)
            unicode = state("tëst", client=True)  # ë is 2 bytes in UTF-8
            emoji = state("😀", client=True)

        sizes = StringView()._debug_state_sizes()

        # The compact JSON text, with its quotes, in UTF-8 as written.
        assert sizes["ascii"]["serialized"] == len('"test"'.encode())  # 6 bytes
        assert sizes["unicode"]["serialized"] == len('"tëst"'.encode())  # 7 bytes
        assert sizes["emoji"]["serialized"] == len('"😀"'.encode())  # 6 bytes

    def test_debug_state_sizes_in_get_debug_info(self):
        """Test that state_sizes is included in get_debug_info() response."""

        class InfoView(LiveView):
            exposure_policy = "explicit"
            template_name = "test.html"

            count = state(100, client=True)

        view = InfoView()

        debug_info = view.get_debug_info()

        assert "state_sizes" in debug_info
        assert "count" in debug_info["state_sizes"]
        assert debug_info["state_sizes"]["count"]["serialized"] == _size(100)

    def test_debug_state_sizes_in_get_debug_update(self):
        """Test that state_sizes is included in get_debug_update() response."""

        class UpdateView(LiveView):
            exposure_policy = "explicit"
            template_name = "test.html"

            count = state(0, client=True)

        view = UpdateView()

        view.count = 4242  # Update state

        debug_update = view.get_debug_update()

        assert "state_sizes" in debug_update
        assert debug_update["state_sizes"]["count"]["serialized"] == _size(4242)

    def test_debug_state_sizes_empty_view(self):
        """Test size report for a view that declares no state."""

        class EmptyView(LiveView):
            exposure_policy = "explicit"
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self._private_only = "hidden"

        view = EmptyView()
        view.mount(None)

        sizes = view._debug_state_sizes()

        # Should return empty dict
        assert sizes == {}
