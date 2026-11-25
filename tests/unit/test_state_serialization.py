"""
Tests for LiveView state serialization validation.

Verifies that an explicit view's get_state() returns only its ``client=True`` fields.
"""

import pytest

from djust import state
from djust._exposure import ExposureError
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


# ---------------------------------------------------------------------------
# get_state tests
# ---------------------------------------------------------------------------


class _ClientView(LiveView):
    """A bare explicit LiveView whose client-visible fields are the ones under test."""

    exposure_policy = "explicit"
    count = state(0, client=True)
    name = state("", client=True)
    items = state(default_factory=list, client=True)
    hidden = state("server only")
    payload = state(None, client=True)
    good = state("", client=True)
    bad = state(None, client=True)


class TestGetState:
    """``get_state()`` hands the client only the declared ``client=True`` fields."""

    def _make_view(self, **attrs):
        view = _ClientView()
        for k, v in attrs.items():
            setattr(view, k, v)
        return view

    def test_serializable_state_returned(self):
        view = self._make_view(count=5, name="test", items=[1, 2, 3])
        state = view.get_state()
        assert state["count"] == 5
        assert state["name"] == "test"
        assert state["items"] == [1, 2, 3]

    def test_only_client_fields_are_returned(self):
        view = self._make_view(count=1, hidden="secret")
        view._internal = "secret"
        view.undeclared = "secret"
        view.my_func = lambda: None
        state = view.get_state()
        assert state["count"] == 1
        for name in ("hidden", "_internal", "undeclared", "my_func"):
            assert name not in state, name

    @pytest.mark.parametrize(
        "make",
        [FakeS3Client, PaymentService, DatabaseConnection, ApiSession],
    )
    def test_a_non_serializable_client_field_is_refused_not_skipped(self, make):
        view = self._make_view(count=1, payload=make())
        with pytest.raises(ExposureError, match="Explicit state projection unavailable") as caught:
            view.get_state()
        assert make.__name__ not in str(caught.value)

    def test_one_bad_field_refuses_the_whole_projection(self):
        view = self._make_view(good="ok", bad=FakeS3Client())
        with pytest.raises(ExposureError):
            view.get_state()
