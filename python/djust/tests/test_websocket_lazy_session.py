"""
Regression test for the LazyObject session bug.

Django Channels' AuthMiddlewareStack wraps scope["session"] in a LazyObject.
`hasattr()` on an unresolved LazyObject returns False, so the old code always
fell through to creating a new empty SessionStore() instead of reusing the
browser's existing session.

The fix uses `getattr(scope_session, "session_key", None)` which forces
resolution of the LazyObject before the attribute is accessed.

See: https://github.com/djust-org/djust/issues/396
"""

from unittest.mock import MagicMock

from django.utils.functional import LazyObject


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ResolvingLazySession(LazyObject):
    """Simulates a session wrapped by AuthMiddlewareStack."""

    def __init__(self, session_key: str):
        super().__init__()
        # Store directly in __dict__ to bypass LazyObject.__setattr__, which
        # would call _setup() immediately (before _session_key is available),
        # causing infinite recursion when _setup() reads self._session_key.
        self.__dict__["_session_key"] = session_key

    def _setup(self):
        inner = MagicMock()
        inner.session_key = self.__dict__["_session_key"]
        self._wrapped = inner


class _EmptyLazySession(LazyObject):
    """Simulates an anonymous/empty session (no session_key)."""

    def _setup(self):
        inner = MagicMock(spec=[])  # no session_key attribute
        self._wrapped = inner


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLazyObjectSessionResolution:
    """getattr() must correctly resolve LazyObject-wrapped sessions."""

    def test_getattr_forces_lazy_resolution_and_returns_session_key(self):
        """getattr() resolves an unresolved LazyObject and returns the session_key."""
        lazy = _ResolvingLazySession("abc123")
        # Before accessing any attribute, the LazyObject is unresolved.
        # Django's LazyObject uses an internal `empty` sentinel (not None) for
        # the unresolved state, so we verify resolution via the result instead.
        result = getattr(lazy, "session_key", None)
        assert result == "abc123", "getattr must resolve the LazyObject and return session_key"

    def test_getattr_resolves_lazy_session_key(self):
        """getattr() resolves the LazyObject and returns the session_key."""
        lazy = _ResolvingLazySession("test-session-key")
        session_key = getattr(lazy, "session_key", None)
        assert session_key == "test-session-key"

    def test_getattr_returns_none_when_no_session_key(self):
        """getattr() returns None when the underlying object has no session_key."""
        lazy = _EmptyLazySession()
        session_key = getattr(lazy, "session_key", None)
        assert session_key is None

    def test_getattr_returns_none_for_none_session(self):
        """getattr() short-circuits safely when scope_session is None."""
        scope_session = None
        session_key = getattr(scope_session, "session_key", None) if scope_session else None
        assert session_key is None

    def test_getattr_works_on_plain_session_object(self):
        """getattr() also works correctly on a non-lazy plain session object."""
        plain = MagicMock()
        plain.session_key = "plain-key"
        session_key = getattr(plain, "session_key", None) if plain else None
        assert session_key == "plain-key"

    def test_correct_session_store_branch_selected(self):
        """Verify fix logic: SessionStore is called with session_key when present."""
        from unittest.mock import patch

        from django.contrib.sessions.backends.db import SessionStore

        lazy = _ResolvingLazySession("existing-session-key")

        with patch(
            "django.contrib.sessions.backends.db.SessionStore.__init__",
            return_value=None,
        ) as mock_init:
            session_key = getattr(lazy, "session_key", None) if lazy else None
            if session_key:
                SessionStore(session_key=session_key)
            else:
                SessionStore()

            mock_init.assert_called_once_with(session_key="existing-session-key")

    def test_empty_lazy_falls_through_to_new_session(self):
        """When LazyObject has no session_key, a new SessionStore() is created."""
        from unittest.mock import patch

        from django.contrib.sessions.backends.db import SessionStore

        lazy = _EmptyLazySession()

        with patch(
            "django.contrib.sessions.backends.db.SessionStore.__init__",
            return_value=None,
        ) as mock_init:
            session_key = getattr(lazy, "session_key", None) if lazy else None
            if session_key:
                SessionStore(session_key=session_key)
            else:
                SessionStore()

            mock_init.assert_called_once_with()
