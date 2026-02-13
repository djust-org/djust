"""
Tests for LiveView state serialization validation.

Verifies that _is_serializable() correctly identifies safe vs unsafe values,
and that get_state() raises TypeError in DEBUG mode or logs in production.
"""

import io
import logging
import socket
import threading

import pytest

from djust.live_view import LiveView


# ---------------------------------------------------------------------------
# Helpers: mock service-like objects
# ---------------------------------------------------------------------------


class FakeS3Client:
    """Simulates an AWS-style client stored in state."""

    pass


class PaymentService:
    """Simulates a payment gateway service instance."""

    pass


class DatabaseConnection:
    """Simulates a raw DB connection object."""

    pass


class ApiSession:
    """Simulates an API session wrapper."""

    pass


# ---------------------------------------------------------------------------
# _is_serializable tests
# ---------------------------------------------------------------------------


class TestIsSerializable:
    """Test the _is_serializable static method."""

    @pytest.mark.parametrize(
        "value",
        [
            None,
            True,
            False,
            0,
            42,
            3.14,
            "",
            "hello",
            [],
            [1, 2, 3],
            {},
            {"key": "value"},
            (),
            (1, 2),
        ],
    )
    def test_primitives_and_collections_are_serializable(self, value):
        assert LiveView._is_serializable(value) is True

    def test_nested_list_is_serializable(self):
        assert LiveView._is_serializable([1, [2, [3]]]) is True

    def test_nested_dict_is_serializable(self):
        assert LiveView._is_serializable({"a": {"b": "c"}}) is True

    def test_file_handle_not_serializable(self):
        f = io.StringIO("test")
        assert LiveView._is_serializable(f) is False
        f.close()

    def test_thread_not_serializable(self):
        t = threading.Thread(target=lambda: None)
        assert LiveView._is_serializable(t) is False

    def test_lock_not_serializable(self):
        lock = threading.Lock()
        assert LiveView._is_serializable(lock) is False

    def test_socket_not_serializable(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            assert LiveView._is_serializable(s) is False
        finally:
            s.close()

    def test_service_instance_not_serializable(self):
        assert LiveView._is_serializable(FakeS3Client()) is False

    def test_client_instance_not_serializable(self):
        assert LiveView._is_serializable(FakeS3Client()) is False

    def test_payment_service_not_serializable(self):
        assert LiveView._is_serializable(PaymentService()) is False

    def test_database_connection_not_serializable(self):
        assert LiveView._is_serializable(DatabaseConnection()) is False

    def test_api_session_not_serializable(self):
        assert LiveView._is_serializable(ApiSession()) is False


# ---------------------------------------------------------------------------
# get_state tests
# ---------------------------------------------------------------------------


class TestGetState:
    """Test the get_state method on LiveView instances."""

    def _make_view(self, **attrs):
        """Create a LiveView instance with given public attributes."""
        view = LiveView()
        for k, v in attrs.items():
            setattr(view, k, v)
        return view

    def test_serializable_state_returned(self, settings):
        settings.DEBUG = True
        view = self._make_view(count=5, name="test", items=[1, 2, 3])
        state = view.get_state()
        assert state["count"] == 5
        assert state["name"] == "test"
        assert state["items"] == [1, 2, 3]

    def test_private_attributes_skipped(self, settings):
        settings.DEBUG = True
        view = self._make_view(count=1)
        view._internal = "secret"
        state = view.get_state()
        assert "_internal" not in state
        assert "count" in state

    def test_callable_attributes_skipped(self, settings):
        settings.DEBUG = True
        view = self._make_view(count=1)
        view.my_func = lambda: None
        state = view.get_state()
        assert "my_func" not in state

    def test_non_serializable_raises_in_debug(self, settings):
        settings.DEBUG = True
        view = self._make_view(s3=FakeS3Client())
        with pytest.raises(TypeError, match="Non-serializable value"):
            view.get_state()

    def test_non_serializable_raises_with_class_name(self, settings):
        settings.DEBUG = True
        view = self._make_view(payment=PaymentService())
        with pytest.raises(TypeError, match="LiveView.payment"):
            view.get_state()

    def test_non_serializable_raises_with_type_name(self, settings):
        settings.DEBUG = True
        view = self._make_view(payment=PaymentService())
        with pytest.raises(TypeError, match="PaymentService"):
            view.get_state()

    def test_non_serializable_raises_with_docs_link(self, settings):
        settings.DEBUG = True
        view = self._make_view(conn=DatabaseConnection())
        with pytest.raises(TypeError, match="docs/guides/services.md"):
            view.get_state()

    def test_non_serializable_logs_in_production(self, settings, caplog):
        settings.DEBUG = False
        view = self._make_view(count=1, s3=FakeS3Client())
        with caplog.at_level(logging.ERROR, logger="djust.live_view"):
            state = view.get_state()
        # Should not raise, should skip the bad attribute
        assert "s3" not in state
        assert "count" in state
        # Should have logged an error
        assert any("Non-serializable value" in r.message for r in caplog.records)

    def test_mixed_state_debug_raises_on_first_bad(self, settings):
        settings.DEBUG = True
        view = self._make_view(good="ok", bad=FakeS3Client())
        with pytest.raises(TypeError):
            view.get_state()

    def test_mixed_state_production_skips_bad(self, settings, caplog):
        settings.DEBUG = False
        view = self._make_view(good="ok", bad=ApiSession())
        with caplog.at_level(logging.ERROR, logger="djust.live_view"):
            state = view.get_state()
        assert state["good"] == "ok"
        assert "bad" not in state
