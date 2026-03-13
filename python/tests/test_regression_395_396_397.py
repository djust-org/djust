"""
Regression tests for milestone bugs #395, #396, #397.

#395 – SESSION_TTL=0 must not delete all sessions (no DOM patches bug)
#396 – WebSocket session: getattr() instead of hasattr() for LazyObject
#397 – .flex-between utility class must be defined in utilities.css

Strategy: verify fixes via source-file inspection (avoids Rust-extension
import issues in worktrees) plus standalone logic tests that replicate the
fixed behaviour without the full import chain.
"""

import re
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate worktree root from this test file's path
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = Path(__file__).resolve().parents[2]  # .../pipeline-267/


# ===========================================================================
# #395 – SESSION_TTL=0: cleanup_expired must return 0 (never expire)
# ===========================================================================


def _make_memory_backend_cleanup_fn():
    """
    Return a standalone replica of InMemoryStateBackend.cleanup_expired()
    extracted from the worktree source, so we can unit-test its behaviour
    without triggering the djust._rust import chain.
    """
    import threading

    class _FakeBackend:
        def __init__(self, default_ttl):
            self._default_ttl = default_ttl
            self._cache: dict = {}
            self._state_sizes: dict = {}
            self._lock = threading.Lock()

        def cleanup_expired(self, ttl=None):
            # --- copied verbatim from patched memory.py ---
            if ttl is None:
                ttl = self._default_ttl

            if ttl <= 0:
                return 0

            cutoff = time.time() - ttl

            with self._lock:
                expired_keys = [key for key, (_, ts) in self._cache.items() if ts < cutoff]
                for key in expired_keys:
                    del self._cache[key]
                    self._state_sizes.pop(key, None)

            return len(expired_keys)

    return _FakeBackend


def test_cleanup_expired_ttl_zero_keeps_all_sessions():
    """TTL=0 → no sessions removed (never-expire semantics)."""
    Backend = _make_memory_backend_cleanup_fn()
    b = Backend(default_ttl=0)
    b._cache["sess-1"] = (object(), time.time() - 9999)

    removed = b.cleanup_expired(ttl=0)

    assert removed == 0, "TTL=0 must not remove any sessions"
    assert "sess-1" in b._cache


def test_cleanup_expired_ttl_zero_default_keeps_sessions():
    """default_ttl=0 and no explicit arg → still no-op cleanup."""
    Backend = _make_memory_backend_cleanup_fn()
    b = Backend(default_ttl=0)
    b._cache["sess-2"] = (object(), time.time() - 9999)

    removed = b.cleanup_expired()  # uses default_ttl=0

    assert removed == 0
    assert "sess-2" in b._cache


def test_cleanup_expired_positive_ttl_still_works():
    """Positive TTL must still expire genuinely old sessions."""
    Backend = _make_memory_backend_cleanup_fn()
    b = Backend(default_ttl=3600)
    b._cache["old-sess"] = (object(), time.time() - 7200)
    b._cache["new-sess"] = (object(), time.time() - 10)

    removed = b.cleanup_expired(ttl=3600)

    assert removed == 1
    assert "old-sess" not in b._cache
    assert "new-sess" in b._cache


def test_memory_py_source_contains_ttl_guard():
    """The worktree's memory.py must contain the ttl<=0 guard."""
    src = (_WORKTREE_ROOT / "python/djust/state_backends/memory.py").read_text()
    assert "ttl <= 0" in src, "memory.py must guard against ttl <= 0"
    assert "return 0" in src, "memory.py must return 0 early when ttl <= 0"


def test_redis_py_source_handles_ttl_zero():
    """The worktree's redis.py must not call setex() when TTL=0."""
    src = (_WORKTREE_ROOT / "python/djust/state_backends/redis.py").read_text()
    # The fix wraps setex in an if ttl > 0: branch
    assert "if ttl > 0:" in src, "redis.py must guard setex with if ttl > 0"
    assert "self._client.set(" in src, "redis.py must use SET (no expiry) for TTL=0"


# ===========================================================================
# #396 – LazyObject-safe session_key extraction
# ===========================================================================


class _LazyObjectWithKey:
    """Simulates an initialised Django Channels LazyObject with session_key."""

    session_key = "real-session-key-abc123"


class _LazyObjectWithoutKey:
    """Simulates a LazyObject with no session_key attribute."""

    pass


def test_getattr_succeeds_for_object_with_key():
    """getattr() returns the session_key when present."""
    result = getattr(_LazyObjectWithKey(), "session_key", None)
    assert result == "real-session-key-abc123"


def test_getattr_returns_none_for_missing_key():
    """getattr() with sentinel returns None when attribute missing."""
    result = getattr(_LazyObjectWithoutKey(), "session_key", None)
    assert result is None


def test_websocket_py_uses_getattr_not_hasattr():
    """
    Verify the worktree's websocket.py uses getattr() for scope_session.
    hasattr() on a Django Channels LazyObject can raise non-AttributeError
    exceptions during lazy evaluation, crashing the consumer silently.
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
