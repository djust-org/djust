"""
Regression tests for InMemoryStateBackend.delete_all() — issue #395 follow-up.

The fix for #395 changed cleanup_expired(ttl=0) to mean "never expire".
That broke the ``djust clear --all`` CLI path which called cleanup_expired(ttl=0)
to delete every session.  The fix adds delete_all() with unambiguous semantics.
"""

from unittest.mock import MagicMock

from djust.state_backends.memory import InMemoryStateBackend


def _make_backend(default_ttl=300):
    return InMemoryStateBackend(default_ttl=default_ttl)


def _add_session(backend, key="sess1"):
    view = MagicMock()
    view.get_state_size.return_value = 0
    backend.set(key, view)
    return view


class TestDeleteAll:
    def test_delete_all_removes_every_session(self):
        backend = _make_backend()
        for i in range(5):
            _add_session(backend, f"sess{i}")
        assert len(backend._cache) == 5

        removed = backend.delete_all()

        assert removed == 5
        assert len(backend._cache) == 0

    def test_delete_all_returns_zero_when_empty(self):
        backend = _make_backend()
        assert backend.delete_all() == 0

    def test_delete_all_is_independent_of_cleanup_expired(self):
        """delete_all() removes sessions regardless of what cleanup_expired does."""
        backend = _make_backend()
        for i in range(3):
            _add_session(backend, f"key{i}")

        removed = backend.delete_all()

        assert removed == 3
        assert len(backend._cache) == 0

    def test_delete_all_clears_state_sizes(self):
        backend = _make_backend()
        _add_session(backend)
        backend._state_sizes["sess1"] = 42

        backend.delete_all()

        assert backend._state_sizes == {}
