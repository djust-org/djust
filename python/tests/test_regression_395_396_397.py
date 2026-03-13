"""
Regression tests for milestone bugs #395, #396, #397.

#395 – SESSION_TTL=0 must not delete all sessions (no DOM patches bug)
#396 – WebSocket session: getattr() instead of hasattr() for LazyObject
#397 – .flex-between utility class must be defined in utilities.css
"""

import re
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Locate worktree root from this test file's path
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = Path(__file__).resolve().parents[2]  # .../pipeline-NNN/


# ===========================================================================
# #395 – SESSION_TTL=0: cleanup_expired must return 0 (never expire)
# ===========================================================================

try:
    from djust.state_backends.memory import InMemoryStateBackend

    _BACKEND_AVAILABLE = True
except ImportError:
    _BACKEND_AVAILABLE = False

_skip_if_no_backend = pytest.mark.skipif(
    not _BACKEND_AVAILABLE, reason="djust._rust extension not available in this environment"
)


@_skip_if_no_backend
def test_cleanup_expired_ttl_zero_keeps_sessions():
    """TTL=0 explicit arg → no sessions removed (never-expire semantics)."""
    backend = InMemoryStateBackend(default_ttl=3600)
    backend._cache["sess-1"] = (object(), time.time() - 9999)

    removed = backend.cleanup_expired(ttl=0)

    assert removed == 0, "TTL=0 must not remove any sessions"
    assert "sess-1" in backend._cache


@_skip_if_no_backend
def test_cleanup_expired_default_ttl_zero_keeps_sessions():
    """default_ttl=0 and no explicit arg → still no-op cleanup."""
    backend = InMemoryStateBackend(default_ttl=0)
    backend._cache["sess-2"] = (object(), time.time() - 9999)

    removed = backend.cleanup_expired()

    assert removed == 0, "default_ttl=0 must not remove any sessions"
    assert "sess-2" in backend._cache


@_skip_if_no_backend
def test_cleanup_expired_positive_ttl_still_works():
    """Positive TTL must still expire genuinely old sessions."""
    backend = InMemoryStateBackend(default_ttl=3600)
    backend._cache["old-sess"] = (object(), time.time() - 7200)
    backend._cache["new-sess"] = (object(), time.time() - 10)

    removed = backend.cleanup_expired(ttl=3600)

    assert removed == 1
    assert "old-sess" not in backend._cache
    assert "new-sess" in backend._cache


@_skip_if_no_backend
def test_cleanup_expired_negative_ttl_keeps_sessions():
    """Negative TTL is treated same as zero — no sessions removed."""
    backend = InMemoryStateBackend(default_ttl=-1)
    backend._cache["sess-3"] = (object(), time.time() - 9999)

    removed = backend.cleanup_expired()

    assert removed == 0
    assert "sess-3" in backend._cache


@_skip_if_no_backend
def test_cleanup_expired_empty_cache_returns_zero():
    """Empty cache must return 0 regardless of TTL."""
    backend = InMemoryStateBackend(default_ttl=3600)
    assert backend.cleanup_expired(ttl=3600) == 0


# ===========================================================================
# #396 – LazyObject-safe session_key extraction
# ===========================================================================


class _LazyObjectWithKey:
    """Simulates an initialised Django Channels LazyObject with session_key."""

    session_key = "real-session-key-abc123"


class _LazyObjectWithoutKey:
    """Simulates a LazyObject where session_key raises AttributeError (uninitialized)."""

    @property
    def session_key(self):
        raise AttributeError("LazyObject not yet initialized")


def test_getattr_succeeds_for_object_with_key():
    """getattr() returns the session_key when present."""
    result = getattr(_LazyObjectWithKey(), "session_key", None)
    assert result == "real-session-key-abc123"


def test_getattr_returns_none_for_missing_key():
    """getattr() with sentinel returns None when attribute missing."""
    result = getattr(_LazyObjectWithoutKey(), "session_key", None)
    assert result is None


def test_getattr_sentinel_handles_raising_lazy():
    """
    getattr() with a default safely handles LazyObject AttributeError.

    Django Channels LazyObjects raise AttributeError when accessed before
    initialization. getattr(obj, 'attr', None) returns the default instead
    of propagating the exception — the correct pattern for scope_session.
    """
    obj = _LazyObjectWithoutKey()

    # hasattr triggers the property, raises AttributeError — returns False
    assert not hasattr(obj, "session_key")

    # getattr with sentinel absorbs AttributeError, returns None
    result = getattr(obj, "session_key", None)
    assert result is None


def test_websocket_py_uses_getattr_not_hasattr():
    """
    Verify the worktree's websocket.py uses getattr() for scope_session.
    hasattr() + attribute access is two operations (TOCTOU on LazyObject).
    getattr() with a sentinel is atomic and idiomatic.
    """
    src = (_WORKTREE_ROOT / "python/djust/websocket.py").read_text()

    assert (
        'getattr(scope_session, "session_key", None)' in src
    ), "websocket.py must use getattr() with sentinel for LazyObject safety"
    assert (
        'hasattr(scope_session, "session_key")' not in src
    ), "websocket.py must not use hasattr() on scope_session"


# ===========================================================================
# #397 – .flex-between utility CSS class
# ===========================================================================


def test_flex_between_utility_defined_in_utilities_css():
    """utilities.css must define a .flex-between class."""
    css_path = _WORKTREE_ROOT / "examples/demo_project/demo_app/static/css/utilities.css"
    content = css_path.read_text()
    assert ".flex-between" in content, "utilities.css must define .flex-between"


def test_flex_between_sets_required_properties():
    """The .flex-between rule must include display:flex, flex-direction:row,
    and justify-content:space-between."""
    css_path = _WORKTREE_ROOT / "examples/demo_project/demo_app/static/css/utilities.css"
    content = css_path.read_text()

    match = re.search(r"\.flex-between\s*\{([^}]+)\}", content, re.DOTALL)
    assert match, ".flex-between rule block not found in utilities.css"

    rule = match.group(1)

    assert "display" in rule and "flex" in rule, ".flex-between must set display: flex"
    assert "flex-direction" in rule and "row" in rule, ".flex-between must set flex-direction: row"
    assert (
        "justify-content" in rule and "space-between" in rule
    ), ".flex-between must set justify-content: space-between"
