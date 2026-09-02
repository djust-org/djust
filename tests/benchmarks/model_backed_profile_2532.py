"""Five-bucket profile support for the model-backed render benchmark (#2532).

This module carries no Django models and no tests. It holds the pieces the
benchmark (``test_model_backed_render_2532.py``) and the terminal-summary hook
in ``conftest.py`` share:

* :class:`Crossings` — the caller-classified counter that
  ``djust.serialization._protect_sidecar_value`` and
  ``normalize_django_value`` are monkeypatched through. Rust resolves both by
  name on every call (``djust_core/src/context.rs`` ``protect_sidecar`` and
  ``djust_core/src/lib.rs`` ``normalize_django_value``), so a module-attribute
  patch is observed by the Rust walk. A call is a **Rust-origin boundary
  crossing** only when it happens while the Rust differ is running
  (``in_rust_render`` is set by the benchmark around
  ``HtmlRenderer.render_with_diff``) and its direct caller is not the
  serializer's own recursion. Everything else is Python-side work the
  eager/JIT path does before Rust is entered and is reported separately, never
  subtracted blindly — the demo context processors add four constant
  Python-side wraps per render (``rust_bridge.py`` wrapping ``WSGIRequest`` /
  ``AnonymousUser`` / ``PermWrapper`` / ``NavbarComponent``).
* :class:`PhaseRow` — one measured phase (mount or one event) of one variant.
* :data:`PROFILE_ROWS` — the process-wide accumulator the benchmark appends to
  and ``pytest_terminal_summary`` reads.
* :func:`summarize` / :func:`format_table` — medians per (variant, phase) and
  the printed five-bucket table.

Bucket definitions (per phase, per variant):

1. ``rust_ms`` — Rust render proper: the differ's ``render_ms`` minus the
   Python-side time of the Rust-origin crossings (bucket 2's Python half).
   The Rust-side cost of a crossing (``py.import`` + ``getattr`` +
   ``maybe_call`` per segment) cannot be timed from Python and stays inside
   this number; ``render_ms − render_ms(list_control)`` is its upper bound.
2. ``xings`` / ``xing_ms`` — direct Rust-origin boundary crossings: count and
   the Python-side time spent inside them (plus the ``proxy`` re-wraps below);
   ``proxy`` — transitive re-wraps the sidecar proxy performed inside one of
   those crossings (Python-internal, counted apart so ``xings`` stays exact);
   ``py_calls`` — calls made while Rust was not running (the eager/JIT path).
3. ``queries`` / ``sql_ms`` — ORM: statements issued and their wall time,
   measured by an ``execute_wrappers`` hook installed in the consumer's worker
   thread; ``list_ms`` (mount only) is the queryset instantiation wall time.
4. ``state_ms`` — state serialization on the event path:
   ``_sync_state_to_rust`` wall time minus the ``get_context_data`` calls it
   made (normalise + change-detect + ``update_state``); ``jit_ms`` is
   ``get_context_data`` (the JIT/eager serialization); ``persist_ms`` is
   ``ViewRuntime._persist_state_after_event`` (0 unless the view opted into
   ``enable_state_snapshot``).
5. ``parse_ms`` / ``diff_ms`` / ``ser_ms`` and ``fast`` — HTML parse + VDOM
   diff + serialization, tagged by which text fast path the differ reports
   (``RenderTiming::fast_path``: ``-`` mount, ``frag`` fragment, ``region``
   text-region, ``full`` parse + diff).

No number here is asserted against a threshold. The assertions the benchmark
makes are on counts and flags (crossings, query counts, the fast-path flag),
which are deterministic under load (v1.0.5-4 rule).
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "BUCKET_COLUMNS",
    "Crossings",
    "FAST_PATH_LABELS",
    "PROFILE_ROWS",
    "PhaseRow",
    "fast_path_label",
    "format_table",
    "install_crossing_counters",
    "summarize",
    "write_json_if_requested",
]


#: ``RenderTiming::fast_path`` values (crates/djust_live/src/lib.rs, #2532).
FAST_PATH_LABELS = {0.0: "full", 1.0: "frag", 2.0: "region"}


def fast_path_label(value: Optional[float]) -> str:
    """Human label for the differ's ``fast_path`` timing value (``-`` = mount)."""
    if value is None:
        return "-"
    return FAST_PATH_LABELS.get(float(value), f"?{value}")


# ---------------------------------------------------------------------------
# Bucket 2 — caller-classified boundary-crossing counter
# ---------------------------------------------------------------------------


class Crossings:
    """Counts calls into the two Python entry points the Rust walk uses.

    ``record(fn_name, secs, caller_file)`` classifies one call:

    * ``"rust"`` — a direct Rust→Python call: made while :attr:`in_rust_render`
      is set for the calling thread and the direct caller is not
      ``djust/serialization.py`` itself.
    * ``"proxy"`` — made while :attr:`in_rust_render` is set but from inside
      ``djust/serialization.py``: the sidecar proxy's transitive re-wrap of a
      resolved attribute (``_SidecarQuerySetProxy`` / model proxy
      ``__getattr__`` → ``_protect_sidecar_value``) or ``normalize_django_value``
      recursing during a Rust-side ``Value`` extraction. Python work that ONE
      Rust ``getattr`` set off, not a separate crossing — counted separately
      so the direct count stays exact. (The spike's 357 = 252 direct
      ``_protect_sidecar_value`` + 100 of these + 5 Python-side.)
    * ``"python"`` — every call made while Rust is not running (the eager/JIT
      path, the ``rust_bridge.py`` sidecar wraps, the serializer's recursion
      there).

    The classifier is a pure function so it can be unit-tested without Django:
    :meth:`classify`.
    """

    SERIALIZER_FILE_SUFFIX = os.path.join("djust", "serialization.py")

    def __init__(self) -> None:
        self._local = threading.local()
        self.reset()

    # -- thread-local "Rust is running" flag ---------------------------------
    @property
    def in_rust_render(self) -> bool:
        return bool(getattr(self._local, "in_rust_render", False))

    @in_rust_render.setter
    def in_rust_render(self, value: bool) -> None:
        self._local.in_rust_render = bool(value)

    # -- counters -------------------------------------------------------------
    def reset(self) -> None:
        self.rust_calls = 0
        self.rust_secs = 0.0
        self.proxy_calls = 0
        self.proxy_secs = 0.0
        self.python_calls = 0
        self.python_secs = 0.0
        self.kinds: Dict[str, int] = {}

    @classmethod
    def classify(cls, *, in_rust_render: bool, caller_file: str) -> str:
        """Pure classification rule shared by both patched entry points."""
        if not in_rust_render:
            return "python"
        if caller_file.endswith(cls.SERIALIZER_FILE_SUFFIX):
            return "proxy"
        return "rust"

    def record(self, kind: str, secs: float, caller_file: str) -> str:
        origin = self.classify(in_rust_render=self.in_rust_render, caller_file=caller_file)
        if origin == "rust":
            self.rust_calls += 1
            self.rust_secs += secs
            self.kinds[kind] = self.kinds.get(kind, 0) + 1
        elif origin == "proxy":
            self.proxy_calls += 1
            self.proxy_secs += secs
        else:
            self.python_calls += 1
            self.python_secs += secs
        return origin


def install_crossing_counters(counter: Crossings) -> Callable[[], None]:
    """Monkeypatch the two Rust-called serializer entry points through ``counter``.

    Returns a restore callable. Patched by module attribute because that is
    how Rust resolves them (``py.import("djust.serialization").getattr(...)``
    per call), so the patch is observed on the Rust-origin path — the only
    path bucket 2 is about.
    """
    import djust.serialization as ser

    orig_protect = ser._protect_sidecar_value
    orig_normalize = ser.normalize_django_value

    def protect(value: Any) -> Any:
        caller = sys._getframe(1).f_code.co_filename
        t0 = time.perf_counter()
        try:
            return orig_protect(value)
        finally:
            counter.record(type(value).__name__, time.perf_counter() - t0, caller)

    def normalize(value: Any, *args: Any, **kwargs: Any) -> Any:
        caller = sys._getframe(1).f_code.co_filename
        t0 = time.perf_counter()
        try:
            return orig_normalize(value, *args, **kwargs)
        finally:
            counter.record(f"normalize:{type(value).__name__}", time.perf_counter() - t0, caller)

    ser._protect_sidecar_value = protect  # type: ignore[assignment]
    ser.normalize_django_value = normalize  # type: ignore[assignment]

    def restore() -> None:
        ser._protect_sidecar_value = orig_protect  # type: ignore[assignment]
        ser.normalize_django_value = orig_normalize  # type: ignore[assignment]

    return restore


# ---------------------------------------------------------------------------
# Rows + accumulator
# ---------------------------------------------------------------------------


@dataclass
class PhaseRow:
    """One measured phase (``mount`` or one event) of one variant."""

    variant: str
    phase: str
    frame_type: str
    total_ms: float
    # bucket 1
    render_ms: float
    # bucket 2
    xings: int
    xing_ms: float
    py_xings: int
    proxy_xings: int = 0
    xing_kinds: Dict[str, int] = field(default_factory=dict)
    # bucket 3
    queries: int = 0
    sql_ms: float = 0.0
    list_ms: float = 0.0
    # bucket 4
    sync_ms: float = 0.0
    jit_ms: float = 0.0
    persist_ms: float = 0.0
    # bucket 5
    parse_ms: float = 0.0
    diff_ms: float = 0.0
    ser_ms: float = 0.0
    fast_path: Optional[float] = None
    patches: Dict[str, int] = field(default_factory=dict)

    @property
    def rust_ms(self) -> float:
        """Bucket 1: Rust render proper (render_ms minus the crossings' Python time)."""
        return max(self.render_ms - self.xing_ms, 0.0)

    @property
    def state_ms(self) -> float:
        """Bucket 4: state serialization minus the JIT part."""
        return max(self.sync_ms - self.jit_ms, 0.0)

    @property
    def fast(self) -> Optional[bool]:
        """``True`` when the differ reports either text fast path; ``None`` on mount."""
        if self.fast_path is None:
            return None
        return self.fast_path > 0.0

    @property
    def vdom_ms(self) -> float:
        """Bucket 5: parse + diff + serialize."""
        return self.parse_ms + self.diff_ms + self.ser_ms


#: Process-wide accumulator. The benchmark appends one :class:`PhaseRow` per
#: measured round; ``pytest_terminal_summary`` prints the medians.
PROFILE_ROWS: List[PhaseRow] = []


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

#: Column order of the printed table. Each entry is (header, PhaseRow attribute).
BUCKET_COLUMNS: Sequence[Tuple[str, str]] = (
    ("variant", "variant"),
    ("phase", "phase"),
    ("total", "total_ms"),
    ("1 rust", "rust_ms"),
    ("2 xings", "xings"),
    ("2 proxy", "proxy_xings"),
    ("2 xing_ms", "xing_ms"),
    ("py_calls", "py_xings"),
    ("3 q", "queries"),
    ("3 sql_ms", "sql_ms"),
    ("3 list_ms", "list_ms"),
    ("4 state", "state_ms"),
    ("4 jit", "jit_ms"),
    ("4 persist", "persist_ms"),
    ("5 parse", "parse_ms"),
    ("5 diff", "diff_ms"),
    ("5 ser", "ser_ms"),
    ("5 fast", "fast"),
)

_NUMERIC = {
    "total_ms",
    "rust_ms",
    "xing_ms",
    "sql_ms",
    "list_ms",
    "state_ms",
    "jit_ms",
    "persist_ms",
    "parse_ms",
    "diff_ms",
    "ser_ms",
    "render_ms",
    "sync_ms",
}
_COUNTS = {"xings", "proxy_xings", "py_xings", "queries"}


def summarize(rows: Iterable[PhaseRow]) -> List[Dict[str, Any]]:
    """Median every numeric column per (variant, phase), in first-seen order.

    Counts and the fast-path flag must agree across rounds of the same
    (variant, phase) — they are deterministic — so their median is exact;
    the flag is reported as the label of the median ``fast_path`` value.
    """
    groups: Dict[Tuple[str, str], List[PhaseRow]] = {}
    for row in rows:
        groups.setdefault((row.variant, row.phase), []).append(row)
    out: List[Dict[str, Any]] = []
    for (variant, phase), rs in groups.items():
        entry: Dict[str, Any] = {"variant": variant, "phase": phase, "rounds": len(rs)}
        for name in _NUMERIC:
            entry[name] = round(statistics.median(getattr(r, name) for r in rs), 3)
        for name in _COUNTS:
            entry[name] = int(statistics.median(getattr(r, name) for r in rs))
        fps = [r.fast_path for r in rs if r.fast_path is not None]
        entry["fast_path"] = statistics.median(fps) if fps else None
        entry["fast"] = fast_path_label(entry["fast_path"])
        entry["frame_type"] = rs[0].frame_type
        entry["patches"] = rs[0].patches
        entry["xing_kinds"] = rs[0].xing_kinds
        out.append(entry)
    return out


def format_table(rows: Iterable[PhaseRow]) -> str:
    """Render the five-bucket table (medians) as aligned plain text."""
    summary = summarize(rows)
    if not summary:
        return ""
    headers = [h for h, _ in BUCKET_COLUMNS]
    body: List[List[str]] = []
    for entry in summary:
        cells: List[str] = []
        for _header, attr in BUCKET_COLUMNS:
            value = entry[attr]
            if isinstance(value, float):
                cells.append(f"{value:.2f}")
            else:
                cells.append(str(value))
        body.append(cells)
    widths = [max(len(h), *(len(r[i]) for r in body)) for i, h in enumerate(headers)]
    lines = [
        "  ".join(
            h.rjust(w) if i > 1 else h.ljust(w) for i, (h, w) in enumerate(zip(headers, widths))
        )
    ]
    lines.append("  ".join("-" * w for w in widths))
    for cells in body:
        lines.append(
            "  ".join(
                c.rjust(w) if i > 1 else c.ljust(w) for i, (c, w) in enumerate(zip(cells, widths))
            )
        )
    return "\n".join(lines)


def write_json_if_requested(
    rows: Iterable[PhaseRow], env: Optional[Dict[str, str]] = None
) -> Optional[str]:
    """Dump the raw rows + medians to ``$DJUST_BENCH_TABLE_JSON`` when it is set."""
    env = os.environ if env is None else env
    path = env.get("DJUST_BENCH_TABLE_JSON")
    if not path:
        return None
    rows = list(rows)
    payload = {
        "rows": [asdict(r) for r in rows],
        "medians": summarize(rows),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, default=str)
    return path
