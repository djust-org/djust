"""AsyncResult — state wrapper for async data loading (v0.5.0).

A small, immutable value object that captures the three possible states of an
asynchronously-loaded piece of data:

    * ``loading`` — work scheduled / in-flight, no result yet.
    * ``ok``      — work completed successfully, ``.result`` holds the payload.
    * ``failed``  — work raised an exception, ``.error`` holds the exception.

Exactly one of the three booleans is ``True`` at any time. Templates read the
flags directly::

    {% if metrics.loading %}<div class="skeleton"></div>{% endif %}
    {% if metrics.ok %}<div>{{ metrics.result.total_users }}</div>{% endif %}
    {% if metrics.failed %}<div class="error">{{ metrics.error }}</div>{% endif %}

``AsyncResult`` is also truthy only when ``.ok`` is set, so a bare
``{% if metrics %}`` behaves like "has loaded successfully" — convenient for
templates that don't care about the failed vs loading distinction.

See :meth:`djust.mixins.async_work.AsyncWorkMixin.assign_async` for the
high-level API that produces these objects. Inspired by Phoenix LiveView's
``assign_async`` / ``AsyncResult``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class AsyncResult:
    """Immutable state wrapper for asynchronously-loaded data.

    Exactly one of ``loading`` / ``ok`` / ``failed`` is ``True``. ``result`` is
    populated only in the ``ok`` state; ``error`` is populated only in the
    ``failed`` state.

    Prefer the :meth:`pending`, :meth:`succeeded`, and :meth:`errored`
    constructors over instantiating the class directly — they guarantee the
    state-flag invariants.
    """

    loading: bool = True
    ok: bool = False
    failed: bool = False
    result: Any = None
    error: Optional[BaseException] = None

    def __post_init__(self) -> None:
        # Enforce the documented invariant: exactly one flag is True. This
        # catches direct-construction mistakes (e.g. `AsyncResult(loading=True,
        # ok=True)`) that would silently short-circuit template logic.
        flags_set = sum(1 for f in (self.loading, self.ok, self.failed) if f)
        if flags_set != 1:
            raise ValueError(
                f"AsyncResult must have exactly one of loading/ok/failed "
                f"set to True, got loading={self.loading}, ok={self.ok}, "
                f"failed={self.failed}. Use AsyncResult.pending(), .succeeded(), "
                f"or .errored() for safe construction."
            )
        if self.ok and self.error is not None:
            raise ValueError("AsyncResult(ok=True) cannot carry an error")
        if self.failed and self.error is None:
            raise ValueError("AsyncResult(failed=True) requires an error")

    @classmethod
    def pending(cls) -> "AsyncResult":
        """Return an ``AsyncResult`` in the loading state."""
        return cls(loading=True, ok=False, failed=False, result=None, error=None)

    @classmethod
    def succeeded(cls, result: Any) -> "AsyncResult":
        """Return an ``AsyncResult`` wrapping a successful loader result."""
        return cls(loading=False, ok=True, failed=False, result=result, error=None)

    @classmethod
    def errored(cls, error: BaseException) -> "AsyncResult":
        """Return an ``AsyncResult`` wrapping a loader exception."""
        return cls(loading=False, ok=False, failed=True, result=None, error=error)

    def __bool__(self) -> bool:
        """Truthy only in the ``ok`` state.

        Allows templates to write ``{% if metrics %}`` as shorthand for
        "finished loading successfully".
        """
        return self.ok

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict representation.

        Used by the JIT serialization path so templates can access
        ``{{ name.loading }}``, ``{{ name.ok }}``, ``{{ name.failed }}``,
        ``{{ name.result }}``, and ``{{ name.error }}`` after the dict
        is injected into template context. ``error`` is stringified
        (matches the ``@action`` decorator's error-recording shape).

        ``result`` is returned as-is — the surrounding ``normalize_django_value``
        call recurses into it. Closes #1274.
        """
        return {
            "loading": self.loading,
            "ok": self.ok,
            "failed": self.failed,
            "result": self.result,
            "error": str(self.error) if self.error is not None else None,
        }
