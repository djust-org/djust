"""
State Fingerprint Optimization for djust LiveViews.

This module implements a fingerprint-based optimization system that tracks
which parts of the state have changed between renders, allowing:

1. **Incremental State Sync**: Only sync changed state keys to Rust
2. **Template Section Caching**: Cache rendered HTML for unchanged template sections
3. **Assign Tracking**: Track which assigns actually changed

Usage:
    class MyLiveView(LiveView):
        # Enable fingerprinting for specific assigns
        fingerprinted_assigns = ['items', 'user']

        # Or use the @fingerprint decorator for fine-grained control
        @fingerprint('items', hash_fn=lambda x: hash(tuple(x.values_list('id'))))
        def get_items(self):
            return Item.objects.filter(user=self.user)

Inspired by Phoenix LiveView's "fingerprint tree" optimization which can
reduce data transfer by 90%+ for large, mostly-static pages.
"""

import hashlib
import json
import logging
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def _compute_hash(value: Any) -> str:
    """
    Compute a stable hash for any Python value.

    Uses JSON serialization for consistent hashing across process restarts.
    Falls back to repr() for non-JSON-serializable objects.
    """
    try:
        # Try JSON serialization first (stable across restarts)
        serialized = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        # Fallback to repr for complex objects
        serialized = repr(value)

    return hashlib.md5(serialized.encode(), usedforsecurity=False).hexdigest()[:16]


class StateFingerprint:
    """
    Tracks fingerprints of state values to detect changes efficiently.

    Instead of deep-comparing entire state dictionaries, we compute
    hashes of values and only mark things as changed when hashes differ.

    This is particularly useful for:
    - Large lists where only a few items changed
    - Nested objects where only leaf values changed
    - QuerySets that haven't changed between renders

    Example:
        fp = StateFingerprint()

        # First render - everything is "changed"
        changed = fp.update({'items': [1,2,3], 'user': 'john'})
        # changed = {'items', 'user'}

        # Second render - only items changed
        changed = fp.update({'items': [1,2,3,4], 'user': 'john'})
        # changed = {'items'}  (user fingerprint unchanged)
    """

    def __init__(self, hash_fn: Optional[Callable[[Any], str]] = None):
        """
        Initialize fingerprint tracker.

        Args:
            hash_fn: Optional custom hash function. Default uses _compute_hash.
        """
        self._fingerprints: Dict[str, str] = {}
        self._hash_fn = hash_fn or _compute_hash
        self._version = 0

    def update(self, state: Dict[str, Any]) -> Set[str]:
        """
        Update fingerprints and return set of changed keys.

        Args:
            state: Dictionary of key-value pairs to fingerprint

        Returns:
            Set of keys whose values have changed since last update
        """
        changed: Set[str] = set()

        for key, value in state.items():
            new_hash = self._hash_fn(value)
            old_hash = self._fingerprints.get(key)

            if old_hash != new_hash:
                changed.add(key)
                self._fingerprints[key] = new_hash

        # Check for removed keys
        removed_keys = set(self._fingerprints.keys()) - set(state.keys())
        changed.update(removed_keys)
        for key in removed_keys:
            del self._fingerprints[key]

        if changed:
            self._version += 1

        return changed

    def get_fingerprint(self, key: str) -> Optional[str]:
        """Get the current fingerprint for a key."""
        return self._fingerprints.get(key)

    def has_changed(self, key: str, value: Any) -> bool:
        """
        Check if a value has changed from its last fingerprint.

        Args:
            key: The key to check
            value: The current value

        Returns:
            True if value has changed or is new
        """
        current_hash = self._hash_fn(value)
        return self._fingerprints.get(key) != current_hash

    def clear(self):
        """Clear all fingerprints."""
        self._fingerprints.clear()
        self._version = 0

    @property
    def version(self) -> int:
        """Get the current version number (increments on changes)."""
        return self._version

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about fingerprint tracking."""
        return {
            "tracked_keys": len(self._fingerprints),
            "version": self._version,
            "keys": list(self._fingerprints.keys()),
        }


class SectionCache:
    """
    Caches rendered HTML for template sections based on fingerprints.

    When combined with StateFingerprint, this allows skipping template
    rendering for sections whose input data hasn't changed.

    Usage:
        cache = SectionCache()
        fingerprint = StateFingerprint()

        # Check if we can use cached section
        changed = fingerprint.update(context)
        if 'sidebar' not in changed:
            html = cache.get('sidebar', fingerprint.get_fingerprint('sidebar'))
            if html:
                # Use cached HTML instead of re-rendering
                pass
    """

    def __init__(self, max_size: int = 100):
        """
        Initialize section cache.

        Args:
            max_size: Maximum number of cached sections per LiveView
        """
        self._cache: Dict[str, Tuple[str, str]] = {}  # section -> (fingerprint, html)
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    def get(self, section: str, fingerprint: str) -> Optional[str]:
        """
        Get cached HTML for a section if fingerprint matches.

        Args:
            section: Section identifier
            fingerprint: Expected fingerprint

        Returns:
            Cached HTML if fingerprint matches, None otherwise
        """
        cached = self._cache.get(section)
        if cached and cached[0] == fingerprint:
            self._hits += 1
            return cached[1]
        self._misses += 1
        return None

    def set(self, section: str, fingerprint: str, html: str):
        """
        Cache HTML for a section with its fingerprint.

        Args:
            section: Section identifier
            fingerprint: Current fingerprint
            html: Rendered HTML
        """
        # Simple LRU: remove oldest if at max size
        if len(self._cache) >= self._max_size and section not in self._cache:
            # Remove first key (oldest)
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]

        self._cache[section] = (fingerprint, html)

    def invalidate(self, section: str):
        """Invalidate a specific section."""
        self._cache.pop(section, None)

    def clear(self):
        """Clear all cached sections."""
        self._cache.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total * 100 if total > 0 else 0

        return {
            "cached_sections": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_percent": round(hit_rate, 1),
        }


class IncrementalStateSync:
    """
    Optimizes state synchronization to Rust by only sending changed values.

    Instead of syncing the entire state dictionary on every render,
    this tracks what has changed and only sends deltas.

    This can significantly reduce serialization overhead for large states.

    Usage:
        sync = IncrementalStateSync()

        # Full state on first sync
        delta = sync.compute_delta({'items': [...], 'count': 100})
        # delta = {'items': [...], 'count': 100}

        # Only count changed
        delta = sync.compute_delta({'items': [...], 'count': 101})
        # delta = {'count': 101}  (items unchanged)
    """

    def __init__(self):
        self._fingerprint = StateFingerprint()
        self._last_state: Dict[str, Any] = {}

    def compute_delta(
        self, new_state: Dict[str, Any], force_keys: Optional[Set[str]] = None
    ) -> Tuple[Dict[str, Any], Set[str]]:
        """
        Compute state delta - only values that have changed.

        Args:
            new_state: New state dictionary
            force_keys: Optional set of keys to always include

        Returns:
            Tuple of (delta_dict, changed_keys)
        """
        force_keys = force_keys or set()
        changed_keys = self._fingerprint.update(new_state)

        # Include forced keys
        keys_to_send = changed_keys | force_keys

        delta = {k: v for k, v in new_state.items() if k in keys_to_send}
        self._last_state = new_state.copy()

        return delta, changed_keys

    def reset(self):
        """Reset sync state, forcing full sync on next call."""
        self._fingerprint.clear()
        self._last_state.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get synchronization statistics."""
        return {
            "fingerprint": self._fingerprint.get_stats(),
            "last_state_keys": list(self._last_state.keys()),
        }


def fingerprint(*keys: str, hash_fn: Optional[Callable[[Any], str]] = None):
    """
    Decorator to mark a method's return value as fingerprinted.

    The decorated method will have its result cached based on
    a hash of the specified state keys.

    Usage:
        class MyLiveView(LiveView):
            @fingerprint('user_id', 'filter')
            def get_items(self):
                return expensive_query(self.user_id, self.filter)

    Args:
        *keys: State keys that affect the method's output
        hash_fn: Optional custom hash function
    """

    def decorator(method: Callable) -> Callable:
        cache_attr = f"_fp_cache_{method.__name__}"
        hash_attr = f"_fp_hash_{method.__name__}"

        @wraps(method)
        def wrapper(self):
            # Compute hash of relevant state keys
            state_values = {k: getattr(self, k, None) for k in keys}
            current_hash = (hash_fn or _compute_hash)(state_values)

            # Check if cached value is still valid
            cached_hash = getattr(self, hash_attr, None)
            if cached_hash == current_hash:
                cached_value = getattr(self, cache_attr, None)
                if cached_value is not None:
                    return cached_value

            # Compute new value and cache
            result = method(self)
            setattr(self, cache_attr, result)
            setattr(self, hash_attr, current_hash)

            return result

        return wrapper

    return decorator


# Mixin class for LiveViews that want fingerprint optimization
class FingerprintMixin:
    """
    Mixin to add fingerprint-based optimization to LiveViews.

    Usage:
        class MyLiveView(FingerprintMixin, LiveView):
            fingerprinted_assigns = ['items', 'user']

            def mount(self, request, **params):
                self.items = []
                self.user = None

    The mixin automatically tracks changes to fingerprinted assigns
    and provides methods to query what has changed.
    """

    # Override in subclass to specify which assigns to fingerprint
    fingerprinted_assigns: List[str] = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._state_fingerprint = StateFingerprint()
        self._section_cache = SectionCache()
        self._incremental_sync = IncrementalStateSync()

    def _get_fingerprinted_state(self) -> Dict[str, Any]:
        """Get dictionary of fingerprinted assigns."""
        return {
            key: getattr(self, key, None)
            for key in self.fingerprinted_assigns
            if hasattr(self, key)
        }

    def get_changed_assigns(self) -> Set[str]:
        """
        Get set of assigns that have changed since last check.

        Call this before rendering to determine what needs updating.
        """
        state = self._get_fingerprinted_state()
        return self._state_fingerprint.update(state)

    def get_cached_section(self, section: str) -> Optional[str]:
        """
        Get cached HTML for a section if still valid.

        Args:
            section: Section name

        Returns:
            Cached HTML or None if invalid/missing
        """
        fingerprint = self._state_fingerprint.get_fingerprint(section)
        if fingerprint:
            return self._section_cache.get(section, fingerprint)
        return None

    def cache_section(self, section: str, html: str):
        """
        Cache rendered HTML for a section.

        Args:
            section: Section name
            html: Rendered HTML
        """
        fingerprint = self._state_fingerprint.get_fingerprint(section)
        if fingerprint:
            self._section_cache.set(section, fingerprint, html)

    def get_optimization_stats(self) -> Dict[str, Any]:
        """Get statistics about fingerprint optimizations."""
        return {
            "fingerprint": self._state_fingerprint.get_stats(),
            "section_cache": self._section_cache.get_stats(),
            "incremental_sync": self._incremental_sync.get_stats(),
        }
